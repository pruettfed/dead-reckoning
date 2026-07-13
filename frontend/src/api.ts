export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
  const query = params ? `?${new URLSearchParams(params)}` : "";
  const res = await fetch(`/api${path}${query}`);
  if (!res.ok) {
    throw new Error(`GET /api${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(`/api${path}`, { method: "POST", headers });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body) => body.detail as string)
      .catch(() => res.statusText);
    throw new Error(`POST /api${path} failed (${res.status}): ${detail}`);
  }
  return (await res.json()) as T;
}
