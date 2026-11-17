"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Minus, Plus, ShoppingBag, Trash2, X } from "lucide-react";

import {
  checkoutCart,
  getCart,
  removeCartItem,
  updateCartItem,
  type CartSummary,
} from "@/lib/api-client";
import { imageUrl } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Cart page (ticket T06, System Design 3.7, PRD FR-4/FR-5).
 *
 * Reads the SAME cart the chat tools mutate (GET /api/cart, cookie identity)
 * and offers quantity updates, line removal, and the demo checkout: a
 * confirmation modal clearly labeled as a demo (no payment), then the order
 * lands on the success page with its order number.
 */

const LAST_ORDER_KEY = "shopmate_last_order";

export default function CartPage() {
  const router = useRouter();
  const [cart, setCart] = useState<CartSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [placing, setPlacing] = useState(false);

  const load = useCallback(async () => {
    try {
      setCart(await getCart());
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Failed to load your cart.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(cartItemId: number, action: () => Promise<CartSummary>) {
    setBusyItem(cartItemId);
    try {
      setCart(await action());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cart update failed.");
    } finally {
      setBusyItem(null);
    }
  }

  async function placeOrder() {
    setPlacing(true);
    try {
      const order = await checkoutCart();
      window.sessionStorage.setItem(LAST_ORDER_KEY, JSON.stringify(order));
      router.push(`/checkout/success?order=${encodeURIComponent(order.order_id)}`);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Checkout failed. Try again.",
      );
      setConfirmOpen(false);
    } finally {
      setPlacing(false);
    }
  }

  const empty = cart !== null && cart.items.length === 0;

  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-10">
      <nav className="flex items-center justify-between text-sm text-muted-foreground">
        <Link href="/" className="hover:underline">
          ← Back to storefront
        </Link>
      </nav>

      <header className="flex items-center gap-2">
        <ShoppingBag className="size-6" />
        <h1 className="text-3xl font-bold tracking-tight">Your cart</h1>
      </header>

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {!cart && !error && (
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" /> Loading your cart…
        </p>
      )}

      {empty && (
        <div className="rounded-xl border border-dashed p-10 text-center">
          <p className="text-muted-foreground">
            Your cart is empty. Ask Maya in chat, or
            {" "}
            <Link href="/" className="underline underline-offset-2">
              browse the storefront
            </Link>
            .
          </p>
        </div>
      )}

      {cart && cart.items.length > 0 && (
        <>
          <ul className="divide-y rounded-xl border">
            {cart.items.map((item) => (
              <li
                key={item.cart_item_id}
                className="flex items-center gap-4 p-4"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={imageUrl(item.image)}
                  alt={`${item.name_en} / ${item.name_zh}`}
                  className="size-16 rounded-lg border object-cover"
                />
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/product/${item.product_id}`}
                    className="line-clamp-1 font-medium hover:underline"
                  >
                    {item.name_en}
                  </Link>
                  <p className="line-clamp-1 text-sm text-muted-foreground" lang="zh">
                    {item.name_zh}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {item.variant
                      ? Object.entries(item.variant).map(([key, value]) => (
                          <Badge key={key} variant="secondary" className="text-[11px]">
                            {key}: {value}
                          </Badge>
                        ))
                      : null}
                    <span className="text-sm text-muted-foreground tabular-nums">
                      ${item.unit_price.toFixed(2)} each
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    aria-label={`Decrease quantity of ${item.name_en}`}
                    variant="outline"
                    size="icon-sm"
                    disabled={busyItem === item.cart_item_id || item.quantity <= 1}
                    onClick={() =>
                      void act(item.cart_item_id, () =>
                        updateCartItem(item.cart_item_id, item.quantity - 1),
                      )
                    }
                  >
                    <Minus />
                  </Button>
                  <span className="w-8 text-center text-sm font-medium tabular-nums">
                    {item.quantity}
                  </span>
                  <Button
                    aria-label={`Increase quantity of ${item.name_en}`}
                    variant="outline"
                    size="icon-sm"
                    disabled={busyItem === item.cart_item_id}
                    onClick={() =>
                      void act(item.cart_item_id, () =>
                        updateCartItem(item.cart_item_id, item.quantity + 1),
                      )
                    }
                  >
                    <Plus />
                  </Button>
                </div>
                <span className="w-20 text-right font-semibold tabular-nums">
                  ${item.line_total.toFixed(2)}
                </span>
                <Button
                  aria-label={`Remove ${item.name_en} from cart`}
                  variant="ghost"
                  size="icon-sm"
                  disabled={busyItem === item.cart_item_id}
                  onClick={() =>
                    void act(item.cart_item_id, () =>
                      removeCartItem(item.cart_item_id),
                    )
                  }
                >
                  {busyItem === item.cart_item_id ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Trash2 />
                  )}
                </Button>
              </li>
            ))}
          </ul>

          <div className="flex items-center justify-between rounded-xl border bg-muted/40 px-4 py-3">
            <span className="text-sm text-muted-foreground">
              {cart.item_count} item{cart.item_count === 1 ? "" : "s"}
            </span>
            <span className="text-lg font-semibold tabular-nums">
              Total: ${cart.total.toFixed(2)}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                {cart.currency}
              </span>
            </span>
          </div>

          <Button
            size="lg"
            className="self-end"
            onClick={() => setConfirmOpen(true)}
          >
            Proceed to checkout
          </Button>
        </>
      )}

      {confirmOpen && cart && cart.items.length > 0 && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
          role="dialog"
          aria-modal="true"
          aria-labelledby="checkout-confirm-title"
        >
          <div className="w-full max-w-md rounded-2xl border bg-background p-6 shadow-2xl">
            <div className="mb-2 flex items-start justify-between">
              <h2 id="checkout-confirm-title" className="text-lg font-semibold">
                Confirm your demo order
              </h2>
              <Button
                aria-label="Cancel checkout"
                variant="ghost"
                size="icon-sm"
                onClick={() => setConfirmOpen(false)}
              >
                <X />
              </Button>
            </div>
            <p className="mb-1 text-sm text-muted-foreground">
              {cart.item_count} item{cart.item_count === 1 ? "" : "s"} · total{" "}
              <span className="font-semibold tabular-nums">
                ${cart.total.toFixed(2)} {cart.currency}
              </span>
            </p>
            {/* FR-5: no payment UI anywhere — the flow is explicitly a demo. */}
            <p className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
              This is a demo store — no payment will be collected and nothing
              will ship. 本店为演示环境：不会产生真实支付，也不会发货。
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setConfirmOpen(false)}
                disabled={placing}
              >
                Cancel
              </Button>
              <Button onClick={() => void placeOrder()} disabled={placing}>
                {placing && <Loader2 className="animate-spin" />}
                Place demo order
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
