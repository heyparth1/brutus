"""Docker + git implementation of the Ralph `Workspace`.

Code execution (the agent and the repo's checks) runs inside a container that
mounts the cloned repo — that's the part we must isolate, since we're running an
autonomous agent against an arbitrary repo's build/test scripts. Git runs on the
host against the same checkout.

All shell-out goes through an injected `run` callable, so the orchestration is
unit-testable without Docker. The default runner uses subprocess.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..models import Candidate
from .detect import detect_commands
from .ralph import CheckResult
from .setup import SETUP_PROMPT, gather_context, parse_commands
from .state import STATE_DIR, init_state, state_dir

COMMANDS_FILE = "commands.json"


@dataclass
class RunResult:
    returncode: int
    stdout: str = ""


Runner = Callable[[list[str]], RunResult]


def _subprocess_runner(args: list[str]) -> RunResult:
    proc = subprocess.run(args, capture_output=True, text=True)
    return RunResult(proc.returncode, proc.stdout + proc.stderr)


class Sandbox:
    def __init__(
        self,
        *,
        issue: Candidate,
        workdir: Path,
        branch: str,
        image: str,
        agent_cmd: str,
        env_names: tuple[str, ...] = (),
        mode: str = "docker",
        stream: bool = False,
        run: Runner = _subprocess_runner,
    ) -> None:
        self.issue = issue
        self.workdir = workdir
        self.branch = branch
        self.image = image
        self.agent_cmd = agent_cmd
        self.env_names = tuple(env_names)
        self.mode = mode  # "docker" (isolated) | "local" (runs on host)
        self.stream = stream  # tee agent/checks output to stdout (for the UI terminal)
        self.run = run
        self.commands: dict[str, str] = {}

    # --- setup -------------------------------------------------------------

    def prepare(self) -> None:
        """Clone the repo (shallow), branch, detect commands, write state files.

        The clone's HEAD SHA is recorded as the diff base, and .brutus/ is excluded
        from git so our state files never end up in the PR.
        """
        self._exec(["git", "clone", "--depth", "1",
                    f"https://github.com/{self.issue.repo}.git", str(self.workdir)])
        base = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("checkout", "-b", self.branch)

        files = {p.name for p in self.workdir.iterdir()} if self.workdir.exists() else set()
        self.commands = detect_commands(files)
        init_state(self.workdir, issue=self.issue, commands=self.commands)
        (state_dir(self.workdir) / "base").write_text(base)
        self._exclude(f"{STATE_DIR}/")
        self._exclude(".claude/")  # where we install the brutus-solve skill
        # Some repos use .agents/skills/ (e.g. next.js) and Claude Code registers our
        # skill there too — exclude it by NAME so it never lands in a PR, anywhere.
        self._exclude("brutus-solve/")

    def setup(self, complete: Callable[[str], str]) -> dict[str, str]:
        """Cheap-model setup pass: plan the repo's commands, create a per-repo venv
        for Python projects, run the install, and adopt build/test/lint as
        backpressure. Returns the planned commands."""
        context = gather_context(self.workdir)
        commands = parse_commands(complete(SETUP_PROMPT.format(context=context)))

        # Isolate Python deps in a venv under .brutus/ (excluded from git, so it never
        # reaches the PR). Once it exists, _wrap puts its bin dir on PATH for every
        # later command — install, checks, and the agent all use it.
        if self._is_python_repo():
            self._exec(self._wrap(f"python3 -m venv {STATE_DIR}/venv"))

        checks = {k: v for k, v in commands.items() if k in ("build", "test", "lint")}
        (state_dir(self.workdir) / COMMANDS_FILE).write_text(json.dumps(checks, indent=2))
        if checks:
            self.commands = checks  # override heuristic detection with the planned commands

        install = commands.get("install")
        if install:
            self._exec(self._wrap(install))
        return commands

    def _is_python_repo(self) -> bool:
        markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        return any((self.workdir / m).exists() for m in markers)

    # --- Workspace protocol ------------------------------------------------

    def run_agent(self) -> str:
        # Pass the standing prompt as the agent's message arg (works for both
        # `opencode run` and `claude -p`); the agent reads the rest from disk.
        prompt = (state_dir(self.workdir) / "PROMPT.md").read_text()
        return self._exec(self._wrap(f"{self.agent_cmd} {shlex.quote(prompt)}")).stdout

    def run_checks(self) -> CheckResult:
        # ponytail: no commands detected => no backpressure; the human review gate
        # is the safety net. The setup pass fills these in for real.
        if not self.commands:
            return CheckResult(ok=True, output="(no checks detected)")
        ok = True
        chunks = []
        for name, cmd in self.commands.items():
            res = self._exec(self._wrap(cmd))
            ok = ok and res.returncode == 0
            chunks.append(f"$ [{name}] {cmd}\n{res.stdout}")
        return CheckResult(ok=ok, output="\n".join(chunks))

    def commit(self, message: str) -> bool:
        self._git("add", "-A")
        return self._git("commit", "-m", message).returncode == 0  # nonzero == nothing to commit

    def worktree_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain").stdout.strip())

    # --- helpers -----------------------------------------------------------

    def _git(self, *args: str) -> RunResult:
        return self.run(["git", "-C", str(self.workdir), *args])

    def _exec(self, argv: list[str]) -> RunResult:
        """Run a command. In stream mode, tee its output to stdout live (so the UI
        terminal sees it) while still capturing it; otherwise use the plain runner."""
        if not self.stream:
            return self.run(argv)
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        captured = []
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            captured.append(line)
        proc.wait()
        return RunResult(proc.returncode, "".join(captured))

    def _wrap(self, shell_cmd: str) -> list[str]:
        """Wrap a shell command for the chosen execution mode. In local mode, if the
        per-repo venv exists, put it on PATH so pip/python/pytest resolve into it."""
        if self.mode == "local":
            cd = f"cd {shlex.quote(str(self.workdir))}"
            venv_bin = state_dir(self.workdir) / "venv" / "bin"
            activate = f" && export PATH={shlex.quote(str(venv_bin))}:$PATH" if venv_bin.exists() else ""
            return ["sh", "-lc", f"{cd}{activate} && {shell_cmd}"]
        return self._docker(shell_cmd)

    def _exclude(self, pattern: str) -> None:
        info = self.workdir / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        with (info / "exclude").open("a") as fh:
            fh.write(pattern + "\n")

    def _docker(self, shell_cmd: str) -> list[str]:
        env_flags: list[str] = []
        for name in self.env_names:
            if os.environ.get(name):  # pass through by name; value never hits argv
                env_flags += ["-e", name]
        return [
            "docker", "run", "--rm",
            *env_flags,
            "-v", f"{self.workdir}:/repo",
            "-w", "/repo",
            self.image,
            "sh", "-lc", shell_cmd,
        ]
