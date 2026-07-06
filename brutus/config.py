"""Runtime config. Resolution order: environment variable > brutus.toml > default.

Secrets (the GitHub token) come from the environment only. The LLM is
caller-provided: `llm_model` and `agent_cli` tell the phase-4 Ralph loop which
model and which coding-agent CLI to drive — they stay empty until you set them.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/brutus.db")
CONFIG_FILE = Path("brutus.toml")


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env into the environment (real env wins).

    Path overridable via BRUTUS_ENV_FILE. Kept dependency-free on purpose.
    """
    path = Path(os.environ.get("BRUTUS_ENV_FILE", ".env"))
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, val = line.partition("=")
        if not sep or not key.strip():
            continue
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    github_token: str | None
    llm_cmd: str          # prompt->text CLI, reads prompt on stdin (e.g. "llm -m <model>"); phase 2
    solver: str           # "claude" (interactive Claude session) | "ralph" (headless loop)
    claude_cmd: str       # the Claude Code command for the claude solver
    review_cmd: str       # one-shot LLM for the PR safety review (reads prompt on stdin)
    agent_cli: str        # ralph solver's agent (e.g. opencode/GLM) run inside the sandbox
    setup_cmd: str        # cheap LLM (Gemini) that plans the repo's setup commands; optional
    agent_image: str      # docker image with the toolchain + agent CLI; phase 4
    agent_env: str        # comma-separated env var names to pass into the sandbox (secrets)
    sandbox: str          # "docker" (isolated) | "local" (runs the agent on the host)
    db_path: Path

    @classmethod
    def load(cls, config_file: Path = CONFIG_FILE) -> "Config":
        _load_dotenv()
        file_cfg: dict[str, object] = {}
        if config_file.exists():
            file_cfg = tomllib.loads(config_file.read_text())

        def pick(key: str, env: str, default: str | None = None) -> str | None:
            return os.environ.get(env) or file_cfg.get(key, default)  # type: ignore[return-value]

        return cls(
            github_token=pick("github_token", "GITHUB_TOKEN"),
            llm_cmd=pick("llm_cmd", "BRUTUS_LLM_CMD", "") or "",
            solver=pick("solver", "BRUTUS_SOLVER", "claude") or "claude",
            claude_cmd=pick("claude_cmd", "BRUTUS_CLAUDE_CMD",
                            "claude --dangerously-skip-permissions --model fable") or "",
            review_cmd=pick("review_cmd", "BRUTUS_REVIEW_CMD", "claude -p --model fable") or "",
            agent_cli=pick("agent_cli", "BRUTUS_AGENT_CLI", "") or "",
            setup_cmd=pick("setup_cmd", "BRUTUS_SETUP_CMD", "") or "",
            agent_image=pick("agent_image", "BRUTUS_AGENT_IMAGE", "") or "",
            agent_env=pick("agent_env", "BRUTUS_AGENT_ENV", "ANTHROPIC_API_KEY") or "",
            sandbox=pick("sandbox", "BRUTUS_SANDBOX", "docker") or "docker",
            db_path=Path(pick("db_path", "BRUTUS_DB_PATH", str(DEFAULT_DB_PATH))),
        )
