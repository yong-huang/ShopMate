/**
 * SSE client for the ShopMate chat endpoint (System Design 3.7).
 *
 * POST /api/chat and parse the `text/event-stream` response incrementally:
 * frames are separated by a blank line and carry an `event:` name plus a
 * JSON `data:` payload. The `shopmate_sid` identity cookie is issued by the
 * backend on the first call and reused by sending `credentials: "include"`.
 */

import { API_BASE_URL } from "@/lib/api-client";

/** One validated product card (§3.7 `product_card` payload — tool-verified). */
export interface ProductCardPayload {
  product_id: string;
  name_en: string;
  name_zh: string;
  price: number;
  currency: string;
  rating: number;
  image_url: string;
  match_hint: string;
}

/** One validated comparison matrix (§3.7 `comparison` payload). */
export interface ComparisonPayload {
  product_ids: string[];
  matrix: {
    columns: string[];
    rows: { label: string; values: Record<string, string | null> }[];
  };
  recommendation: string;
}

/** One parsed SSE event from the chat stream (§3.7 event contract). */
export type ChatSseEvent =
  | { type: "session"; session_id: string; user_id: string }
  | { type: "token"; text: string }
  | { type: "tool_call"; name: string; args_summary: string }
  | { type: "product_card"; card: ProductCardPayload }
  | { type: "comparison"; comparison: ComparisonPayload }
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

/** Build the comparison payload defensively (catalog-verified matrix only). */
function toComparison(data: Record<string, unknown>): ComparisonPayload | null {
  if (!Array.isArray(data.product_ids) || !data.product_ids.length || !data.matrix) {
    return null;
  }
  const matrix = (data.matrix ?? {}) as Record<string, unknown>;
  const columns = Array.isArray(matrix.columns) ? matrix.columns.map(String) : [];
  const rawRows = Array.isArray(matrix.rows) ? matrix.rows : [];
  const rows = rawRows.map((row) => {
    const record = (row ?? {}) as Record<string, unknown>;
    const values = (record.values ?? {}) as Record<string, unknown>;
    return {
      label: String(record.label ?? ""),
      values: Object.fromEntries(
        Object.entries(values).map(([key, value]) => [
          key,
          value === null || value === undefined ? null : String(value),
        ]),
      ),
    };
  });
  return {
    product_ids: data.product_ids.map(String),
    matrix: { columns, rows },
    recommendation: String(data.recommendation ?? ""),
  };
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
    case "product_card":
      // Validated payload only (backend guardrail, System Design 3.5).
      if (!data.product_id) return null;
      return {
        type: "product_card",
        card: {
          product_id: String(data.product_id),
          name_en: String(data.name_en ?? ""),
          name_zh: String(data.name_zh ?? ""),
          price: Number(data.price ?? 0),
          currency: String(data.currency ?? "USD"),
          rating: Number(data.rating ?? 0),
          image_url: String(data.image_url ?? ""),
          match_hint: String(data.match_hint ?? ""),
        },
      };
    case "comparison": {
      const comparison = toComparison(data);
      return comparison ? { type: "comparison", comparison } : null;
    }
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
      // cart_update arrives with T06.
      return null;
  }
}
