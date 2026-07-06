"""Command-line entrypoint. Phase 0 wires `init-db`; later phases fill the rest.

Run as `brutus <cmd>` (after `pip install -e .`) or `python -m brutus.cli <cmd>`.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import db, llm
from .classify import run_classify
from .config import Config
from .db import InvalidTransition
from .fetch import github_search, goodfirstissue, gsoc, nlsearch
from .fetch.images import download_images
from .models import Status
from .pr import (
    PublishError,
    comment_pr,
    compute_diff,
    edit_pr_body,
    fetch_feedback,
    fetch_issue,
    fetch_referenced_issues,
    gather_guidelines,
    generate_pr_text,
    publish,
    push_update,
    read_pr,
    safety_check,
    write_pr,
)
from .solve import Sandbox, run_loop
from .solve.skill import install_skill
from .solve.state import state_dir

app = typer.Typer(
    help="Brutus — find, solve, and PR open-source issues.",
    no_args_is_help=True,
)
console = Console()


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite schema."""
    cfg = Config.load()
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    console.print(f"[green]Initialized[/] database at {cfg.db_path}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="bind host"),
    port: int = typer.Option(8000, help="bind port"),
) -> None:
    """Run the HTTP API server for the web UI."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]server deps missing[/] — run: pip install -e '.[server]'")
        raise typer.Exit(1)
    console.print(f"[green]Brutus API[/] on http://{host}:{port}")
    uvicorn.run("brutus.server:app", host=host, port=port)


@app.command()
def status() -> None:
    """Show how many candidates sit at each pipeline stage."""
    conn = db.connect(Config.load().db_path)
    db.init_db(conn)
    counts = db.count_by_status(conn)
    if not counts:
        console.print("[yellow]no candidates yet[/] — run `brutus fetch`")
        return
    table = Table(show_header=False, box=None)
    for st in Status:  # enum order == pipeline order
        table.add_row(st.value, str(counts.get(st.value, 0)))
    console.print(table)
    console.print(f"[dim]total {sum(counts.values())}[/]")


@app.command()
def run(
    lang: Optional[str] = typer.Option(None, help="filter by language"),
    label: Optional[str] = typer.Option(None, help="issue label; omit for ALL difficulties"),
    limit: int = typer.Option(100, help="max issues to fetch"),
    min_score: int = typer.Option(4, help="only show candidates at or above this score"),
    skip_classify: bool = typer.Option(False, help="fetch only, don't score"),
) -> None:
    """Fetch, classify, and show the top candidates — the daily browse."""
    cfg = Config.load()
    if not cfg.github_token:
        console.print("[red]GITHUB_TOKEN not set[/]")
        raise typer.Exit(1)
    if not skip_classify and not cfg.llm_cmd:
        console.print("[red]BRUTUS_LLM_CMD not set[/] — set it or pass --skip-classify")
        raise typer.Exit(1)

    conn = db.connect(cfg.db_path)
    db.init_db(conn)

    fetched = github_search.fetch_github(
        conn, token=cfg.github_token, label=label, language=lang, limit=limit
    )
    console.print(f"[green]Fetched[/] {fetched} issues")

    if not skip_classify:
        stats = run_classify(conn, complete=lambda p: llm.complete(p, cmd=cfg.llm_cmd))
        console.print(
            f"[green]Classified[/] — scored {stats['scored']}, dropped {stats['dropped']}"
        )

    top = db.list_candidates(conn, status=Status.SCORED, language=lang,
                             min_score=min_score, limit=20)
    _render_candidates(top)


def _todo(phase: int) -> None:
    console.print(f"[yellow]not implemented[/] — coming in phase {phase}")
    raise typer.Exit(code=1)


@app.command()
def fetch(
    source: str = typer.Option("github", help="github | feeds | gsoc"),
    label: Optional[str] = typer.Option(None, help="issue label; omit for ALL difficulties"),
    lang: Optional[str] = typer.Option(None, help="filter by repo language"),
    limit: int = typer.Option(100, help="max issues per query"),
    query: Optional[str] = typer.Option(None, help="extra raw search qualifiers"),
    org: Optional[list[str]] = typer.Option(None, help="GSoC org/repo (repeatable; source=gsoc)"),
) -> None:
    """Discover candidate issues from GitHub, beginner feeds, or GSoC orgs."""
    cfg = Config.load()
    if not cfg.github_token:
        console.print("[red]GITHUB_TOKEN not set[/] — export a token and retry.")
        raise typer.Exit(1)

    conn = db.connect(cfg.db_path)
    db.init_db(conn)

    if source == "github":
        n = github_search.fetch_github(
            conn, token=cfg.github_token, label=label, language=lang,
            extra=query, limit=limit,
        )
    elif source == "feeds":
        n = goodfirstissue.fetch_feeds(
            conn, token=cfg.github_token, language=lang, limit_per_label=limit,
        )
    elif source == "gsoc":
        if not org:
            console.print("[red]--org is required for source=gsoc[/]")
            raise typer.Exit(1)
        n = gsoc.fetch_gsoc(
            conn, token=cfg.github_token, orgs=org, label=label, limit_per_org=limit,
        )
    else:
        console.print(f"[red]unknown source[/] {source!r} (use github | feeds | gsoc)")
        raise typer.Exit(1)

    console.print(f"[green]Fetched[/] {n} issues from {source}")


@app.command()
def search(
    query: str = typer.Argument(..., help="natural language, e.g. 'python bugs in popular repos this month'"),
    limit: int = typer.Option(30, help="max issues to fetch"),
) -> None:
    """Natural-language issue search — an LLM builds the GitHub query + stars filter."""
    from datetime import datetime, timezone

    cfg = Config.load()
    if not cfg.github_token:
        console.print("[red]GITHUB_TOKEN not set[/]")
        raise typer.Exit(1)
    if not cfg.llm_cmd:
        console.print("[red]BRUTUS_LLM_CMD not set[/] — needed to parse the query")
        raise typer.Exit(1)

    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    today = datetime.now(timezone.utc).date().isoformat()
    stats = nlsearch.fetch_search(
        conn, token=cfg.github_token, nl=query,
        complete=lambda p: llm.complete(p, cmd=cfg.llm_cmd), today=today, limit=limit,
    )
    console.print(f"[dim]query:[/] {stats['query']}  [dim]min_stars:[/] {stats['min_stars']}")
    console.print(f"[green]Fetched[/] {stats['fetched']} new ({stats['kept']} matched). Now: brutus classify")


@app.command()
def classify(
    batch_size: int = typer.Option(20, help="issues per LLM call"),
    limit: Optional[int] = typer.Option(None, help="max candidates to classify"),
) -> None:
    """Score candidates by how tractable an automated PR is."""
    cfg = Config.load()
    if not cfg.llm_cmd:
        console.print(
            "[red]LLM command not set[/] — set BRUTUS_LLM_CMD (e.g. 'llm -m <model>')."
        )
        raise typer.Exit(1)

    conn = db.connect(cfg.db_path)
    db.init_db(conn)

    stats = run_classify(
        conn,
        complete=lambda prompt: llm.complete(prompt, cmd=cfg.llm_cmd),
        batch_size=batch_size,
        limit=limit,
    )
    console.print(
        f"[green]Classified[/] — scored {stats['scored']}, "
        f"dropped {stats['dropped']}, skipped {stats['skipped']}"
    )


@app.command("list")
def list_(
    lang: Optional[str] = typer.Option(None, help="filter by language"),
    min_score: Optional[int] = typer.Option(None, help="hide candidates below this score"),
    label: Optional[str] = typer.Option(None, help="require this label"),
    status: str = typer.Option("scored", help="pipeline status to list"),
    limit: int = typer.Option(30, help="max rows"),
) -> None:
    """List and filter candidates, highest score first."""
    try:
        st = Status(status)
    except ValueError:
        console.print(f"[red]unknown status[/] {status!r}")
        raise typer.Exit(1)

    conn = db.connect(Config.load().db_path)
    db.init_db(conn)
    candidates = db.list_candidates(conn, status=st, language=lang, min_score=min_score)
    if label:
        candidates = [c for c in candidates if label in c.labels]
    candidates = candidates[:limit]

    _render_candidates(candidates)


def _render_candidates(candidates) -> None:
    if not candidates:
        console.print("[yellow]no candidates[/] match those filters")
        return
    table = Table(show_lines=False)
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Repo", style="magenta")
    table.add_column("Issue")
    table.add_column("Title")
    table.add_column("Why", style="dim")
    for c in candidates:
        table.add_row(
            str(c.id),
            "-" if c.score is None else str(c.score),
            c.repo,
            f"#{c.number}",
            _truncate(c.title, 50),
            _truncate(c.score_reason or "", 40),
        )
    console.print(table)


@app.command()
def pick(candidate_id: int = typer.Argument(..., help="candidate ID from `list`")) -> None:
    """Choose one scored issue to solve."""
    conn = db.connect(Config.load().db_path)
    candidate = db.get_candidate(conn, candidate_id)
    if candidate is None:
        console.print(f"[red]no candidate[/] with id {candidate_id}")
        raise typer.Exit(1)
    try:
        db.update_status(conn, candidate_id, Status.PICKED)
    except InvalidTransition:
        console.print(
            f"[red]can't pick[/] — candidate is '{candidate.status.value}', expected 'scored'"
        )
        raise typer.Exit(1)

    console.print(f"[green]Picked[/] {candidate.repo}#{candidate.number}")
    console.print(f"  {candidate.title}")
    console.print(f"  score [bold]{candidate.score}[/] — {candidate.score_reason or ''}")
    console.print(f"  labels: {', '.join(candidate.labels) or '(none)'}")
    console.print(f"  {candidate.url}")


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


@app.command()
def solve(
    candidate_id: int = typer.Argument(..., help="a picked candidate's ID"),
    max_iters: int = typer.Option(20, help="hard cap on Ralph iterations"),
    stall_limit: int = typer.Option(3, help="give up after N iterations with no progress"),
    local: bool = typer.Option(False, "--local", help="run the agent on the host (no Docker)"),
    stream: bool = typer.Option(False, "--stream", help="tee agent/checks output (for the UI)"),
) -> None:
    """Clone + set up a picked issue, then solve it.

    Default solver opens an interactive Claude session in the repo; set
    BRUTUS_SOLVER=ralph for the headless opencode/GLM loop instead.
    """
    cfg = Config.load()
    mode = "local" if local else (cfg.sandbox or "docker")

    if cfg.solver == "ralph":
        needed = [("BRUTUS_AGENT_CLI", cfg.agent_cli)]
        if mode == "docker":
            needed.append(("BRUTUS_AGENT_IMAGE", cfg.agent_image))
        missing = [name for name, val in needed if not val]
        if missing:
            console.print(f"[red]not configured[/] — set {', '.join(missing)}")
            raise typer.Exit(1)

    conn = db.connect(cfg.db_path)
    candidate = db.get_candidate(conn, candidate_id)
    if candidate is None:
        console.print(f"[red]no candidate[/] with id {candidate_id}")
        raise typer.Exit(1)
    if candidate.status is not Status.PICKED:
        console.print(
            f"[red]not picked[/] — candidate is '{candidate.status.value}'. "
            f"Run `brutus pick {candidate_id}` first."
        )
        raise typer.Exit(1)

    if mode == "local":
        console.print(
            "[yellow]local mode[/] — agent and checks run on your host with NO container "
            "isolation, and checks need the repo's toolchain installed locally."
        )

    workdir = _workdir(cfg, candidate_id)
    if workdir.exists():
        console.print(f"[red]workdir exists[/] — remove {workdir} and retry")
        raise typer.Exit(1)

    sandbox = Sandbox(
        issue=candidate,
        workdir=workdir,
        branch=f"brutus/issue-{candidate.number}",
        image=cfg.agent_image,
        agent_cmd=cfg.agent_cli,
        env_names=tuple(n.strip() for n in cfg.agent_env.split(",") if n.strip()),
        mode=mode,
        stream=stream,
    )

    db.update_status(conn, candidate_id, Status.SOLVING)
    console.print(f"[blue]Solving[/] {candidate.repo}#{candidate.number} in {workdir}")
    console.print("[blue]Cloning[/] repo…")
    sandbox.prepare()

    if cfg.setup_cmd:
        console.print("[blue]Setting up repo[/] (setup model)…")
        try:
            commands = sandbox.setup(lambda p: llm.complete(p, cmd=cfg.setup_cmd))
            console.print(f"  commands: {commands or '(none planned)'}")
        except Exception as exc:  # setup is best-effort; never block the solve
            console.print(f"  [yellow]setup failed[/] ({exc}); continuing with detected commands")

    _add_issue_thread(candidate, workdir)  # comments/labels/activity → ISSUE.md

    if cfg.solver == "claude":
        _solve_with_claude(conn, candidate, sandbox, cfg)
        return

    def report(i: int, checks, progressed: bool) -> None:
        mark = "[green]checks ok[/]" if checks.ok else "[red]checks fail[/]"
        console.print(f"  iter {i}: {mark}{'' if progressed else ' (no progress)'}")

    outcome = run_loop(sandbox, max_iters=max_iters, stall_limit=stall_limit, on_iteration=report)

    db.update_status(conn, candidate_id, Status.REVIEW if outcome.done else Status.FAILED)
    color = "green" if outcome.done else "yellow"
    console.print(
        f"[{color}]{outcome.reason}[/] after {outcome.iterations} iterations → "
        f"status '{'review' if outcome.done else 'failed'}'"
    )
    console.print(f"  workdir: {workdir}")


_CLAUDE_PROMPT = (
    "Use the brutus-solve skill to resolve the GitHub issue described in "
    "./.brutus/ISSUE.md. Follow its steps: read the issue, write a plan, execute it "
    "step by step, prove the fix with a regression test (kept in the PR, matching the "
    "repo's test conventions), clean up scratch artifacts, and write ./.brutus/SUMMARY.md. "
    "Do not run git or open a PR."
)


def _solve_with_claude(conn, candidate, sandbox: Sandbox, cfg: Config) -> None:
    """Hand off to an interactive Claude session in the repo, driven by the
    brutus-solve skill. Inherits this process's tty (the macOS Terminal), so you
    watch and guide it. On exit we commit whatever changed, then guide you through
    review → approve → PR in the same terminal."""
    candidate_id = candidate.id
    workdir = sandbox.workdir
    install_skill(workdir)  # .claude/skills/brutus-solve (excluded from git)
    env = _agent_env(workdir)  # venv on PATH; gh/push disabled so it can't open a PR

    console.print(f"[blue]Starting Claude session[/] in {workdir} (brutus-solve skill) — exit when done.")
    subprocess.run([*shlex.split(cfg.claude_cmd), _CLAUDE_PROMPT], cwd=str(workdir), env=env)

    # Commit anything Claude left uncommitted (excludes .brutus/). Claude usually
    # self-commits; the real signal is whether the branch advanced beyond base.
    # Message is the issue title (no automation trace) — a no-op when Claude committed.
    sandbox.commit(candidate.title)
    db.update_status(conn, candidate_id, Status.REVIEW)

    if not compute_diff(workdir).strip():
        console.print(
            f"[yellow]No changes detected[/] → status 'review'. The session may not have "
            f"edited anything; inspect {workdir} or re-run `brutus reset {candidate_id}`."
        )
        return

    console.print("[green]Changes ready[/] → status 'review'.\n")
    if not sys.stdin.isatty():  # non-interactive: leave it for the human to drive
        console.print(f"Next: brutus review {candidate_id} → brutus pr {candidate_id} --approve")
        return

    # Guide the human through review → approve → PR in this same terminal.
    _draft_pr(candidate, workdir, cfg)
    if typer.confirm("\nOpen a pull request for this now?", default=False):
        _safety_and_publish(conn, candidate, workdir, cfg)
    else:
        console.print(f"Left at 'review'. When ready: [bold]brutus pr {candidate_id} --approve[/]")


@app.command()
def review(candidate_id: int = typer.Argument(..., help="a solved candidate's ID")) -> None:
    """Show the diff and draft a PR description into .brutus/PR.md for you to edit."""
    cfg = Config.load()
    conn = db.connect(cfg.db_path)
    candidate = _require_status(conn, candidate_id, Status.REVIEW)
    workdir = _workdir(cfg, candidate_id)
    if not workdir.exists():
        console.print(f"[red]no solve output[/] at {workdir} — run `brutus solve` first")
        raise typer.Exit(1)
    if _draft_pr(candidate, workdir, cfg):
        console.print(f"\nEdit .brutus/PR.md if needed, then: [bold]brutus pr {candidate_id} --approve[/]")


def _draft_pr(candidate, workdir: Path, cfg: Config) -> bool:
    """Show the summary + diff and draft .brutus/PR.md. Returns False if the diff is empty."""
    summary = state_dir(workdir) / "SUMMARY.md"
    if summary.exists():
        console.print("[bold]Summary:[/]")
        console.print(summary.read_text().strip())
        console.print()

    diff = compute_diff(workdir)
    if not diff.strip():
        console.print(
            f"[yellow]empty diff[/] — no committed changes. Inspect {workdir} or "
            "re-run `brutus reset` then solve again."
        )
        return False
    console.print(diff)

    if cfg.llm_cmd:
        text = generate_pr_text(candidate, diff, lambda p: llm.complete(p, cmd=cfg.llm_cmd))
        write_pr(workdir, text)
        console.print(f"\n[green]Drafted[/] {state_dir(workdir) / 'PR.md'}\n")
        console.print(text)
    else:
        console.print("[yellow]BRUTUS_LLM_CMD not set[/] — no description draft")
    return True


def _safety_and_publish(conn, candidate, workdir: Path, cfg: Config, *,
                        force: bool = False, no_claim: bool = False) -> bool:
    """Safety review, then publish. Returns True on success, False if blocked/failed."""
    try:
        pr_text = "\n".join(read_pr(workdir))
    except PublishError as exc:
        console.print(f"[red]{exc}[/]")
        return False

    if cfg.review_cmd:
        diff = compute_diff(workdir)
        verdict = safety_check(candidate, diff, pr_text, gather_guidelines(workdir),
                               lambda p: llm.complete(p, cmd=cfg.review_cmd, timeout=300))
        if verdict["concerns"]:
            console.print("[bold]Safety review:[/]")
            for c in verdict["concerns"]:
                console.print(f"  [yellow]•[/] {c}")
        if verdict["notes"]:
            console.print(f"  [dim]{verdict['notes']}[/]")
        if verdict["block"] and not force:
            console.print("[red]blocked[/] by safety review — fix, or re-run with [bold]--force[/]")
            return False
        if not verdict["concerns"]:
            console.print("[green]safety review: no concerns[/]")
    else:
        console.print("[yellow]BRUTUS_REVIEW_CMD not set[/] — skipping safety review")

    try:
        url = publish(workdir, candidate, f"brutus/issue-{candidate.number}", claim=not no_claim)
    except PublishError as exc:
        console.print(f"[red]publish failed[/] — {exc}")
        return False

    db.update_status(conn, candidate.id, Status.PUSHED)
    console.print(f"[green]Opened PR[/] {url}")
    return True


@app.command()
def pr(
    candidate_id: int = typer.Argument(..., help="a reviewed candidate's ID"),
    approve: bool = typer.Option(False, "--approve", help="required to actually publish"),
    no_claim: bool = typer.Option(False, "--no-claim", help="skip the claim comment"),
    force: bool = typer.Option(False, "--force", help="publish despite a safety block"),
) -> None:
    """Run the safety review, then auto-fork (if needed), push, and open the PR."""
    cfg = Config.load()
    conn = db.connect(cfg.db_path)
    candidate = _require_status(conn, candidate_id, Status.REVIEW)
    workdir = _workdir(cfg, candidate_id)
    if not approve:
        console.print("[yellow]not published[/] — re-run with [bold]--approve[/] to publish")
        raise typer.Exit(1)
    if not _safety_and_publish(conn, candidate, workdir, cfg, force=force, no_claim=no_claim):
        raise typer.Exit(1)


@app.command()
def reset(candidate_id: int = typer.Argument(..., help="candidate to reset for retry")) -> None:
    """Send a solving/failed/reviewed candidate back to 'picked' and clear its workdir."""
    cfg = Config.load()
    conn = db.connect(cfg.db_path)
    candidate = db.get_candidate(conn, candidate_id)
    if candidate is None:
        console.print(f"[red]no candidate[/] with id {candidate_id}")
        raise typer.Exit(1)
    try:
        db.update_status(conn, candidate_id, Status.PICKED)
    except InvalidTransition:
        console.print(f"[red]can't reset[/] — candidate is '{candidate.status.value}'")
        raise typer.Exit(1)

    workdir = _workdir(cfg, candidate_id)
    if workdir.exists():
        shutil.rmtree(workdir)
    console.print(f"[green]Reset[/] {candidate_id} → picked (workdir cleared)")


def _add_issue_thread(candidate, workdir: Path) -> None:
    """Append the full issue thread + referenced issues (parent/blocked-by/mentioned)
    to .brutus/ISSUE.md, so the agent sees everything the author wrote — it can't
    fetch it itself (gh is locked in the session)."""
    thread = fetch_issue(candidate)
    body = candidate.raw.get("body") or ""
    text = f"{body}\n{thread}"
    refs = fetch_referenced_issues(candidate, text)
    images = download_images(text, state_dir(workdir) / "images")

    sections = []
    if thread.strip():
        sections.append("## Full issue thread (comments & activity)\n\n" + thread.strip())
    if refs.strip():
        sections.append("## Referenced issues (parent / blocked-by / mentioned)\n\n" + refs.strip())
    if images:
        listing = "\n".join(f"- {p.relative_to(workdir)}" for p in images)
        sections.append(
            "## Screenshots / images from the issue\n\n"
            "The issue includes images. READ these files for visual context — they "
            "show the bug/UI:\n" + listing
        )
    if not sections:
        return
    with (state_dir(workdir) / "ISSUE.md").open("a") as fh:
        fh.write("\n\n" + "\n\n".join(sections) + "\n")
    console.print("[dim]  added comments + referenced issues to ISSUE.md[/]")


def _agent_env(workdir: Path) -> dict:
    """Environment for an agent session: the repo venv on PATH, and gh/push DISABLED.

    The agent must not push or open PRs — that's the human's job (brutus review/pr).
    Pointing GH_CONFIG_DIR at an empty dir and blanking the tokens makes `gh` and
    `git push` unauthenticated inside the session, so the human gate can't be bypassed.
    Local commits still work.
    """
    env = os.environ.copy()
    venv_bin = workdir / ".brutus" / "venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}:{env['PATH']}"
    no_gh = workdir / ".brutus" / "no-gh"
    no_gh.mkdir(parents=True, exist_ok=True)
    env["GH_CONFIG_DIR"] = str(no_gh)  # empty => gh is logged out in this session
    env["GH_TOKEN"] = ""
    env["GITHUB_TOKEN"] = ""
    return env


_REVISE_PROMPT = (
    "Maintainer feedback on your open PR is in ./.brutus/FEEDBACK.md. Address every "
    "requested change for the issue in ./.brutus/ISSUE.md:\n"
    "- If it asks for CODE changes: make the edits and keep the regression test passing.\n"
    "- If it asks to change the PR DESCRIPTION/template: rewrite ./.brutus/PR.md with the "
    "improved description (this becomes the PR body).\n"
    "- Write a short (1-3 sentence) reply to the maintainer in ./.brutus/REPLY.md, "
    "summarizing what you changed. Professional and human; no mention of automation.\n"
    "- Update ./.brutus/SUMMARY.md. Do NOT run git or open/edit a PR — the harness does that."
)


@app.command()
def revise(
    candidate_id: int = typer.Argument(..., help="a pushed candidate (open PR) to revise"),
    local: bool = typer.Option(False, "--local", help="run the agent on the host (no Docker)"),
) -> None:
    """Pull maintainer feedback, open Claude to address it, and update the PR."""
    cfg = Config.load()
    conn = db.connect(cfg.db_path)
    candidate = _require_status(conn, candidate_id, Status.PUSHED)
    workdir = _workdir(cfg, candidate_id)
    if not workdir.exists():
        console.print(f"[red]no workdir[/] at {workdir} — nothing to revise")
        raise typer.Exit(1)

    try:
        feedback = fetch_feedback(workdir, candidate)
    except PublishError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    console.print("[bold]Maintainer feedback (.brutus/FEEDBACK.md):[/]")
    console.print(feedback.strip()[:2000] or "[yellow](no comments found)[/]")

    branch = f"brutus/issue-{candidate.number}"
    install_skill(workdir)
    env = _agent_env(workdir)  # venv on PATH; gh/push disabled so it can't push itself

    pr_md = state_dir(workdir) / "PR.md"
    body_before = pr_md.read_text() if pr_md.exists() else ""

    # --continue resumes the SAME session from the original solve (same workdir),
    # so Claude keeps full context of the fix it made.
    console.print(f"[blue]Resuming Claude session[/] to address feedback in {workdir} — exit when done.")
    subprocess.run([*shlex.split(cfg.claude_cmd), "--continue", _REVISE_PROMPT],
                   cwd=str(workdir), env=env)

    sandbox = Sandbox(issue=candidate, workdir=workdir, branch=branch, image="",
                      agent_cmd="", mode="local" if local else (cfg.sandbox or "docker"))

    did_something = False

    # 1. Code changes → commit + push to the PR branch.
    if sandbox.commit("Address review feedback"):
        try:
            push_update(workdir, candidate, branch)
            console.print("[green]Pushed code update[/] to the PR.")
            did_something = True
        except PublishError as exc:
            console.print(f"[red]push failed[/] — {exc}")

    # 2. Description feedback → update the PR body if Claude rewrote PR.md.
    body_after = pr_md.read_text() if pr_md.exists() else ""
    if body_after and body_after != body_before:
        if edit_pr_body(workdir, candidate):
            console.print("[green]Updated the PR description.[/]")
            did_something = True

    # 3. Reply to the maintainer.
    reply_file = state_dir(workdir) / "REPLY.md"
    reply = (reply_file.read_text().strip() if reply_file.exists()
             else "Thanks for the review — I've addressed the feedback.")
    if comment_pr(workdir, candidate, reply):
        console.print("[green]Replied to the reviewer[/] on the PR.")

    if not did_something:
        console.print("[yellow]No code or description change was made.[/]")


def _workdir(cfg: Config, candidate_id: int) -> Path:
    return cfg.db_path.parent / "work" / f"issue-{candidate_id}"


def _require_status(conn, candidate_id: int, expected: Status):
    candidate = db.get_candidate(conn, candidate_id)
    if candidate is None:
        console.print(f"[red]no candidate[/] with id {candidate_id}")
        raise typer.Exit(1)
    if candidate.status is not expected:
        console.print(
            f"[red]wrong state[/] — candidate is '{candidate.status.value}', "
            f"expected '{expected.value}'"
        )
        raise typer.Exit(1)
    return candidate


if __name__ == "__main__":
    app()
