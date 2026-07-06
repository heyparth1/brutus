import pytest

from brutus.db import connect, init_db


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    # Don't let the repo's real .env leak into Config during tests.
    monkeypatch.setenv("BRUTUS_ENV_FILE", "/nonexistent-brutus.env")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    yield c
    c.close()
