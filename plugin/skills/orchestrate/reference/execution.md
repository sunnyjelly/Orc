# Host execution and diagnostics

## Windows dependencies

Codex's Windows sandbox may execute commands as another user. Its Python launcher, user-site packages, accessible directories and environment can differ from the lead's. A successful lead-side check does not prove a worker can import dependencies. A `PYTHONPATH` pointing into the lead's AppData may still fail with access denied.

Before a dependency-heavy task, establish an interpreter and compatible virtual environment or wheel/target directory that the sandbox can actually read. Include their absolute paths in the brief and ask the worker to probe the interpreter and required imports before implementation; stop with `blocked` if they fail. Prefer an existing accessible environment over reinstalling packages repeatedly. Match Python version, architecture and binary ABI. Do not copy arbitrary distribution `RECORD` entries: script entries can escape the destination and `.pth` handling may differ under `PYTHONPATH`.

Orc does not automatically install or vendor dependencies. Plan fields not supported by this version are rejected rather than ignored. Read-only workers/verifiers keep the read-only sandbox; some tests needing scratch writes may be unavailable there. Do not bypass the sandbox to make a verifier pass: use the harness logs and report the independent-reproduction limitation.

## Gates and integration

`checks_repeat = N` at plan/default/task level repeats every configured worktree check. All repetitions must succeed; a failure is retained even if a later repetition passes. Choose repetitions explicitly for timing-dependent tests.

`post_merge_checks = ["py smoke_test.py"]` runs on the lead's machine after a successful merge, in the main repository. These are explicit host commands, outside the Codex sandbox; choose commands whose effects you intend. They are process-tree bounded to 15 minutes each. Failure exits 2 and preserves merged commits, with results in state.json and post-merge-checks.log. Fix the integration and retry merge to rerun failed smoke checks.

Use real binaries and entry points at least once from a foreign cwd. Exercise the target locale, path format, hook configuration and supported runtime; mocks of those behaviors cannot establish integration correctness. Keep external live checks optional when they require credentials, deliver messages or otherwise have effects outside the local machine.

Every passing worktree gate records its commit, base and specification fingerprint. Merge uses the resolved commit and refuses a moved branch or changed specification, including when the task is named explicitly. Tasks from earlier Orc versions without a recorded gate need rechecking with `steer`. Explicitly naming a failed task still allows deliberate recovery merges. Merge and clean are serialized with other run mutations and repository integration operations.

## Research and accounting

Research defaults to standard: 10 minutes and 40 observed tool calls. Quick is 3 minutes / 12; deep is 30 minutes / 100. Depth instructions guide scope; the deadline and observed tool budget are enforced by the launcher. They are not token or subscription-quota caps. Tool events can arrive after work begins; hitting a limit fails the call and may leave only partial event output rather than a final answer. Cancellation/deadline cleanup owns the complete process tree (Windows jobs; POSIX process groups).

Per-call `.progress.json` files expose elapsed time and command/web/tool counts while a call runs. Stderr emits a heartbeat every 30 seconds. Event files add `orc_received_at` timestamps; these measure event receipt, not exact model-side execution times. `show RUN/TASK --commands` prints completed commands and clipped output; `--events` prints full events.

`report` states its scope. Without session detection it includes project history; use `--since` or explicit run IDs. `--calls` shows individual model/effort, duration, output rate and quota snapshots. Task time sums calls, which overlap under parallelism. Quota snapshots include other sessions' account usage and cannot perfectly attribute consumption. `continues_mode = "summary"` avoids inheriting full thread history but trades away detail; cumulative input totals are not the current context size.
