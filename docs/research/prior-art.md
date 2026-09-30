# Prior art: driving Codex from Claude Code

| Project | Approach | What we took | Why not use it directly |
|---|---|---|---|
| [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc) (official) | Claude Code plugin: `/codex:review`, `/codex:adversarial-review`, `/codex:rescue` (hand-off via a subagent, `--background`, `--resume`/`--fresh`, `--model`/`--effort`), plus an optional Stop-hook "review gate". Wraps the Codex app server and reuses `codex login`. | Adversarial review as a separate pass; resuming threads for follow-ups. | Built for single hand-offs and reviews, not planned fan-out with gates. It can be installed alongside orc for ad-hoc reviews. |
| [scasella/claude-dynamic-workflows-codex](https://github.com/scasella/claude-dynamic-workflows-codex) | `/codex-workflows` skill: Claude writes a `.workflow.js` DSL script, and a Node runner executes it on the Codex app server. Includes an HTML run viewer, resumable journal, warm "sessionful" workers (`agent.start` / `steer`), and GoalLint / ClaimCheck harnesses. One frontier model for all agents; effort scales with fan-out width. | Goal hardening as acceptance criteria; claim checking; warm steering; a single model for every worker. | Heavy: a DSL, app-server dependency and viewer. The lead has to author code, which costs more tokens than filling in a TOML plan. |
| [six-ddc/codex-dynamic-workflows](https://github.com/six-ddc/codex-dynamic-workflows) | Bun/TS runtime with `agent/parallel/pipeline/workflow` primitives, pluggable backends (Codex SDK, Gemini, pi), schema-validated outputs with retries, a concurrency cap and worktree isolation. | Schema-validated output; per-writer worktrees; a concurrency cap. | Another runtime and DSL to learn. |
| [xz1220/open-dynamic-workflows](https://github.com/xz1220/open-dynamic-workflows) | Portable Node CLI for dynamic workflows across agents. | — | Same trade-off as above. |
| [Kuberwastaken/subagentmaxxing](https://github.com/Kuberwastaken/subagentmaxxing) | One Python CLI (`subagent run/fanout/models/doctor`) that normalizes Codex, Cursor and Claude into one envelope, plus a skill teaching the lead when to delegate. `--sandbox read`, `--worktree`, `--background`. | A small CLI plus a skill as the whole interface; read-only first; a normalized result envelope. | No verification pipeline or usage accounting. |
| [robertanton81/claude-subagent-router](https://github.com/robertanton81/claude-subagent-router) | Hooks route Claude subagent calls to Claude tiers or to Codex workers (`codex-implementer`, `codex-reviewer`), switching to Codex as Claude usage nears its limit, with a verification hook. | Separate implementer and reviewer roles; being aware of usage limits. | Routing through hooks is implicit. We want the lead to decide and explain the split. |
| [mgoulart/codex-subagents](https://github.com/mgoulart/codex-subagents) | Plugin that splits tasks into parallel Codex agents run in batches. | — | Parallelism without verification gates. |

Codex primitives used by orc (`codex exec`):
- `--json`: JSONL events on stdout
- `-o/--output-last-message FILE`
- `--output-schema FILE`
- `-m MODEL` and `-c model_reasoning_effort=...`
- `-s read-only|workspace-write`
- `-C DIR`
- `--skip-git-repo-check`
- `resume <thread_id>` for follow-ups
- the prompt on stdin (`-`)
