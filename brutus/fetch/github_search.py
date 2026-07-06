"""GitHub issue search — the one HTTP path all sources share.

Pure helpers (`build_query`, `ingest_items`, `_to_candidate`) are separated from
the I/O (`search_issues`) so they can be tested against recorded payloads without
hitting the network. `ingest_items` is the seam the tests use.
"""

from __future__ import annotations

import time
from typing import Callable

import httpx

from .. import db
from ..models import Candidate

GITHUB_API = "https://api.github.com"
SEARCH_PATH = "/search/issues"
PER_PAGE = 100
MAX_TOTAL = 1000  # GitHub Search API hard cap, regardless of pagination
USER_AGENT = "brutus-issue-finder"

# Default qualifiers: open issues nobody has claimed yet.
BASE_QUALIFIERS = ("is:issue", "is:open", "no:assignee")


def build_query(
    *,
    label: str | None = None,
    language: str | None = None,
    extra: str | None = None,
) -> str:
    """Compose a GitHub search `q` string. `extra` is raw qualifiers (e.g. 'repo:o/n')."""
    parts = list(BASE_QUALIFIERS)
    if label:
        parts.append(f'label:"{label}"')
    if language:
        parts.append(f"language:{language}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def fetch_github(
    conn,
    *,
    token: str | None,
    label: str | None = None,
    language: str | None = None,
    extra: str | None = None,
    limit: int = PER_PAGE,
    sort: str = "updated",
    client: httpx.Client | None = None,
) -> int:
    """Search GitHub and upsert the results. Returns the number of issues ingested.

    label=None fetches issues of ANY difficulty (no label filter) — the classifier
    scores tractability afterward. `sort` orders results (default: recently active)."""
    query = build_query(label=label, language=language, extra=extra)
    items = search_issues(query, token=token, limit=limit, sort=sort, client=client)
    return ingest_items(conn, items, source="github", language=language,
                        token=token, client=client)


# --- I/O -------------------------------------------------------------------

def search_issues(
    query: str,
    *,
    token: str | None,
    limit: int = PER_PAGE,
    sort: str = "updated",
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Return up to `limit` issue items for `query`, paginating and backing off on
    rate limits. Caps at the API's 1000-result ceiling."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    target = min(limit, MAX_TOTAL)
    items: list[dict] = []
    try:
        page = 1
        while len(items) < target:
            batch = _get_page(client, query, page, token=token, sort=sort, sleep=sleep)["items"]
            if not batch:
                break
            items.extend(batch)
            if len(batch) < PER_PAGE:  # last page
                break
            page += 1
    finally:
        if owns_client:
            client.close()
    return items[:target]


def _get_page(
    client: httpx.Client,
    query: str,
    page: int,
    *,
    token: str | None,
    sort: str = "updated",
    sleep: Callable[[float], None],
    max_retries: int = 3,
) -> dict:
    params: dict = {"q": query, "per_page": PER_PAGE, "page": page}
    if sort:
        params["sort"] = sort
        params["order"] = "desc"
    resp = None
    for attempt in range(max_retries + 1):
        resp = client.get(GITHUB_API + SEARCH_PATH, params=params, headers=_headers(token))
        if resp.status_code == 200:
            return resp.json()
        # 403/429 here are rate limits (primary or secondary); back off and retry.
        if resp.status_code in (403, 429) and attempt < max_retries:
            sleep(_retry_after(resp))
            continue
        break
    resp.raise_for_status()  # surfaces 422 bad-query and exhausted retries
    return {"items": []}


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _retry_after(resp: httpx.Response) -> float:
    """Seconds to wait before retrying, bounded so we never hang for long.

    ponytail: bounded 60s cap instead of computing the exact reset window — good
    enough for a single-user tool; revisit if we ever fetch at high volume.
    """
    if (retry_after := resp.headers.get("Retry-After")) is not None:
        return min(float(retry_after), 60)
    return 30 if resp.headers.get("X-RateLimit-Remaining") == "0" else 5


# --- pure mapping ----------------------------------------------------------

def ingest_items(
    conn, items: list[dict], *, source: str = "github", language: str | None = None,
    token: str | None = None, client: httpx.Client | None = None,
) -> int:
    """Map raw search items to candidates and upsert them. Returns the number of
    NEWLY discovered issues (existing ones are refreshed but not counted). Skips PRs
    and unattributable rows. When `token` is given, enriches each repo's star count."""
    candidates = []
    for item in items:
        if "pull_request" in item:
            continue
        candidate = _to_candidate(item, source=source, language=language)
        if candidate is not None:
            candidates.append(candidate)

    if token:
        enrich_stars(candidates, token=token, client=client)

    new = 0
    for candidate in candidates:
        is_new = db.candidate_id_for(conn, candidate.repo, candidate.number) is None
        db.upsert_candidate(conn, candidate)
        new += is_new
    return new


def _to_candidate(item: dict, *, source: str, language: str | None) -> Candidate | None:
    repo = _repo_from_api_url(item.get("repository_url", ""))
    if not repo:
        return None
    return Candidate(
        source=source,
        repo=repo,
        number=item["number"],
        title=item["title"],
        url=item["html_url"],
        labels=[label["name"] for label in item.get("labels", [])],
        language=language,  # search payload omits repo language; we know it from the query
        stars=0,  # ponytail: not in the issue-search payload; enrich in phase 3 if filtering needs it
        raw=item,
    )


def _repo_from_api_url(url: str) -> str:
    """'https://api.github.com/repos/owner/name' -> 'owner/name'."""
    marker = "/repos/"
    return url.split(marker, 1)[1] if marker in url else ""


def enrich_stars(candidates: list[Candidate], *, token: str | None,
                 client: httpx.Client | None = None) -> None:
    """Set each candidate's `stars` from its repo (stars aren't in the issue payload).
    One GET per unique repo, cached within the call."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    cache: dict[str, int] = {}
    try:
        for candidate in candidates:
            if candidate.repo not in cache:
                resp = client.get(f"{GITHUB_API}/repos/{candidate.repo}", headers=_headers(token))
                cache[candidate.repo] = (
                    resp.json().get("stargazers_count", 0) if resp.status_code == 200 else 0
                )
            candidate.stars = cache[candidate.repo]
    finally:
        if owns_client:
            client.close()
