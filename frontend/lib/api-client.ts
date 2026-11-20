/**
 * API client for the ShopMate backend.
 *
 * Every frontend call to the FastAPI backend goes through here. The SSE
 * chat client will live alongside this module as chat lands.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Shape of `GET /api/health` (see backend/app/api/health.py). */
export interface HealthResponse {
  status: string;
  version: string;
  provider: {
    name: string;
    model: string;
    base_url: string | null;
    api_key_configured: boolean;
    status: "configured" | "not_configured";
  };
}

/** Catalog summary fields (card/list shape, System Design 3.8). */
export interface ProductSummary {
  id: string;
  slug: string;
  name_en: string;
  name_zh: string;
  category: string;
  price: number;
  currency: string;
  rating: number;
  image: string;
  image_url: string;
  tags: string[];
  featured: boolean;
}

/** Full product record behind the detail page. */
export interface ProductDetail extends ProductSummary {
  description_en: string;
  description_zh: string;
  attributes: Record<string, string>;
  specs: { label: string; value: string }[];
  reviews: { author: string; rating: number; comment: string }[];
  stock: number;
  sizing: { sizes: string[]; fit: string } | null;
  care: string | null;
  materials: Record<string, string> | null;
}

/** `GET /api/products` response. */
export interface ProductListResponse {
  items: ProductSummary[];
  total: number;
}

export interface ProductQuery {
  category?: string;
  q?: string;
  sort?: "featured" | "price_asc" | "price_desc" | "rating";
  limit?: number;
}

/** The four storefront categories with product counts. */
export interface CategorySummary {
  name: string;
  count: number;
}

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status = 0, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let code: string | undefined;
  let message = `Request failed with status ${response.status}`;
  try {
    const body = (await response.json()) as { code?: string; message?: string };
    code = body.code;
    if (body.message) message = body.message;
  } catch {
    // Non-JSON error body: keep the status-based message.
  }
  return new ApiError(message, response.status, code);
}

/**
 * One backend REST request: anonymous-cookie credentials on every call, and
 * one honest `ApiError` (parsed `{code, message}` body) on any non-2xx.
 * Every endpoint function below routes through this so the fetch/boilerplate
 * and the error shape have a single home.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    ...init,
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as T;
}

/** Fetch backend health (liveness + configured model provider status). */
export async function getHealth(
  signal?: AbortSignal,
): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", {
    signal,
    cache: "no-store",
  });
}

/** Browse the catalog (category / keyword filters, sort). */
export async function getProducts(
  query: ProductQuery = {},
  signal?: AbortSignal,
): Promise<ProductListResponse> {
  const params = new URLSearchParams();
  if (query.category) params.set("category", query.category);
  if (query.q) params.set("q", query.q);
  if (query.sort) params.set("sort", query.sort);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<ProductListResponse>(`/api/products${suffix}`, {
    signal,
    cache: "no-store",
  });
}

/** Fetch one product's full detail; throws ApiError (404) when unknown. */
export async function getProduct(
  id: string,
  signal?: AbortSignal,
): Promise<ProductDetail> {
  return request<ProductDetail>(
    `/api/products/${encodeURIComponent(id)}`,
    { signal, cache: "no-store" },
  );
}

/** Absolute URL for a product image served by the backend. */
export function imageUrl(imageUrlPath: string): string {
  return `${API_BASE_URL}${imageUrlPath}`;
}

/** Derive the category list with counts from a full catalog listing. */
export function summarizeCategories(items: ProductSummary[]): CategorySummary[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.category, (counts.get(item.category) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);
}

/** Shape of `GET /api/profile` (System Design 3.6 / 3.8, ticket T07). */
export interface ProfileResponse {
  interests: string[];
  price_band: string | null;
  sizes: Record<string, string>;
  likes: string[];
  dislikes: string[];
  notes: string[];
  interaction_log: ProfileInteractionEvent[];
  updated_at: string | null;
}

/** One interaction-log entry (product viewed / added / purchased). */
export interface ProfileInteractionEvent {
  type: string;
  at: string;
  product_id: string | null;
  session_id: string | null;
}

/** Fetch the shopper's cross-session profile (cookie-linked). */
export async function getProfile(
  signal?: AbortSignal,
): Promise<ProfileResponse> {
  return request<ProfileResponse>("/api/profile", {
    signal,
    cache: "no-store",
  });
}

/** Clear the profile entirely (preferences and interaction log). */
export async function deleteProfile(signal?: AbortSignal): Promise<void> {
  // 200 with the cleared-profile body; we only care that it succeeded.
  await request<unknown>("/api/profile", { method: "DELETE", signal });
}

/**
 * Record one product-view interaction (System Design 3.6 capture stage).
 *
 * Fire-and-forget from the product page: `keepalive` lets the request
 * complete even if the shopper navigates away immediately, and the result is
 * never awaited or surfaced, so this stays a bare fetch (no shared request
 * wrapper, nothing to throw); the interaction log is best-effort by design.
 */
export function recordProductView(productId: string): void {
  fetch(`${API_BASE_URL}/api/products/${encodeURIComponent(productId)}/view`, {
    method: "POST",
    credentials: "include",
    keepalive: true,
  }).catch(() => {
    // Best-effort capture: never disturb the product page.
  });
}

/**
 * Cart API (ticket T06, System Design 3.7 / PRD FR-4). The same cart the
 * chat tools mutate: the anonymous `shopmate_sid` cookie is the identity,
 * so every request sends `credentials: "include"`.
 */

/** One cart line (server-computed line prices; variant when present). */
export interface CartItemPayload {
  cart_item_id: number;
  product_id: string;
  name_en: string;
  name_zh: string;
  unit_price: number;
  currency: string;
  quantity: number;
  variant: Record<string, string> | null;
  line_total: number;
  image: string;
}

/** Shape of `GET /api/cart` and of every cart mutation response. */
export interface CartSummary {
  items: CartItemPayload[];
  item_count: number;
  total: number;
  currency: string;
}

/** A demo order placed by `POST /api/checkout` (PRD FR-5). */
export interface OrderPayload {
  order_id: string;
  status: string;
  currency: string;
  total: number;
  items: {
    product_id: string;
    name: string;
    unit_price: number;
    quantity: number;
    variant: Record<string, string> | null;
    line_total: number;
  }[];
  demo_notice: string;
  shipping_note: string | null;
}

/** Fetch the shopper's cart (empty summary before anything is added). */
export async function getCart(signal?: AbortSignal): Promise<CartSummary> {
  return request<CartSummary>("/api/cart", { signal, cache: "no-store" });
}

/** Add one product line (optionally with size/color variant) to the cart. */
export async function addCartItem(body: {
  product_id: string;
  quantity?: number;
  variant?: Record<string, string>;
}): Promise<CartSummary> {
  return request<CartSummary>("/api/cart/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Update one cart line's quantity. */
export async function updateCartItem(
  cartItemId: number,
  quantity: number,
): Promise<CartSummary> {
  return request<CartSummary>(
    `/api/cart/items/${encodeURIComponent(String(cartItemId))}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quantity }),
    },
  );
}

/** Remove one cart line. */
export async function removeCartItem(
  cartItemId: number,
): Promise<CartSummary> {
  return request<CartSummary>(
    `/api/cart/items/${encodeURIComponent(String(cartItemId))}`,
    { method: "DELETE" },
  );
}

/**
 * Place the demo order (PRD FR-5). Refuses an empty cart with a 409
 * `empty_cart` ApiError; never involves payment.
 */
export async function checkoutCart(
  shippingNote?: string,
): Promise<OrderPayload> {
  return request<OrderPayload>("/api/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shipping_note: shippingNote ?? null }),
  });
}
