# Brutus

An agent that **finds** tractable open-source issues, **solves** them with a Claude
session in a sandbox, and **opens PRs** — with human review gates so nothing reaches a
maintainer without your approval.

```
fetch / search → classify → pick → solve (Claude + skill) → review → pr → revise
        │            │         │        │                      │       │      │
     GitHub API   GLM/Gemini  you    isolated repo +         you    fork+  address
     (+ stars)    score 1–5         Gemini setup + Claude    approve  PR   feedback
```

- **Backend** (repo root): Python CLI (`brutus`) + FastAPI server (`brutus serve`), SQLite storage.
- **Frontend** (`web/`): Next.js UI to browse, filter, pick, and drive solves/reviews.

---

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| Python 3.11+ | backend | — |
| Node 18+ | frontend | — |
| [`gh`](https://cli.github.com) (authenticated) | fork / push / PRs | `brew install gh && gh auth login` |
| [`claude`](https://claude.com/claude-code) CLI | the solve agent | — |
| `git` | clones, commits | — |
| An LLM CLI or key | classify/search + setup | OpenCode/Gemini keys (below) |
| Docker (optional) | isolated sandbox | only if `BRUTUS_SANDBOX=docker` |

---

## Setup

### Backend
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,glm,server]"
cp .env.example .env          # then fill in your keys (see Configuration)
.venv/bin/brutus init-db
```

### Frontend
```bash
cd web
npm install
cp .env.local.example .env.local   # set BRUTUS_DIR (this repo) and BRUTUS_BIN (.venv/bin/brutus)
```

---

## Configuration (`.env`)

`.env` is **gitignored** — never commit real keys.

| Var | Purpose |
|---|---|
| `GITHUB_TOKEN` | GitHub search/fetch (a read/search-scoped PAT is fine) |
| `OPENCODE_API_KEY`, `LLM_BASE_URL`, `GLM_MODEL`, `BRUTUS_LLM_CMD` | LLM for **classify/search** (GLM via OpenCode Zen by default) |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `BRUTUS_SETUP_CMD` | cheap model that **plans repo setup** (install/test cmds) |
| `BRUTUS_SOLVER` | `claude` (default, interactive) or `ralph` (headless loop) |
| `BRUTUS_CLAUDE_CMD` | the Claude Code command (default `claude --dangerously-skip-permissions --model fable`) |
| `BRUTUS_REVIEW_CMD` | model for the pre-PR **safety review** (Claude by default) |
| `BRUTUS_SANDBOX` | `local` (runs on host) or `docker` |

> **Auth note:** `gh` (for PRs) uses your `gh auth login` credentials, kept separate from
> `GITHUB_TOKEN`. Fetch uses the token; PRs use `gh`.

---

## Usage

### Fastest path (UI)
```bash
# terminal 1 — API
.venv/bin/brutus serve

# terminal 2 — UI
cd web && npm run dev          # http://localhost:3000
```
In the browser:
1. **Search** in plain English — e.g. *"python bugs in 1000+ star repos updated this month"* — it fetches, scores, and lists them (with **stars**, **complexity**, and **raised time**).
2. Filter by **Min score** (1 = all difficulties, 5 = quick wins) and language.
3. **Pick & Solve** a row → a macOS Terminal opens with the Claude session. Exit it when done → it walks you through **review → approve → PR** in the same window.
4. Open PRs appear under **"Under review"** — click **Address feedback** to resume the session and handle maintainer comments.

### CLI (full control)
```bash
# Discover
.venv/bin/brutus search "rust CLIs, good first issues, popular repos"   # NL → query + fetch + score
.venv/bin/brutus run --lang python --min-score 4                        # fetch + classify + list
.venv/bin/brutus fetch --lang python                                    # fetch only (any difficulty)
.venv/bin/brutus classify                                               # score unscored issues
.venv/bin/brutus list --min-score 4                                     # browse
.venv/bin/brutus status                                                 # pipeline funnel

# Solve one
.venv/bin/brutus pick <id>
.venv/bin/brutus solve <id> --local        # clone → Gemini setup + venv → Claude session
.venv/bin/brutus review <id>               # show diff + draft PR body
.venv/bin/brutus pr <id> --approve         # safety review → auto-fork → push → open PR

# Iterate on maintainer feedback
.venv/bin/brutus revise <id>               # pull comments → resume Claude → push + reply

# Recover a stuck/abandoned solve
.venv/bin/brutus reset <id>                # back to 'picked', clears the workdir
```

### What a solve does
1. Shallow-clones the repo into `data/work/issue-<id>/` on a fresh branch.
2. **Gemini** plans install/test/lint commands and creates an isolated `.brutus/venv`.
3. Gathers full context into `.brutus/ISSUE.md`: body, **all comments**, referenced issues (parent/blocked-by), labels, and **downloaded screenshots** (Claude is multimodal).
4. Runs an interactive **Claude session** driven by the `brutus-solve` skill: plan → execute → add a regression test → clean up → write `SUMMARY.md`.
5. On exit, brutus commits and guides you through review/approve/PR.

---

## Safety & etiquette (important)

This tool can get accounts banned if misused. See [docs/SAFETY.md](docs/SAFETY.md). Key guardrails already built in:

- **Human gates** at *pick* and *approve* — nothing is pushed without you.
- The Claude session is **`gh`-locked** — the agent physically cannot push or open PRs itself.
- A **safety review** (Claude) reads the repo's guidelines + your diff and blocks PRs to repos that ban AI contributions, flags CLA/signed-commit requirements, etc.
- Quality over volume; one PR at a time. Read the diff before you approve.

---

## Architecture

```
brutus/
├── fetch/        GitHub search, NL search, star enrichment, image download
├── classify/     heuristics + batched LLM tractability scoring
├── solve/        sandbox (clone/venv/checks), Ralph loop, brutus-solve skill, state files
├── pr/           diff, PR draft, safety review, fork+push, feedback, revise helpers
├── server.py     FastAPI endpoints for the UI
├── cli.py        the `brutus` command
├── db.py         SQLite (the pipeline queue)
└── config.py     .env loader
web/              Next.js UI (reads SQLite directly; actions call the API)
```

See [PLAN.md](PLAN.md) for the phased design.

---

## Tests
```bash
.venv/bin/python -m pytest -q      # backend (no network/keys needed)
```
