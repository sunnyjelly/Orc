---
name: orchestrate
description: Lead coding tasks with OpenAI Codex (GPT) workers through the bundled `orc` CLI. Use for any non-trivial coding, refactoring, debugging, review or codebase-research task. Decides whether to do the work yourself, hand it to one worker, or run a small team; plans it, runs it with independent verification, and ends with a token, cost and usage-limit report.
compatibility: Claude Code with a local shell. Needs Python 3.11+, git, and the Codex CLI signed in (`codex login`).
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/orc *)
---

# Lead with Codex workers

**Run orc as `python3 ${CLAUDE_SKILL_DIR}/scripts/orc <command>`** (written `orc` below; always use the full form, which is pre-approved). If a command fails because Codex is missing or signed out, run `orc doctor` and relay its fix to the user.

You are the lead engineer. Workers are Codex runs (GPT-6.1 Sol on the user's ChatGPT plan), launched by `orc`. Your job is judgment: decide the split, write precise briefs, check evidence, integrate. Aim for the best result with the least total spend: your tokens, Codex quota (ChatGPT Plus allows roughly 15–160 Sol messages per 5 hours) and the user's time. Multi-agent work costs many times the tokens of doing a task directly, so it has to earn its cost.

## Quick consults: `orc ask` (any mode, runs in the foreground)

- `orc ask "<question>"`: GPT-6.1 Sol at high effort, read-only in the repo. Use it for a second opinion from a different model family on a design choice, diagnosis or risky assumption. State your position and ask it to attack that position.
- `--model luna`: much cheaper, only for simple lookups and triage where depth doesn't matter.
- `orc ask --web "…"`: a fast web lookup. The raw search results stay out of your context.
- `orc ask --research "…"`: multi-source web research. You get the summary; the full cited report goes to a file you open only if needed.
- Put everything the consultant needs into the first question; it has not seen this conversation.
- `orc ask --continue <ask-id|last> "…"`: a follow-up in the same Codex thread, which remembers the earlier questions and answers (same model and mode). Each answer's footer names its ask id.
- `orc ask --continue <run>/<task> "…"`: ask a finished worker about its own work, read-only.
- Skip it when one grep or file read answers the question. Prefer Sol; Luna never implements or verifies.

## 1. Triage (in your head, no tool calls)

Pick the smallest mode that does the job well:

| Mode | Use when |
|---|---|
| **Solo**: do it yourself | You can finish in about 10 tool calls; the task depends on this conversation's context or taste; requirements are unclear (ask the user instead); or writing the brief would cost about as much as doing the work. |
| **Single**: one worker | One self-contained, well-specified chunk that would take you many calls: a long implement-test-debug loop, bulk mechanical edits, or a broad code read whose raw output you don't need in your context. |
| **Team**: 2–4 workers | Independent pieces: parallel read-only investigations of separate areas, or implementation that splits into disjoint file sets. Or high stakes, where explore → implement → adversarial review pays off. |

- Parallelize reading freely. Parallelize writing only across disjoint files; tightly coupled changes go to one implementer. Parallel writers make conflicting hidden decisions.
- Don't delegate a small change you would have to re-read completely to trust.
- `orc check` prints current Codex and Claude limits. If a 5h window is at 80% or more, prefer Solo or a smaller plan and tell the user.
- Prefer `orc` workers over Claude subagents (the Agent tool), which spend the user's Claude usage.

## 2. Plan (Single and Team)

1. Look around only as much as good briefs require. If that look-around is itself big, make it an explorer task in a first plan and write the implementation plan after it returns.
2. Commit your own pending changes first: worktrees start from HEAD.
3. Write `.orc/plan.toml`. Read `reference/plan-format.md` and `reference/briefs.md` the first time in a session.
4. Run `orc check .orc/plan.toml`. Show the user its table plus at most 3 lines: why this split, what you will do yourself, why this verification level. Wait for approval, unless the user already told you to proceed autonomously.

**Verification is separate from execution by design.** Implementers never grade themselves. `orc` runs the plan's checks (tests, lint, build), then a fresh read-only verifier with no stake in the change. At risk high, it also runs an adversarial reviewer. Choose `risk` honestly:
- low: docs, config, isolated scripts
- medium: normal feature work (the default)
- high: auth, payments, data migrations, concurrency, public APIs

Always give implementers real `checks`: exit codes beat opinions.

## 3. Run

- Run `orc run .orc/plan.toml` with `run_in_background: true`. You are notified when it finishes. Do not poll, sleep, or read logs while it runs.
- One `orc run` per phase, containing every task of that phase. Use `after` for dependencies only when the later brief can be fully written now. Otherwise run a second plan.
- While it runs, only do work that doesn't touch files owned by its tasks, or just wait.

## 4. Integrate

The run summary normally has everything you need. Open files under `.orc/runs/` only to debug a failure.

- **verified / checks-passed**: at low or medium risk, trust the gates; don't re-review the whole diff. At high risk, read the diff yourself: you are the cross-model check.
- **failed / uncertain**: take the cheapest fix that works:
  - Trivial: fix it yourself after merging.
  - Clear feedback: `orc steer <run> <task> "<specific fix>"`. This reuses the worker's warm context and re-checks and re-verifies automatically. If tasks were skipped because of the failure, `orc resume <run>` runs them afterwards.
  - Wrong approach: rewrite the brief and re-plan.
  - After two failed rounds on the same task, do it yourself or ask the user.
- Explorer findings are claims: spot-check the one or two that your decisions rest on.
- `orc merge <run> && <project test command>` in one call (merge exits non-zero on a conflict), then fix anything the combined result breaks.
- `blocked` means the worker stopped to ask something: answer its open question with `orc steer`, or re-plan.
- **stale**: an upstream task was steered after this task ran. Merge the updated upstream task, then write a new plan for the stale work; its old branch is preserved.
- **Follow-up work** on a merged, stale or older task: in the new plan, set `continues = "<run>/<task>"` on the task. It resumes that worker's warm thread in a fresh worktree, with the usual gates. This is cheaper than a cold worker that has to re-read everything.
- `orc history [--grep text]` lists earlier runs and asks. `orc show <run>|<run>/<task>|<ask-id>` re-reads one of them, with thread ids and every finding.

## 5. Report (always, also for Solo work)

At the end run `orc report` once and include its output in your final message, with one line on who did what. It shows tokens, API-equivalent cost and usage-limit percentages for Codex and for this Claude session. Don't compute these yourself. If it says no status line snapshot was found, tell the user to run `orc setup` once.

## Efficiency rules

- Every tool call re-sends your whole context. Batch commands with `&&`, and put all of a phase's tasks in one plan.
- Keep briefs tight and complete; a missing constraint costs a whole repair round.
- Effort:
  - explorer: `medium`
  - implementer: `high`
  - `xhigh`: tricky algorithms, concurrency or security
  - `max`/`ultra`: only on explicit need, since they burn quota fast
- Don't paste run output back in prose; point to it. Keep your messages to the user short.
- Never "just re-run" a failure: fix the cause (brief, checks or code) first.
