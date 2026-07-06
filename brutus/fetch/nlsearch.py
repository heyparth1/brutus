"""Natural-language issue search.

A cheap LLM turns a free-text request ("popular python repos, good first issues
from this month") into GitHub issue-search qualifiers + a stars threshold. Stars
aren't searchable on issues, so we fetch, enrich each repo's star count, then filter.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from .. import db
from . import github_search as gh

NL_PROMPT = """\
Turn the user's request into a GitHub ISSUE search.

Return ONLY JSON: {{"q": "<qualifiers>", "min_stars": <int or null>}}

Rules for q:
- Always include: is:issue is:open no:assignee
- You MAY add: language:<lang>, label:"<label>", created:>YYYY-MM-DD,
  updated:>YYYY-MM-DD, comments:<N, repo:<owner/name>, org:<name>
- Repository star count is NOT searchable on issues — put any "popular / N+ stars"
  requirement in min_stars (integer), otherwise null.
- Use dates relative to today = {today} for time phrases (this week/month/year).
- Don't invent labels; only add label: if the user clearly implies one (e.g. "good
  first issue", "bug", "help wanted"). Difficulty is scored later, so don't force a label.

Request: {nl}
"""


def nl_to_spec(nl: str, complete: Callable[[str], str], *, today: str) -> dict:
    """LLM: natural language -> {"q": <qualifiers>, "min_stars": int|None}."""
    data = _parse(complete(NL_PROMPT.format(nl=nl, today=today)))
    q = str(data.get("q") or "").strip() or "is:issue is:open no:assignee"
    for required in ("is:issue", "is:open", "no:assignee"):
        if required not in q:
            q = f"{required} {q}"
    min_stars = data.get("min_stars")
    return {
        "q": q,
        "min_stars": int(min_stars) if isinstance(min_stars, (int, float)) else None,
    }


def fetch_search(
    conn,
    *,
    token: str | None,
    nl: str,
    complete: Callable[[str], str],
    today: str,
    limit: int = gh.PER_PAGE,
    client=None,
) -> dict:
    """Translate `nl`, search, enrich stars, filter by min_stars, upsert. Returns
    {fetched, kept, query, min_stars}."""
    spec = nl_to_spec(nl, complete, today=today)
    items = gh.search_issues(spec["q"], token=token, limit=limit, client=client)

    candidates = []
    for item in items:
        if "pull_request" in item:
            continue
        candidate = gh._to_candidate(item, source="github", language=None)
        if candidate:
            candidates.append(candidate)

    gh.enrich_stars(candidates, token=token, client=client)
    min_stars = spec["min_stars"] or 0
    kept = [c for c in candidates if c.stars >= min_stars]

    new = 0
    for candidate in kept:
        is_new = db.candidate_id_for(conn, candidate.repo, candidate.number) is None
        db.upsert_candidate(conn, candidate)
        new += is_new
    return {"fetched": new, "kept": len(kept), "query": spec["q"], "min_stars": min_stars}


def _parse(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
