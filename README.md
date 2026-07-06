# Brutus

An agent that finds tractable open-source issues, solves them with a Claude session
in a sandbox, and opens PRs — with human review gates at pick and approve.

```
fetch/search → classify → pick → solve (Claude + skill) → review → pr → revise
```

- **Backend** (this dir): Python CLI + FastAPI server (`brutus/`)
- **Frontend** (`web/`): Next.js UI to browse, pick, and drive solves

## Setup

**Backend**
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,glm,server]"
cp .env.example .env        # fill in GITHUB_TOKEN + LLM keys
.venv/bin/brutus run --lang python
.venv/bin/brutus serve      # API for the UI
```

**Frontend**
```bash
cd web
npm install
cp .env.local.example .env.local   # set BRUTUS_DIR + BRUTUS_BIN
npm run dev
```

See [PLAN.md](PLAN.md) for the phased design and [docs/SAFETY.md](docs/SAFETY.md)
for contribution etiquette / anti-ban guardrails.

> Requires the `gh`, `claude`, and (optionally) an LLM CLI on PATH. Secrets live in
> `.env` (gitignored). Never commit real keys.
