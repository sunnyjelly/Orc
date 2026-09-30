---
name: orchestrate
description: Lead coding tasks with Codex (GPT) workers through the `orc` CLI. Use for any non-trivial coding, refactoring, debugging, review or codebase-research task. Decides whether to do the work yourself, hand it to one worker, or run a small team; plans it, runs it with independent verification, and ends with a token, cost and usage-limit report.
allowed-tools: Bash(orc check:*), Bash(orc run:*), Bash(orc steer:*), Bash(orc merge:*), Bash(orc report:*), Bash(orc doctor)
---

# Lead with Codex workers

You are the lead engineer. Workers are Codex runs (GPT-6.1 Sol on the user's ChatGPT plan), launched by `orc`. Your job is judgment: decide the split, write precise briefs, check evidence, integrate. Aim for the best result with the least total spend: your tokens, Codex quota (ChatGPT Plus allows roughly 15–160 Sol messages per 5 hours) and the user's time. Multi-agent work costs many times the tokens of doing a task directly, so it has to earn its cost.

## 1. Triage (in your head, no tool calls)

Pick the smallest mode that does the job well:

| Mode | Use when |
|---|---|
| **Solo**: do it yourself | You can finish in about 10 tool calls; the task depends on this conversation's context or taste; requirements are unclear (ask the user instead); or writing the brief would cost about as much as doing the work. |
| **Single**: one worker | One self-contained, well-specified chunk that would take you many calls: a long implement-test-debug loop, bulk mechanical edits, or a broad code read whose raw output you don't need in your context. |
| **Team**: 2–4 workers | Independent pieces: parallel read-only investigations of separate areas, or implementation that splits into disjoint file sets. Or high stakes, where explore → implement → adversarial review pays off. |

- Parallelize reading freely. Parallelize writing only across disjoint files; tightly coupled changes go to one implementer. Parallel writers make conflicting hidden decisions.
- Don't delegate a small change you would have to re-read completely to trust.
- Check the status line: if Codex's 5h window is at 80% or more, prefer Solo or a smaller plan and tell the user.
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
  - Clear feedback: `orc steer <run> <task> "<specific fix>"`. This reuses the worker's warm context and re-checks and re-verifies automatically.
  - Wrong approach: rewrite the brief and re-plan.
  - After two failed rounds on the same task, do it yourself or ask the user.
- Explorer findings are claims: spot-check the one or two that your decisions rest on.
- `orc merge <run> && <project test command>` in one call, then fix anything the combined result breaks.

## 5. Report (always, also for Solo work)

At the end run `orc report` once and include its output in your final message, with one line on who did what. It shows tokens, API-equivalent cost and usage-limit percentages for Codex and for this Claude session. Don't compute these yourself.

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
