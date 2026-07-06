"""Repo setup planning. A cheap LLM reads the repo's signals and returns the
install/build/test/lint commands as JSON. brutus runs the install; the rest
become the Ralph loop's backpressure. Pure helpers here; the sandbox runs them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CONTEXT_FILES = ("README.md", "README.rst", "CONTRIBUTING.md", "pyproject.toml",
                 "setup.py", "package.json", "Cargo.toml", "go.mod", "Makefile", "tox.ini")
FILE_BUDGET = 2000  # chars per context file

SETUP_PROMPT = """\
You are setting up a freshly cloned repo so an automated agent can build and test it.
From the files below, output ONLY a JSON object with the shell commands to use:

{{"install": "<install deps, or empty>", "build": "<or empty>",
  "test": "<run the test suite>", "lint": "<or empty>"}}

For Python repos, a fresh virtualenv is ALREADY active (pip/python/pytest point into
it) — do NOT create a venv; just give the plain install command (e.g. "pip install -e ."
or "pip install -r requirements.txt"). Use the repo's real toolchain. Omit a command
(empty string) if it doesn't apply. No prose, no code fences — just the JSON.

## Repo files
{context}
"""


def gather_context(workdir: Path) -> str:
    """Top-level file listing + truncated contents of known setup files."""
    if not workdir.exists():
        return ""
    listing = sorted(p.name + ("/" if p.is_dir() else "") for p in workdir.iterdir())
    chunks = [f"Top-level entries: {', '.join(listing)}"]
    for name in CONTEXT_FILES:
        f = workdir / name
        if f.is_file():
            chunks.append(f"\n--- {name} ---\n{f.read_text(errors='replace')[:FILE_BUDGET]}")
    return "\n".join(chunks)


def parse_commands(text: str) -> dict[str, str]:
    """Extract the commands JSON, tolerating fences/preamble. Returns only non-empty
    string values among install/build/test/lint."""
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
    allowed = ("install", "build", "test", "lint")
    return {k: v for k in allowed if isinstance(v := data.get(k), str) and v.strip()}
