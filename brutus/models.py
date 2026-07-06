"""Domain models. Pure data — no persistence, no I/O.

A `Candidate` is one issue we might solve. It moves through the pipeline as a
`Status`; the legal moves are declared once in `_TRANSITIONS` and enforced by
`db.update_status`, so an illegal jump (e.g. fetched -> pushed) is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    FETCHED = "fetched"  # discovered, not yet scored
    SCORED = "scored"    # tractability score assigned
    PICKED = "picked"    # human chose this one to solve
    SOLVING = "solving"  # Ralph loop running
    REVIEW = "review"    # diff ready, awaiting human approval
    PUSHED = "pushed"    # PR opened
    FAILED = "failed"    # gave up; needs a human


# Legal transitions. The `-> PICKED` edges are retry paths: a solve that was
# interrupted, failed, or reviewed can be sent back to picked and re-run.
_TRANSITIONS: dict[Status, set[Status]] = {
    Status.FETCHED: {Status.SCORED},
    Status.SCORED: {Status.PICKED},
    Status.PICKED: {Status.SOLVING},
    Status.SOLVING: {Status.REVIEW, Status.FAILED, Status.PICKED},
    Status.REVIEW: {Status.PUSHED, Status.FAILED, Status.PICKED},
    Status.PUSHED: set(),
    Status.FAILED: {Status.PICKED},
}


def can_transition(src: Status, dst: Status) -> bool:
    return dst in _TRANSITIONS[src]


@dataclass
class Candidate:
    """One issue, plus the discovery metadata we filter and score on."""

    source: str               # "github" | "goodfirstissue" | "gsoc"
    repo: str                 # "owner/name"
    number: int               # issue number within the repo
    title: str
    url: str
    labels: list[str] = field(default_factory=list)
    language: str | None = None
    stars: int = 0
    raw: dict[str, Any] = field(default_factory=dict)  # original API payload

    status: Status = Status.FETCHED
    score: int | None = None          # tractability 1–5 (phase 2)
    score_reason: str | None = None

    # Set by the database layer.
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
