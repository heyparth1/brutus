// Client-side calls to the brutus HTTP API (run `brutus serve`).
// Reads still come from the SQLite file via lib/db.ts; actions go through here.

export const API = process.env.NEXT_PUBLIC_BRUTUS_API || "http://localhost:8000";

async function post(pathname: string, body?: unknown) {
  const res = await fetch(`${API}${pathname}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `request failed (${res.status})`);
  return data;
}

export const apiFetch = (body: { lang?: string; label?: string; limit?: number }) =>
  post("/api/fetch", body);
export const apiSearch = (body: { query: string; limit?: number }) =>
  post("/api/search", body);
export const apiClassify = () => post("/api/classify");
export const apiPick = (id: number) => post(`/api/pick/${id}`);
// Opens the macOS Terminal to run the solve; returns immediately.
export const apiSolve = (id: number) => post(`/api/solve/${id}`);
// Opens the macOS Terminal to address maintainer feedback on an open PR.
export const apiRevise = (id: number) => post(`/api/revise/${id}`);
