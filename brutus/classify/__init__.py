"""Classification: cheap heuristics drop junk, then a batched LLM scores survivors.

Both paths end at `Status.SCORED` with a 1–5 score, so the queue is fully drained
each run and phase 3's `--min-score` filter hides the junk. Dropped issues get a
deterministic score of 1 (no LLM spent on them); survivors get the model's score.
"""

from __future__ import annotations

from typing import Iterator

from .. import db
from ..models import Status
from . import llm_score
from .heuristics import evaluate
from .llm_score import CompleteFn


def run_classify(
    conn,
    *,
    complete: CompleteFn,
    batch_size: int = llm_score.BATCH_SIZE,
    limit: int | None = None,
) -> dict[str, int]:
    """Classify all FETCHED candidates. Returns counts of dropped/scored/skipped."""
    candidates = db.list_candidates(conn, status=Status.FETCHED, limit=limit)

    survivors = []
    dropped = 0
    for candidate in candidates:
        result = evaluate(candidate)
        if not result.keep:
            db.update_score(conn, candidate.id, 1, "dropped: " + "; ".join(result.reasons))
            db.update_status(conn, candidate.id, Status.SCORED)
            dropped += 1
        else:
            survivors.append((candidate, result))

    scored = 0
    for batch in _chunks(survivors, batch_size):
        results = llm_score.score_batch(batch, complete)
        for candidate, _ in batch:
            scored_entry = results.get(candidate.id)
            if scored_entry is None:
                continue  # model omitted it; stays FETCHED and retries next run
            score, reason = scored_entry
            db.update_score(conn, candidate.id, score, reason)
            db.update_status(conn, candidate.id, Status.SCORED)
            scored += 1

    return {"dropped": dropped, "scored": scored, "skipped": len(survivors) - scored}


def _chunks(seq: list, size: int) -> Iterator[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]
