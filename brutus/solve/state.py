"""The Ralph loop's on-disk memory. Each iteration starts with a fresh agent
context, so everything it needs must be readable from these files.

All brutus files live under a `.brutus/` dir inside the clone, which the sandbox
adds to `.git/info/exclude` — so they never get staged into the actual PR and
never collide with files the target repo already tracks.

  .brutus/PROMPT.md    static standing instruction (piped to the agent)
  .brutus/ISSUE.md     the issue + an acceptance checklist
  .brutus/fix_plan.md  mutable TODO the agent rewrites
  .brutus/progress.txt append-only learnings (why, not just what)
  .brutus/AGENTS.md    repo conventions + detected build/test/lint commands
"""

from __future__ import annotations

from pathlib import Path

from ..models import Candidate

STATE_DIR = ".brutus"

# Static. The agent reads this verbatim every iteration.
PROMPT_MD = """\
# Standing instructions

You are fixing ONE GitHub issue in small, reversible steps. You have NO memory of
previous iterations — everything you need is on disk. Read these every run:

- .brutus/ISSUE.md     — the issue and its acceptance checklist
- .brutus/fix_plan.md  — the current TODO; the source of truth for what's left
- .brutus/progress.txt — append-only log of what past iterations did and WHY
- .brutus/AGENTS.md    — repo conventions and the build/test/lint commands

Each run:
1. Read all of the above.
2. Pick the SINGLE highest-priority unchecked item in .brutus/fix_plan.md.
3. Make the smallest change that advances it. One logical change only.
4. Update .brutus/fix_plan.md, and APPEND to .brutus/progress.txt what you did and
   WHY — future iterations cannot see your reasoning unless it is written down.
5. Do NOT run git, and do NOT touch the .brutus/ directory's purpose. The harness
   commits your code changes for you when the checks pass.

Backpressure: after you stop, the harness runs the build/test/lint commands from
.brutus/AGENTS.md. If they fail, the next iteration must fix them first.

Only when every item in .brutus/ISSUE.md's acceptance checklist is satisfied, output
this exact line on its own and nothing else like it:

<promise>COMPLETE</promise>
"""

_DEFAULT_PLAN = """\
# Fix plan

- [ ] Understand the issue and locate the relevant code
- [ ] Implement the smallest change that resolves it
- [ ] Add or update a test that proves the fix
- [ ] Make build / test / lint pass
"""


def state_dir(workdir: Path) -> Path:
    return workdir / STATE_DIR


def init_state(workdir: Path, *, issue: Candidate, commands: dict[str, str]) -> None:
    """Write the initial state files into the clone's .brutus/ directory."""
    d = state_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "PROMPT.md").write_text(PROMPT_MD)
    (d / "ISSUE.md").write_text(_render_issue(issue))
    (d / "fix_plan.md").write_text(_DEFAULT_PLAN)
    (d / "progress.txt").write_text("")
    (d / "AGENTS.md").write_text(_render_agents(commands))


def append_progress(workdir: Path, note: str) -> None:
    with (state_dir(workdir) / "progress.txt").open("a") as fh:
        fh.write(note.rstrip() + "\n")


def _render_issue(issue: Candidate) -> str:
    body = (issue.raw.get("body") or "(no description provided)").strip()
    labels = ", ".join(issue.labels) or "(none)"
    return (
        f"# {issue.title}\n\n"
        f"{issue.url}\n"
        f"Repo: {issue.repo}  ·  Issue #{issue.number}  ·  Labels: {labels}\n\n"
        f"## Description\n\n{body}\n\n"
        "## Acceptance checklist\n\n"
        "- [ ] The change resolves the issue described above\n"
        "- [ ] Existing tests still pass\n"
        "- [ ] New behavior is covered by a test\n"
    )


def _render_agents(commands: dict[str, str]) -> str:
    lines = [
        "# Repo notes (maintained by brutus)",
        "",
        "Detected build/test/lint commands. If any are wrong, FIX THEM HERE — future",
        "iterations read this file instead of rediscovering the commands.",
        "",
    ]
    if commands:
        lines += [f"- {name}: `{cmd}`" for name, cmd in commands.items()]
    else:
        lines.append("- (none detected — add the correct commands here)")
    return "\n".join(lines) + "\n"
