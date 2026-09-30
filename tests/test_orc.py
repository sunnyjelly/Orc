"""End-to-end tests for bin/orc against tests/fake_codex.py. Run: python3 -m unittest discover tests"""
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORC = [sys.executable, str(ROOT / "plugin" / "skills" / "orchestrate" / "scripts" / "orc")]

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
        self.assertIn("| 2 | impl-a | implementer · sol high", p.stdout)
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

    def test_ask_and_research(self):
        self.orc("statusline", stdin=json.dumps({"session_id": "s2", "workspace": {"project_dir": str(self.repo)}}))
        p = self.orc("ask", "is this design sound?")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("fake answer (model=gpt-6.1-sol effort=high web=False)", p.stdout)  # Sol at high by default
        self.assertIn("— sol · high ·", p.stdout)
        p = self.orc("ask", "--model", "luna", "--effort", "xhigh", "--web", "quick lookup?")
        self.assertIn("model=gpt-6-luna effort=xhigh web=True", p.stdout)
        p = self.orc("ask", "--research", "what changed in X?")
        self.assertIn("- fake research finding (model=gpt-6.1-sol effort=high web=True)", p.stdout)
        self.assertNotIn("long details", p.stdout)  # only the summary reaches the lead
        report_path = p.stdout.split("Full report: ")[1].splitlines()[0]
        self.assertIn("long details", Path(report_path).read_text())
        rep = self.orc("report").stdout
        self.assertIn("| ask ×1 | sol · high | 1 ok | 1 |", rep)
        self.assertIn("| ask ×1 | luna · xhigh | 1 ok | 1 |", rep)
        self.assertIn("| research ×1 | sol · high | 1 ok | 1 |", rep)
        self.assertIn("gpt-6-luna, gpt-6.1-sol, ChatGPT plan):** 3 calls", rep)

    def test_plan_model_alias_and_web(self):
        plan = PLAN.replace('brief = "Look around."', 'brief = "Look around."\nmodel = "luna"\nweb = true')
        (self.repo / "p2.toml").write_text(plan)
        p = self.orc("check", "p2.toml")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("| 1 | scout | explorer · luna medium · web |", p.stdout)
        bad = PLAN.replace('files = ["impl-c.txt"]', 'files = ["impl-c.txt"]\nweb = true')
        (self.repo / "p3.toml").write_text(bad)
        self.assertIn("`web` is for explorer/reviewer tasks", self.orc("check", "p3.toml").stdout)

    # ---- regression tests for the independent audit ------------------------------------------

    def plan(self, name, body):
        (self.repo / name).write_text(textwrap.dedent(body))
        return name

    def state(self):
        run_dir = sorted((self.repo / ".orc" / "runs").iterdir())[-1]
        return run_dir.name, json.loads((run_dir / "state.json").read_text())

    def test_steer_is_saved_then_resume_and_merge(self):
        self.plan("s.toml", """
            goal = "steer"
            checks = ["! grep -rq BROKEN --include=*.txt ."]
            [[task]]
            id = "base"
            role = "implementer"
            why = "w"
            brief = "FAKE_BREAK_UNTIL_STEER"
            acceptance = ["ok"]
            verify = "checks"
            [[task]]
            id = "child"
            role = "implementer"
            after = ["base"]
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
        """)
        self.assertEqual(self.orc("run", "s.toml").returncode, 2)
        run_id, st = self.state()
        self.assertEqual((st["tasks"]["base"]["status"], st["tasks"]["child"]["status"]), ("failed", "skipped"))
        p = self.orc("steer", run_id, "base", "STEER_FIX the check")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"orc resume {run_id}", p.stdout)
        _, st = self.state()
        self.assertEqual(st["tasks"]["base"]["status"], "checks-passed")          # persisted (audit #1)
        self.assertEqual([c["name"] for c in st["tasks"]["base"]["calls"]], ["worker", "repair1", "steer2"])
        p = self.orc("resume", run_id)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        _, st = self.state()
        self.assertEqual(st["tasks"]["child"]["status"], "checks-passed")
        self.assertTrue((Path(st["tasks"]["child"]["worktree"]) / "base.txt").exists())
        p = self.orc("merge", run_id)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("merged: base, child", p.stdout)
        self.assertIn("ok", (self.repo / "base.txt").read_text())

    def test_reviewer_after_implementer_sees_its_code(self):
        self.plan("r.toml", """
            goal = "chain"
            checks = ["true"]
            [[task]]
            id = "impl-a"
            role = "implementer"
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
            [[task]]
            id = "rev"
            role = "reviewer"
            after = ["impl-a"]
            why = "w"
            brief = "review impl-a"
            [[task]]
            id = "fix"
            role = "implementer"
            after = ["rev"]
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
        """)
        p = self.orc("run", "r.toml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        _, st = self.state()
        self.assertEqual(st["tasks"]["rev"]["result"]["summary"], "fake worker in impl-a")   # audit #2
        self.assertTrue((Path(st["tasks"]["fix"]["worktree"]) / "impl-a.txt").exists())
        self.assertEqual(st["tasks"]["fix"]["parent"], "impl-a")

    def test_blocked_worker_and_check_artifacts(self):
        self.plan("b.toml", """
            goal = "blocked"
            checks = ["touch artifact.log", "! grep -rq BROKEN --include=*.txt ."]
            [[task]]
            id = "stuck"
            role = "implementer"
            why = "w"
            brief = "FAKE_BLOCKED"
            acceptance = ["ok"]
            files = ["stuck.txt"]
            [[task]]
            id = "fine"
            role = "implementer"
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            files = ["fine.txt"]
            verify = "checks"
        """)
        self.assertEqual(self.orc("run", "b.toml").returncode, 2)
        run_id, st = self.state()
        self.assertEqual(st["tasks"]["stuck"]["status"], "blocked")                    # audit #10
        self.assertEqual(len(st["tasks"]["stuck"]["calls"]), 1)                         # no checks/verify/repair
        self.assertEqual(self.orc("merge", run_id).returncode, 0)
        self.assertTrue((self.repo / "fine.txt").exists())
        self.assertFalse((self.repo / "artifact.log").exists())                         # audit #9

    def test_merge_conflict_fails(self):
        self.plan("c.toml", """
            goal = "conflict"
            checks = ["true"]
            [[task]]
            id = "impl-a"
            role = "implementer"
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
        """)
        self.assertEqual(self.orc("run", "c.toml").returncode, 0)
        (self.repo / "impl-a.txt").write_text("main's version\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "conflicting"], cwd=self.repo, check=True)
        p = self.orc("merge")
        self.assertEqual(p.returncode, 1)                                               # audit #5
        self.assertIn("merge conflict", p.stdout)

    def test_quota_stop_ignores_reset_windows(self):
        limits = Path(self.env["ORC_HOME"]) / "codex-limits.json"
        limits.parent.mkdir(parents=True, exist_ok=True)
        window = lambda resets: {"rate_limits": {"primary": {"used_percent": 95, "window_minutes": 300, "resets_at": resets}}}
        limits.write_text(json.dumps(window(1)))                                      # reset long ago
        self.assertEqual(self.orc("ask", "q").returncode, 0)                            # audit #3
        limits.write_text(json.dumps(window(4102444800)))                             # resets in 2100
        p = self.orc("ask", "q")
        self.assertEqual(p.returncode, 1)
        self.assertIn("skipped: Codex 5h limit at 95%", p.stdout)

    def test_report_for_solo_session_ignores_older_runs(self):
        status = lambda sid: json.dumps({"session_id": sid, "workspace": {"project_dir": str(self.repo)}})
        self.orc("statusline", stdin=status("old"))
        self.assertEqual(self.orc("run", "plan.toml").returncode, 0)
        run_id, _ = self.state()
        self.assertRegex(run_id, r"^\d{8}-\d{6}")                                     # audit #8: year in run id
        self.orc("statusline", stdin=status("new"))
        self.assertIn("done by the lead alone", self.orc("report").stdout)             # audit #4

    def test_report_ignores_other_projects_session(self):
        other = {"session_id": "elsewhere", "workspace": {"project_dir": "/some/other/project"}, "cost": {"total_cost_usd": 9}}
        self.orc("statusline", stdin=json.dumps(other))
        self.assertIn("no statusline snapshot found", self.orc("report").stdout)       # audit #12

    def test_setup_installs_copy_and_statusline(self):
        cfg = self.tmp / "claude-config"
        self.env["CLAUDE_CONFIG_DIR"] = str(cfg)
        cfg.mkdir()
        (cfg / "settings.json").write_text(json.dumps({"model": "opus", "statusLine": {"type": "command", "command": "mine.sh"}}))
        p = self.orc("setup")
        self.assertIn("left unchanged", p.stdout)                           # never clobbers a user's status line
        self.assertEqual(json.loads((cfg / "settings.json").read_text())["statusLine"]["command"], "mine.sh")
        self.orc("setup", "--force")
        settings = json.loads((cfg / "settings.json").read_text())
        self.assertEqual(settings["model"], "opus")                          # other settings kept
        app = Path(self.env["ORC_HOME"]) / "app" / "orc"
        self.assertIn(str(app), settings["statusLine"]["command"])
        p = subprocess.run([sys.executable, str(app), "statusline"], input=json.dumps({"model": {"display_name": "Opus"}}),
                           text=True, capture_output=True, env=self.env)
        self.assertEqual(p.stdout.strip(), "Opus")

    def test_report_without_runs(self):
        p = self.orc("report")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("done by the lead alone", p.stdout)


class PackagingTest(unittest.TestCase):
    def test_dist_skills_match_sources(self):
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "build_skills.py"), "--check"], text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_plugin_layout(self):
        manifest = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(market["plugins"][0]["name"], manifest["name"])
        self.assertTrue((ROOT / market["plugins"][0]["source"] / "skills" / "orchestrate" / "SKILL.md").exists())
        skill = (ROOT / "plugin" / "skills" / "orchestrate" / "SKILL.md").read_text()
        self.assertIn("allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/orc *)", skill)


if __name__ == "__main__":
    unittest.main()
