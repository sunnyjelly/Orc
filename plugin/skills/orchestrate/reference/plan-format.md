# plan.toml format

```toml
goal = "One sentence: the outcome the user asked for"   # required
risk = "medium"            # low | medium | high: sets default verification for implementers
context = """
Decisions every worker must share: conventions, chosen approach, names, things not to touch.
Workers see this, the goal, and a one-line list of the other tasks.
"""
checks = ["npm test --silent", "npm run lint"]   # run in each implementer's worktree after it finishes
max_parallel = 3           # concurrent Codex processes (default 3)
quota_stop = 90            # don't start new Codex calls once a Codex limit window is at this % (default 90)
base = "HEAD"              # where worktrees branch from (default HEAD; commit your changes first)

[defaults]                 # optional
model = "gpt-6.1-sol"
implementer_effort = "high"   # also explorer_effort, reviewer_effort
max_repairs = 1               # automatic repair rounds after failed checks or verification
timeout_min = 30
network = false               # let implementers use the network (e.g. package installs)

[[task]]
id = "map-auth"            # lowercase-kebab, unique
role = "explorer"          # explorer | reviewer (read-only) | implementer (writes, in its own worktree)
model = "luna"             # optional: sol (default, gpt-6.1-sol) | luna (gpt-6-luna, cheap: simple scouting only) | full id
web = true                 # optional, explorer/reviewer only: live web search
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
  - `skipped`: a dependency didn't succeed, or the Codex quota guard stopped it; `orc resume <run>` runs skipped tasks later
- A read-only task that comes after an implementer (directly or through other read-only tasks) runs in that implementer's worktree, so it sees the new code. An implementer that comes after one branches from its branch.
- Checks run on your machine, outside Codex's sandbox, on code a worker wrote. Only use `checks` you would run yourself on an untrusted branch.
