// Read-only access to the backend's SQLite file via Node's built-in sqlite module
// (Node 22+). No native dependency. Writes go through the brutus CLI, never here —
// the CLI owns the pipeline state machine.

import { DatabaseSync } from "node:sqlite";
import fs from "node:fs";
import path from "node:path";

const BACKEND_DIR = process.env.BRUTUS_DIR
  ? path.resolve(process.env.BRUTUS_DIR)
  : path.resolve(process.cwd(), "../brutus");

const DB_PATH = process.env.BRUTUS_DB_PATH
  ? path.resolve(process.env.BRUTUS_DB_PATH)
  : path.join(BACKEND_DIR, "data", "brutus.db");

export type Candidate = {
  id: number;
  repo: string;
  number: number;
  title: string;
  url: string;
  labels: string[];
  language: string | null;
  stars: number;
  score: number | null;
  scoreReason: string | null;
  status: string;
  raisedAt: string | null; // when the GitHub issue was opened (from the raw payload)
};

function open(): DatabaseSync | null {
  if (!fs.existsSync(DB_PATH)) return null;
  return new DatabaseSync(DB_PATH, { readOnly: true });
}

export function listScored(minScore: number, language?: string): Candidate[] {
  const db = open();
  if (!db) return [];
  try {
    let sql = "SELECT * FROM candidates WHERE status = 'scored'";
    const params: (string | number)[] = [];
    if (Number.isFinite(minScore)) {
      sql += " AND score >= ?";
      params.push(minScore);
    }
    if (language) {
      sql += " AND language = ?";
      params.push(language);
    }
    // Newest issue first (created_at lives in the raw JSON payload).
    sql += " ORDER BY json_extract(raw, '$.created_at') DESC, score DESC LIMIT 100";
    return db.prepare(sql).all(...params).map(toCandidate);
  } finally {
    db.close();
  }
}

export function listUnderReview(): Candidate[] {
  const db = open();
  if (!db) return [];
  try {
    return db
      .prepare("SELECT * FROM candidates WHERE status = 'pushed' ORDER BY id DESC LIMIT 50")
      .all()
      .map(toCandidate);
  } finally {
    db.close();
  }
}

export function statusCounts(): Record<string, number> {
  const db = open();
  if (!db) return {};
  try {
    const rows = db
      .prepare("SELECT status, COUNT(*) AS n FROM candidates GROUP BY status")
      .all() as { status: string; n: number }[];
    return Object.fromEntries(rows.map((r) => [r.status, r.n]));
  } finally {
    db.close();
  }
}

function toCandidate(row: any): Candidate {
  return {
    id: row.id,
    repo: row.repo,
    number: row.number,
    title: row.title,
    url: row.url,
    labels: safeLabels(row.labels),
    language: row.language,
    stars: row.stars ?? 0,
    score: row.score,
    scoreReason: row.score_reason,
    status: row.status,
    raisedAt: rawCreatedAt(row.raw),
  };
}

function rawCreatedAt(raw: unknown): string | null {
  try {
    return JSON.parse(String(raw ?? "{}"))?.created_at ?? null;
  } catch {
    return null;
  }
}

function safeLabels(raw: unknown): string[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
