"""The default Claude solver: clone+setup, then an interactive Claude session,
then status -> review. Sandbox.prepare and the claude subprocess are mocked."""

from typer.testing import CliRunner

import brutus.cli as cli
from brutus import db
from brutus.models import Candidate, Status
from brutus.solve.sandbox import Sandbox

runner = CliRunner()


def _seed_picked(db_path) -> int:
    conn = db.connect(db_path)
    db.init_db(conn)
    cid = db.upsert_candidate(conn, Candidate(source="github", repo="o/r", number=7,
                                              title="t", url="u"))
    db.update_score(conn, cid, 5, "x")
    db.update_status(conn, cid, Status.SCORED)
    db.update_status(conn, cid, Status.PICKED)
    conn.close()
    return cid


def test_claude_solver_starts_session_then_review(tmp_path, monkeypatch):
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BRUTUS_DB_PATH", str(db_path))
    monkeypatch.setenv("BRUTUS_SOLVER", "claude")
    monkeypatch.setenv("BRUTUS_CLAUDE_CMD", "claude --dangerously-skip-permissions")
    cid = _seed_picked(db_path)

    monkeypatch.setattr(Sandbox, "prepare", lambda self: None)  # no real clone
    monkeypatch.setattr(Sandbox, "commit", lambda self, msg: True)  # no real git
    monkeypatch.setattr(cli, "fetch_issue", lambda c: "")  # no real gh
    monkeypatch.setattr(cli, "fetch_referenced_issues", lambda c, t: "")
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: calls.append(argv))

    result = runner.invoke(cli.app, ["solve", str(cid), "--local"])
    assert result.exit_code == 0

    # Claude was launched with the issue prompt...
    assert calls and calls[0][0] == "claude"
    assert any("ISSUE.md" in arg for arg in calls[0])
    # ...and the candidate advanced to review.
    conn = db.connect(db_path)
    assert db.get_candidate(conn, cid).status is Status.REVIEW
