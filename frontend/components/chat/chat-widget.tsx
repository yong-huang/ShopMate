"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Maximize2, MessageCircle, Minimize2, Plus, Send, Square, X } from "lucide-react";

import {
  ChatError,
  sendChatMessage,
  type ChatSseEvent,
  type ComparisonPayload,
  type ProductCardPayload,
} from "@/lib/chat-sse";
import { API_BASE_URL, imageUrl } from "@/lib/api-client";
import { RatingStars } from "@/components/storefront/rating-stars";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";

/**
 * Maya chat widget (ticket T04: widget shell; T05: product cards).
 *
 * Consumes the backend SSE stream (text streaming, tool-call progress) and
 * keeps one conversation thread per browser via a localStorage session id;
 * the anonymous `shopmate_sid` cookie is managed by the backend through
 * `credentials: "include"`.
 *
 * Product cards and comparison matrices render ONLY from validated
 * `product_card` / `comparison` SSE events (backend guardrail, System
 * Design 3.5) — never from the model's prose. The `product-card` fenced
 * blocks Maya writes are stripped from displayed text.
 */

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  cards?: ProductCardPayload[];
  comparison?: ComparisonPayload;
  lang?: "en" | "zh";
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

/** Strip Maya's `product-card` fenced blocks — cards render from SSE only. */
export function stripCardBlocks(text: string): string {
  const withoutComplete = text.replace(/```product-card\s*\n[\s\S]*?```\n?/g, "");
  // Drop a still-streaming (unclosed) block tail as well.
  const openIndex = withoutComplete.indexOf("```product-card");
  if (openIndex === -1) return withoutComplete;
  return withoutComplete.slice(0, openIndex);
}

/** Best-effort en/zh detection (same CJK-share heuristic as the backend). */
function detectLang(text: string): "en" | "zh" {
  if (!text.trim()) return "en";
  const cjk = [...text].filter((ch) => ch >= "\u4e00" && ch <= "\u9fff").length;
  return cjk / text.length >= 0.2 ? "zh" : "en";
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
  const cardsRef = useRef<ProductCardPayload[]>([]);
  const comparisonRef = useRef<ComparisonPayload | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSessionId(getOrCreateSessionId());
  }, []);

  useEffect(() => {
    // Browser-unload ping (ticket T07, System Design 3.6): `pagehide` fires
    // the `POST /api/sessions/{id}/end` beacon so the backend closes the
    // session and schedules the background profile extraction. sendBeacon is
    // fire-and-forget and survives the page teardown; the cookie travels
    // with it (credentials mode include), and failures are silently ignored.
    function onUnload() {
      const currentSession = window.localStorage.getItem(SESSION_KEY);
      if (!currentSession || !navigator.sendBeacon) return;
      const body = JSON.stringify({ session_id: currentSession });
      navigator.sendBeacon(
        `${API_BASE_URL}/api/sessions/${currentSession}/end`,
        new Blob([body], { type: "text/plain" }),
      );
    }
    window.addEventListener("pagehide", onUnload);
    return () => window.removeEventListener("pagehide", onUnload);
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
    cardsRef.current = [];
    comparisonRef.current = null;
    setErrorText(null);
    setToolProgress(null);
  }

  async function send() {
    const text = draft.trim();
    if (!text || streaming || !sessionId) return;

    const userLang = detectLang(text);
    setMessages((current) => [...current, { role: "user", content: text }]);
    setDraft("");
    setErrorText(null);
    setStreaming(true);
    setPartial("");
    partialRef.current = "";
    cardsRef.current = [];
    comparisonRef.current = null;

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
        case "product_card":
          // Validated card event (3.5) — the only product rendering source.
          cardsRef.current = [...cardsRef.current, event.card];
          break;
        case "comparison":
          comparisonRef.current = event.comparison;
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
      if (partialRef.current || cardsRef.current.length || comparisonRef.current) {
        const assistantText = stripCardBlocks(partialRef.current);
        const cards = cardsRef.current;
        const comparison = comparisonRef.current;
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            content: assistantText,
            cards: cards.length ? cards : undefined,
            comparison: comparison ?? undefined,
            lang: userLang,
          },
        ]);
      }
      partialRef.current = "";
      cardsRef.current = [];
      comparisonRef.current = null;
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
              <div key={index} className="space-y-2">
                <Bubble message={{ ...message, content: stripCardBlocks(message.content) }} />
                {message.cards?.length ? (
                  <MessageCards cards={message.cards} lang={message.lang ?? "en"} />
                ) : null}
                {message.comparison ? <ComparisonMatrix comparison={message.comparison} /> : null}
              </div>
            ))}

            {partial && (
              <Bubble
                message={{ role: "assistant", content: stripCardBlocks(partial) }}
                streaming
              />
            )}

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

/** One validated product card: image, bilingual name, price, rating, hint. */
function MessageCards({ cards, lang }: { cards: ProductCardPayload[]; lang: "en" | "zh" }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {cards.map((card) => (
        <ChatProductCard key={card.product_id} card={card} lang={lang} />
      ))}
    </div>
  );
}

function ChatProductCard({ card, lang }: { card: ProductCardPayload; lang: "en" | "zh" }) {
  const primaryName = lang === "zh" ? card.name_zh || card.name_en : card.name_en;
  const secondaryName = lang === "zh" ? card.name_en : card.name_zh;
  return (
    <Card size="sm" className="gap-2 py-2">
      {/* Backend-served generated SVG; payload is guardrail-validated. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageUrl(card.image_url)}
        alt={`${card.name_en} / ${card.name_zh}`}
        className="aspect-square w-full object-cover"
        loading="lazy"
      />
      <CardContent className="space-y-1 px-2">
        <p className="line-clamp-2 text-xs leading-snug font-medium">{primaryName}</p>
        <p className="line-clamp-1 text-muted-foreground text-[11px]" lang={lang === "zh" ? "en" : "zh"}>
          {secondaryName}
        </p>
        {card.match_hint && (
          <p className="text-muted-foreground line-clamp-2 text-[11px] italic">{card.match_hint}</p>
        )}
      </CardContent>
      <CardFooter className="justify-between gap-1 px-2 pb-1">
        <span className="text-xs font-semibold tabular-nums">
          ${card.price.toFixed(2)}
        </span>
        <RatingStars rating={card.rating} className="text-[11px]" />
      </CardFooter>
    </Card>
  );
}

/** Validated comparison matrix (catalog attributes only, System Design 3.5). */
function ComparisonMatrix({ comparison }: { comparison: ComparisonPayload }) {
  const { columns, rows } = comparison.matrix;
  return (
    <div className="overflow-x-auto rounded-xl border bg-background p-2">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            <th className="text-muted-foreground px-2 py-1 text-left font-medium">#</th>
            {columns.map((id) => (
              <th key={id} className="px-2 py-1 text-left font-semibold">
                {id}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-t">
              <td className="text-muted-foreground px-2 py-1 font-medium">{row.label}</td>
              {columns.map((id) => (
                <td key={id} className="px-2 py-1 tabular-nums">
                  {row.values[id] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
