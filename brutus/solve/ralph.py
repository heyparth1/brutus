"""The Ralph loop — pure control flow over a `Workspace`.

Each iteration runs the agent once (fresh context), then the repo's own checks act
as backpressure: changes are committed only when checks pass. The loop stops on
three conditions — success (agent signals complete + checks green + tree clean),
stall (no progress for N iterations), or the hard iteration cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"


@dataclass
class CheckResult:
    ok: bool
    output: str = ""


@dataclass
class Outcome:
    done: bool
    iterations: int
    reason: str


class Workspace(Protocol):
    def run_agent(self) -> str:
        """Run one fresh-context agent iteration; return its stdout."""

    def run_checks(self) -> CheckResult:
        """Run build/test/lint backpressure."""

    def commit(self, message: str) -> bool:
        """Commit any pending changes; return True iff a commit was created."""

    def worktree_dirty(self) -> bool:
        """True if there are uncommitted changes."""


def run_loop(
    ws: Workspace,
    *,
    max_iters: int = 20,
    stall_limit: int = 3,
    on_iteration: Callable[[int, CheckResult, bool], None] | None = None,
) -> Outcome:
    stalls = 0
    for i in range(1, max_iters + 1):
        text = ws.run_agent()
        checks = ws.run_checks()

        committed = False
        if checks.ok:
            committed = ws.commit(f"brutus: solve iteration {i}")
            # Complete only when the agent says so AND everything is committed clean.
            if COMPLETION_SIGNAL in text and not ws.worktree_dirty():
                _notify(on_iteration, i, checks, True)
                return Outcome(True, i, "completed with green checks")

        # Progress = the agent either committed or left new edits to build on.
        progressed = committed or ws.worktree_dirty()
        stalls = 0 if progressed else stalls + 1
        _notify(on_iteration, i, checks, progressed)

        if stalls >= stall_limit:
            return Outcome(False, i, f"stalled: no progress for {stall_limit} iterations")

    return Outcome(False, max_iters, "hit iteration cap")


def _notify(cb, i: int, checks: CheckResult, progressed: bool) -> None:
    if cb is not None:
        cb(i, checks, progressed)
