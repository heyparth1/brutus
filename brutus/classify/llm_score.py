"""Batched LLM tractability scoring.

One LLM call per batch of survivors returns a JSON array of {id, score, reason}.
The prompt is the only thing the model sees; parsing is defensive so a stray code
fence or preamble doesn't sink the whole batch.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from ..models import Candidate
from .heuristics import HeuristicResult

BATCH_SIZE = 20
BODY_EXCERPT = 800  # chars of issue body sent per issue

# (score, reason) keyed by candidate id.
CompleteFn = Callable[[str], str]

_PROMPT_HEADER = """\
You are triaging open-source issues for an automated coding agent. For EACH issue,
rate how tractable it is for an agent to fix and open a correct PR, on a 1–5 scale:

  5 = crisp, self-contained, clear acceptance; ideal for an agent
  3 = doable but ambiguous or touches several files
  1 = vague, needs design discussion, or depends on missing context

Return ONLY a JSON array, one object per issue, no prose:
[{"id": <int>, "score": <1-5>, "reason": "<one short sentence>"}]

Issues:
"""


def score_batch(
    batch: list[tuple[Candidate, HeuristicResult]], complete: CompleteFn
) -> dict[int, tuple[int, str]]:
    if not batch:
        return {}
    raw = complete(build_prompt(batch))

    scores: dict[int, tuple[int, str]] = {}
    for entry in _parse(raw):
        cid, score = entry.get("id"), entry.get("score")
        if isinstance(cid, int) and isinstance(score, int):
            clamped = max(1, min(5, score))
            scores[cid] = (clamped, str(entry.get("reason", ""))[:300])
    return scores


def build_prompt(batch: list[tuple[Candidate, HeuristicResult]]) -> str:
    issues = [
        {
            "id": candidate.id,
            "repo": candidate.repo,
            "title": candidate.title,
            "labels": candidate.labels,
            "language": candidate.language,
            "comments": result.features.get("comments"),
            "age_days": result.features.get("age_days"),
            "body": (candidate.raw.get("body") or "")[:BODY_EXCERPT],
        }
        for candidate, result in batch
    ]
    return _PROMPT_HEADER + json.dumps(issues, indent=2)


def _parse(text: str) -> list[dict]:
    """Pull a JSON array out of the model's reply, tolerating code fences/preamble."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
