"use client";

import { useEffect } from "react";

import { recordProductView } from "@/lib/api-client";

/**
 * Product-view capture (ticket T07, System Design 3.6 capture stage).
 *
 * Client island on the server-rendered product page: fires the lightweight
 * `POST /api/products/{id}/view` interaction hook once on mount so the
 * shopper's interaction log reflects what they looked at.
 */
export function ViewPing({ productId }: { productId: string }) {
  useEffect(() => {
    recordProductView(productId);
  }, [productId]);
  return null;
}
