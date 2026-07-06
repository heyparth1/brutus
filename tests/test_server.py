"""HTTP API: status/candidates reads, and fetch/classify/pick actions (mocked)."""

import pytest
from fastapi.testclient import TestClient

import brutus.server as server
from brutus import db
from brutus.models import Candidate, Status


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUTUS_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("BRUTUS_LLM_CMD", "fake")
    return TestClient(server.app)


def _seed_scored(db_path, number=1, status=Status.SCORED) -> int:
    conn = db.connect(db_path)
    db.init_db(conn)
    cid = db.upsert_candidate(conn, Candidate(
        source="github", repo="o/r", number=number, title="t", url="u",
        raw={"body": "b" * 80}))
    db.update_score(conn, cid, 5, "great")
    db.update_status(conn, cid, Status.SCORED)
    conn.close()
    return cid


def test_status_and_candidates(client, tmp_path):
    cid = _seed_scored(tmp_path / "b.db")
    assert client.get("/api/status").json() == {"scored": 1}
    rows = client.get("/api/candidates", params={"min_score": 4}).json()
    assert [r["id"] for r in rows] == [cid]
    assert rows[0]["scoreReason"] == "great"


def test_fetch_endpoint(client, tmp_path, monkeypatch):
    def fake_fetch(conn, **kwargs):
        db.upsert_candidate(conn, Candidate(source="github", repo="o/x", number=9,
                                            title="t", url="u"))
        return 1
    monkeypatch.setattr(server.github_search, "fetch_github", fake_fetch)

    res = client.post("/api/fetch", json={"lang": "python", "limit": 5})
    assert res.status_code == 200
    assert res.json() == {"fetched": 1}


def test_classify_endpoint(client, tmp_path, monkeypatch):
    _seed_fetched(tmp_path / "b.db")
    monkeypatch.setattr(server, "run_classify",
                        lambda conn, **kw: {"dropped": 0, "scored": 1, "skipped": 0})
    res = client.post("/api/classify")
    assert res.json()["scored"] == 1


def test_pick_endpoint(client, tmp_path):
    cid = _seed_scored(tmp_path / "b.db")
    res = client.post(f"/api/pick/{cid}")
    assert res.json() == {"ok": True, "status": "picked"}

    conn = db.connect(tmp_path / "b.db")
    assert db.get_candidate(conn, cid).status is Status.PICKED


def test_pick_missing_returns_404(client):
    assert client.post("/api/pick/999").status_code == 404


def test_solve_open_writes_command_and_launches(client, monkeypatch):
    calls = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda argv, *a, **k: calls.append(argv))
    res = client.post("/api/solve/5")
    assert res.json() == {"ok": True, "launched": True}
    assert calls and calls[0][0] == "open"  # launched via macOS `open`
    assert calls[0][-1].endswith("brutus-solve-5.command")


def _seed_fetched(db_path):
    conn = db.connect(db_path)
    db.init_db(conn)
    db.upsert_candidate(conn, Candidate(source="github", repo="o/r", number=1,
                                        title="t", url="u", raw={"body": "x" * 80}))
    conn.close()
