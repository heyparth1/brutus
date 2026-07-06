"""Diff, draft, and publish. The draft lives in .brutus/PR.md so you can edit it
before publishing; `publish` reads it back. All git/gh shell-out goes through an
injected runner, so the flow is testable without a network.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..models import Candidate
from ..solve.sandbox import RunResult, _subprocess_runner
from ..solve.state import state_dir


def _gh_runner(args: list[str]) -> RunResult:
    """Run gh/git with GITHUB_TOKEN/GH_TOKEN stripped, so `gh` uses your
    `gh auth login` (OAuth) credentials instead of brutus's fetch-scoped PAT —
    that PAT can't create pull requests."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    proc = subprocess.run(args, capture_output=True, text=True, env=env)
    return RunResult(proc.returncode, proc.stdout + proc.stderr)

Runner = Callable[[list[str]], RunResult]

PR_FILE = "PR.md"
BASE_FILE = "base"
DIFF_BUDGET = 6000  # chars of diff sent to the model
BODY_BUDGET = 1500  # chars of issue body sent to the model

CLAIM_BODY = "I'd like to work on this — opening a PR shortly."

_PR_PROMPT = """\
Write a GitHub pull request for the issue below, based on the diff.

Output format: the FIRST line is the PR title prefixed with "# ", then a blank line,
then the description in markdown. End the description with a line "Closes #{number}".
Do NOT include the diff itself. Keep it concise and factual.

## Issue: {title} ({repo}#{number})
{body}

## Diff
{diff}
"""


class PublishError(RuntimeError):
    pass


def compute_diff(workdir: Path, *, run: Runner = _subprocess_runner) -> str:
    """Diff the work branch against the clone's recorded base SHA. Returns "" if the
    base wasn't recorded (e.g. the workdir was never prepared)."""
    base_file = state_dir(workdir) / BASE_FILE
    if not base_file.exists():
        return ""
    base = base_file.read_text().strip()
    return run(["git", "-C", str(workdir), "diff", f"{base}..HEAD"]).stdout


def generate_pr_text(issue: Candidate, diff: str, complete: Callable[[str], str]) -> str:
    prompt = _PR_PROMPT.format(
        title=issue.title,
        repo=issue.repo,
        number=issue.number,
        body=(issue.raw.get("body") or "")[:BODY_BUDGET],
        diff=diff[:DIFF_BUDGET],
    )
    return complete(prompt).strip()


def write_pr(workdir: Path, text: str) -> Path:
    path = state_dir(workdir) / PR_FILE
    path.write_text(text.rstrip() + "\n")
    return path


def _push_fork(run, workdir, branch, fork_url, sleep, attempts: int = 5):
    """Push to the fork, retrying on 'not found' — `gh repo fork` is async and the
    fork may not be provisioned the instant we push."""
    res = run(["git", "-C", str(workdir), "push", fork_url, f"{branch}:{branch}"])
    for i in range(1, attempts):
        if res.returncode == 0 or "not found" not in (res.stdout or "").lower():
            return res
        sleep(min(3 * i, 10))  # wait for GitHub to finish creating the fork
        res = run(["git", "-C", str(workdir), "push", fork_url, f"{branch}:{branch}"])
    return res


def read_pr(workdir: Path) -> tuple[str, str]:
    """Return (title, body) from .brutus/PR.md. First line is the '# '-prefixed title."""
    path = state_dir(workdir) / PR_FILE
    if not path.exists():
        raise PublishError(f"no PR draft at {path} — run `brutus review` first (let it finish)")
    text = path.read_text().strip()
    lines = text.splitlines()
    if not lines:
        raise PublishError("PR.md is empty — run `brutus review` first")
    title = lines[0].lstrip("#").strip()
    body = "\n".join(lines[1:]).strip()
    return title, body


def publish(
    workdir: Path,
    issue: Candidate,
    branch: str,
    *,
    run: Runner = _gh_runner,
    claim: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Claim the issue, push the branch, and open the PR. Returns the PR URL.

    If you don't own the repo, this forks it (idempotent), pushes the branch to
    your fork, and opens the PR as `<you>:<branch>`. If you own it (or the login
    can't be determined), it pushes to `origin`.
    """
    title, body = read_pr(workdir)
    head = _push_branch(workdir, issue, branch, run=run, sleep=sleep)

    if claim:
        run(["gh", "issue", "comment", str(issue.number),
             "--repo", issue.repo, "--body", CLAIM_BODY])

    created = run(["gh", "pr", "create", "--repo", issue.repo,
                   "--head", head, "--title", title, "--body", body])
    if created.returncode != 0:
        raise PublishError(f"gh pr create failed: {created.stdout.strip()[:500]}")
    url = created.stdout.strip()
    (state_dir(workdir) / "pr_url").write_text(url)  # so `revise` can find the PR later
    return url


def _push_branch(workdir: Path, issue: Candidate, branch: str, *, run: Runner,
                 sleep: Callable[[float], None]) -> str:
    """Push `branch` to your fork (if you don't own the repo) or origin. Returns the
    PR `head` ref (`<login>:<branch>` for a fork, else `<branch>`)."""
    owner, name = issue.repo.split("/", 1)
    login = run(["gh", "api", "user", "-q", ".login"]).stdout.strip()
    if login and login != owner:
        run(["gh", "repo", "fork", issue.repo, "--clone=false"])  # idempotent
        fork_url = f"https://github.com/{login}/{name}.git"
        push = _push_fork(run, workdir, branch, fork_url, sleep)
        head = f"{login}:{branch}"
    else:
        push = run(["git", "-C", str(workdir), "push", "-u", "origin", branch])
        head = branch
    if push.returncode != 0:
        raise PublishError(f"git push failed: {push.stdout.strip()[:500]}")
    return head


def fetch_issue(issue: Candidate, *, run: Runner = _gh_runner) -> str:
    """The full issue thread — body + all comments + activity — via gh. '' on failure."""
    res = run(["gh", "issue", "view", str(issue.number),
               "--repo", issue.repo, "--comments"])
    return res.stdout if res.returncode == 0 else ""


# `#123` (same repo) or `owner/name#123` (cross-repo).
_REF_RE = re.compile(r"(?:([\w.-]+/[\w.-]+))?#(\d+)")


def fetch_referenced_issues(issue: Candidate, text: str, *,
                            run: Runner = _gh_runner, limit: int = 5) -> str:
    """Pull the title+body (no comments) of issues referenced in `text` — parent,
    blocked-by, mentioned (#338, owner/name#5). Deduped, self excluded, capped."""
    keys: list[str] = []
    for repo_ref, num in _REF_RE.findall(text):
        repo = repo_ref or issue.repo
        if repo == issue.repo and num == str(issue.number):
            continue  # don't re-fetch this issue
        key = f"{repo}#{num}"
        if key not in keys:
            keys.append(key)

    chunks = []
    for key in keys[:limit]:
        repo, num = key.split("#")
        res = run(["gh", "issue", "view", num, "--repo", repo])  # no --comments (just body)
        if res.returncode == 0 and res.stdout.strip():
            chunks.append(f"### {key}\n{res.stdout.strip()[:1500]}")
    return "\n\n".join(chunks)


def fetch_feedback(workdir: Path, issue: Candidate, *, run: Runner = _gh_runner) -> str:
    """Pull the PR's comments + reviews into .brutus/FEEDBACK.md and return the text."""
    url_file = state_dir(workdir) / "pr_url"
    if not url_file.exists():
        raise PublishError("no PR recorded for this issue — was it opened with `brutus pr`?")
    number = url_file.read_text().strip().rstrip("/").rsplit("/", 1)[-1]
    res = run(["gh", "pr", "view", number, "--repo", issue.repo, "--comments"])
    if res.returncode != 0:
        raise PublishError(f"could not fetch PR feedback: {res.stdout.strip()[:300]}")
    (state_dir(workdir) / "FEEDBACK.md").write_text(res.stdout)
    return res.stdout


def push_update(workdir: Path, issue: Candidate, branch: str, *,
                run: Runner = _gh_runner, sleep: Callable[[float], None] = time.sleep) -> str:
    """Push new commits to an existing PR's branch (no new PR, no claim)."""
    return _push_branch(workdir, issue, branch, run=run, sleep=sleep)


def _pr_number(workdir: Path) -> str | None:
    f = state_dir(workdir) / "pr_url"
    if not f.exists():
        return None
    return f.read_text().strip().rstrip("/").rsplit("/", 1)[-1] or None


def edit_pr_body(workdir: Path, issue: Candidate, *, run: Runner = _gh_runner) -> bool:
    """Replace the open PR's description with .brutus/PR.md. Returns True on success."""
    number = _pr_number(workdir)
    body = state_dir(workdir) / PR_FILE
    if not number or not body.exists():
        return False
    res = run(["gh", "pr", "edit", number, "--repo", issue.repo, "--body-file", str(body)])
    return res.returncode == 0


def comment_pr(workdir: Path, issue: Candidate, text: str, *, run: Runner = _gh_runner) -> bool:
    """Post a comment on the open PR (e.g. a reply to the reviewer)."""
    number = _pr_number(workdir)
    if not number or not text.strip():
        return False
    res = run(["gh", "pr", "comment", number, "--repo", issue.repo, "--body", text])
    return res.returncode == 0


# --- Gemini safety gate ----------------------------------------------------

_GUIDELINE_FILES = ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "CONTRIBUTING.rst",
                    "CODE_OF_CONDUCT.md", "AI_POLICY.md", ".github/PULL_REQUEST_TEMPLATE.md")

SAFETY_PROMPT = """\
You are a contribution-safety reviewer for an automated GitHub PR. Using the repo's
guidelines and the proposed change, flag anything that could get the PR rejected or
the contributor banned. Consider:
- Does the project forbid AI-generated / automated contributions?
- Does it require a signed CLA or DCO sign-off?
- Is the change security-sensitive (auth, crypto, checksums, CI, secrets, deps)?
- Is it low-quality, off-topic, or spammy?

Return ONLY JSON: {{"block": <bool>, "concerns": ["short concern", ...], "notes": "<one line>"}}
Set block=true ONLY for hard blockers (an explicit AI ban, or a clearly inappropriate PR).

## Repo: {repo}  (issue #{number})
## Contribution guidelines (may be empty)
{guidelines}
## Proposed PR
{pr}
## Diff (truncated)
{diff}
"""


def gather_guidelines(workdir: Path) -> str:
    chunks = []
    for rel in _GUIDELINE_FILES:
        f = workdir / rel
        if f.is_file():
            chunks.append(f"--- {rel} ---\n{f.read_text(errors='replace')[:2500]}")
    return "\n".join(chunks)


def safety_check(
    issue: Candidate, diff: str, pr_text: str, guidelines: str,
    complete: Callable[[str], str],
) -> dict:
    """Ask the (cheap) model to review the PR for ban/rejection risks. Returns
    {block: bool, concerns: list[str], notes: str}; fails open (no block) on a
    parse/LLM error so it never silently stops a legitimate PR."""
    prompt = SAFETY_PROMPT.format(
        repo=issue.repo, number=issue.number,
        guidelines=guidelines[:6000] or "(none found)",
        pr=pr_text[:1500], diff=diff[:6000],
    )
    try:
        raw = complete(prompt).strip()
    except Exception as exc:  # noqa: BLE001 — surface, don't block
        return {"block": False, "concerns": [f"safety check failed: {exc}"], "notes": ""}

    import json as _json
    import re as _re
    fence = _re.search(r"```(?:json)?\s*(.*?)```", raw, _re.S)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {"block": False, "concerns": ["safety check returned no JSON"], "notes": raw[:200]}
    try:
        data = _json.loads(raw[start : end + 1])
    except _json.JSONDecodeError:
        return {"block": False, "concerns": ["safety check JSON invalid"], "notes": raw[:200]}
    return {
        "block": bool(data.get("block")),
        "concerns": [str(c) for c in data.get("concerns", []) if str(c).strip()],
        "notes": str(data.get("notes", "")),
    }
