"""Download images referenced in an issue's body/comments so the (multimodal)
agent can actually see screenshots — not just their URLs. Claude Code's Read tool
renders local image files, so we save them into .brutus/images/ and point at them.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

# GitHub-hosted image URLs, or any URL ending in an image extension.
_IMG_RE = re.compile(
    r"https?://(?:"
    r"user-images\.githubusercontent\.com/\S+"
    r"|github\.com/user-attachments/assets/\S+"
    r"|\S+\.(?:png|jpe?g|gif|webp))",
    re.IGNORECASE,
)


def extract_image_urls(text: str, limit: int = 6) -> list[str]:
    urls: list[str] = []
    for match in _IMG_RE.finditer(text or ""):
        url = match.group(0).rstrip(').,"\'>')
        if url not in urls:
            urls.append(url)
    return urls[:limit]


def download_images(
    text: str, dest_dir: Path, *, client: httpx.Client | None = None, limit: int = 6
) -> list[Path]:
    """Save up to `limit` images found in `text` into dest_dir. Returns saved paths;
    failures are skipped (best-effort — never blocks a solve)."""
    urls = extract_image_urls(text, limit)
    if not urls:
        return []

    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=30)
    saved: list[Path] = []
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for i, url in enumerate(urls, 1):
            try:
                resp = client.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200 or not resp.content:
                continue
            path = dest_dir / f"image_{i}{_ext(url, resp.headers.get('content-type', ''))}"
            path.write_bytes(resp.content)
            saved.append(path)
    finally:
        if owns_client:
            client.close()
    return saved


def _ext(url: str, content_type: str) -> str:
    for e in (".png", ".jpeg", ".jpg", ".gif", ".webp"):
        if url.lower().endswith(e):
            return e
    if "png" in content_type:
        return ".png"
    if "gif" in content_type:
        return ".gif"
    if "webp" in content_type:
        return ".webp"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    return ".png"
