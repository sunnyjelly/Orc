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
  plugin "orc": skill orchestrate (SKILL.md ≈90 lines; 3 reference files loaded on demand) + skill delegate
  statusLine → `python3 ~/.orc/app/orc statusline` (installed by `orc setup`) ──► ~/.orc/claude/<session>.json  (cost, limits, transcript path)
  Bash ──► orc check | run (background) | steer | merge | report | ask [--continue] | show
                 │
                 ▼
orc (one stdlib Python file, bundled at plugin/skills/orchestrate/scripts/orc)
  plan.toml → validate → DAG scheduler (semaphore = max_parallel Codex processes)
  per task: codex exec (worker) → commit → checks → fresh verifier [→ adversary]
            → on failure: codex exec resume <same thread> with the failures → re-check → new verifier
  writes .orc/runs/<run>/{plan.toml,state.json,summary.md,<task>/*}
  reads ~/.codex/sessions/**/rollout-*-<thread>.jsonl → Codex limit % → ~/.orc/codex-limits.json
```

## Packaging

The repo is a Claude Code plugin marketplace: `.claude-plugin/marketplace.json` points to the plugin in `plugin/`.
- **The plugin** holds two skills:
  - `orchestrate`: the lead workflow, with orc, its prompts, schemas and pricing inside the skill's `scripts/` folder
  - `delegate`: portable guidance, with no scripts
- **How the skill finds orc:** it runs orc as `python3 ${CLAUDE_SKILL_DIR}/scripts/orc …`. Claude Code substitutes the skill's folder, and the skill's `allowed-tools` pre-approves exactly that command. The same skill therefore works as a plugin skill, as an unzipped personal skill, and from a `.skill` file.
- **The plugin has no `bin/` folder.** claude.ai and Cowork refuse to install plugins that ship executables there, and nothing needs orc on the PATH.
- **The status line needs a path that survives plugin updates.** The plugin cache path changes with each version, so `orc setup` copies the scripts to `~/.orc/app` and points the status line there. Plugins can't set `statusLine` themselves; plugin settings only take `agent` and `subagentStatusLine`.
- **`dist/*.skill`** files are deterministic zips of each skill folder, built by `tools/build_skills.py`, and a test checks they match the sources. Skill frontmatter sticks to the six fields claude.ai uploads accept.
- **Which skill goes where:**
  - `delegate` is the one to upload to claude.ai. From there it syncs into Claude Code, cloud sessions included.
  - `orchestrate` only makes sense where Codex is signed in, i.e. Claude Code on your machine.
- **Why no MCP server:** Claude Code has a shell, so a skill plus a CLI costs nothing until it's used. An MCP server's tool definitions would sit in context on every turn. MCP would only be needed for clients without a shell, such as plain Claude chat, and there orc can't reach your Codex login anyway.

## Security

- **Checks run outside the sandbox.** Plan `checks` run on the user's machine, outside the Codex sandbox, on code a worker wrote. A worker could change a test or a `package.json` script. Only configure checks you'd run on an untrusted branch. Running checks inside `codex sandbox` is a possible hardening step, but not implemented.
- **Windows.** Checks are POSIX shell, like Claude Code's own Bash tool: `/bin/sh` elsewhere, Git Bash on Windows (found next to `git`, or `CLAUDE_CODE_GIT_BASH_PATH`); `orc doctor` reports it. orc resolves `codex` with `shutil.which` (npm installs `codex.cmd`), reads and writes UTF-8 explicitly, and checks worker PIDs with `OpenProcess`, since `os.kill(pid, 0)` sends Ctrl+C there.
- **Windows batch launchers.** `.cmd`/`.bat` arguments are quoted for the worker's argv parser and escaped for `cmd.exe`, with AutoRun and delayed expansion disabled. Repository paths and model IDs remain literal, including shell metacharacters and percent signs; newline and NUL arguments are rejected. Prompts are sent over stdin.
- **Web findings are marked untrusted.** Explorer or reviewer findings from `web = true` tasks are labelled as untrusted data in later workers' prompts. Web pages are a prompt-injection path into implementers.
- **Web threads never get write access.** A Codex thread that ever used web search is never resumed by an implementer (`continues`) or steered with write access. Questions to a worker can't turn on web search. See Follow-ups and history.
- **Branch cleanup preserves unfinished work.** `merge` retains branches and worktrees while unfinished descendants need them, verifies that current ancestor commits are included before marking them merged, and removes only clean worktrees whose commits are in the current branch. `clean` explicitly removes the run's unmerged worktrees and branches. Failed workers, repairs, and steers are committed before returning an error, so partial work remains available.
- **Execution errors revoke success.** A call succeeds only if it has a final message and no process or event error. Failed verifiers produce `uncertain`; a final `pass` cannot override an execution error. Steering revokes previous gates before running the worker.
- **Changed dependencies invalidate old results.** Steering a task marks previously started descendants `stale`. Their branches and call history remain available, but they cannot be steered or merged through orc. Merge the updated upstream task and write a new plan on that code. Dependents that never started remain skipped and can resume normally.
- **Git operations are kept separate.** Merge refuses to run while a merge, rebase, cherry-pick, or revert is already in progress. It aborts only a merge started by its own invocation.
- **Settings failures preserve the file.** Setup refuses to overwrite existing Claude settings that cannot be read as a JSON object, including with `--force`.

## Why a CLI and a skill (not an MCP server, not Claude subagents)

- **Context cost.** MCP tool schemas sit in the lead's context on every turn. A skill costs about 100 lines, and only when triggered. The CLI's help text is never loaded.
- **Tool calls.** A whole phase is **one** background Bash call: `orc run` handles fan-out, gates and repairs itself, and prints one compact summary. The lead is woken once, when the run finishes.
- **Claude usage.** A Claude subagent wrapping `codex exec` would spend Claude tokens just to relay output. `orc` calls Codex directly.
- **Portability.** Plain files and git, so it works the same from the desktop app, the CLI or CI.

## Roles and defaults

| Role | Sandbox | Effort | Where | Verification default |
|---|---|---|---|---|
| explorer | read-only | medium | repo root | none; the lead spot-checks key claims |
| reviewer | read-only | medium for Sol; high otherwise | repo root | none |
| implementer | workspace-write | medium for Sol; high otherwise | own worktree, branch `orc/<run>/<task>` | from `risk`: low → checks, medium → checks + verifier, high → checks + verifier + adversary |
| verifier (internal) | read-only | medium for Sol; high otherwise | implementer's worktree | fresh thread every round |
| adversary (internal) | read-only | xhigh | implementer's worktree | fresh thread |

## Consults (`orc ask`)

A consult is a single read-only Codex call that runs in the foreground, with no plan, worktree or verifier. There are three uses:
- **Second opinion**: `--model sol`. A different model family catches different mistakes.
- **Quick lookup**: `--web` turns on Codex live web search (`-c web_search="live"`).
- **Deep research**: `--research`. A research preamble makes one agentic call search, cross-check and write a cited Markdown report to `.orc/asks/<id>.final.md`. The lead gets only the `## Summary` section and the file path.

The default model is **GPT-6.1 Sol at medium effort**, the preferred model everywhere. `--model luna` (GPT-6 Luna, about 20× cheaper per token) is opt-in for simple lookups; use it at high effort, since Luna at low effort is too weak to be useful. Luna is never used for implementation or verification. Explicit task and plan effort settings override the model default; ask continuations retain their previous effort. The adversary still uses xhigh.

Deep research is a flag on `ask`, not a plan feature. Research needs no worktrees, checks or merges, and one agentic call already runs many searches. For broad research that splits into independent areas, a plan with several `web = true` explorers does it in parallel.

Each consult is appended to `.orc/asks/asks.jsonl` with its usage and cost. `orc report` includes this session's consults as `ask ×N` and `research ×N` rows.

## Follow-ups and history

Every Codex call keeps its thread, and each run or ask records the thread id, so earlier work can be continued warm instead of re-explained. Warm resumes can preserve useful context and cache, but long histories can become more expensive. Use `continues_mode = "summary"` to seed a fresh thread with the earlier final result when inherited detail no longer earns its cost.

- **Ask follow-ups**: `orc ask --continue <ask-id|last> "…"` resumes the ask's thread with only the new question. It keeps the ask's model, effort and web or research mode unless overridden. Research follow-ups keep the report format and still print only the summary. Each ask records `parent` and `root`, so a conversation can be replayed, and the footer prints the ask id. `last` means this Claude session's newest ask (any session's only when no session is known).
- **Questions to a worker**: `orc ask --continue <run>/<task> "…"` resumes a task's own thread (never a verifier's) read-only, with no output schema. Each worker records its actual `cwd`, including a reviewer or explorer using an implementer dependency’s worktree. Questions use that checkout while it exists, then fall back to the repo; older records resolve the dependency from the plan. The prompt says where the worker's changes are. A follow-up to such a question stays a question to that worker. `--web` and `--research` are refused here, and web search stays off even if the worker's task had it, so a question never adds web pages to a worker's thread.
- **Follow-up work**: a plan task with `continues = "<run>/<task>"` resumes that thread on its first call. Otherwise it is an ordinary task: its own worktree (branched as usual, not from the old branch), brief, checks, verifier and repairs. The thread remembers an old worktree path, so a preamble names the new working directory and says whether the earlier commits are in it (`git merge-base --is-ancestor`). The resolved sources are stored in the run's `state.json` under `continues`, so `resume` can find them. `steer` still refuses merged and stale tasks, because their branch is final, and it points to `continues`. Continuing ask threads was cut: put what an ask found into the brief or `context`.
- **Thread ownership**: a Codex thread belongs to the newest task holding it (`thread_history`, which scans all runs). A task holds a thread when it is its own worker thread. A `continues` task claims its thread when its run starts, before its first call, so a skipped or `--only`-filtered task still holds it. Once a plan continues a task, `steer`, `ask --continue` and `continues` refuse the old task and name the new one. They also refuse while the owner is running: tasks record the pid of the orc process running them, and a dead pid doesn't count. Ownership validation and claim persistence share an inter-process lock in `.orc/locks/`. Every resumed call rechecks ownership and availability under that lock, including skipped-task recovery, read-only workers, repairs and steers. An OS reservation holds the thread for the full call; questions retain it until their ask record is appended, including on failure. Availability scans include active reservations, and the OS releases locks when a process exits. Commands that mutate the same run are serialized to avoid overwriting each other’s state; separate runs and unrelated worker threads still run in parallel. A plan may not give one thread to two tasks. Runs sort by id with a numeric same-second suffix (`-2` < `-10`).
- **Web taint**: a thread that ever used web search, through a `web = true` task or a web ask, is never resumed with write access, whether by `continues` on an implementer or by `steer` on one. It is tracked per thread, not per plan entry, so a read-only task that continues a web thread can't pass the taint on to a writer. Each task records `web` in `state.json`. A task that hasn't run yet is judged by its plan entry, and an unreadable plan counts as web. An implementer that continues a thread re-checks the taint right before its first call, since `orc resume` can run long after `orc check`. Every call explicitly passes `web_search="live"` or `web_search="disabled"`, overriding inherited user settings. Questions to a worker always pass `disabled`.
- **Browsing**: `orc show` with no ref lists runs and ask conversations, newest first: two lines per run, one per conversation (questions to a worker are grouped under that worker). `--grep` also searches plans, results, prompts and answers. With a ref it prints a run's summary, a task's full result (all findings, changes, claims, thread id, where its code is), or an ask conversation from its root, with earlier answers clipped. Run, task, call and result shapes are validated before rendering: an unreadable selected record produces a clear error, while malformed unrelated records are skipped. `orc check` creates nothing, including when probing reservations.

Model short names live in `MODEL_ALIASES`: `sol` → `gpt-6.1-sol`, `luna` → `gpt-6-luna`. Update them when OpenAI ships new versions.

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

Quota is checked after acquiring a Codex process slot, and the completed call's usage snapshot is saved before that slot is released. Already-running calls can still consume more quota. Quota-skipped implementers reuse their existing worktrees and call history when resumed; retries are not treated as completed dependencies by the scheduler. Plan validation requires positive integer parallelism and timeouts, nonnegative integer repair counts, and a finite quota threshold above 0 and at most 100.

## Accounting

- **Codex tokens**: `turn.completed.usage` summed per call from the `--json` event stream.
- **Codex API-equivalent cost**: `(input − cached) × input_price + cached × cached_price + output × output_price`. `output_tokens` is assumed to include reasoning tokens (the OpenAI convention); `pricing.toml` holds the prices.
- **Codex limit %**: `rate_limits` from the thread's rollout file after each call (newest rollouts as a fallback). The snapshot taken at run start gives the "before" value.
- **Claude tokens**: assistant-message usage in the session transcript and its `subagents/*.jsonl`, deduplicated by message id and request id.
- **Claude cost**: `cost.total_cost_usd` from the status line, which Claude Code computes at list price. It falls back to `pricing.toml`.
- **Claude limit %**: `rate_limits.five_hour` / `seven_day` from the status line. The first value seen in the session is kept, so the report shows start → now.
- **Scope**: `orc report` covers every run started from the current Claude session (matched by session id), plus the whole Claude session. Without a matching session snapshot, all project history is included and clearly labeled. Explicit run IDs, `--all`, `--since` and `--calls` control scope and detail.

## Assumptions to verify on the first real run

Everything is tested against a fake `codex` (`tests/fake_codex.py`), because Codex can't be logged in from the cloud container where this was built. Before trusting results, check these:

1. **`codex exec ... resume <id> -` flag order.** orc passes the exec flags (`--json -o --output-schema -m -c -s -C`) before `resume`. If a Codex version rejects that, adjust `Run.codex()`: the order is built in one place.
2. **Model ids and efforts.** `gpt-6.1-sol` and `gpt-6-luna` with `model_reasoning_effort` in `{low, medium, high, xhigh, max, ultra}`. Check with `codex` → `/model`.
3. **Strict output schemas.** Both schemas use `additionalProperties: false`, and every property is required, which strict structured output needs.
4. **Rate limits in rollout files.** `orc doctor` prints the limits it finds. If they show "n/a" after a run, the rollout format changed; see `codex_limits()`.
5. **Commits inside worktrees.** The Codex sandbox may block `.git` writes from the worker. orc commits on the worker's behalf outside the sandbox.
6. **Web search.** orc enables it with `-c web_search="live"`, which secondary sources say replaced the removed `--search` flag. Confirm that a `--web` ask shows `web_search` events in `.orc/asks/<id>.events.jsonl`.
7. **Network.** `workspace-write` blocks network by default. Set `network = true` for tasks that install packages.
8. **Status line.** `rate_limits` appears only for Pro and Max plans. On other plans, the Claude limit column shows "n/a".
9. **Per-turn vs cumulative usage.** orc adds up `turn.completed.usage` for each call. If `codex exec resume` reports the thread's cumulative total instead of the new turn's, repairs and steers are over-counted. Compare one repaired task's numbers with its rollout file's final `total_token_usage`. If they are cumulative, subtract the previous call's total on the same thread in `codex_exec()`.
10. **`resume` keeps exec flags.** A repair has to keep `-C` (the worktree), `-s workspace-write` and `--output-schema`. Check one repair's events and final JSON.
11. **Resume with a different sandbox, directory or schema.** Follow-ups resume a thread with `-s read-only` and no `--output-schema` (questions to a worker), or with a new `-C` worktree (`continues`). Check that Codex applies the new flags to the resumed turn, and that a worker asked a question answers in prose even though its earlier turns used a JSON schema.
12. **Schema metadata.** The schemas carry `$schema` and `title` at the top level. If strict mode rejects them, remove both.

## Deliberate non-goals (for now)

- **No HTML run viewer.** The summary and `state.json` are enough for the lead; the user sees the status line and the report.
- **No cross-provider routing inside orc.** The lead chooses Solo (Claude) versus orc (Codex).
- **No automatic merging.** Merging stays a lead decision after reading the summary.

## Possible next steps

- `orc run --dry-run` to print the assembled prompts without calling Codex.
- A PreToolUse hook that blocks the Agent tool when Codex quota is available, if the lead over-uses Claude subagents.
- Best-of-N implementers with a judge, for hard, well-tested problems.
- Optionally pair with `openai/codex-plugin-cc` for `/codex:adversarial-review` on non-orc work.
- **Research sources via MCP** (not built yet). Give research workers primary sources beyond Codex's built-in web search. Details are below.

### Option: MCP research sources

Codex can attach MCP servers for a single run with `-c mcp_servers.<name>.…`. orc would read a small `sources.toml` and attach the servers only to `ask --research`, `ask --web` and plan tasks marked `web = true`. Implementers and verifiers would not get them, so they don't pay for extra tool definitions.

Candidate sources:

| Source | What it adds | Cost |
|---|---|---|
| Context7 | Current, version-specific library and framework docs | Free tier, hosted |
| GitHub MCP (read-only tools only) | Source, issues, releases and changelogs: primary sources for "what changed / is this a known bug" | Free; needs a GitHub token |
| arXiv MCP | Paper search and full text | Free |
| Exa, Brave or Tavily (optional) | Better search results than the built-in search | Free tier, then paid |

Rules to follow if this is built:

- **Pre-approve each server.** Set `default_tools_approval_mode = "approve"` for each one. Otherwise non-interactive `codex exec` silently refuses every MCP call under approval policy `never`. The worker can't tell a refusal from a dead server, so it answers without evidence. See [devstandard#358](https://github.com/LeonJoeeee/devstandard/issues/358) and [codex#31565](https://github.com/openai/codex/issues/31565).
- **Restrict tools.** Give every server an explicit `enabled_tools` allow-list, so GitHub's write tools are never exposed. Read tokens from environment variables.
- **Tell the worker which source to use.** Extend `prompts/research.md`:
  - library or API behavior → Context7 first
  - bugs and changes → GitHub
  - methods and evidence → arXiv
  - everything else → web search
  - quote the source for key claims
- **Report source use.** Count the `mcp_tool_call` events in the summary (e.g. `sources: context7 ×3, github ×2, web ×5`) and flag refused calls, so the lead can see thin evidence.
- **Check the servers.** Have `orc doctor` start each configured source and confirm it responds. It also needs to confirm that MCP servers have network access while the worker runs `-s read-only`.

## Field-feedback fixes

- **Gate identity:** successful gates persist the tested commit/base and a fingerprint of the task, goal, context and amendments. Merge refuses a moved branch or changed spec, and merges the resolved commit. Old successful records without gate identity must be rechecked.
- **Mutation serialization:** merge and clean share the run command lock and a repository integration lock. The resolved run stays fixed while locked, including when using the latest-run default. Active workers prevent cleanup/merge.
- **Process ownership:** Windows children launch suspended, join a kill-on-close Job Object, then resume; POSIX calls get their own process group. Deadlines, cancellation and completed calls terminate descendants before ownership is released.
- **Full specifications:** persisted steer amendments reach workers, verifiers and adversaries, with later amendments overriding conflicts. Explicit acceptance replacements and file extensions survive reload. Out-of-ownership root-cause findings become blocked, avoiding futile automatic repair rounds.
- **Preflight:** known field names/types are validated before normalization, and check verifies an implementer's Git base. Bare check prints usage limits without writing state. Windows session matching normalizes paths; quota reports compare reset metadata, not only percentages.
- **Research:** quick/standard/deep default to 3/10/30 minutes and 12/40/100 observed tool calls. These are time/activity bounds, not token or quota predictions. Events stream with receipt timestamps, progress files and a 30-second stderr heartbeat. Hitting a limit can leave partial events instead of a final answer.
- **Integration:** checks_repeat preserves failures across all repetitions. Explicit post_merge_checks run on the lead host in the merged checkout; failure returns 2, records logs and preserves commits. A subsequent merge retries failed smoke checks even if there is nothing new to merge.
- **Observability:** report labels its scope, shows task models, and optionally prints per-call rates/quota snapshots. show supports task events and commands without changing cwd.

Windows dependency provisioning, read-only scratch support, and multi-parent integration remain separate features; this update documents the existing limitations rather than weakening sandbox boundaries or silently installing dependencies. See `plugin/skills/orchestrate/reference/execution.md`.
