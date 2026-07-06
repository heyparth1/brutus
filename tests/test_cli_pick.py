"""Phase 3 acceptance: `list` renders the scored pool; `pick` moves one to picked."""

from typer.testing import CliRunner

from brutus import db
from brutus.cli import app
from brutus.models import Candidate, Status

runner = CliRunner()


def _seed(db_path, *, score=4):
    conn = db.connect(db_path)
    db.init_db(conn)
    cid = db.upsert_candidate(
        conn,
        Candidate(source="github", repo="o/r", number=5, title="Fix the thing",
                  url="https://x/5", labels=["good first issue"]),
    )
    db.update_score(conn, cid, score, "clear and scoped")
    db.update_status(conn, cid, Status.SCORED)
    conn.close()
    return cid


def test_list_renders_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUTUS_DB_PATH", str(tmp_path / "b.db"))
    _seed(tmp_path / "b.db")

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "o/r" in result.stdout
    assert "#5" in result.stdout


def test_pick_moves_to_picked(tmp_path, monkeypatch):
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BRUTUS_DB_PATH", str(db_path))
    cid = _seed(db_path)

    result = runner.invoke(app, ["pick", str(cid)])
    assert result.exit_code == 0
    assert "Picked" in result.stdout

    conn = db.connect(db_path)
    picked = db.list_candidates(conn, status=Status.PICKED)
    assert [c.id for c in picked] == [cid]


def test_reset_recovers_stuck_solving(tmp_path, monkeypatch):
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BRUTUS_DB_PATH", str(db_path))
    cid = _seed(db_path)
    conn = db.connect(db_path)
    db.update_status(conn, cid, Status.PICKED)
    db.update_status(conn, cid, Status.SOLVING)  # simulate an interrupted solve
    conn.close()

    result = runner.invoke(app, ["reset", str(cid)])
    assert result.exit_code == 0

    conn = db.connect(db_path)
    assert db.get_candidate(conn, cid).status is Status.PICKED


def test_pick_rejects_unscored(tmp_path, monkeypatch):
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BRUTUS_DB_PATH", str(db_path))
    conn = db.connect(db_path)
    db.init_db(conn)
    cid = db.upsert_candidate(
        conn, Candidate(source="github", repo="o/r", number=1, title="t", url="u")
    )  # still FETCHED, not scored
    conn.close()

    result = runner.invoke(app, ["pick", str(cid)])
    assert result.exit_code == 1
    assert "can't pick" in result.stdout
