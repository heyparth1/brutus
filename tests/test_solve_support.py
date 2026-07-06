"""Phase 4 support: command detection, state-file rendering, and sandbox
orchestration over a fake runner (no Docker, no network)."""

from pathlib import Path

from brutus.models import Candidate
from brutus.solve.detect import detect_commands
from brutus.solve.sandbox import RunResult, Sandbox
from brutus.solve.setup import gather_context, parse_commands
from brutus.solve.skill import SKILL_NAME, install_skill
from brutus.solve.state import append_progress, init_state, state_dir


def _issue(**kw) -> Candidate:
    base = dict(source="github", repo="o/r", number=7, title="Fix the bug",
                url="https://x/7", raw={"body": "Steps to reproduce: ..."})
    base.update(kw)
    return Candidate(**base)


# --- detect ----------------------------------------------------------------

def test_detect_python():
    cmds = detect_commands({"pyproject.toml", "README.md"})
    assert "pytest" in cmds["test"] and cmds["lint"] == "ruff check ."


def test_detect_node_overrides_test():
    cmds = detect_commands({"package.json"})
    assert cmds["test"] == "npm test" and "build" in cmds


def test_detect_none():
    assert detect_commands({"README.md"}) == {}


# --- state files -----------------------------------------------------------

def test_init_state_writes_all_files(tmp_path: Path):
    init_state(tmp_path, issue=_issue(), commands={"test": "pytest -q"})
    sd = state_dir(tmp_path)
    for name in ("PROMPT.md", "ISSUE.md", "fix_plan.md", "progress.txt", "AGENTS.md"):
        assert (sd / name).exists()
    assert "Fix the bug" in (sd / "ISSUE.md").read_text()
    assert "pytest -q" in (sd / "AGENTS.md").read_text()


def test_append_progress(tmp_path: Path):
    init_state(tmp_path, issue=_issue(), commands={})
    append_progress(tmp_path, "tried X, it worked")
    assert "tried X" in (state_dir(tmp_path) / "progress.txt").read_text()


# --- sandbox orchestration -------------------------------------------------

class FakeRunner:
    """Returns results keyed by a substring of the command; records all calls."""

    def __init__(self, rules):
        self.rules = rules  # list[(needle, RunResult)]
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        joined = " ".join(args)
        for needle, result in self.rules:
            if needle in joined:
                return result
        return RunResult(0, "")


def _sandbox(run):
    return Sandbox(issue=_issue(), workdir=Path("/tmp/wd"), branch="brutus/issue-7",
                   image="img", agent_cmd="agent", run=run)


def test_run_checks_passes_only_when_all_commands_succeed():
    sb = _sandbox(FakeRunner([("pytest", RunResult(0, "ok")), ("ruff", RunResult(1, "lint err"))]))
    sb.commands = {"test": "pytest -q", "lint": "ruff check ."}
    result = sb.run_checks()
    assert result.ok is False
    assert "lint err" in result.output


def test_run_checks_ok_with_no_commands():
    sb = _sandbox(FakeRunner([]))
    sb.commands = {}
    assert sb.run_checks().ok is True


def test_commit_reports_false_when_nothing_to_commit():
    # `git commit` exits nonzero when there's nothing staged.
    sb = _sandbox(FakeRunner([("commit", RunResult(1, "nothing to commit"))]))
    assert sb.commit("msg") is False


def test_worktree_dirty_reflects_status_output():
    dirty = _sandbox(FakeRunner([("status", RunResult(0, " M file.py\n"))]))
    clean = _sandbox(FakeRunner([("status", RunResult(0, ""))]))
    assert dirty.worktree_dirty() is True
    assert clean.worktree_dirty() is False


def test_parse_commands_keeps_only_nonempty_strings():
    cmds = parse_commands('```json\n{"install":"pip install -e .","test":"pytest","lint":""}\n```')
    assert cmds == {"install": "pip install -e .", "test": "pytest"}


def test_parse_commands_handles_garbage():
    assert parse_commands("sorry, no idea") == {}


def test_gather_context_includes_manifests(tmp_path):
    (tmp_path / "README.md").write_text("Run with pytest")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
    ctx = gather_context(tmp_path)
    assert "README.md" in ctx and "Run with pytest" in ctx
    assert "pyproject.toml" in ctx


def test_setup_plans_commands_runs_install_and_overrides_checks(tmp_path):
    init_state(tmp_path, issue=_issue(), commands={"test": "detected"})
    runner = FakeRunner([("install", RunResult(0, "installed"))])
    sb = Sandbox(issue=_issue(), workdir=tmp_path, branch="b", image="",
                 agent_cmd="agent", mode="local", run=runner)
    sb.commands = {"test": "detected"}

    def fake_complete(prompt):
        assert "Repo files" in prompt
        return '{"install": "pip install -e .", "test": "pytest -q"}'

    commands = sb.setup(fake_complete)
    assert commands["test"] == "pytest -q"
    assert sb.commands == {"test": "pytest -q"}  # planned commands override detection
    assert (state_dir(tmp_path) / "commands.json").exists()
    assert any("pip install -e ." in " ".join(c) for c in runner.calls)  # install ran


def test_setup_creates_venv_for_python_repo(tmp_path):
    init_state(tmp_path, issue=_issue(), commands={})
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
    runner = FakeRunner([])
    sb = Sandbox(issue=_issue(), workdir=tmp_path, branch="b", image="",
                 agent_cmd="agent", mode="local", run=runner)

    sb.setup(lambda p: '{"install": "pip install -e .", "test": "pytest -q"}')
    joined = [" ".join(c) for c in runner.calls]
    assert any(f"python3 -m venv {state_dir(tmp_path).name}/venv" in c for c in joined)


def test_wrap_activates_venv_when_present(tmp_path):
    sb = Sandbox(issue=_issue(), workdir=tmp_path, branch="b", image="",
                 agent_cmd="agent", mode="local", run=FakeRunner([]))
    assert "export PATH" not in " ".join(sb._wrap("pytest"))  # no venv yet

    (state_dir(tmp_path) / "venv" / "bin").mkdir(parents=True)
    wrapped = " ".join(sb._wrap("pytest"))
    assert "export PATH" in wrapped and "venv/bin" in wrapped


def test_install_skill_writes_skill_file(tmp_path):
    path = install_skill(tmp_path)
    assert path == tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
    text = path.read_text()
    assert "name: brutus-solve" in text
    assert "KEEP the regression test" in text  # tests ship in the PR (real-PR norm)


def test_local_mode_wraps_with_cd(tmp_path):
    init_state(tmp_path, issue=_issue(), commands={})
    runner = FakeRunner([("agent", RunResult(0, "agent output"))])
    sb = Sandbox(issue=_issue(), workdir=tmp_path, branch="b", image="",
                 agent_cmd="opencode run", mode="local", run=runner)
    sb.commands = {"test": "pytest -q"}

    sb.run_checks()
    sb.run_agent()
    joined = [" ".join(c) for c in runner.calls]
    assert any(f"cd {tmp_path}" in c and "pytest -q" in c for c in joined)  # check ran on host
    assert any("opencode run" in c for c in joined)                        # agent invoked
    assert not any("docker" in c for c in joined)                          # never used docker


def test_docker_command_passes_env_by_name(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    sb = Sandbox(issue=_issue(), workdir=Path("/tmp/wd"), branch="b", image="img",
                 agent_cmd="claude -p", env_names=("ANTHROPIC_API_KEY",), run=FakeRunner([]))
    argv = sb._docker("echo hi")
    assert "-e" in argv and "ANTHROPIC_API_KEY" in argv
    assert "secret" not in argv  # value passed by name, never on argv
