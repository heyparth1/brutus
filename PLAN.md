# Brutus — Plan

An AI agent that **finds** tractable open-source issues (GitHub Search, good-first-issue
feeds, GSoC orgs), lets **you pick** one, **solves** it with a Ralph loop driving an LLM you
provide, and opens a PR **after you approve the diff**.

## Decisions (locked)

- **Goal:** personal contribution helper. Quality over volume.
- **Solver:** wrap an existing coding-agent CLI (Claude Code / opencode / aider) inside a
  Ralph loop. We do *not* build a bespoke tool-calling agent.
- **Human gates:** you pick the issue (gate 1); you approve the diff before push (gate 2).
- **Scope:** fetch broadly across repos/languages → classify → you filter & pick. Because a
  human picks before solving, per-repo env setup only happens for the one selected repo.
- **Tool language:** Python. **Storage:** SQLite. **PR:** `gh` CLI. **Sandbox:** Docker.
- **LLM:** caller-provided. The Ralph loop is model-agnostic — model + agent CLI are config.

## Pipeline

```
[Fetch] GitHub Search API + good-first-issue feeds + GSoC orgs  →  raw candidates (sqlite)
   ↓
[Classify] heuristics drop junk  →  batched LLM tractability score (1–5 + reason)
   ↓
[Browse]  you filter (lang/label/score/stars) and pick ONE issue        ← gate 1
   ↓
[Solve]   Docker sandbox  →  Ralph loop drives LLM  →  tests green  →  diff
   ↓
[Review]  you read diff + generated PR description                       ← gate 2
   ↓
[PR]      claim issue (comment)  →  push branch  →  gh pr create
```

State lives in one SQLite table; each issue moves through statuses:
`fetched → scored → picked → solving → review → pushed | failed`. That table *is* the queue.

## Project layout (target)

```
brutus/
  pyproject.toml
  brutus/
    config.py            # env + TOML config: tokens, LLM model, agent CLI command
    db.py                # sqlite schema + status enum + tiny query helpers
    models.py            # Issue / Candidate dataclasses
    cli.py               # typer entrypoints: fetch list pick solve review pr
    fetch/
      github_search.py   # REST /search/issues
      goodfirstissue.py  # curated feed importers
      gsoc.py            # annual org/idea-page importer (one-off)
    classify/
      heuristics.py      # cheap filters, no LLM
      llm_score.py       # batched tractability scoring
    solve/
      sandbox.py         # docker: clone, detect+run build/test/lint
      ralph.py           # the loop: iterate, backpressure, stop conditions
      agent.py           # invoke the coding-agent CLI with the chosen model
      prompts/           # PROMPT.md, AGENTS.md templates
    pr/
      publish.py         # claim comment + branch push + gh pr create
  data/brutus.db
```

---

## Phase 0 — Foundations

**Goal:** runnable skeleton, config, DB. Nothing clever.

- `pyproject.toml` (typer, httpx, pydantic, rich; PyGithub optional).
- `config.py`: load `GITHUB_TOKEN`, LLM model id, agent CLI template from env/TOML.
- `db.py`: SQLite schema for `candidates` (id, source, repo, number, title, url, labels,
  language, stars, raw_json, status, score, score_reason, timestamps) + status enum.
- `cli.py`: typer app with stub subcommands wired to no-op handlers.

**Acceptance:** `brutus --help` lists all subcommands; `brutus init-db` creates the table;
a `test_db.py` asserts insert→read round-trips and the status enum transitions are legal.

**Deferred:** web UI, multi-user, migrations framework (one `CREATE TABLE IF NOT EXISTS`).

---

## Phase 1 — Fetch

**Goal:** populate `candidates` from three sources, deduped, idempotent.

- `github_search.py`: `GET /search/issues` with query builder (`label:"good first issue"
  state:open no:assignee`, plus language/stars filters). Handle pagination + rate limits
  (respect `Retry-After`, `X-RateLimit-Remaining`). Upsert by `(repo, number)`.
- `goodfirstissue.py`: import from curated feeds (goodfirstissue.dev, up-for-grabs). Whatever
  has JSON; HTML scrape only where unavoidable.
- `gsoc.py`: importer for a GSoC org/ideas list. **One-off seasonal**, not a live source —
  takes a year/org list, pulls their repos, feeds back into github_search.

**Acceptance:** `brutus fetch --source github --label "good first issue" --lang python` writes
N rows; re-running adds 0 dupes. `test_fetch.py` runs the query builder + upsert against a
recorded API fixture (no live calls in the test).

**Deferred:** GH Archive/BigQuery, trend mining.

---

## Phase 2 — Classify

**Goal:** rank candidates by how tractable an automated PR is. Two tiers, LLM-frugal.

- `heuristics.py` (no LLM, runs on everything): drop/flag by — assigned?, has CI?, has tests
  dir?, repo merges outside PRs?, issue body length, age, comment count, "good first issue"
  present. Produces a cheap prefilter + features.
- `llm_score.py` (LLM, only on survivors): **batched** call — for each issue emit
  `score 1–5 + one-line reason + risk flags`. Prompt anchors what "tractable" means (clear
  acceptance, small surface, reproducible). Store `score` + `score_reason`.

**Acceptance:** `brutus classify` moves rows `fetched→scored` and fills score/reason.
`test_classify.py` asserts heuristics drop a known-bad fixture and keep a known-good one;
LLM call is mocked.

**Deferred:** fine-tuned scorer, embeddings/similarity.

---

## Phase 3 — Browse & Pick (gate 1)

**Goal:** you filter and pick one issue. CLI/TUI, not web.

- `cli.py list`: rich table with filters (`--lang --min-score --label --min-stars
  --sort score`). Shows score + reason.
- `cli.py pick <id>`: moves one row `scored→picked`, prints the issue + repo summary.

**Acceptance:** `brutus list --min-score 4 --lang python` renders a sorted table;
`brutus pick <id>` sets status and is the only row in `picked`.

**Deferred:** TUI app, web UI. Add when a terminal table genuinely hurts.

---

## Phase 4 — Solve (Ralph loop) — the core

**Goal:** given a picked issue, produce a tested diff via a fresh-context Ralph loop driving
the caller's LLM inside a Docker sandbox. Model-agnostic.

### 4a. Sandbox — `sandbox.py`
- Docker container per solve job; clone repo at a fresh branch `brutus/issue-<n>`.
- **Detect** build/test/lint commands from repo signals (CONTRIBUTING.md, CI config,
  package manager files) → write them into `AGENTS.md`. This detection is the flakiest part;
  keep it explicit and let the agent refine it.
- Expose `run(cmd) -> (exit_code, output)` for backpressure checks.

### 4b. Ralph state files (per job, on disk in the repo workdir)
- `PROMPT.md` — **static** standing instruction (the loop's spec). Short; references the
  others. Tells the agent: pick the single highest-priority unchecked step from `fix_plan.md`,
  make one small change, run the checks, commit only if green, update plan + progress, and
  emit `<promise>COMPLETE</promise>` only when the issue's acceptance is met.
- `ISSUE.md` — the issue body + a derived acceptance checklist.
- `fix_plan.md` — **mutable** TODO the agent rewrites each iteration (checkbox list).
- `progress.txt` — **append-only** learnings (why decisions were made, dead ends). This is how
  the next fresh-context iteration inherits reasoning — the loop has no conversation memory.
- `AGENTS.md` — repo conventions + the detected build/test/lint commands + gotchas.

### 4c. The loop — `ralph.py`
```
for i in range(max_iters):
    run fresh agent process (agent.py) with PROMPT.md + the state files   # clean context
    run backpressure: build, tests, lint                                  # repo's own cmds
    if any check fails: record failure, loop (agent must fix next iter)
    if checks pass: commit "one logical change"
    if "<promise>COMPLETE</promise>" emitted AND checks green: stop -> done
    if no new commit for `stall_limit` iters: stop -> failed (needs human)
stop on max_iters -> failed
```
Guardrails (from the research): hard iteration cap; stall detection (no-progress N iters);
all changes inside the container; one logical change per commit; backpressure (tests/lint/
build) is what rejects bad work; write the *why* into commits + progress.txt.

### 4d. Agent invocation — `agent.py`
- Thin wrapper that runs the configured coding-agent CLI in headless/non-interactive mode with
  the configured model, fed the prompt files. Provider config = `{cli_command_template, model,
  env}`. No bespoke tool loop — the CLI already edits files and runs tools; Ralph re-invokes it
  with fresh context each iteration.

**Acceptance:** `brutus solve <id>` on a seeded toy repo with one failing test drives the loop
until the test passes and stops on `<promise>COMPLETE</promise>`. `test_ralph.py` uses a fake
agent (deterministic edits) to assert: stop-on-complete, stop-on-stall, stop-on-cap, and that a
failing backpressure check forces another iteration.

**Deferred:** parallel multi-issue solving, self-tuning prompts, fancy localization.

---

## Phase 5 — Review & PR (gate 2)

**Goal:** you approve, then publish responsibly.

- `cli.py review <id>`: show the diff (`git diff`) + an LLM-generated PR title/description that
  links the issue and summarizes the change. Status `solving→review`.
- `pr/publish.py` on approval: post a claim comment if not already claimed, push the branch,
  `gh pr create` with the description, link the issue. Respect `CONTRIBUTING.md`. Status
  `review→pushed`.

**Acceptance:** `brutus review <id>` prints diff + description and requires explicit
`--approve` to advance. `test_publish.py` asserts the gh command is assembled correctly
(subprocess mocked) and that nothing publishes without approval.

**Etiquette (non-negotiable):** never auto-push without your approval; one PR per issue;
claim before solving; honor each repo's contribution rules.

---

## Phase 6 — Glue & polish

- End-to-end `brutus run` that chains fetch→classify→list for a daily browse.
- Logging + a `status` view of the pipeline table.
- Prompt tuning loop: when the solver fails a recurring way, the fix goes into PROMPT.md /
  AGENTS.md, not into your head ("tune it like a guitar").

---

## Build order

Thin vertical slice first: **Phase 0 → 1 (github only) → 2 → 3 → 4 → 5**, then widen Phase 1
sources (feeds, GSoC) and add Phase 6. Each phase ends with one runnable check and is useful on
its own, so a broken later phase never blocks an earlier one.

## Sources (Ralph technique)
- Huntley, *how-to-ralph-wiggum*: https://github.com/ghuntley/how-to-ralph-wiggum
- snarktank/ralph (prd.json + progress.txt + completion signal): https://github.com/snarktank/ralph
- aihero, *11 Tips for AI Coding with Ralph Wiggum*: https://www.aihero.dev/tips-for-ai-coding-with-ralph-wiggum
- *The Ralph Loop*: https://thomas-wiegold.com/blog/ralph-loop-how-recursive-ai-agents-work/
