# orc: Claude leads, Codex works

Claude Code (Opus 5.5) acts as the lead engineer. It delegates work to OpenAI Codex workers (GPT-6.1 Sol) that run on your ChatGPT subscription, and every change is checked by a verifier that did not write it. At the end you get one report: tokens, API-equivalent cost, and 5-hour/weekly limit usage for both Claude and Codex.

```
you ──► Opus 5.5 (lead, skill "orchestrate")
          ├─ `orc ask`: second opinion · web lookup · --research   (Sol 6.1 high by default; Luna optional)
          ├─ triage: solo │ one worker │ team of 2–4
          ├─ plan.toml ──► `orc check`  → plan table for you to approve
          ├─ `orc run` (one background call)
          │     explorer / reviewer   read-only Codex
          │     implementer           Codex in its own git worktree
          │        → harness checks → fresh verifier (→ adversary) → repair on the same thread
          ├─ `orc merge && tests`
          └─ `orc report` → tokens · ≈API $ · limit % (Claude + Codex)
```

- Research behind the design: [docs/research/findings.md](docs/research/findings.md)
- Prior art: [docs/research/prior-art.md](docs/research/prior-art.md)
- Architecture and open assumptions: [docs/design.md](docs/design.md)

## Install (your machine)

Requirements:
- Python 3.11+ and git
- Node, for the Codex CLI
- Claude Code (desktop app or CLI)
- A ChatGPT plan with Codex access

```bash
git clone https://github.com/SteliosRapt/Playground orc && cd orc
./install.sh                      # symlinks bin/orc into ~/.local/bin and the skill into ~/.claude/skills
npm i -g @openai/codex && codex login
orc doctor
```

Add the status line to `~/.claude/settings.json`. It shows live Claude and Codex usage and saves the session data that `orc report` reads:

```json
{ "statusLine": { "type": "command", "command": "orc statusline", "refreshInterval": 30 } }
```

The skill pre-approves its own `orc` commands. To skip prompts outside the skill too, add `"permissions": {"allow": ["Bash(orc check:*)", "Bash(orc run:*)", "Bash(orc steer:*)", "Bash(orc merge:*)", "Bash(orc report:*)"]}`.

## Use

Open any git repo in Claude Code and ask for work as usual. Set the model to Opus 5.5 at medium or high effort. The `orchestrate` skill makes Opus:

1. **Triage.** Small or context-heavy work it does itself. Otherwise it plans one worker or a small team.
2. **Plan.** It writes `.orc/plan.toml` and shows you `orc check`'s table: each task, its role and effort, dependencies, verification, and why it is delegated. It waits for your OK unless you told it to go ahead.
3. **Run.** `orc run` in the background. You see progress in the status line.
4. **Integrate.** It reads the summary, then steers or fixes failures, merges verified branches and runs the tests.
5. **Report.** `orc report`, for example:

```
| task | role · effort | result | calls | tokens in (cached) / out | ≈ API $ | time |
| map-auth   | explorer · medium    | done     | 1 | 180k (120k) / 6k  | $0.19 | 2m10s |
| rate-limit | implementer · high   | verified | 3 | 610k (450k) / 31k | $0.68 | 9m02s |
**Codex (gpt-6.1-sol, ChatGPT plan):** 4 calls · 827k tokens … · ≈ $0.87 API-equivalent · 5h 12% → 31%, wk 40% → 43%
**Claude (Opus 5.5, this session):** 1.9M tokens (1.7M cache reads, 38k output) · ≈ $2.40 API-equivalent · 5h 9% → 15%, wk 22% → 23%
**Total ≈ $3.27 API-equivalent**
```

## Commands

| Command | What it does |
|---|---|
| `orc ask [--model sol\|luna] [--effort E] [--web] [--research] "Q"` | Quick read-only consult (default GPT-6.1 Sol at high effort): a second opinion, lookup, or web research. `--research` saves a cited report to `.orc/asks/` and prints only its summary. |
| `orc check PLAN` | Validates the plan (roles, acceptance criteria, file ownership, cycles) and prints the plan table, estimated Codex calls and current limits. No Codex calls. |
| `orc run PLAN [--only a,b]` | Runs the plan. Prints progress to stderr and a compact summary to stdout. Exit 0 if everything passed, 2 otherwise. |
| `orc steer RUN TASK "msg"` | Follow-up on a task's own Codex thread (warm context), then re-check and re-verify. |
| `orc merge [RUN] [TASK…]` | Merges verified branches (`--no-ff`) and removes their worktrees. Stops at the first conflict. |
| `orc clean [RUN]` | Removes a run's leftover worktrees and branches. |
| `orc report [RUN…] [--all]` | Tokens, ≈ API cost and limit % for this Claude session's runs plus the session itself. |
| `orc statusline` | Claude Code status line command. |
| `orc doctor` | Checks the Codex CLI, login, git and status line setup. |

Plan format: [skill/orchestrate/reference/plan-format.md](skill/orchestrate/reference/plan-format.md). Brief-writing guide: [skill/orchestrate/reference/briefs.md](skill/orchestrate/reference/briefs.md).

## Layout

```
bin/orc                     the CLI (stdlib Python, one file)
prompts/*.md                role preambles for explorer, implementer, reviewer, verifier, adversary
schemas/*.json              strict output schemas (worker result, verdict)
pricing.toml                API list prices, used for "≈ API $"
skill/orchestrate/          the Claude Code skill (SKILL.md + reference/)
docs/                       research findings, prior art, design
tests/                      end-to-end tests against a fake codex
```

Run state goes to `<repo>/.orc/runs/` (git-excluded automatically). Worktrees and usage snapshots go to `~/.orc/`.

## Tests

```bash
python3 -m unittest discover tests -v
```

The tests drive the full cycle (explore → parallel implementers → repair after a failed check → repair after a failed verdict → dependent implementer → merge → report) against `tests/fake_codex.py`. They have not yet run against real Codex; see the "Assumptions to verify" section in `docs/design.md`.
