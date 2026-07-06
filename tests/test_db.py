"""Phase 0 acceptance: round-trips, idempotent upsert, and the status machine."""

import pytest

from brutus.db import (
    InvalidTransition,
    get_candidate,
    list_candidates,
    update_status,
    upsert_candidate,
)
from brutus.models import Candidate, Status


def _candidate(**overrides) -> Candidate:
    base = dict(
        source="github", repo="o/r", number=1, title="t", url="u",
        labels=["good first issue"], language="python", stars=10, raw={"x": 1},
    )
    base.update(overrides)
    return Candidate(**base)


def test_insert_roundtrip(conn):
    cid = upsert_candidate(conn, _candidate())
    got = get_candidate(conn, cid)
    assert got.repo == "o/r"
    assert got.labels == ["good first issue"]  # JSON column survives the trip
    assert got.raw == {"x": 1}
    assert got.status == Status.FETCHED


def test_upsert_dedupes_on_repo_and_number(conn):
    first = upsert_candidate(conn, _candidate(title="old"))
    again = upsert_candidate(conn, _candidate(title="new"))
    assert first == again
    assert get_candidate(conn, first).title == "new"  # discovery fields refreshed
    assert len(list_candidates(conn)) == 1


def test_upsert_preserves_pipeline_progress(conn):
    cid = upsert_candidate(conn, _candidate())
    update_status(conn, cid, Status.SCORED)
    upsert_candidate(conn, _candidate(title="re-fetched"))
    assert get_candidate(conn, cid).status == Status.SCORED  # not reset to FETCHED


def test_legal_transitions(conn):
    cid = upsert_candidate(conn, _candidate())
    update_status(conn, cid, Status.SCORED)
    update_status(conn, cid, Status.PICKED)
    assert get_candidate(conn, cid).status == Status.PICKED


def test_illegal_transition_rejected(conn):
    cid = upsert_candidate(conn, _candidate())
    with pytest.raises(InvalidTransition):
        update_status(conn, cid, Status.PUSHED)  # fetched -> pushed is not allowed


def test_list_filters_by_score(conn):
    upsert_candidate(conn, _candidate(repo="o/a", number=1))
    high = upsert_candidate(conn, _candidate(repo="o/b", number=2))
    update_status(conn, high, Status.SCORED)
    conn.execute("UPDATE candidates SET score = 5 WHERE id = ?", (high,))
    conn.commit()

    results = list_candidates(conn, min_score=4)
    assert [c.id for c in results] == [high]
