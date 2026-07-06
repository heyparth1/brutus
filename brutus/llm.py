"""Provider-agnostic LLM call.

`llm_cmd` is any command that reads a prompt on stdin and prints the completion to
stdout — `llm -m <model>`, `claude -p`, `ollama run <model>`, a wrapper script, etc.
This is the "LLM you provide" seam: brutus never hard-codes a vendor SDK.
"""

from __future__ import annotations

import shlex
import subprocess


class LLMError(RuntimeError):
    pass


def complete(prompt: str, *, cmd: str, timeout: int = 180) -> str:
    if not cmd:
        raise LLMError(
            "no LLM command configured — set BRUTUS_LLM_CMD or llm_cmd in brutus.toml "
            "(e.g. 'llm -m claude-opus-4-8')"
        )
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise LLMError(f"LLM command not found: {cmd!r}") from exc

    if proc.returncode != 0:
        raise LLMError(
            f"LLM command failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
        )
    return proc.stdout
