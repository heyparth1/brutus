"""Phase 4 core: the Ralph loop stops on completion, stall, and cap, and treats
failing checks as work-in-progress rather than a stall."""

from brutus.solve.ralph import COMPLETION_SIGNAL, CheckResult, run_loop


class FakeWorkspace:
    """Each step: {text, ok, commit, dirty} describing one iteration's behavior."""

    def __init__(self, steps):
        self.steps = steps
        self.i = -1
        self.commits = []

    def _step(self):
        return self.steps[min(self.i, len(self.steps) - 1)]

    def run_agent(self):
        self.i += 1
        return self._step()["text"]

    def run_checks(self):
        return CheckResult(self._step()["ok"])

    def commit(self, message):
        made = self._step().get("commit", False)
        if made:
            self.commits.append(message)
        return made

    def worktree_dirty(self):
        return self._step().get("dirty", False)


def test_completes_when_signaled_and_green():
    ws = FakeWorkspace([
        {"text": "working", "ok": True, "commit": True, "dirty": False},
        {"text": f"done {COMPLETION_SIGNAL}", "ok": True, "commit": True, "dirty": False},
    ])
    outcome = run_loop(ws, max_iters=10)
    assert outcome.done is True
    assert outcome.iterations == 2
    assert len(ws.commits) == 2


def test_failing_checks_are_not_a_stall():
    # Agent keeps editing (dirty) while checks fail, then fixes and completes.
    ws = FakeWorkspace([
        {"text": "fixing", "ok": False, "dirty": True},
        {"text": "fixing", "ok": False, "dirty": True},
        {"text": f"{COMPLETION_SIGNAL}", "ok": True, "commit": True, "dirty": False},
    ])
    outcome = run_loop(ws, max_iters=10, stall_limit=2)
    assert outcome.done is True
    assert outcome.iterations == 3


def test_stalls_when_no_progress():
    ws = FakeWorkspace([{"text": "idle", "ok": True, "commit": False, "dirty": False}])
    outcome = run_loop(ws, max_iters=10, stall_limit=3)
    assert outcome.done is False
    assert outcome.iterations == 3
    assert "stalled" in outcome.reason


def test_hits_iteration_cap():
    # Always making edits but never passing checks -> never completes, never stalls.
    ws = FakeWorkspace([{"text": "churn", "ok": False, "dirty": True}])
    outcome = run_loop(ws, max_iters=4, stall_limit=3)
    assert outcome.done is False
    assert outcome.iterations == 4
    assert "cap" in outcome.reason


def test_signal_without_clean_tree_does_not_complete():
    # Claims done but left uncommitted changes -> not accepted; loop continues to cap.
    ws = FakeWorkspace([{"text": COMPLETION_SIGNAL, "ok": True, "commit": False, "dirty": True}])
    outcome = run_loop(ws, max_iters=3, stall_limit=5)
    assert outcome.done is False
