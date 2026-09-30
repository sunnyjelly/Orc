# Writing briefs

A worker knows only what the brief, the plan `context`, and the repo tell it. Vague briefs cause duplicated work, scope creep and wasted repair rounds. A good brief is short and complete:

1. **Objective**: the outcome, in one or two sentences.
2. **Where**: the files, functions or entry points to start from (`src/api/login.ts:42`), so the worker doesn't rediscover them.
3. **Constraints**: the approach you chose and why; what must not change (public API, schema, other modules); conventions to follow.
4. **Done means**: this goes in `acceptance`, one checkable statement per line. Prefer observable behavior ("returns 429 after 5 failures") over activity ("add rate limiting"). Include "existing tests pass" when relevant.
5. **Out of scope**: what a diligent worker might be tempted to do but shouldn't.
6. **When stuck**: "if X turns out to be true, stop and report it in open_questions" beats a worker guessing.

## Explorer briefs

Ask a question with a clear stopping point, and name the shape of the answer:

> Find every place that reads `user.plan` to decide feature access. For each one, give the file:line, what it gates, and whether it handles `plan == null`. Stop after `src/` (skip tests and scripts). Don't propose fixes.

Split explorers by area (backend / frontend / infra), not by topic, so they don't overlap.

## Implementer example

```toml
[[task]]
id = "login-rate-limit"
role = "implementer"
why = "Self-contained change with a clear test; one worker avoids conflicting edits"
brief = """
Add per-IP rate limiting to POST /login in src/api/login.ts (handler starts at line 40).
Use the existing RateLimiter in src/lib/ratelimit.ts; do not add dependencies.
Limit: 5 failed attempts per IP per 60s; the 6th gets 429 with a Retry-After header.
Successful logins do not count. Keep the handler's response shape unchanged.
Add tests in tests/api/login.test.ts next to the existing ones.
Out of scope: account lockout, CAPTCHA, other endpoints.
If RateLimiter cannot key by IP, stop and report it.
"""
acceptance = [
  "6th failed login from one IP within 60s returns 429 with Retry-After",
  "a successful login does not count toward the limit",
  "new tests cover both cases and pass; existing tests pass",
]
files = ["src/api/login.ts", "tests/api/login.test.ts"]
checks = ["npm test --silent -- tests/api"]
```

## Steering (`orc steer`)

Name the exact problem and the expected result: "The Retry-After header is in seconds but the test expects ms; use seconds and fix the test." Not "please fix the issues".
