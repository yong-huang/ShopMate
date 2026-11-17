"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CircleCheck } from "lucide-react";

import { type OrderPayload } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Demo checkout success page (ticket T06, PRD FR-5): shows the order number
 * from the just-placed demo order. The order payload is stashed in
 * sessionStorage by the cart page (checkout has no payment step; the order
 * itself is persisted server-side).
 */

const LAST_ORDER_KEY = "shopmate_last_order";

function SuccessBody() {
  const searchParams = useSearchParams();
  const orderParam = searchParams.get("order");
  const [order, setOrder] = useState<OrderPayload | null>(null);

  useEffect(() => {
    try {
      const raw = window.sessionStorage.getItem(LAST_ORDER_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as OrderPayload;
      // Only trust the stash when it matches the URL's order number.
      if (!orderParam || parsed.order_id === orderParam) setOrder(parsed);
    } catch {
      // Corrupted stash: the order number from the URL is still shown.
    }
  }, [orderParam]);

  return (
    <div className="flex flex-col items-center gap-4 py-16 text-center">
      <CircleCheck className="size-12 text-emerald-500" />
      <h1 className="text-3xl font-bold tracking-tight">Order placed</h1>
      <p className="text-muted-foreground max-w-prose">
        Thanks! Your demo order was recorded.
        <span lang="zh"> 感谢！您的演示订单已记录。</span>
      </p>

      <div className="mt-2 rounded-xl border px-8 py-6">
        <p className="text-sm text-muted-foreground">Order number</p>
        <p className="mt-1 font-mono text-2xl font-semibold tracking-wide">
          {orderParam ?? "—"}
        </p>
        {order && (
          <>
            <ul className="mt-4 space-y-1 border-t pt-4 text-left text-sm">
              {order.items.map((item) => (
                <li key={item.product_id} className="flex justify-between gap-8">
                  <span>
                    {item.name}
                    {item.variant
                      ? ` (${Object.entries(item.variant)
                          .map(([key, value]) => `${key}: ${value}`)
                          .join(", ")})`
                      : ""}{" "}
                    × {item.quantity}
                  </span>
                  <span className="tabular-nums">
                    ${item.line_total.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-3 border-t pt-3 text-right font-semibold tabular-nums">
              Total: ${order.total.toFixed(2)} {order.currency}
            </p>
          </>
        )}
      </div>

      {/* FR-5 AC5.2: the copy clearly labels the flow as a demo, no payment. */}
      <Badge variant="outline" className="border-amber-500/50 text-amber-700 dark:text-amber-400">
        Demo checkout — no payment was processed, nothing will ship · 演示结账，未产生真实支付
      </Badge>

      <div className="mt-2 flex gap-2">
        <Button asChild>
          <Link href="/">Continue shopping</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/cart">Back to cart</Link>
        </Button>
      </div>
    </div>
  );
}

export default function CheckoutSuccessPage() {
  return (
    <div className="mx-auto min-h-screen max-w-2xl px-6 py-10">
      <Suspense
        fallback={<p className="text-muted-foreground py-16 text-center">Loading…</p>}
      >
        <SuccessBody />
      </Suspense>
    </div>
  );
}
