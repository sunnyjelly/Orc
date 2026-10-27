# Lead/sidekick architectures vs orc

Researched 2026-10-27 by orc itself (two GPT-6.1 Terra web explorers and a Terra reviewer, run `20261027-141336`). Claims are as the explorers reported them, with their sources; the lead did not re-read every page. The Artificial Analysis numbers came from a LinkedIn post read through a third-party mirror.

## Devin Fusion (Cognition)

- [Devin Fusion](https://cognition.com/blog/devin-fusion), 2026-06-29: two parallel, tool-equipped agents with separate persistent, cached contexts. The frontier lead owns the plan, ambiguity calls and final review; the sidekick explores and executes. Model switches can happen at context compaction.
- [Fusion in Desktop and CLI](https://cognition.com/blog/local-fusion), 2026-09-11: the lead sends briefs with constraints and success criteria; the sidekick explores, implements, runs tests and reports back. They exchange briefs, results and feedback, not whole conversations. The lead reviews and can take over. Recommended pairing: Claude Opus 5.5 lead, GPT-6 Luna sidekick.
- Artificial Analysis (Coding Agent Index v1.5): Fusion CLI with Fable 5.1 xhigh + SWE-2 scored 61.7 at $7.90/task vs Claude Code with Fable 5.1 at 62.2 for $12.40 (36% cheaper). Both Fusion configurations were on its score-vs-cost Pareto frontier at the time of the post. Cognition's chart, updated 2026-10-19: Opus 5.5 + Luna scored 68.1, 31% cheaper than Claude Code with Opus 5.5.
- Cognition's own counterexample: delegating a judgment-heavy feature cut its score from 54 to 27 while saving only 28% of the cost.

## Related results

- Aider architect/editor ([2024-09-26](https://aider.chat/2024/09/26/architect.html), [2025-01-24](https://aider.chat/2025/01/24/r1-sonnet.html)): a strong model proposes, a cheaper one edits. It gains when the editor is good at edits; o1 + Sonnet did not beat o1 alone.
- Anthropic [advisor pattern](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence): executor consults an advisor; +1.7 points at about 2.1× cost, near run-to-run noise.
- Anthropic posts in 2025–26 ([multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system), long-running harnesses, the parallel-Claudes C compiler, multi-agent patterns and problems) agree: parallelize independent, verifiable work; dependency-heavy shared coding produces conflicts; integration and CI become the bottleneck; agents declare completion too early without end-to-end checks.

## How orc compares

orc covers the sidekick loop (explore → plan → implement and test → review → repair → final) and adds parallel disjoint writers, worktrees, host-run checks, dependency scheduling and quota-aware stopping. Two defaults differed and were changed:

- **Persistent sidekick context.** Fusion's sidekick implements in the same context it explored in. orc started phase-2 implementers cold. SKILL.md now tells the lead to set `continues = "<run>/<explorer>"` when the explorer used no web.
- **Who reviews.** Fusion's lead reviews; orc adds a model verifier at medium/high risk. No orc data yet shows the verifier pays for its calls, so `orc report` now prints a Gates line (verifier rounds, failed rounds, repairs, passes) to collect that evidence.

Also changed from this review: verification docs no longer promise a verifier on every change, plans with 2+ implementers warn without `post_merge_checks`, and the lead inspects the diff of checks-only results.

Not adopted: Luna as the default sidekick. orc keeps Terra (high) for basic work and Sol (medium) for hard tasks; Luna stays opt-in for lookups.
