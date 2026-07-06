"""Phase 1 acceptance: query building + payload mapping + idempotent ingest.

No network: we feed recorded-shape search items straight into `ingest_items`.
"""

from brutus.db import list_candidates
from brutus.fetch.github_search import (
    _repo_from_api_url,
    build_query,
    ingest_items,
)


def _item(number, repo="o/r", pr=False):
    item = {
        "number": number,
        "title": f"issue {number}",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "labels": [{"name": "good first issue"}],
    }
    if pr:
        item["pull_request"] = {"url": "https://api.github.com/repos/x/y/pulls/1"}
    return item


def test_build_query():
    q = build_query(label="good first issue", language="python")
    assert 'label:"good first issue"' in q
    assert "language:python" in q
    assert all(part in q for part in ("is:issue", "is:open", "no:assignee"))


def test_build_query_with_extra_scope():
    assert "repo:foo/bar" in build_query(extra="repo:foo/bar")


def test_ingest_maps_fields_and_skips_prs(conn):
    n = ingest_items(conn, [_item(1), _item(2), _item(3, pr=True)], language="python")
    assert n == 2  # PR skipped

    cands = {c.number: c for c in list_candidates(conn)}
    assert set(cands) == {1, 2}
    assert cands[1].repo == "o/r"
    assert cands[1].labels == ["good first issue"]
    assert cands[1].language == "python"
    assert cands[1].source == "github"


def test_ingest_is_idempotent(conn):
    ingest_items(conn, [_item(1)])
    ingest_items(conn, [_item(1)])  # re-fetch
    assert len(list_candidates(conn)) == 1


def test_ingest_skips_unattributable_items(conn):
    bad = {"number": 9, "title": "x", "html_url": "u", "repository_url": "garbage"}
    assert ingest_items(conn, [bad]) == 0


def test_repo_from_api_url():
    assert _repo_from_api_url("https://api.github.com/repos/foo/bar") == "foo/bar"
    assert _repo_from_api_url("nonsense") == ""
