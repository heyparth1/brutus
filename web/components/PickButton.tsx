"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPick, apiSolve } from "@/lib/api";

export function PickButton({ id, title }: { id: number; title: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function pickAndSolve() {
    setBusy(true);
    setError(null);
    try {
      await apiPick(id); // scored -> picked
      await apiSolve(id); // opens macOS Terminal running the solve
      setMsg("opened in Terminal ↗");
      router.refresh(); // row moves to 'picked', leaves the table
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="pick" onClick={pickAndSolve} disabled={busy}>
        {busy ? "…" : "Pick & Solve"}
      </button>
      {msg && <span className="msg">{msg}</span>}
      {error && <span className="err" title={error}>!</span>}
    </>
  );
}
