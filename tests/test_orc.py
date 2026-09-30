"""End-to-end tests for bin/orc against tests/fake_codex.py. Run: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORC = [sys.executable, str(ROOT / "bin" / "orc")]

PLAN = textwrap.dedent('''
    goal = "Demo: add two files and a follow-up"
    risk = "medium"
    context = "Keep it small."
    checks = ["! grep -rq BROKEN --include=*.txt ."]
    max_parallel = 2

    [[task]]
    id = "scout"
    role = "explorer"
    why = "Find where things live"
    brief = "Look around."

    [[task]]
    id = "impl-a"
    role = "implementer"
    after = ["scout"]
    why = "First file"
    brief = "Write the file. FAKE_BREAK_CHECK"
    acceptance = ["file says ok"]
    files = ["impl-a.txt"]

    [[task]]
    id = "impl-b"
    role = "implementer"
    after = ["scout"]
    why = "Second file"
    brief = "Write the other file. FAKE_FAIL_VERIFY_ONCE"
    acceptance = ["file says ok"]
    files = ["impl-b.txt"]

    [[task]]
    id = "impl-c"
    role = "implementer"
    after = ["impl-a"]
    why = "Builds on impl-a"
    brief = "Follow up."
    acceptance = ["file says ok"]
    files = ["impl-c.txt"]
    verify = "checks"
''')


class OrcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, ORC_CODEX_BIN=str(ROOT / "tests" / "fake_codex.py"),
                        CODEX_HOME=str(self.tmp / "codex"), ORC_HOME=str(self.tmp / "orc"))
        (self.tmp / "codex").mkdir()
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.repo, check=True)
        (self.repo / "README").write_text("hi\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.repo, check=True)
        (self.repo / "plan.toml").write_text(PLAN)
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "plan"], cwd=self.repo, check=True)

    def orc(self, *args, stdin=None):
        return subprocess.run(ORC + list(args), cwd=self.repo, env=self.env, text=True, capture_output=True, input=stdin)

    def test_check_renders_and_rejects_overlap(self):
        p = self.orc("check", "plan.toml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| 2 | impl-a | implementer · high", p.stdout)
        bad = PLAN.replace('files = ["impl-b.txt"]', 'files = ["impl-*.txt"]')
        (self.repo / "bad.toml").write_text(bad)
        p = self.orc("check", "bad.toml")
        self.assertEqual(p.returncode, 1)
        self.assertIn("overlapping files", p.stdout)

    def test_full_cycle(self):
        # Claude statusline snapshot + a transcript with two assistant messages (one duplicated line)
        transcript = self.tmp / "t.jsonl"
        msg = {"type": "assistant", "requestId": "r1", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {
            "input_tokens": 100, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 5000, "output_tokens": 200}}}
        msg2 = {"type": "assistant", "requestId": "r2", "message": {"id": "m2", "model": "claude-opus-5-5", "usage": {
            "input_tokens": 50, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 6000, "output_tokens": 300}}}
        transcript.write_text("\n".join(json.dumps(m) for m in (msg, msg, msg2)) + "\n")
        status = {"session_id": "s1", "transcript_path": str(transcript), "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
                  "workspace": {"project_dir": str(self.repo)}, "cost": {"total_cost_usd": 0.42},
                  "context_window": {"used_percentage": 12}, "rate_limits": {"five_hour": {"used_percentage": 20, "resets_at": 1790000000},
                                                                             "seven_day": {"used_percentage": 41, "resets_at": 1790500000}}}
        p = self.orc("statusline", stdin=json.dumps(status))
        self.assertIn("Opus 5.5 · ctx 12% · 5h 20% wk 41%", p.stdout)
        status["rate_limits"]["five_hour"]["used_percentage"] = 26
        self.orc("statusline", stdin=json.dumps(status))

        p = self.orc("run", "plan.toml")
        out = p.stdout
        self.assertEqual(p.returncode, 0, out + p.stderr)
        self.assertIn("✓ scout [explorer·medium] done", out)
        self.assertIn("✓ impl-a [implementer·high] verified", out)   # repaired after failing check
        self.assertIn("✓ impl-b [implementer·high] verified", out)   # repaired after failing verifier
        self.assertIn("✓ impl-c [implementer·high] checks-passed", out)
        self.assertIn("orc merge", out)
        run_dir = next((self.repo / ".orc" / "runs").iterdir())
        state = json.loads((run_dir / "state.json").read_text())
        a = state["tasks"]["impl-a"]
        self.assertEqual([c["name"] for c in a["calls"]], ["worker", "repair1", "verifier2"])
        self.assertEqual([c["name"] for c in state["tasks"]["impl-b"]["calls"]], ["worker", "verifier1", "repair1", "verifier3"])
        # the repair resumed the implementer's own thread; verifiers got fresh threads
        b = state["tasks"]["impl-b"]["calls"]
        self.assertEqual(b[0]["thread"], b[2]["thread"])
        self.assertNotEqual(b[0]["thread"], b[1]["thread"])
        # impl-c branched from impl-a, so it sees impl-a.txt
        self.assertTrue((Path(state["tasks"]["impl-c"]["worktree"]) / "impl-a.txt").exists())
        prompt = (run_dir / "impl-c" / "worker.prompt.md").read_text()
        self.assertIn("## Input from `impl-a`", prompt)
        self.assertIn("Keep it small.", prompt)
        vprompt = (run_dir / "impl-b" / "verifier1.prompt.md").read_text()
        self.assertIn("check them, do not trust them", vprompt)
        self.assertNotIn("fake worker in", vprompt)  # the implementer's own summary is withheld from the verifier

        p = self.orc("merge")
        self.assertIn("merged: impl-a, impl-b, impl-c", p.stdout, p.stderr)
        for f in ("impl-a.txt", "impl-b.txt", "impl-c.txt"):
            self.assertTrue((self.repo / f).exists(), f)

        p = self.orc("report")
        rep = p.stdout
        self.assertIn("| impl-a | implementer · high | verified (merged) | 3 |", rep)
        self.assertIn("**Codex (gpt-6.1-sol, ChatGPT plan):** 9 calls", rep)
        self.assertIn("5h 20% → 26%", rep)                      # Claude window delta
        self.assertIn("13k tokens (11k cache reads, 500 output)", rep)  # dedup of repeated message lines
        self.assertIn("≈ $0.42 API-equivalent", rep)
        self.assertIn("**Total ≈", rep)

    def test_report_without_runs(self):
        p = self.orc("report")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("done by the lead alone", p.stdout)


if __name__ == "__main__":
    unittest.main()
