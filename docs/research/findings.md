# Research findings: multi-agent coding with a Claude lead and Codex workers

Researched 2026-09-30. Sources are listed at the bottom; the "→" lines say how `orc` applies each finding.

## 1. When multiple agents are worth it

- Multi-agent systems use about **15× the tokens of a chat** (single agents about 4×). They pay off only for high-value tasks that parallelize. Token usage explains about 80% of performance variance in Anthropic's research system. [A1]
  → Triage first. **Solo** is the default for small tasks; the skill caps teams at 2–4 workers.
- Common failures: spawning too many agents for simple queries, agents that continue after they have enough, and duplicated work from vague task boundaries. [A1]
  → Explicit triage table and mandatory `why` per task; explorer briefs need a stopping point.
- Embed effort-scaling rules in the lead's prompt: simple tasks get 1 agent, complex ones get several with divided responsibilities. [A1]
- Split work into orchestration (judgment, about 10–15% of tokens, strong model) and execution (85–90%, cheaper or quota-funded model). [P3]
  → Opus plans, verifies and integrates; Codex does the bulk.

## 2. Share context; don't run conflicting writers

- "Share context, and share full agent traces, not just individual messages." "Actions carry implicit decisions, and conflicting decisions carry bad results." Parallel writers that can't see each other make incompatible choices. [C1]
  → The plan `context` block (shared decisions) goes to every worker; each worker also sees the goal and the list of other tasks. Parallel implementers must own disjoint `files` (enforced). Dependent implementers branch from their parent's branch and receive its summary.
- Keep a single integrator. [C1][A1]
  → Only the lead merges (`orc merge`).

## 3. Separate verification from execution

- Agents that grade their own work fall for **self-evaluation bias**: they accept plausible changes and mistake stagnation for progress. Externally grounded verification fixes this. [R1]
- Self-authored tests and metrics are unreliable acceptance gates. [R2] An LLM judge is not an oracle, and self-improving loops need deterministic guardrails. [R3]
- Models correct errors from outside sources far better than their own ("self-correction illusion"). [R4]
- More debate among homogeneous judges doesn't add evidence. [R5] Auditing reasoning with evidence beats majority vote. [R6]
- Anthropic's long-running harness found agents "marking features as complete without proper testing"; its fix was explicit, feature-list-driven end-to-end verification. [A2]
  → The verification ladder:
    1. Deterministic `checks` run by the harness, not by the model.
    2. A **fresh** read-only verifier: a new thread with no stake in the change. It gets the brief, the criteria and the diff, and sees the implementer's claims only as items to check. The implementer's own summary is withheld.
    3. At high risk, an adversarial reviewer at xhigh effort.
    4. The Opus lead as the cross-model check for high-risk diffs.
- Repairs go back to the implementer's **own** thread, which keeps warm context. Each repair is followed by a **new** verifier. The doer fixes; a fresh checker judges.

## 4. Delegation quality

- Each subagent needs an objective, output format, tool and source guidance, and clear task boundaries. [A1]
  → `reference/briefs.md`: objective, where, constraints, acceptance, out of scope, when to stop.
- Harden vague goals into falsifiable contracts before expensive runs ("GoalLint"), and check claims against repo artifacts ("ClaimCheck"). [P1]
  → Acceptance criteria are required for implementers; outputs have a `claims` field that the verifier checks.
- Structured output with schema validation, retrying invalid output. [P2]
  → `codex exec --output-schema` with `schemas/*.json`.

## 5. State, artifacts, efficiency

- Workers write results to storage rather than routing everything through the lead's context. [A1]
  → Everything goes to `.orc/runs/<run>/<task>/`; the lead gets a compact summary.
- Warm follow-ups on the same thread ("steer") were about 3× cheaper and 16× faster than a cold re-read in one measurement. [P1]
  → `orc steer` and automatic repairs use `codex exec resume`.
- Durable, resumable execution with checkpoints; deterministic safeguards around adaptive agents. [A1]
- Use git commits and progress files for continuity; don't declare victory early. [A2]
  → The harness commits each round; `state.json` records every call.

## 6. Tool surface for the lead

- Tool descriptions and interfaces matter as much as prompts; bad descriptions send agents down wrong paths. [A1]
- Every tool definition sits in context on every turn; skills load only when used (progressive disclosure).
  → One CLI (`orc`) plus one skill, and **no MCP server**. The skill is about 100 lines and loads its two reference files only when writing a plan. The lead's per-phase cost is: one `orc check`, one background `orc run`, then one `orc merge && tests` and one `orc report`.

## 7. Telemetry (tokens, cost, limits)

- `codex exec --json` emits `thread.started {thread_id}` and `turn.completed {usage: input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens}`. [O1]
- Rate limits are **not reliably** present in `exec --json` output (openai/codex#14728). Every rollout file `~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl` logs `token_count` events whose `rate_limits` hold `primary` and `secondary` windows, each with `used_percent`, `window_minutes` (300 = 5h, 10080 = weekly) and `resets_at`. [O2][O3]
- Claude Code's status line receives JSON on stdin that includes:
  - `cost.total_cost_usd`: list-price estimate for the session
  - `transcript_path`
  - `context_window.*`
  - `rate_limits.five_hour` and `rate_limits.seven_day`, each with `used_percentage` and `resets_at`. These are present only for Pro and Max, and only after the first response.
  It runs on events, debounced 300 ms, plus an optional `refreshInterval`. [A3]
  → `orc statusline` saves that JSON for each session (keeping the first-seen limits) and shows Claude and Codex usage live. `orc report` combines it with Codex run records in one call.

## 8. Models and prices (API-equivalent)

- **GPT-6.1 Sol** (released 2026-09-29):
  - $2 input / $0.10 cached input / $10 output per 1M tokens.
  - Above 272k input: $4 / $0.20 / $15.
  - Efforts low…max; Codex also lists `ultra`; no none/minimal. [O4][O5]
  - ChatGPT Plus: roughly 15–160 Sol messages per 5h, plus weekly limits. [O6]
- **GPT-6 Luna** (`gpt-6-luna`, released 2026-09-22; there is no 6.1 Luna yet):
  - OpenAI's efficient tier: $0.10 input / $0.50 output per 1M tokens.
  - 1.05M context.
  - In Codex for Plus and above. Suited to high-volume and lightweight agentic tasks; stronger at higher effort. [O7]
- **Claude Opus 5.5**:
  - $4 input / $20 output per 1M tokens; cache reads $0.20; 5-minute cache writes 1.25× input.
  - Default effort `medium`. [A4]

## Sources

- [A1] Anthropic, *How we built our multi-agent research system*: https://www.anthropic.com/engineering/multi-agent-research-system
- [A2] Anthropic, *Effective harnesses for long-running agents*: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- [A3] Claude Code docs, *Customize your status line*: https://code.claude.com/docs/en/statusline
- [A4] Anthropic model table (claude-api skill, cached 2026-09-25)
- [C1] Cognition, *Don't Build Multi-Agents*: https://cognition.com/blog/dont-build-multi-agents
- [R1] *When Do Agent Loops Mistake Stagnation for Progress? Self-Evaluation Bias…* (2607.25152): https://www.alphaxiv.org/abs/2607.25152
- [R2] *Self-Authored Verification Is Unreliable in Heuristic Self-Improving Agents* (2607.24300): https://www.alphaxiv.org/abs/2607.24300
- [R3] *LLM-as-a-Judge Is Not an Oracle* (2609.02246): https://www.alphaxiv.org/abs/2609.02246
- [R4] *The Self-Correction Illusion* (2606.05976): https://www.alphaxiv.org/abs/2606.05976
- [R5] *More Debate, Same Evidence* (2608.00243): https://www.alphaxiv.org/abs/2608.00243
- [R6] *Auditing Multi-Agent LLM Reasoning Trees Outperforms Majority Vote and LLM-as-Judge* (2602.09341): https://www.alphaxiv.org/abs/2602.09341
- [P1] scasella/claude-dynamic-workflows-codex: https://github.com/scasella/claude-dynamic-workflows-codex
- [P2] six-ddc/codex-dynamic-workflows: https://github.com/six-ddc/codex-dynamic-workflows
- [P3] Kuberwastaken/subagentmaxxing: https://github.com/Kuberwastaken/subagentmaxxing
- [O1] openai/codex `codex-rs/exec/src/exec_events.rs` and `cli.rs`: https://github.com/openai/codex
- [O2] openai/codex#14728 (rate limits in exec JSONL): https://github.com/openai/codex/issues/14728
- [O3] statico/macotron-plugin-codex-usage (rollout rate-limit parsing): https://github.com/statico/macotron-plugin-codex-usage
- [O4] OpenAI, GPT-6.1 Sol model page: https://developers.openai.com/api/docs/models/gpt-6.1-sol
- [O5] Pricing coverage: https://mixed-news.com/en/gpt-6-1-sol-pricing-cached-input-halved/
- [O6] Codex usage limits overview: https://simplemetrics.xyz/chatgpt-codex-limits-2026/
- [O7] OpenAI, *Introducing GPT-6 Sol and Luna*: https://openai.com/index/introducing-gpt-6-sol-and-luna/ ; GitHub changelog: https://github.blog/changelog/2026-09-22-openais-gpt-6-sol-and-gpt-6-luna-now-available/
- [O8] Codex web search in exec mode (`-c web_search="live"`): https://github.com/parisbs/codex-subagent-mcp/pull/49
