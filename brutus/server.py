"""Thin HTTP API over the existing pipeline functions, for the web UI.

Reuses the same db/fetch/classify code the CLI uses — no logic duplicated here.
Run with `brutus serve`. CORS is open for local dev (UI on :3000, API on :8000).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from datetime import datetime, timezone

from . import db, llm
from .classify import run_classify
from .config import Config
from .db import InvalidTransition
from .fetch import github_search, nlsearch
from .models import Candidate, Status

app = FastAPI(title="Brutus API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _conn():
    cfg = Config.load()
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    return conn, cfg


def _serialize(c: Candidate) -> dict:
    return {
        "id": c.id,
        "repo": c.repo,
        "number": c.number,
        "title": c.title,
        "url": c.url,
        "labels": c.labels,
        "language": c.language,
        "score": c.score,
        "scoreReason": c.score_reason,
        "status": c.status.value,
    }


@app.get("/api/status")
def status() -> dict[str, int]:
    conn, _ = _conn()
    return db.count_by_status(conn)


@app.get("/api/candidates")
def candidates(
    min_score: int | None = None,
    lang: str | None = None,
    status: str = "scored",
    limit: int = 100,
) -> list[dict]:
    conn, _ = _conn()
    try:
        st = Status(status)
    except ValueError:
        raise HTTPException(400, f"unknown status {status!r}")
    rows = db.list_candidates(conn, status=st, language=lang, min_score=min_score, limit=limit)
    return [_serialize(c) for c in rows]


class FetchReq(BaseModel):
    lang: str | None = None
    label: str | None = None  # None/empty = all difficulties (no label filter)
    limit: int = 30


@app.post("/api/fetch")
def fetch(req: FetchReq) -> dict:
    conn, cfg = _conn()
    if not cfg.github_token:
        raise HTTPException(400, "GITHUB_TOKEN not set")
    n = github_search.fetch_github(
        conn, token=cfg.github_token, label=(req.label or None),
        language=req.lang, limit=req.limit,
    )
    return {"fetched": n}


class SearchReq(BaseModel):
    query: str  # natural language, e.g. "python bug fixes in popular repos this month"
    limit: int = 30


@app.post("/api/search")
def search(req: SearchReq) -> dict:
    conn, cfg = _conn()
    if not cfg.github_token:
        raise HTTPException(400, "GITHUB_TOKEN not set")
    if not cfg.llm_cmd:
        raise HTTPException(400, "BRUTUS_LLM_CMD not set (needed to parse the query)")
    today = datetime.now(timezone.utc).date().isoformat()
    return nlsearch.fetch_search(
        conn, token=cfg.github_token, nl=req.query,
        complete=lambda p: llm.complete(p, cmd=cfg.llm_cmd),
        today=today, limit=req.limit,
    )


@app.post("/api/classify")
def classify() -> dict:
    conn, cfg = _conn()
    if not cfg.llm_cmd:
        raise HTTPException(400, "BRUTUS_LLM_CMD not set")
    return run_classify(conn, complete=lambda p: llm.complete(p, cmd=cfg.llm_cmd))


@app.post("/api/pick/{candidate_id}")
def pick(candidate_id: int) -> dict:
    conn, _ = _conn()
    if db.get_candidate(conn, candidate_id) is None:
        raise HTTPException(404, "candidate not found")
    try:
        db.update_status(conn, candidate_id, Status.PICKED)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "status": "picked"}


def _open_terminal(candidate_id: int, subcommand: str, extra: str = "") -> dict:
    """Open the macOS Terminal running `brutus <subcommand> <id> --local`, kept open."""
    backend = os.getcwd()  # `brutus serve` runs from the backend dir
    script = (
        "#!/bin/zsh\n"
        f"cd {shlex.quote(backend)}\n"
        f"{shlex.quote(sys.executable)} -m brutus.cli {subcommand} {candidate_id} --local {extra}\n"
        'echo\n'
        f'echo "──────── brutus {subcommand} finished — window stays open ────────"\n'
        "exec $SHELL\n"
    )
    path = Path(tempfile.gettempdir()) / f"brutus-{subcommand}-{candidate_id}.command"
    path.write_text(script)
    path.chmod(0o755)
    subprocess.Popen(["open", "-a", "Terminal", str(path)])
    return {"ok": True, "launched": True}


@app.post("/api/solve/{candidate_id}")
def solve_open(candidate_id: int) -> dict:
    """Open the macOS Terminal and run `brutus solve` (Claude session)."""
    return _open_terminal(candidate_id, "solve", "--stream")


@app.post("/api/revise/{candidate_id}")
def revise_open(candidate_id: int) -> dict:
    """Open the macOS Terminal and run `brutus revise` — pull maintainer feedback,
    address it in a Claude session, and push the PR update."""
    return _open_terminal(candidate_id, "revise")
