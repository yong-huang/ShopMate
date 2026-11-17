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

/** Fetch backend health (liveness + configured model provider status). */
export async function getHealth(
  signal?: AbortSignal,
): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`, {
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as HealthResponse;
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
  const response = await fetch(`${API_BASE_URL}/api/products${suffix}`, {
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as ProductListResponse;
}

/** Fetch one product's full detail; throws ApiError (404) when unknown. */
export async function getProduct(
  id: string,
  signal?: AbortSignal,
): Promise<ProductDetail> {
  const response = await fetch(`${API_BASE_URL}/api/products/${encodeURIComponent(id)}`, {
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as ProductDetail;
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
