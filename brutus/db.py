"""SQLite persistence for candidates. One table, plain `sqlite3`, no ORM.

The `candidates` table doubles as the pipeline queue: a row's `status` is its
position in the pipeline. `upsert_candidate` is idempotent on `(repo, number)`
so re-fetching never creates duplicates and never resets work already in flight.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Candidate, Status, can_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,
    repo         TEXT    NOT NULL,
    number       INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    labels       TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    language     TEXT,
    stars        INTEGER NOT NULL DEFAULT 0,
    raw          TEXT    NOT NULL DEFAULT '{}',    -- JSON object (original payload)
    status       TEXT    NOT NULL DEFAULT 'fetched',
    score        INTEGER,
    score_reason TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(repo, number)
);
"""


class InvalidTransition(Exception):
    """Raised when a status change isn't allowed by the pipeline state machine."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Concurrency: WAL lets the UI read while a solve writes; busy_timeout makes a
    # second writer wait instead of erroring with "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_candidate(conn: sqlite3.Connection, c: Candidate) -> int:
    """Insert, or update discovery fields if `(repo, number)` already exists.

    Pipeline fields (`status`, `score`, `score_reason`) are deliberately NOT
    overwritten on conflict — re-fetching an issue we've already picked or solved
    must not drag it back to square one.
    """
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO candidates
            (source, repo, number, title, url, labels, language, stars, raw,
             status, score, score_reason, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, number) DO UPDATE SET
            title      = excluded.title,
            url        = excluded.url,
            labels     = excluded.labels,
            language   = excluded.language,
            stars      = excluded.stars,
            raw        = excluded.raw,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (
            c.source, c.repo, c.number, c.title, c.url, json.dumps(c.labels),
            c.language, c.stars, json.dumps(c.raw), c.status.value, c.score,
            c.score_reason, now, now,
        ),
    )
    candidate_id = cur.fetchone()["id"]
    conn.commit()
    return candidate_id


def candidate_id_for(conn: sqlite3.Connection, repo: str, number: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM candidates WHERE repo = ? AND number = ?", (repo, number)
    ).fetchone()
    return row["id"] if row else None


def get_candidate(conn: sqlite3.Connection, candidate_id: int) -> Candidate | None:
    row = conn.execute(
        "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    return _row_to_candidate(row) if row else None


def list_candidates(
    conn: sqlite3.Connection,
    *,
    status: Status | None = None,
    language: str | None = None,
    min_score: int | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """Filtered listing, highest score first. Filters widen in phase 3."""
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status.value)
    if language is not None:
        clauses.append("language = ?")
        params.append(language)
    if min_score is not None:
        clauses.append("score >= ?")
        params.append(min_score)

    sql = "SELECT * FROM candidates"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY score DESC NULLS LAST, stars DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return [_row_to_candidate(r) for r in conn.execute(sql, params)]


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM candidates GROUP BY status")
    return {row["status"]: row["n"] for row in rows}


def update_score(
    conn: sqlite3.Connection, candidate_id: int, score: int, reason: str
) -> None:
    conn.execute(
        "UPDATE candidates SET score = ?, score_reason = ?, updated_at = ? WHERE id = ?",
        (score, reason, _now(), candidate_id),
    )
    conn.commit()


def update_status(
    conn: sqlite3.Connection, candidate_id: int, new_status: Status
) -> None:
    row = conn.execute(
        "SELECT status FROM candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"candidate {candidate_id} not found")

    current = Status(row["status"])
    if not can_transition(current, new_status):
        raise InvalidTransition(f"{current.value} -> {new_status.value}")

    conn.execute(
        "UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?",
        (new_status.value, _now(), candidate_id),
    )
    conn.commit()


def _row_to_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        id=row["id"],
        source=row["source"],
        repo=row["repo"],
        number=row["number"],
        title=row["title"],
        url=row["url"],
        labels=json.loads(row["labels"]),
        language=row["language"],
        stars=row["stars"],
        raw=json.loads(row["raw"]),
        status=Status(row["status"]),
        score=row["score"],
        score_reason=row["score_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
