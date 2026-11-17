"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Check, Loader2, ShoppingBag } from "lucide-react";

import { addCartItem, type ProductDetail } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * "Add to cart" for the product detail page (ticket T06, PRD FR-4).
 *
 * Variant pickers appear where the catalog defines them: a size picker when
 * the product lists `sizing.sizes`, and the single catalog `colour` as fixed
 * info (the demo catalog defines exactly one colour per product, so there is
 * nothing to pick). Posts to `POST /api/cart/items` — the same cart the chat
 * tools mutate — with the cookie identity.
 */

const MAX_QUANTITY_CHOICES = 5;

export function AddToCart({ product }: { product: ProductDetail }) {
  const sizes = useMemo(
    () => product.sizing?.sizes ?? [],
    [product.sizing],
  );
  const colour = product.attributes["colour"] ?? null;
  const [size, setSize] = useState<string | null>(sizes[0] ?? null);
  const [quantity, setQuantity] = useState(1);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const quantityChoices = Math.min(
    MAX_QUANTITY_CHOICES,
    Math.max(1, product.stock),
  );

  async function addToCart() {
    if (sizes.length > 0 && !size) return;
    setAdding(true);
    setAdded(false);
    setError(null);
    try {
      const variant: Record<string, string> = {};
      if (size) variant["size"] = size;
      await addCartItem({
        product_id: product.id,
        quantity,
        variant: Object.keys(variant).length ? variant : undefined,
      });
      setAdded(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add to cart.");
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border p-4">
      {colour && (
        <p className="text-sm text-muted-foreground">
          Colour: <span className="font-medium text-foreground">{colour}</span>
        </p>
      )}

      {sizes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Size</span>
          <div className="flex flex-wrap gap-1.5">
            {sizes.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setSize(option)}
                aria-pressed={size === option}
                className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                  size === option
                    ? "border-primary bg-primary text-primary-foreground"
                    : "bg-background hover:border-foreground/40"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <span className="text-sm font-medium">Quantity</span>
        <div className="flex flex-wrap gap-1.5">
          {Array.from({ length: quantityChoices }, (_, index) => index + 1).map(
            (option) => (
              <button
                key={option}
                type="button"
                onClick={() => setQuantity(option)}
                aria-pressed={quantity === option}
                className={`size-9 rounded-md border text-sm tabular-nums transition-colors ${
                  quantity === option
                    ? "border-primary bg-primary text-primary-foreground"
                    : "bg-background hover:border-foreground/40"
                }`}
              >
                {option}
              </button>
            ),
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <Button
          onClick={() => void addToCart()}
          disabled={adding || (sizes.length > 0 && !size)}
        >
          {adding ? <Loader2 className="animate-spin" /> : <ShoppingBag />}
          Add to cart
        </Button>
        {added && (
          <span className="flex items-center gap-1 text-sm text-emerald-600 dark:text-emerald-400">
            <Check className="size-4" /> Added —{" "}
            <Link href="/cart" className="underline underline-offset-2">
              view cart
            </Link>
          </span>
        )}
      </div>
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {product.stock === 0 && (
        <Badge variant="outline" className="w-fit">
          Out of stock
        </Badge>
      )}
    </div>
  );
}
