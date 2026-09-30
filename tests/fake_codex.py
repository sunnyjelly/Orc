#!/usr/bin/env python3
"""Stand-in for `codex exec` used by the tests.

Emits the JSONL events orc parses, writes the -o final message as schema-shaped
JSON, appends a rollout file with rate limits under $CODEX_HOME, and (for
workspace-write runs) creates a file. Behaviour is steered by the prompt text:
  FAKE_FAIL_VERIFY_ONCE  -> the first verifier returns "fail"
  FAKE_BREAK_CHECK       -> the implementer writes a file the checks reject until repaired
"""
import fcntl
import json
import os
import sys
import uuid
from pathlib import Path

args = sys.argv[1:]
if args and args[0] == "--version":
    print("codex-cli 0.0.0-fake")
    sys.exit(0)
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT (fake)")
    sys.exit(0)

def opt(name):
    return args[args.index(name) + 1] if name in args else None

out, schema, sandbox, cwd = opt("-o"), opt("--output-schema"), opt("-s"), Path(opt("-C") or ".")
resume = args[args.index("resume") + 1] if "resume" in args else None
prompt = sys.stdin.read()
if resume and cwd.name == "impl-a":
    import time
    time.sleep(1.5)  # keep impl-a mid-repair while impl-b finishes (scheduler regression test)
thread = resume or str(uuid.uuid4())
home = Path(os.environ["CODEX_HOME"])
state = home / "fake-state.json"
lock = open(home / "fake.lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)  # parallel fake workers share the state file
st = json.loads(state.read_text()) if state.exists() else {"calls": 0, "verifier_failed": False}
st["calls"] += 1

print(json.dumps({"type": "thread.started", "thread_id": thread}))
print(json.dumps({"type": "turn.started"}))

if schema is None:  # orc ask: plain-text answer that echoes how it was called
    model = opt("-m")
    effort = next(a.split("=", 1)[1].strip('"') for a in args if a.startswith("model_reasoning_effort="))
    web = 'web_search="live"' in args
    info = f"model={model} effort={effort} web={web}"
    if "## Disagreements and uncertainty" in prompt:
        final = f"## Summary\n- fake research finding ({info})\n\n## Details\nlong details\n\n## Sources\n1. https://example.com\n"
    else:
        final = f"fake answer ({info})"
    Path(out).write_text(final)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5000, "cached_input_tokens": 1000,
                                                           "output_tokens": 500, "reasoning_output_tokens": 200}}))
    state.write_text(json.dumps(st))
    sys.exit(0)

if schema.endswith("verdict.schema.json"):
    verdict = "pass"
    if "FAKE_FAIL_VERIFY_ONCE" in prompt and not st["verifier_failed"]:
        st["verifier_failed"] = True
        verdict = "fail"
    final = {"verdict": verdict, "summary": f"fake {verdict}",
             "criteria": [{"criterion": "works", "met": "yes" if verdict == "pass" else "no", "evidence": "fake.txt:1"}],
             "issues": [] if verdict == "pass" else [{"severity": "major", "location": "fake.txt:1", "problem": "fake problem"}]}
else:
    changes = []
    if sandbox == "workspace-write":
        target = cwd / f"{cwd.name}.txt"
        broken = "FAKE_BREAK_CHECK" in prompt and not resume
        target.write_text(("BROKEN" if broken else "ok") + f" {st['calls']}\n")
        changes = [{"path": target.name, "what": "wrote fake output"}]
    final = {"status": "done", "summary": f"fake worker in {cwd.name}", "findings": ["fake finding at a.py:1"],
             "changes": changes, "claims": ["fake.txt exists"], "commands_run": [{"cmd": "true", "exit_code": 0}],
             "open_questions": []}

Path(out).write_text(json.dumps(final))
print(json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": json.dumps(final)}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10000, "cached_input_tokens": 6000,
                                                       "output_tokens": 1000, "reasoning_output_tokens": 400}}))
day = home / "sessions" / "2026" / "09" / "30"
day.mkdir(parents=True, exist_ok=True)
with open(day / f"rollout-2026-09-30T10-00-00-{thread}.jsonl", "a") as f:
    f.write(json.dumps({"timestamp": "2026-09-30T10:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {"total_tokens": 11000}},
        "rate_limits": {"primary": {"used_percent": 10 + st["calls"], "window_minutes": 300, "resets_at": 1790000000},
                        "secondary": {"used_percent": 40 + st["calls"] / 10, "window_minutes": 10080, "resets_at": 1790500000}}}}) + "\n")
state.write_text(json.dumps(st))
