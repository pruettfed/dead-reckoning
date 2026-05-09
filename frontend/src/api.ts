export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) {
    throw new Error(`GET /api${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}
