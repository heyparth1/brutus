"""Phase 5: diff/draft/parse and the publish flow over a fake runner (no network)."""

import pytest

from brutus.models import Candidate
from brutus.pr.publish import (
    comment_pr,
    edit_pr_body,
    fetch_issue,
    fetch_referenced_issues,
    PublishError,
    compute_diff,
    fetch_feedback,
    gather_guidelines,
    generate_pr_text,
    publish,
    push_update,
    read_pr,
    safety_check,
    write_pr,
)
from brutus.solve.sandbox import RunResult
from brutus.solve.state import STATE_DIR


def _issue(**kw) -> Candidate:
    base = dict(source="github", repo="o/r", number=7, title="Fix the bug",
                url="https://x/7", raw={"body": "repro steps"})
    base.update(kw)
    return Candidate(**base)


def _workdir(tmp_path):
    d = tmp_path / STATE_DIR
    d.mkdir()
    (d / "base").write_text("abc123\n")
    return tmp_path


class FakeRunner:
    def __init__(self, rules=None):
        self.rules = rules or []
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        joined = " ".join(args)
        for needle, result in self.rules:
            if needle in joined:
                return result
        return RunResult(0, "")


def test_compute_diff_uses_recorded_base(tmp_path):
    wd = _workdir(tmp_path)
    runner = FakeRunner([("diff", RunResult(0, "diff body"))])
    assert compute_diff(wd, run=runner) == "diff body"
    assert ["git", "-C", str(wd), "diff", "abc123..HEAD"] in runner.calls


def test_write_then_read_pr_roundtrips(tmp_path):
    wd = _workdir(tmp_path)
    write_pr(wd, "# Add retry logic\n\nThis adds retries.\nCloses #7")
    title, body = read_pr(wd)
    assert title == "Add retry logic"
    assert body.startswith("This adds retries.")
    assert "Closes #7" in body


def test_generate_pr_text_passes_issue_and_diff():
    captured = {}

    def fake_complete(prompt):
        captured["prompt"] = prompt
        return "# Title\n\nbody Closes #7"

    text = generate_pr_text(_issue(), "the diff", fake_complete)
    assert text.startswith("# Title")
    assert "Fix the bug" in captured["prompt"]
    assert "the diff" in captured["prompt"]


def test_publish_claims_pushes_and_creates_pr(tmp_path):
    wd = _workdir(tmp_path)
    write_pr(wd, "# T\n\nbody")
    runner = FakeRunner([("pr create", RunResult(0, "https://github.com/o/r/pull/9"))])

    url = publish(wd, _issue(), "brutus/issue-7", run=runner)
    assert url == "https://github.com/o/r/pull/9"

    joined = [" ".join(c) for c in runner.calls]
    assert any("issue comment 7" in c for c in joined)
    assert any("push -u origin brutus/issue-7" in c for c in joined)
    assert any("pr create" in c for c in joined)


def test_publish_forks_when_not_owner(tmp_path):
    wd = _workdir(tmp_path)
    write_pr(wd, "# T\n\nbody")
    runner = FakeRunner([
        ("gh api user", RunResult(0, "alice")),  # login != repo owner "o"
        ("pr create", RunResult(0, "https://github.com/o/r/pull/9")),
    ])
    url = publish(wd, _issue(), "brutus/issue-7", run=runner)
    assert url == "https://github.com/o/r/pull/9"

    joined = [" ".join(c) for c in runner.calls]
    assert any("repo fork o/r" in c for c in joined)
    assert any("push https://github.com/alice/r.git brutus/issue-7:brutus/issue-7" in c for c in joined)
    assert any("--head alice:brutus/issue-7" in c for c in joined)


def test_publish_records_pr_url(tmp_path):
    wd = _workdir(tmp_path)
    write_pr(wd, "# T\n\nbody")
    runner = FakeRunner([("pr create", RunResult(0, "https://github.com/o/r/pull/9"))])
    publish(wd, _issue(), "brutus/issue-7", run=runner)
    assert (wd / STATE_DIR / "pr_url").read_text().strip() == "https://github.com/o/r/pull/9"


def test_fetch_feedback_reads_pr_comments(tmp_path):
    wd = _workdir(tmp_path)
    (wd / STATE_DIR / "pr_url").write_text("https://github.com/o/r/pull/9")
    runner = FakeRunner([("pr view 9", RunResult(0, "Please rename the variable."))])
    text = fetch_feedback(wd, _issue(), run=runner)
    assert "rename the variable" in text
    assert "rename the variable" in (wd / STATE_DIR / "FEEDBACK.md").read_text()


def test_fetch_issue_returns_thread():
    runner = FakeRunner([("issue view 7", RunResult(0, "Body...\n\n@alice: please also handle X"))])
    text = fetch_issue(_issue(), run=runner)
    assert "handle X" in text
    joined = " ".join(runner.calls[0])
    assert "issue view 7 --repo o/r --comments" in joined


def test_fetch_referenced_issues_dedups_and_excludes_self():
    runner = FakeRunner([("issue view", RunResult(0, "referenced body"))])
    text = "Parent #338\nBlocked by #341\nsee #341 again and self #7 and other/repo#5"
    out = fetch_referenced_issues(_issue(), text, run=runner)  # _issue() is o/r#7

    viewed = [" ".join(c) for c in runner.calls]
    assert any("issue view 338 --repo o/r" in c for c in viewed)
    assert any("issue view 341 --repo o/r" in c for c in viewed)
    assert any("issue view 5 --repo other/repo" in c for c in viewed)  # cross-repo
    assert not any("issue view 7" in c for c in viewed)  # self excluded
    assert sum("issue view 341" in c for c in viewed) == 1  # deduped
    assert "referenced body" in out


def test_fetch_feedback_without_pr_url_raises(tmp_path):
    with pytest.raises(PublishError, match="no PR recorded"):
        fetch_feedback(_workdir(tmp_path), _issue(), run=FakeRunner([]))


def test_edit_pr_body_and_comment_pr(tmp_path):
    wd = _workdir(tmp_path)
    (wd / STATE_DIR / "pr_url").write_text("https://github.com/o/r/pull/9")
    write_pr(wd, "# T\n\nnew body")

    runner = FakeRunner([])
    assert edit_pr_body(wd, _issue(), run=runner) is True
    assert comment_pr(wd, _issue(), "thanks, addressed it", run=runner) is True

    joined = [" ".join(c) for c in runner.calls]
    assert any("pr edit 9 --repo o/r --body-file" in c for c in joined)
    assert any("pr comment 9 --repo o/r --body thanks, addressed it" in c for c in joined)


def test_comment_pr_skips_when_no_pr(tmp_path):
    assert comment_pr(_workdir(tmp_path), _issue(), "hi", run=FakeRunner([])) is False


def test_push_update_pushes_to_fork(tmp_path):
    wd = _workdir(tmp_path)
    runner = FakeRunner([("gh api user", RunResult(0, "alice"))])
    head = push_update(wd, _issue(), "brutus/issue-7", run=runner)
    assert head == "alice:brutus/issue-7"
    joined = [" ".join(c) for c in runner.calls]
    assert any("push https://github.com/alice/r.git" in c for c in joined)


def test_safety_check_blocks_on_explicit_ban():
    verdict = safety_check(
        _issue(), "some diff", "pr body", "No AI-generated contributions allowed.",
        lambda p: '{"block": true, "concerns": ["repo forbids AI PRs"], "notes": "see CONTRIBUTING"}',
    )
    assert verdict["block"] is True
    assert verdict["concerns"] == ["repo forbids AI PRs"]


def test_safety_check_fails_open_on_bad_output():
    verdict = safety_check(_issue(), "d", "p", "g", lambda p: "sorry no json here")
    assert verdict["block"] is False  # never silently blocks a legit PR on a parse error


def test_gather_guidelines_reads_contributing(tmp_path):
    (tmp_path / "CONTRIBUTING.md").write_text("Please do not submit AI-generated PRs.")
    assert "AI-generated" in gather_guidelines(tmp_path)


def test_publish_skips_claim_when_disabled(tmp_path):
    wd = _workdir(tmp_path)
    write_pr(wd, "# T\n\nbody")
    runner = FakeRunner([("pr create", RunResult(0, "url"))])
    publish(wd, _issue(), "brutus/issue-7", run=runner, claim=False)
    assert not any("issue comment" in " ".join(c) for c in runner.calls)


def test_publish_raises_on_push_failure(tmp_path):
    wd = _workdir(tmp_path)
    write_pr(wd, "# T\n\nbody")
    runner = FakeRunner([("push", RunResult(1, "permission denied"))])
    with pytest.raises(PublishError, match="push failed"):
        publish(wd, _issue(), "brutus/issue-7", run=runner)
