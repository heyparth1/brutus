"""Beginner-label feeds.

Sites like goodfirstissue.dev and up-for-grabs.net are, in practice, curated views
over a fixed set of GitHub labels. Rather than scrape brittle HTML, we run the same
label searches directly through `github_search`.
ponytail: revisit only if a feed ever surfaces issues a label search can't.
"""

from __future__ import annotations

import httpx

from . import github_search

# The labels these communities standardize on.
BEGINNER_LABELS = (
    "good first issue",
    "good-first-issue",
    "help wanted",
    "first-timers-only",
    "beginner-friendly",
)


def fetch_feeds(
    conn,
    *,
    token: str | None,
    language: str | None = None,
    labels: tuple[str, ...] = BEGINNER_LABELS,
    limit_per_label: int = github_search.PER_PAGE,
    client: httpx.Client | None = None,
) -> int:
    """Search every beginner label and upsert results. Returns total ingested
    (dedup across labels is handled by the upsert's `(repo, number)` constraint)."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        return sum(
            github_search.fetch_github(
                conn,
                token=token,
                label=label,
                language=language,
                limit=limit_per_label,
                client=client,
            )
            for label in labels
        )
    finally:
        if owns_client:
            client.close()
