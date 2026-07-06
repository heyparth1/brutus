"""Solve node: a Ralph loop drives a coding-agent CLI inside a Docker sandbox
until the repo's own checks pass and the agent signals completion.

The loop (`ralph.run_loop`) is pure logic over a `Workspace`; `Sandbox` is the
Docker/git implementation. Fresh agent context every iteration — state lives in
files on disk, not in a conversation."""

from .ralph import CheckResult, Outcome, Workspace, run_loop
from .sandbox import Sandbox

__all__ = ["CheckResult", "Outcome", "Workspace", "run_loop", "Sandbox"]
