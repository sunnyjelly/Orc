# orc: Claude leads, Codex works

Claude Code (Opus 5.5) acts as the lead engineer. It delegates work to OpenAI Codex workers (GPT-6.1 Sol) that run on your ChatGPT subscription, and every change is checked by a verifier that did not write it. At the end you get one report: tokens, API-equivalent cost, and 5-hour/weekly limit usage for both Claude and Codex.

```
you ──► Opus 5.5 (lead, skill "orchestrate")
          ├─ `orc ask`: second opinion · web lookup · --research   (Sol 6.1 high by default; Luna optional)
          ├─ triage: solo │ one worker │ team of 2–4   (portable version: skill "delegate")
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

## What's in the box

| Piece | What it is | Where it works |
|---|---|---|
| **`orchestrate` skill** | Opus leads, Codex workers do the work through the bundled `orc` CLI | Claude Code on your machine (desktop app or CLI), with Codex signed in |
| **`delegate` skill** | Portable guidance on when and how to use subagents (triage, briefs, independent verification). No scripts. | Anywhere Claude has subagents, including Claude Code cloud sessions |

Both ship together as one Claude Code plugin, and each is also a standalone `.skill` file in [`dist/`](dist/).

## Install

### Option A: the plugin (recommended for Claude Code)

In Claude Code, whether in the desktop app's Code tab or the CLI:

```
/plugin marketplace add SteliosRapt/Playground
/plugin install orc@orc-tools
```

Then, once, in a terminal:

```bash
npm i -g @openai/codex && codex login     # sign in with your ChatGPT account
```

Then ask Claude to run `orc setup`, or run it yourself. It copies orc to `~/.orc/app` and sets Claude Code's status line to show live Claude and Codex usage, which `orc report` also reads. It never replaces an existing status line unless you pass `--force`. Restart Claude Code afterwards. `orc doctor` checks everything.

Updates arrive through `/plugin` when the version in `plugin/.claude-plugin/plugin.json` changes.

### Option B: standalone skill files

Download a `.skill` file from [`dist/`](dist/). It is a zip of the skill folder.

- **Claude Code:** unzip it into `~/.claude/skills/` (you get `~/.claude/skills/orchestrate/SKILL.md`).
- **claude.ai** (Settings → Capabilities → Skills → upload): upload `delegate.skill`. Skills enabled on claude.ai also sync into Claude Code, including cloud sessions. `orchestrate` needs your local Codex login, so it is only useful in Claude Code on your machine.

### Requirements for `orchestrate`

- Python 3.11+ and git
- the Codex CLI signed in with a ChatGPT plan that includes Codex
- Claude Code; Opus 5.5 at medium or high effort works well as the lead

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
| `orc merge [RUN] [TASK…]` | Merges verified branches (`--no-ff`) and removes clean worktrees once dependents no longer need them. Refuses stale dependencies or an existing Git operation; exits 1 at the first conflict. |
| `orc clean [RUN]` | Removes a run's leftover worktrees and branches. |
| `orc report [RUN…] [--all]` | Tokens, ≈ API cost and limit % for this Claude session's runs plus the session itself. |
| `orc resume [RUN]` | Runs a run's skipped tasks, e.g. after `steer` fixed the task they depend on. |
| `orc setup [--force]` | Copies orc to `~/.orc/app` and sets Claude Code's status line to it. |
| `orc statusline` | The status line command itself (reads Claude Code's session JSON on stdin). |
| `orc doctor` | Checks the Codex CLI, login, git and status line setup. |

Inside Claude Code the skill runs these as `python3 ${CLAUDE_SKILL_DIR}/scripts/orc <command>`, which the skill pre-approves. After `orc setup` you can also run them yourself with `alias orc='python3 ~/.orc/app/orc'`.

Plan format: [plan-format.md](plugin/skills/orchestrate/reference/plan-format.md). Brief-writing guide: [briefs.md](plugin/skills/orchestrate/reference/briefs.md).

**Safety note:** plan `checks` run on your machine, outside Codex's sandbox, on code a worker just wrote. Only use checks you would run on an untrusted branch.

## Layout

```
.claude-plugin/marketplace.json      makes this repo a plugin marketplace ("orc-tools")
plugin/                              the plugin
  .claude-plugin/plugin.json
  skills/orchestrate/SKILL.md        lead workflow (+ reference/ loaded on demand)
  skills/orchestrate/scripts/orc     the CLI (stdlib Python, one file)
  skills/orchestrate/scripts/{prompts,schemas,pricing.toml}
  skills/delegate/SKILL.md           portable delegation guidance
dist/*.skill                         standalone skill zips (built by tools/build_skills.py)
docs/                                research findings, prior art, design
tests/                               end-to-end tests against a fake codex
```

Run state goes to `<repo>/.orc/runs/` (git-excluded automatically). Worktrees, usage snapshots and the `orc setup` copy go to `~/.orc/`.

## Tests

```bash
python3 -m unittest discover tests -v
python3 tools/build_skills.py          # after changing anything under plugin/skills/ (a test checks dist/ is current)
claude plugin validate . && claude plugin validate ./plugin
```

The tests run against `tests/fake_codex.py`. They cover:
- the full cycle: explore → parallel implementers → repair after a failed check → repair after a failed verdict → dependent implementer → merge → report
- steer and resume
- implement → review → fix chains
- blocked workers and merge conflicts
- failed workers, repairs, steers and verifiers, with partial-work preservation
- stale descendants, changed parent branches, existing Git operations, and partial merges with pending dependents
- malformed settings, numeric plan validation, and quota recovery and queueing
- the quota guard
- report scoping and setup
- the packaged skills They have not yet run against real Codex; see the "Assumptions to verify" section in `docs/design.md`.
