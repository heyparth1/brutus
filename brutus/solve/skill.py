"""The `brutus-solve` Claude Code skill.

brutus writes this into the cloned repo's `.claude/skills/` so the Claude session
follows a disciplined plan→execute→verify→cleanup workflow. The `.claude/` dir is
excluded from git, so the skill never lands in the PR.

To change the workflow permanently, edit SKILL_MD here. It's regenerated into each
repo on every solve, so per-repo edits are throwaway.
"""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "brutus-solve"

SKILL_MD = """\
---
name: brutus-solve
description: Resolve one GitHub issue methodically — read the issue, write a plan, execute it step by step, prove the fix with a regression test that follows the repo's conventions, clean up scratch/debug artifacts, and summarize for human review. Use when resolving the issue in ./.brutus/ISSUE.md.
---

# brutus-solve

You are resolving ONE GitHub issue, described in `./.brutus/ISSUE.md`. Follow these
steps in order. Do NOT run git and do NOT open a PR — a human reviews and approves
your work afterward.

## 1. Understand
- Read `./.brutus/ISSUE.md` in full, including its acceptance checklist.
- Read `./.brutus/AGENTS.md` for the repo's build/test/lint commands.
- Explore the relevant code before changing anything. Don't guess.

## 2. Plan
Write a short ordered plan to `./.brutus/PLAN.md` (3–7 steps). Each step is one
small, independently-verifiable change. Restate the issue's acceptance criteria at
the top so you can check against them later.

## 3. Execute — one step at a time
For each step in the plan:
- Make the smallest change that advances that step. One logical change.
- Run the repo's build/lint after the change to catch breakage early.
- Tick the step off in `./.brutus/PLAN.md` and note what you did.

## 4. Prove it with a regression test
- Write a focused test that PROVES the issue is fixed: it must FAIL before your fix
  and PASS after. Confirm both directions.
- Put it where the repo keeps its tests, matching their framework, file layout,
  naming, and style — read neighboring tests first. This test ships in the PR.
- Run it green, then run the existing suite — do not break anything.

## 5. Clean up (keep the regression test)
- KEEP the regression test from step 4 — maintainers expect the fix to be proven in
  the PR, and a missing test is the most common reason a PR gets pushback.
- Remove ONLY scratch/debug artifacts you added while working: stray prints,
  commented-out code, temp/throwaway files, one-off experiments. The final diff =
  the fix + its test, nothing else.
- Re-run build/lint/tests to confirm everything is still green.

## 6. Summarize for review
Write `./.brutus/SUMMARY.md` covering:
- What changed and why (file by file).
- Which acceptance criteria are now met.
- The regression test you added and what it asserts.
- Anything the human should double-check.

The human reads `SUMMARY.md` + the diff, then approves the PR. Stop after writing it.
"""


def install_skill(workdir: Path) -> Path:
    """Write the skill into <workdir>/.claude/skills/brutus-solve/SKILL.md."""
    skill_dir = workdir / ".claude" / "skills" / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(SKILL_MD)
    return path
