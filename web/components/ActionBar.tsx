"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiClassify, apiFetch, apiSearch } from "@/lib/api";

export function ActionBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [lang, setLang] = useState("python");
  const [label, setLabel] = useState("");
  const [limit, setLimit] = useState(30);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function act(label: string, fn: () => Promise<any>) {
    setBusy(label);
    setMsg(null);
    try {
      const data = await fn();
      setMsg(summarize(label, data));
      router.refresh(); // table + funnel re-read the db
    } catch (e: any) {
      setMsg(`error: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  // Search fetches issues (status 'fetched'), then classifies them so they show up
  // in the scored list immediately.
  async function searchAndClassify() {
    const found = await apiSearch({ query, limit });
    const scored = await apiClassify();
    return { ...found, scored: scored.scored, dropped: scored.dropped };
  }

  return (
    <section className="actions">
      <div className="nl-search">
        <input
          className="nl-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search in plain English — e.g. 'python bugs in 1000+ star repos updated this month'"
          onKeyDown={(e) => {
            if (e.key === "Enter" && query.trim()) act("Search", searchAndClassify);
          }}
        />
        <button
          disabled={!!busy || !query.trim()}
          onClick={() => act("Search", searchAndClassify)}
        >
          {busy === "Search" ? "Searching…" : "Search"}
        </button>
      </div>
      <label>
        Language
        <input value={lang} onChange={(e) => setLang(e.target.value)} placeholder="any" />
      </label>
      <label>
        Label
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="any difficulty"
        />
      </label>
      <label>
        Limit
        <input
          type="number"
          value={limit}
          min={1}
          max={100}
          onChange={(e) => setLimit(Number(e.target.value))}
        />
      </label>
      <button
        disabled={!!busy}
        onClick={() =>
          act("Fetch", () =>
            apiFetch({ lang: lang || undefined, label: label || undefined, limit })
          )
        }
      >
        {busy === "Fetch" ? "Fetching…" : "Fetch from GitHub"}
      </button>
      <button disabled={!!busy} onClick={() => act("Classify", apiClassify)}>
        {busy === "Classify" ? "Scoring…" : "Classify"}
      </button>
      {msg && <span className="msg">{msg}</span>}
    </section>
  );
}

function summarize(label: string, data: any): string {
  if (label === "Fetch") return `fetched ${data.fetched}`;
  if (label === "Search")
    return `found ${data.fetched} new (${data.kept} matched), scored ${data.scored}`;
  if (label === "Classify") return `scored ${data.scored}, dropped ${data.dropped}`;
  return "done";
}
