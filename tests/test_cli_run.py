"""Phase 6: `status` funnel view and the `run` fetch→classify→browse chain."""

from typer.testing import CliRunner

import brutus.cli as cli
from brutus import db
from brutus.models import Candidate, Status

runner = CliRunner()


def test_status_shows_counts(tmp_path, monkeypatch):
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BRUTUS_DB_PATH", str(db_path))
    conn = db.connect(db_path)
    db.init_db(conn)
    cid = db.upsert_candidate(conn, Candidate(source="github", repo="o/r", number=1, title="t", url="u"))
    db.upsert_candidate(conn, Candidate(source="github", repo="o/r", number=2, title="t", url="u"))
    db.update_status(conn, cid, Status.SCORED)
    conn.close()

    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "fetched" in result.stdout
    assert "scored" in result.stdout
    assert "total 2" in result.stdout


def test_run_chains_fetch_classify_browse(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUTUS_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("BRUTUS_LLM_CMD", "fake")

    def fake_fetch(conn, **kwargs):
        db.upsert_candidate(conn, Candidate(
            source="github", repo="o/win", number=1, title="Winning issue",
            url="u", raw={"body": "b" * 80}))
        return 1

    def fake_classify(conn, **kwargs):
        for c in db.list_candidates(conn, status=Status.FETCHED):
            db.update_score(conn, c.id, 5, "great")
            db.update_status(conn, c.id, Status.SCORED)
        return {"dropped": 0, "scored": 1, "skipped": 0}

    monkeypatch.setattr(cli.github_search, "fetch_github", fake_fetch)
    monkeypatch.setattr(cli, "run_classify", fake_classify)

    result = runner.invoke(cli.app, ["run", "--min-score", "4"])
    assert result.exit_code == 0
    assert "Winning issue" in result.stdout


def test_run_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUTUS_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 1
    assert "GITHUB_TOKEN" in result.stdout
