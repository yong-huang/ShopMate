"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Maximize2, MessageCircle, Minimize2, Plus, Send, Square, X } from "lucide-react";

import { ChatError, sendChatMessage, type ChatSseEvent } from "@/lib/chat-sse";
import { Button } from "@/components/ui/button";

/**
 * Maya chat widget (ticket T04): floating launcher + expandable panel.
 *
 * Consumes the backend SSE stream (text streaming, tool-call progress) and
 * keeps one conversation thread per browser via a localStorage session id;
 * the anonymous `shopmate_sid` cookie is managed by the backend through
 * `credentials: "include"`. Product cards arrive in T05.
 */

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

const GREETING: ChatMessage = {
  role: "assistant",
  content:
    "Hi! I'm Maya, your ShopMate shopping assistant. What are you looking for today?\n\n你好！我是 ShopMate 购物助手 Maya。今天想看点什么？",
};

const SESSION_KEY = "shopmate_session_id";

function getOrCreateSessionId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  window.localStorage.setItem(SESSION_KEY, created);
  return created;
}

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [partial, setPartial] = useState("");
  const [toolProgress, setToolProgress] = useState<{
    name: string;
    args_summary: string;
  } | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState("");

  const partialRef = useRef("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSessionId(getOrCreateSessionId());
  }, []);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [messages, partial, toolProgress, open, expanded]);

  function startNewChat() {
    abortRef.current?.abort();
    const fresh = crypto.randomUUID();
    window.localStorage.setItem(SESSION_KEY, fresh);
    setSessionId(fresh);
    setMessages([GREETING]);
    setPartial("");
    partialRef.current = "";
    setErrorText(null);
    setToolProgress(null);
  }

  async function send() {
    const text = draft.trim();
    if (!text || streaming || !sessionId) return;

    setMessages((current) => [...current, { role: "user", content: text }]);
    setDraft("");
    setErrorText(null);
    setStreaming(true);
    setPartial("");
    partialRef.current = "";

    const controller = new AbortController();
    abortRef.current = controller;

    const handleEvent = (event: ChatSseEvent) => {
      switch (event.type) {
        case "session":
          // Cookie identity is handled server-side; nothing to store.
          break;
        case "token":
          partialRef.current += event.text;
          setPartial(partialRef.current);
          setToolProgress(null);
          break;
        case "tool_call":
          setToolProgress({ name: event.name, args_summary: event.args_summary });
          break;
        case "done":
          break;
        case "error":
          setErrorText(event.message);
          break;
      }
    };

    try {
      await sendChatMessage(sessionId, text, {
        signal: controller.signal,
        onEvent: handleEvent,
      });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        // User pressed stop; keep whatever streamed so far.
      } else if (cause instanceof ChatError) {
        setErrorText(cause.message);
      } else {
        setErrorText("Something went wrong while chatting. Please try again.");
      }
    } finally {
      if (partialRef.current) {
        const assistantText = partialRef.current;
        setMessages((current) => [...current, { role: "assistant", content: assistantText }]);
      }
      partialRef.current = "";
      setPartial("");
      setToolProgress(null);
      setStreaming(false);
      abortRef.current = null;
    }
  }

  const panelPosition = expanded
    ? "fixed inset-0 z-50 rounded-none border-0"
    : "fixed bottom-5 right-5 z-50 h-[560px] w-[min(380px,calc(100vw-2.5rem))] rounded-2xl";

  return (
    <>
      {!open && (
        <Button
          aria-label="Open chat"
          onClick={() => setOpen(true)}
          size="icon-lg"
          className="fixed right-5 bottom-5 z-50 rounded-full shadow-lg"
        >
          <MessageCircle className="size-5" />
        </Button>
      )}

      {open && (
        <div
          className={`flex flex-col overflow-hidden border bg-background shadow-2xl ${panelPosition}`}
        >
          <header className="flex items-center gap-2 border-b px-4 py-2.5">
            <span className="relative flex size-2">
              <span
                className={`absolute inline-flex h-full w-full rounded-full ${
                  streaming ? "animate-ping bg-amber-400" : "bg-emerald-500"
                } opacity-60`}
              />
              <span
                className={`relative inline-flex size-2 rounded-full ${
                  streaming ? "bg-amber-400" : "bg-emerald-500"
                }`}
              />
            </span>
            <div className="flex flex-col">
              <span className="text-sm leading-tight font-semibold">Maya</span>
              <span className="text-muted-foreground text-xs leading-tight">
                {streaming ? "thinking…" : "ShopMate assistant"}
              </span>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <Button
                aria-label="New chat"
                variant="ghost"
                size="icon-sm"
                onClick={startNewChat}
                title="Start a new chat"
              >
                <Plus />
              </Button>
              <Button
                aria-label={expanded ? "Collapse chat" : "Expand chat"}
                variant="ghost"
                size="icon-sm"
                onClick={() => setExpanded((value) => !value)}
                title={expanded ? "Collapse to widget" : "Expand to full page"}
              >
                {expanded ? <Minimize2 /> : <Maximize2 />}
              </Button>
              <Button
                aria-label="Close chat"
                variant="ghost"
                size="icon-sm"
                onClick={() => setOpen(false)}
              >
                <X />
              </Button>
            </div>
          </header>

          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
            {messages.map((message, index) => (
              <Bubble key={index} message={message} />
            ))}

            {partial && <Bubble message={{ role: "assistant", content: partial }} streaming />}

            {toolProgress && (
              <div className="text-muted-foreground flex items-center gap-2 text-xs">
                <Loader2 className="size-3 animate-spin" />
                <span>
                  Running tool <span className="font-medium">{toolProgress.name}</span>
                  {toolProgress.args_summary !== "{}" && (
                    <code className="bg-muted ml-1 rounded px-1 py-0.5">
                      {toolProgress.args_summary}
                    </code>
                  )}
                </span>
              </div>
            )}

            {errorText && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {errorText}
              </div>
            )}
          </div>

          <div className="flex items-end gap-2 border-t px-3 py-2.5">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
              rows={1}
              placeholder="Ask Maya about products…"
              className="placeholder:text-muted-foreground max-h-24 min-h-9 flex-1 resize-none rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring"
              disabled={!sessionId}
            />
            {streaming ? (
              <Button
                aria-label="Stop generating"
                variant="outline"
                size="icon"
                onClick={() => abortRef.current?.abort()}
              >
                <Square />
              </Button>
            ) : (
              <Button
                aria-label="Send message"
                size="icon"
                disabled={!draft.trim() || !sessionId}
                onClick={() => void send()}
              >
                <Send />
              </Button>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function Bubble({
  message,
  streaming = false,
}: {
  message: ChatMessage;
  streaming?: boolean;
}) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap ${
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : "bg-muted rounded-bl-sm"
        }`}
      >
        {message.content}
        {streaming && <span className="ml-0.5 animate-pulse">▍</span>}
      </div>
    </div>
  );
}
