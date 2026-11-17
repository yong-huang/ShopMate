/**
 * API client stub for the ShopMate backend.
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

export class ApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiError";
  }
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
    throw new ApiError(`Health check failed with status ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}
