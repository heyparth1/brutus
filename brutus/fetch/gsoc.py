"""GSoC org importer.

GSoC publishes participating orgs as annual HTML, not an API. You supply the org
logins (or 'owner/name' repos) once per season; we pull their beginner issues via
`github_search` scoped with `org:` / `repo:`.
ponytail: a hand-maintained org list beats a scraper that breaks every year.
"""

from __future__ import annotations

import httpx

from . import github_search


def fetch_gsoc(
    conn,
    *,
    token: str | None,
    orgs: list[str],
    label: str = "good first issue",
    limit_per_org: int = github_search.PER_PAGE,
    client: httpx.Client | None = None,
) -> int:
    """Fetch beginner issues for each GSoC org/repo. An entry with '/' is treated as
    a specific repo; otherwise as a whole org."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        total = 0
        for org in orgs:
            scope = f"repo:{org}" if "/" in org else f"org:{org}"
            total += github_search.fetch_github(
                conn,
                token=token,
                label=label,
                extra=scope,
                limit=limit_per_org,
                client=client,
            )
        return total
    finally:
        if owns_client:
            client.close()
