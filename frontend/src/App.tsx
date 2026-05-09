import { useQuery } from "@tanstack/react-query";

import { apiGet } from "./api";

type Health = { status: string };
type Vessel = Record<string, unknown>;

export default function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<Health>("/health"),
  });

  const vessels = useQuery({
    queryKey: ["vessels"],
    queryFn: () => apiGet<Vessel[]>("/vessels"),
  });

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>Dark Vessel Detection</h1>
      <section>
        <h2>API health</h2>
        {health.isPending && <p>checking…</p>}
        {health.isError && <p>error: {String(health.error)}</p>}
        {health.data && <pre>{JSON.stringify(health.data, null, 2)}</pre>}
      </section>
      <section>
        <h2>Vessels</h2>
        {vessels.isPending && <p>loading…</p>}
        {vessels.isError && <p>error: {String(vessels.error)}</p>}
        {vessels.data && <pre>{JSON.stringify(vessels.data, null, 2)}</pre>}
      </section>
    </main>
  );
}
