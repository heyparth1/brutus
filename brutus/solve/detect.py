"""Guess a repo's build/test/lint commands from its marker files.

These are starting defaults only — they're written into AGENTS.md, and the agent
is told to correct them when wrong. Refining commands is exactly what the loop's
file-based state is for, so a rough guess here is fine.
"""

from __future__ import annotations

# marker file -> commands it implies. Later markers override earlier `test`/`build`.
_RULES: list[tuple[str, dict[str, str]]] = [
    ("pyproject.toml", {"test": "pytest -q", "lint": "ruff check ."}),
    ("setup.py", {"test": "pytest -q"}),
    ("tox.ini", {"test": "tox"}),
    ("package.json", {
        "test": "npm test",
        "build": "npm run build --if-present",
        "lint": "npm run lint --if-present",
    }),
    ("Cargo.toml", {"test": "cargo test", "build": "cargo build", "lint": "cargo clippy"}),
    ("go.mod", {"test": "go test ./...", "build": "go build ./..."}),
    ("Makefile", {"test": "make test"}),
]


def detect_commands(files: set[str]) -> dict[str, str]:
    """Return {name: command} for the toolchains present. Empty if none recognized."""
    commands: dict[str, str] = {}
    for marker, implied in _RULES:
        if marker in files:
            commands.update(implied)
    return commands
