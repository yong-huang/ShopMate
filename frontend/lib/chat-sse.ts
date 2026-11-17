/**
 * SSE client for the ShopMate chat endpoint (System Design 3.7).
 *
 * POST /api/chat and parse the `text/event-stream` response incrementally:
 * frames are separated by a blank line and carry an `event:` name plus a
 * JSON `data:` payload. The `shopmate_sid` identity cookie is issued by the
 * backend on the first call and reused by sending `credentials: "include"`.
 */

import { API_BASE_URL } from "@/lib/api-client";

/** One parsed SSE event from the chat stream (§3.7 event contract). */
export type ChatSseEvent =
  | { type: "session"; session_id: string; user_id: string }
  | { type: "token"; text: string }
  | { type: "tool_call"; name: string; args_summary: string }
  | { type: "done"; message_id: number | null }
  | { type: "error"; code: string; message: string };

export interface ChatStreamHandlers {
  /** Called for every well-formed event, in wire order. */
  onEvent: (event: ChatSseEvent) => void;
  /** Abort signal so the widget can stop a stream mid-turn. */
  signal?: AbortSignal;
}

export class ChatError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ChatError";
    this.status = status;
  }
}

/**
 * Send one chat turn and consume its SSE stream.
 *
 * Resolves when the stream ends (after `done` or `error`); rejects on
 * transport failure. Events already delivered stay valid if the stream
 * errors late — the widget keeps the partial text and shows the failure.
 */
export async function sendChatMessage(
  sessionId: string,
  message: string,
  handlers: ChatStreamHandlers,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ session_id: sessionId, message }),
      signal: handlers.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ChatError("Cannot reach the ShopMate backend.");
  }

  if (!response.ok || !response.body) {
    let detail = `Chat request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { message?: string };
      if (body.message) detail = body.message;
    } catch {
      // Non-JSON error body: keep the status-based message.
    }
    throw new ChatError(detail, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseFrame(frame);
        if (event) handlers.onEvent(event);
        boundary = buffer.indexOf("\n\n");
      }
    }
    // Flush any trailing frame that lacks its final blank line.
    const event = parseFrame(buffer);
    if (event) handlers.onEvent(event);
  } finally {
    reader.releaseLock();
  }
}

/** Parse one SSE frame ("event: name\ndata: json") into a typed event. */
function parseFrame(frame: string): ChatSseEvent | null {
  let name = "";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      name = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (!name) return null;

  let data: Record<string, unknown>;
  try {
    data = dataLines.length ? (JSON.parse(dataLines.join("\n")) as Record<string, unknown>) : {};
  } catch {
    return null;
  }

  switch (name) {
    case "session":
      return {
        type: "session",
        session_id: String(data.session_id ?? ""),
        user_id: String(data.user_id ?? ""),
      };
    case "token":
      return { type: "token", text: String(data.text ?? "") };
    case "tool_call":
      return {
        type: "tool_call",
        name: String(data.name ?? ""),
        args_summary: String(data.args_summary ?? ""),
      };
    case "done":
      return {
        type: "done",
        message_id: typeof data.message_id === "number" ? data.message_id : null,
      };
    case "error":
      return {
        type: "error",
        code: String(data.code ?? "internal"),
        message: String(data.message ?? "Unknown chat error."),
      };
    default:
      // product_card / comparison / cart_update arrive in later tickets.
      return null;
  }
}
