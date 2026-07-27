// GET only. The client reads; it never asks for imagery. Analysis is scheduled
// server-side, and the admin endpoints that remain are reached with curl.
export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
  const query = params ? `?${new URLSearchParams(params)}` : "";
  const res = await fetch(`/api${path}${query}`);
  if (!res.ok) {
    throw new Error(`GET /api${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}
