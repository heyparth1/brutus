"""Cheap, LLM-free triage. Runs only on the issue payload we already stored at
fetch time — no extra API calls. Hard-drops the obviously-unworkable; everything
it keeps carries `features` for the LLM scorer to weigh.

ponytail: repo-level signals (has CI? has tests? merges outside PRs?) would each
cost an extra API round-trip per repo — deferred until phase 3 shows we need them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..models import Candidate

MIN_BODY_LEN = 40  # below this, an issue is too thin to act on

BEGINNER_LABELS = {
    "good first issue",
    "good-first-issue",
    "help wanted",
    "first-timers-only",
    "beginner-friendly",
}


@dataclass
class HeuristicResult:
    keep: bool
    reasons: list[str] = field(default_factory=list)  # why dropped (empty if kept)
    features: dict[str, Any] = field(default_factory=dict)


def evaluate(candidate: Candidate, *, now: datetime | None = None) -> HeuristicResult:
    raw = candidate.raw or {}
    body = (raw.get("body") or "").strip()

    reasons: list[str] = []
    if raw.get("assignee") or raw.get("assignees"):
        reasons.append("already assigned")
    if raw.get("locked"):
        reasons.append("locked")
    if len(body) < MIN_BODY_LEN:
        reasons.append("body too short to be actionable")

    features = {
        "body_len": len(body),
        "comments": raw.get("comments", 0),
        "age_days": _age_days(raw.get("created_at"), now),
        "has_beginner_label": bool(
            {label.lower() for label in candidate.labels} & BEGINNER_LABELS
        ),
    }
    return HeuristicResult(keep=not reasons, reasons=reasons, features=features)


def _age_days(created_at: str | None, now: datetime | None) -> int | None:
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ((now or datetime.now(timezone.utc)) - created).days
