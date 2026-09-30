# Design

## Goals

1. Claude Opus 5.5 (the **lead**) decides per task whether to work solo, use one worker, or run a small team, and shows the user a clear plan: who does what and why.
2. Workers run on the user's ChatGPT subscription via Codex (GPT-6.1 Sol, effort medium/high/xhigh).
3. Verification is always independent of execution.
4. The lead's tool surface and context stay minimal: few tool calls, no polling, no log reading on the happy path.
5. At the end, one cheap call reports tokens, API-equivalent cost and usage-limit percentages for both sides.

## Components

```
Claude Code (desktop) — Opus 5.5 lead
  skill: orchestrate (SKILL.md ≈100 lines; 2 reference files loaded on demand)
  statusLine → `orc statusline` ──► ~/.orc/claude/<session>.json  (cost, limits, transcript path)
  Bash ──► orc check | run (background) | steer | merge | report
                 │
                 ▼
orc (one stdlib Python file)
  plan.toml → validate → DAG scheduler (semaphore = max_parallel Codex processes)
  per task: codex exec (worker) → commit → checks → fresh verifier [→ adversary]
            → on failure: codex exec resume <same thread> with the failures → re-check → new verifier
  writes .orc/runs/<run>/{plan.toml,state.json,summary.md,<task>/*}
  reads ~/.codex/sessions/**/rollout-*-<thread>.jsonl → Codex limit % → ~/.orc/codex-limits.json
```

## Why a CLI and a skill (not an MCP server, not Claude subagents)

- **Context cost.** MCP tool schemas sit in the lead's context on every turn. A skill costs about 100 lines, and only when triggered. The CLI's help text is never loaded.
- **Tool calls.** A whole phase is **one** background Bash call: `orc run` handles fan-out, gates and repairs itself, and prints one compact summary. The lead is woken once, when the run finishes.
- **Claude usage.** A Claude subagent wrapping `codex exec` would spend Claude tokens just to relay output. `orc` calls Codex directly.
- **Portability.** Plain files and git, so it works the same from the desktop app, the CLI or CI.

## Roles and defaults

| Role | Sandbox | Effort | Where | Verification default |
|---|---|---|---|---|
| explorer | read-only | medium | repo root | none; the lead spot-checks key claims |
| reviewer | read-only | high | repo root | none |
| implementer | workspace-write | high | own worktree, branch `orc/<run>/<task>` | from `risk`: low → checks, medium → checks + verifier, high → checks + verifier + adversary |
| verifier (internal) | read-only | high | implementer's worktree | fresh thread every round |
| adversary (internal) | read-only | xhigh | implementer's worktree | fresh thread |

## Principles → mechanisms

| Principle (see research/findings.md) | Mechanism |
|---|---|
| Start with the smallest configuration that works | Triage table in SKILL.md; Solo is a first-class mode, and `orc report` works without runs |
| Plan visibly | `orc check` renders the plan table (task, role, effort, dependencies, verification, why) plus an estimate of Codex calls and current limits |
| Share context | Plan-level `context` goes to every worker, plus the goal and the list of other tasks; dependencies' results are injected as inputs |
| No conflicting parallel writers | Disjoint `files` enforced for implementers that can run concurrently; one integrator (`orc merge`) |
| Separate doer and checker | Harness-run checks first; a fresh read-only verifier that gets claims, not the implementer's summary; an optional adversary; the lead reviews high-risk diffs |
| Exit codes over opinions | Checks run in the worktree by orc; a verifier runs only if the checks pass (saving quota) |
| Bounded repair | `max_repairs` (default 1) resumes the implementer's warm thread; after that the lead decides (steer, re-plan or do it itself) |
| Structured handoffs | `--output-schema` for worker results and verdicts; everything written to disk |
| Budget awareness | `quota_stop`: no new Codex calls once a Codex window passes 90%; the status line shows live Claude and Codex usage; the skill says to shrink plans when usage is high |

## Accounting

- **Codex tokens**: `turn.completed.usage` summed per call from the `--json` event stream.
- **Codex API-equivalent cost**: `(input − cached) × input_price + cached × cached_price + output × output_price`. `output_tokens` is assumed to include reasoning tokens (the OpenAI convention); `pricing.toml` holds the prices.
- **Codex limit %**: `rate_limits` from the thread's rollout file after each call (newest rollouts as a fallback). The snapshot taken at run start gives the "before" value.
- **Claude tokens**: assistant-message usage in the session transcript and its `subagents/*.jsonl`, deduplicated by message id and request id.
- **Claude cost**: `cost.total_cost_usd` from the status line, which Claude Code computes at list price. It falls back to `pricing.toml`.
- **Claude limit %**: `rate_limits.five_hour` / `seven_day` from the status line. The first value seen in the session is kept, so the report shows start → now.
- **Scope**: `orc report` covers every run started from the current Claude session (matched by session id), plus the whole Claude session. That fits the intended pattern of one task per conversation.

## Assumptions to verify on the first real run

Everything is tested against a fake `codex` (`tests/fake_codex.py`), because Codex can't be logged in from the cloud container where this was built. Before trusting results, check these:

1. **`codex exec ... resume <id> -` flag order.** orc passes the exec flags (`--json -o --output-schema -m -c -s -C`) before `resume`. If a Codex version rejects that, adjust `Run.codex()`: the order is built in one place.
2. **Model id and efforts.** `gpt-6.1-sol` with `model_reasoning_effort` in `{low, medium, high, xhigh, max, ultra}`. Check with `codex` → `/model`.
3. **Strict output schemas.** Both schemas use `additionalProperties: false`, and every property is required, which strict structured output needs.
4. **Rate limits in rollout files.** `orc doctor` prints the limits it finds. If they show "n/a" after a run, the rollout format changed; see `codex_limits()`.
5. **Commits inside worktrees.** The Codex sandbox may block `.git` writes from the worker. orc commits on the worker's behalf outside the sandbox.
6. **Network.** `workspace-write` blocks network by default. Set `network = true` for tasks that install packages.
7. **Status line.** `rate_limits` appears only for Pro and Max plans. On other plans, the Claude limit column shows "n/a".

## Deliberate non-goals (for now)

- **No HTML run viewer.** The summary and `state.json` are enough for the lead; the user sees the status line and the report.
- **No cross-provider routing inside orc.** The lead chooses Solo (Claude) versus orc (Codex).
- **No automatic merging.** Merging stays a lead decision after reading the summary.

## Possible next steps

- `orc run --dry-run` to print the assembled prompts without calling Codex.
- A PreToolUse hook that blocks the Agent tool when Codex quota is available, if the lead over-uses Claude subagents.
- Best-of-N implementers with a judge, for hard, well-tested problems.
- Optionally pair with `openai/codex-plugin-cc` for `/codex:adversarial-review` on non-orc work.
