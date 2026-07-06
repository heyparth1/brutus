"""Natural-language search: LLM query translation + stars filtering (no network)."""

import json

from brutus import db
from brutus.fetch import github_search, nlsearch
from brutus.models import Status


def test_nl_to_spec_forces_base_qualifiers():
    spec = nlsearch.nl_to_spec(
        "popular python bugs this month",
        lambda p: json.dumps({"q": "language:python label:bug", "min_stars": 1000}),
        today="2026-07-02",
    )
    assert "is:issue" in spec["q"] and "is:open" in spec["q"] and "no:assignee" in spec["q"]
    assert "language:python" in spec["q"]
    assert spec["min_stars"] == 1000


def test_nl_to_spec_handles_null_stars_and_garbage():
    spec = nlsearch.nl_to_spec("anything", lambda p: "not json", today="2026-07-02")
    assert spec["min_stars"] is None
    assert spec["q"] == "is:issue is:open no:assignee"


def test_fetch_search_filters_by_stars(conn, monkeypatch):
    # LLM returns a query + a 500-star floor.
    complete = lambda p: json.dumps({"q": "language:python", "min_stars": 500})

    # Two issues, different repos.
    items = [
        {"number": 1, "title": "big", "html_url": "u",
         "repository_url": "https://api.github.com/repos/big/repo", "labels": []},
        {"number": 2, "title": "small", "html_url": "u",
         "repository_url": "https://api.github.com/repos/small/repo", "labels": []},
    ]
    monkeypatch.setattr(github_search, "search_issues", lambda q, **kw: items)

    def fake_enrich(candidates, **kw):
        for c in candidates:
            c.stars = 900 if c.repo == "big/repo" else 10  # only big/repo clears 500
    monkeypatch.setattr(github_search, "enrich_stars", fake_enrich)

    stats = nlsearch.fetch_search(conn, token="t", nl="popular", complete=complete,
                                  today="2026-07-02")
    assert stats["min_stars"] == 500
    assert stats["fetched"] == 1  # only the 900-star repo's issue kept

    rows = db.list_candidates(conn, status=Status.FETCHED)
    assert {c.repo for c in rows} == {"big/repo"}
    assert rows[0].stars == 900
