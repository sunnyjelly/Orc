# plan.toml format

```toml
goal = "One sentence: the outcome the user asked for"   # required
risk = "medium"            # low | medium | high: sets default verification for implementers
context = """
Decisions every worker must share: conventions, chosen approach, names, things not to touch.
Workers see this, the goal, and a one-line list of the other tasks.
"""
checks = ["npm test --silent", "npm run lint"]   # run in each implementer's worktree after it finishes
max_parallel = 3           # positive integer: concurrent Codex processes (default 3)
quota_stop = 90            # number > 0 and <= 100: stop new calls at this limit % (default 90)
base = "HEAD"              # where worktrees branch from (default HEAD; commit your changes first)

[defaults]                 # optional
model = "gpt-6.1-sol"
implementer_effort = "high"   # also explorer_effort, reviewer_effort
max_repairs = 1               # nonnegative integer: automatic repair rounds
timeout_min = 30              # positive integer
network = false               # let implementers use the network (e.g. package installs)

[[task]]
id = "map-auth"            # lowercase-kebab, unique
role = "explorer"          # explorer | reviewer (read-only) | implementer (writes, in its own worktree)
model = "luna"             # optional: sol (default, gpt-6.1-sol) | luna (gpt-6-luna, cheap: simple scouting only) | full id
web = true                 # optional, explorer/reviewer only: live web search
continues = "20260930-101010/map-auth"   # optional: resume that earlier task's Codex thread; see below
why = "Shown to the user and to other workers: why this task exists and why it is delegated"
brief = """What to find or do. See briefs.md."""

[[task]]
id = "rate-limit"
role = "implementer"
after = ["map-auth"]       # waits for it, and receives its summary and findings as input
why = "..."
brief = """..."""
acceptance = [             # required for implementers: each one checkable by the verifier
  "POST /login returns 429 after 5 failed attempts within 60s from one IP",
  "existing auth tests still pass",
]
files = ["src/auth/**", "tests/auth/**"]   # owned paths; required when implementers could run in parallel
checks = ["pytest tests/auth -q"]          # added to the plan-level checks
effort = "high"
verify = "codex"           # none | checks | codex | codex+adversary (default from risk: low→checks, medium→codex, high→codex+adversary)
verify_effort = "high"     # verifier reasoning effort (the adversary always uses xhigh)
```

Rules `orc check` enforces:
- Implementers need `acceptance`.
- Implementers that could run at the same time need disjoint `files`.
- An implementer may build on at most one other implementer: it branches from that implementer's branch.
- No dependency cycles.

How a task runs:
- **explorer / reviewer**: one read-only Codex call in the repo. The summary includes its findings.
- **implementer**:
  1. Codex works in its own worktree on branch `orc/<run>/<task>`.
  2. orc commits the result and runs the checks.
  3. A fresh read-only verifier checks the acceptance criteria (plus an adversary at `codex+adversary`).
  4. If the checks or the verdict fail, orc resumes the implementer's own thread with the failures, up to `max_repairs` times, then re-checks with a fresh verifier.
- Statuses:
  - `verified`: checks and verifier passed
  - `checks-passed`: `verify = "checks"`
  - `unverified`: no checks and verify none
  - `failed`: checks or verification still fail after the repair rounds
  - `uncertain`: the verifier couldn't decide; the lead decides
  - `blocked`: the worker stopped and asked something (see its open questions); answer with `orc steer`
  - `error`: Codex failed (missing, timed out, no valid output)
  - `stale`: an upstream task was steered after this task ran; merge the updated upstream task and write a new plan for this work. Its old branch is preserved; even an explicit merge cannot bypass stale dependencies.
  - `skipped`: a dependency didn't succeed, or the Codex quota guard stopped it; `orc resume <run>` runs skipped tasks later
- `continues`: the task's first call resumes the Codex thread of `<run>/<task>` from any earlier run, instead of starting cold. The worker keeps its memory of the earlier work. It still gets the full brief, runs in a fresh worktree (or the usual read-only directory), and goes through the normal checks, verification and repairs. The prompt tells it whether its earlier changes are already in the checkout (merged) or not (then it must redo what it still needs). Use it for follow-up work on merged tasks, redoing stale work, or implementing what an explorer worked out. Rules:
  - The thread belongs to the new task from the moment its run starts. Steer, question or continue that task from now on; orc refuses the old one, and any task while it is running.
  - One thread per plan.
  - An implementer can't continue a thread that ever used web search. Keep the same model, so the prompt cache carries over.
- A read-only task that comes after an implementer (directly or through other read-only tasks) runs in that implementer's worktree, so it sees the new code. An implementer that comes after one branches from its branch.
- A failed worker, repair, or steer cannot pass on the strength of final JSON alone. Partial implementation work is committed for recovery. A verifier execution error produces `uncertain`, even if its final JSON says `pass`.
- `resume` reuses a quota-skipped implementer's existing worktree and call history. Partial merges retain branches and worktrees while unfinished tasks still need them. Merge refuses to run during another Git operation and leaves dirty worktrees available for inspection.
- Checks are POSIX shell (Git Bash on Windows). They run on your machine, outside Codex's sandbox, on code a worker wrote. Only use `checks` you would run yourself on an untrusted branch.
