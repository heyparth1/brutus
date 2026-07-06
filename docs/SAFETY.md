# Safety & Etiquette — keeping accounts unbanned

Automated issue-solving is fine as a **drafting + human-review assistant**. It becomes
ban-bait the moment it's a fire-and-forget PR cannon. The real risk is **GitHub** (spam /
low-quality automated PRs get accounts and bots banned), not the LLM providers.

This doc lists the guardrails, what the code enforces **today**, and what to **implement
next**. Treat the "Next" items as the backlog.

References: [GitHub Acceptable Use Policies](https://docs.github.com/site-policy/acceptable-use-policies/github-acceptable-use-policies), Anthropic Usage Policy.

---

## 1. Never auto-push — human-review every PR
The single biggest protection. A human must read the diff and approve before anything is pushed.

- **Today:** `brutus pr <id>` refuses to publish without `--approve` (gate 2); `review` shows
  the diff + drafts `.brutus/PR.md` first. Picking and approving are two separate human steps.
- **Next:**
  - [ ] A hard config kill-switch `BRUTUS_ALLOW_PUSH` (default off) so push is impossible
        unless explicitly enabled, independent of `--approve`.
  - [ ] Block `pr` unless the latest checks were green (see §2).

## 2. Quality over volume
A few PRs that genuinely fix the issue and pass tests ≫ many mediocre ones.

- **Today:** the solver runs the repo's tests as backpressure; nothing yet blocks a PR whose
  tests don't pass, and there's no volume cap.
- **Next:**
  - [ ] Record the last check result per candidate; `pr` refuses if tests aren't green.
  - [ ] Daily PR cap (e.g. `BRUTUS_MAX_PRS_PER_DAY`, default 3) enforced in `publish`.
  - [ ] Require a non-empty diff + at least one added/updated test before allowing `pr`.

## 3. Check CONTRIBUTING.md / AI policy before submitting
Many repos now explicitly forbid AI-generated contributions. PRing them anyway gets reported.

- **Today:** not checked.
- **Next:**
  - [ ] During classify (or a pre-solve step), fetch `CONTRIBUTING.md` / `.github/` and scan
        for AI-policy signals ("no AI", "AI-generated", "LLM"). Store an `ai_allowed` flag.
  - [ ] `solve`/`pr` refuse (or warn loudly) when `ai_allowed` is false; hide such issues in
        the UI by default.

## 4. Disclose AI assistance
Be transparent. Some projects require it; all appreciate honesty.

- **Today:** the claim comment says "drafted with automation; reviewed by a human." PR body
  is LLM-drafted but has no mandatory disclosure line.
- **Next:**
  - [ ] Always append a disclosure footer to the PR body in `publish` (not just the prompt),
        e.g. "Drafted with AI assistance and reviewed by a human before submitting."
  - [ ] Make the disclosure text configurable but non-removable.

## 5. Don't spam claim comments
Only claim what you'll actually finish. Comment spam is classic ban-bait.

- **Today:** `publish` posts a claim comment by default; `--no-claim` disables it. Claiming
  happens at PR time (good — not at pick time).
- **Next:**
  - [ ] Flip the default to **no claim**; require opting in (`--claim`) per PR.
  - [ ] Never claim during `pick`/`solve` — only at the moment of an approved push.

## 6. Go slow — act like a contributor, not a bot
Low volume, real engagement. Bursts look like a bot farm even if each PR is fine.

- **Today:** no throttling.
- **Next:**
  - [ ] Min interval between PRs (e.g. `BRUTUS_MIN_PR_INTERVAL_MIN`).
  - [ ] Spread across repos; cap PRs per repo per week.
  - [ ] Surface a "today: N PRs" counter in `status` / the UI.

## 7. GSoC — extra caution
Submitting AI-generated PRs during GSoC can violate the org's and the program's rules and
torch your reputation.

- **Today:** `fetch --source gsoc` exists with no warning.
- **Next:**
  - [ ] Tag GSoC-sourced candidates; require an explicit `--i-understand-gsoc-rules` ack
        before `solve`/`pr` on them.
  - [ ] Loud UI banner on GSoC-tagged issues.

---

## Quick rules of thumb (until the above lands)
- Keep both human gates (pick, approve). Never wire up auto-push.
- Submit only what you've read and would stand behind under your own name.
- Read `CONTRIBUTING.md` manually before submitting; skip "no AI" repos.
- Use `--no-claim` until you're actually about to push.
- A handful of PRs, spaced out — not a flood.
- This is not legal advice; read the platform policies yourself before any scale.
