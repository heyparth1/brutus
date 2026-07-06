"use client";

import { useState } from "react";
import { apiRevise } from "@/lib/api";

export function ReviseButton({ id }: { id: number }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function revise() {
    setBusy(true);
    setError(null);
    try {
      await apiRevise(id); // opens macOS Terminal: pull feedback → Claude → push update
      setMsg("opened in Terminal ↗");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="pick" onClick={revise} disabled={busy}>
        {busy ? "…" : "Address feedback"}
      </button>
      {msg && <span className="msg">{msg}</span>}
      {error && <span className="err" title={error}>!</span>}
    </>
  );
}
