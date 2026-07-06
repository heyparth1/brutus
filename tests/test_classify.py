"""Phase 2 acceptance: heuristics drop/keep correctly, and run_classify scores
survivors via a mocked LLM while spending nothing on dropped issues."""

import json

from brutus.classify import run_classify
from brutus.classify.heuristics import evaluate
from brutus.classify.llm_score import _parse
from brutus.db import get_candidate, upsert_candidate
from brutus.models import Candidate, Status

LONG_BODY = "A clearly described, self-contained issue with steps to reproduce. " * 3


def _cand(number, body, **raw_extra) -> Candidate:
    raw = {"body": body, "comments": 1, "created_at": "2025-01-01T00:00:00Z", **raw_extra}
    return Candidate(
        source="github", repo=f"o/r{number}", number=number, title="t",
        url="u", labels=["good first issue"], language="python", raw=raw,
    )


def test_heuristics_drops_short_body():
    result = evaluate(_cand(1, "too short"))
    assert not result.keep
    assert any("too short" in r for r in result.reasons)


def test_heuristics_drops_assigned():
    result = evaluate(_cand(1, LONG_BODY, assignees=[{"login": "bob"}]))
    assert not result.keep
    assert "already assigned" in result.reasons


def test_heuristics_keeps_good_issue():
    result = evaluate(_cand(1, LONG_BODY))
    assert result.keep
    assert result.features["has_beginner_label"] is True
    assert result.features["body_len"] > 40


def test_run_classify_scores_survivors_and_drops_junk(conn):
    good1 = upsert_candidate(conn, _cand(1, LONG_BODY))
    good2 = upsert_candidate(conn, _cand(2, LONG_BODY))
    junk = upsert_candidate(conn, _cand(3, "x"))  # short body -> dropped

    captured = {}

    def fake_complete(prompt: str) -> str:
        captured["prompt"] = prompt
        payload = [
            {"id": good1, "score": 4, "reason": "clear and scoped"},
            {"id": good2, "score": 2, "reason": "somewhat vague"},
        ]
        return f"```json\n{json.dumps(payload)}\n```"  # fenced, to exercise parsing

    stats = run_classify(conn, complete=fake_complete)

    assert stats == {"dropped": 1, "scored": 2, "skipped": 0}
    assert get_candidate(conn, good1).score == 4
    assert get_candidate(conn, good1).status == Status.SCORED
    assert get_candidate(conn, junk).score == 1
    assert get_candidate(conn, junk).status == Status.SCORED

    # The LLM only ever saw survivors, never the dropped issue's repo.
    assert "o/r1" in captured["prompt"]
    assert "o/r3" not in captured["prompt"]


def test_parse_tolerates_fences_and_garbage():
    assert _parse('```json\n[{"id": 1, "score": 3}]\n```') == [{"id": 1, "score": 3}]
    assert _parse("no json here") == []
