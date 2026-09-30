"""End-to-end tests for bin/orc against tests/fake_codex.py. Run: python3 -m unittest discover tests"""
import json
import copy
import io
import os
import re
import runpy
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
ORC = [sys.executable, str(ROOT / "plugin" / "skills" / "orchestrate" / "scripts" / "orc")]


def codex_bin(script: Path) -> str:
    """ORC_CODEX_BIN for a Python stand-in. Windows can't exec a .py, so wrap it in a .cmd, like npm's codex.cmd."""
    if os.name != "nt":
        return str(script)
    cmd = Path(tempfile.gettempdir()) / f"{script.stem}.{os.getpid()}.cmd"
    cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return str(cmd)

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
        self.env = dict(os.environ, ORC_CODEX_BIN=codex_bin(ROOT / "tests" / "fake_codex.py"),
                        CODEX_HOME=str(self.tmp / "codex"), ORC_HOME=str(self.tmp / "orc"))
        (self.tmp / "codex").mkdir()
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.repo, check=True)
        (self.repo / "README").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.repo, check=True)
        (self.repo / "plan.toml").write_text(PLAN, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "plan"], cwd=self.repo, check=True)

    def orc(self, *args, stdin=None):
        return subprocess.run(ORC + list(args), cwd=self.repo, env=self.env, text=True, encoding="utf-8", errors="replace", capture_output=True, input=stdin)

    def test_check_renders_and_rejects_overlap(self):
        p = self.orc("check", "plan.toml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| 2 | impl-a | implementer · sol high", p.stdout)
        bad = PLAN.replace('files = ["impl-b.txt"]', 'files = ["impl-*.txt"]')
        (self.repo / "bad.toml").write_text(bad, encoding="utf-8")
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
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        a = state["tasks"]["impl-a"]
        self.assertEqual([c["name"] for c in a["calls"]], ["worker", "repair1", "verifier2"])
        self.assertEqual([c["name"] for c in state["tasks"]["impl-b"]["calls"]], ["worker", "verifier1", "repair1", "verifier3"])
        # the repair resumed the implementer's own thread; verifiers got fresh threads
        b = state["tasks"]["impl-b"]["calls"]
        self.assertEqual(b[0]["thread"], b[2]["thread"])
        self.assertNotEqual(b[0]["thread"], b[1]["thread"])
        # impl-c branched from impl-a, so it sees impl-a.txt
        self.assertTrue((Path(state["tasks"]["impl-c"]["worktree"]) / "impl-a.txt").exists())
        prompt = (run_dir / "impl-c" / "worker.prompt.md").read_text(encoding="utf-8")
        self.assertIn("## Input from `impl-a`", prompt)
        self.assertIn("Keep it small.", prompt)
        vprompt = (run_dir / "impl-b" / "verifier1.prompt.md").read_text(encoding="utf-8")
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
        self.assertIn("long details", Path(report_path).read_text(encoding="utf-8"))
        rep = self.orc("report").stdout
        self.assertIn("| ask ×1 | sol · high | 1 ok | 1 |", rep)
        self.assertIn("| ask ×1 | luna · xhigh | 1 ok | 1 |", rep)
        self.assertIn("| research ×1 | sol · high | 1 ok | 1 |", rep)
        self.assertIn("gpt-6-luna, gpt-6.1-sol, ChatGPT plan):** 3 calls", rep)

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher boundary")
    def test_batch_launcher_preserves_shell_characters_in_worker_argv(self):
        renamed = self.repo.with_name("repo&ver&rem%OS%!")
        renamed.resolve().relative_to(self.tmp.resolve())
        self.repo.rename(renamed)
        self.repo = renamed
        model = "luna&ver&rem%OS%!"
        p = self.orc("ask", "--model", model, "unicode question: Καλημέρα")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"model={model} effort=high web=False", p.stdout)
        calls = json.loads((self.tmp / "codex" / "fake-state.json").read_text(encoding="utf-8"))["invocations"]
        self.assertEqual(calls[-1]["cwd"], str(self.repo))
        self.assertEqual(calls[-1]["sandbox"], "read-only")

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher boundary")
    def test_batch_launcher_roundtrips_quotes_and_escaped_paths(self):
        mod = runpy.run_path(ORC[1], run_name="argv_test")
        directory = self.tmp / "launcher & space %OS%!"
        directory.mkdir()
        script = directory / "echo.py"
        script.write_text("import json, sys; print(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        shim = directory / "echo.cmd"
        shim.write_text(f'@"{sys.executable}" "%~dp0echo.py" %*\n', encoding="utf-8")
        args = ["", 'model_reasoning_effort="high"', "a&ver&rem", "spaces and \"quotes\"", "end\\",
                "%OS%", "!OS!", "^caret", "(parentheses)", "<in>|out", "Καλημέρα"]
        p = mod["run_argv"]([str(shim), *args], text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout), args)
        for bad in ("line\nbreak", "line\rbreak", "nul\0byte"):
            with self.assertRaises(ValueError):
                mod["run_argv"]([str(shim), bad])

    def test_main_accepts_redirected_text_streams(self):
        mod = runpy.run_path(ORC[1], run_name="main_test")
        with patch.object(sys, "stdout", io.StringIO()) as out, patch.object(sys, "stderr", io.StringIO()), \
                patch.object(sys, "stdin", io.StringIO("{}")), patch.dict(os.environ, self.env):
            mod["main"].__globals__["ORC_HOME"] = self.tmp / "orc"
            self.assertEqual(mod["main"](["statusline"]), 0)
            self.assertIn("Claude", out.getvalue())

    def test_invalid_utf8_records_and_plans_fail_safely(self):
        (self.repo / "bad.toml").write_bytes(b'goal = "\xff"\n')
        p = self.orc("check", "bad.toml")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("cannot read plan", p.stdout)
        self.assertNotIn("Traceback", p.stderr)
        mod = runpy.run_path(ORC[1], run_name="utf8_test")
        self.assertEqual(mod["plan_task"](self.repo, "a"), {})
        (self.repo / "plan.toml").write_bytes(b'goal = "\xff"\n')
        self.assertIsNone(mod["plan_task"](self.repo, "a"))
        asks = self.repo / ".orc" / "asks"
        asks.mkdir(parents=True)
        (asks / "asks.jsonl").write_bytes(b'\xff\n{"id":"readable", "question":"still here"}\n')
        p = self.orc("show")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("still here", p.stdout)
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.write_bytes(b'# legacy comment: \xff\n')
        mod["runs_dir"](self.repo)
        self.assertTrue(exclude.read_bytes().startswith(b'# legacy comment: \xff\n'))
        self.assertIn(b".orc/", exclude.read_bytes())

    def test_plan_model_alias_and_web(self):
        plan = PLAN.replace('brief = "Look around."', 'brief = "Look around."\nmodel = "luna"\nweb = true')
        (self.repo / "p2.toml").write_text(plan, encoding="utf-8")
        p = self.orc("check", "p2.toml")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("| 1 | scout | explorer · luna medium · web |", p.stdout)
        bad = PLAN.replace('files = ["impl-c.txt"]', 'files = ["impl-c.txt"]\nweb = true')
        (self.repo / "p3.toml").write_text(bad, encoding="utf-8")
        self.assertIn("`web` is for explorer/reviewer tasks", self.orc("check", "p3.toml").stdout)

    # ---- regression tests for the independent audit ------------------------------------------

    def plan(self, name, body):
        (self.repo / name).write_text(textwrap.dedent(body), encoding="utf-8")
        return name

    def state(self):
        run_dir = sorted((self.repo / ".orc" / "runs").iterdir())[-1]
        return run_dir.name, json.loads((run_dir / "state.json").read_text(encoding="utf-8"))

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
        self.assertIn("ok", (self.repo / "base.txt").read_text(encoding="utf-8"))

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
        (self.repo / "impl-a.txt").write_text("main's version\n", encoding="utf-8")
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

    def test_limits_found_despite_old_top_level_rollouts(self):
        self.assertEqual(self.orc("run", "plan.toml").returncode, 0)                    # fake writes dated rollouts
        (self.tmp / "codex" / "sessions" / "rollout-2025-08-07-old.json").write_text("{}", encoding="utf-8")
        p = self.orc("doctor")
        self.assertIn("Codex limits:", p.stdout)
        self.assertNotIn("limits n/a", p.stdout)

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
        (cfg / "settings.json").write_text(json.dumps({"model": "opus", "statusLine": {"type": "command", "command": "mine.sh"}}), encoding="utf-8")
        p = self.orc("setup")
        self.assertIn("left unchanged", p.stdout)                           # never clobbers a user's status line
        self.assertEqual(json.loads((cfg / "settings.json").read_text(encoding="utf-8"))["statusLine"]["command"], "mine.sh")
        self.orc("setup", "--force")
        settings = json.loads((cfg / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["model"], "opus")                          # other settings kept
        app = Path(self.env["ORC_HOME"]) / "app" / "orc"
        self.assertIn(app.as_posix(), settings["statusLine"]["command"])
        p = subprocess.run([sys.executable, str(app), "statusline"], input=json.dumps({"model": {"display_name": "Opus"}}),
                           text=True, encoding="utf-8", errors="replace", capture_output=True, env=self.env)
        self.assertEqual(p.stdout.strip(), "Opus")

    def test_report_without_runs(self):
        p = self.orc("report")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("done by the lead alone", p.stdout)


    # Regression tests added before fixing the nine repository-review findings.
    def review_plan(self, *, child=False, verify="checks", brief="b", extra=""):
        body = f'''
            goal = "review regressions"
            checks = ["! grep -rq BROKEN --include=*.txt ."]
            {extra}
            [[task]]
            id = "base"
            role = "implementer"
            why = "regression"
            brief = "{brief}"
            acceptance = ["ok"]
            verify = "{verify}"
        '''
        if child:
            body += '''
                [[task]]
                id = "child"
                role = "implementer"
                after = ["base"]
                why = "regression"
                brief = "b"
                acceptance = ["ok"]
                verify = "checks"
            '''
        return self.plan("review.toml", body)

    def fake_script(self, body):
        script = self.tmp / "wrapper.py"
        script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
        script.chmod(0o755)
        self.env["ORC_CODEX_BIN"] = codex_bin(script)

    def test_failed_steer_preserves_partial_work_and_revokes_success(self):
        self.assertEqual(self.orc("run", self.review_plan()).returncode, 0)
        rid, st = self.state()
        wt = Path(st["tasks"]["base"]["worktree"])
        self.fake_script('''
            import sys
            from pathlib import Path
            args = sys.argv
            cwd = Path(args[args.index("-C") + 1])
            (cwd / "unfinished.txt").write_text("valuable partial work", encoding="utf-8")
            sys.exit(1)
        ''')
        p = self.orc("steer", rid, "base", "fix something")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        _, st = self.state()
        r = st["tasks"]["base"]
        self.assertEqual(r["status"], "error")
        saved = subprocess.check_output(["git", "show", f"{r['branch']}:unfinished.txt"], cwd=self.repo, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(saved, "valuable partial work")
        self.assertEqual(self.orc("merge", rid).returncode, 0)
        self.assertTrue(wt.exists())
        self.assertFalse(self.state()[1]["tasks"]["base"].get("merged"))
        self.env["ORC_CODEX_BIN"] = codex_bin(ROOT / "tests" / "fake_codex.py")
        self.assertEqual(self.orc("steer", rid, "base", "finish the work").returncode, 0)
        self.assertEqual(self.orc("merge", rid).returncode, 0)
        self.assertEqual((self.repo / "unfinished.txt").read_text(encoding="utf-8"), "valuable partial work")

    def test_merge_preserves_an_existing_merge_and_manual_resolution(self):
        self.assertEqual(self.orc("run", self.review_plan()).returncode, 0)
        rid, _ = self.state()
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.repo, text=True, encoding="utf-8", errors="replace", capture_output=True)
        self.assertEqual(git("checkout", "-qb", "other").returncode, 0)
        (self.repo / "README").write_text("other branch\n", encoding="utf-8")
        self.assertEqual(git("add", "README").returncode, 0)
        self.assertEqual(git("commit", "-qm", "other version").returncode, 0)
        self.assertEqual(git("checkout", "-q", "main").returncode, 0)
        (self.repo / "README").write_text("main branch\n", encoding="utf-8")
        self.assertEqual(git("add", "README").returncode, 0)
        self.assertEqual(git("commit", "-qm", "main version").returncode, 0)
        self.assertEqual(git("merge", "other").returncode, 1)
        resolution = "valuable in-progress manual resolution\n"
        (self.repo / "README").write_text(resolution, encoding="utf-8")
        merge_head = (self.repo / ".git" / "MERGE_HEAD").read_text(encoding="utf-8")
        p = self.orc("merge", rid)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual((self.repo / "README").read_text(encoding="utf-8"), resolution)
        self.assertEqual((self.repo / ".git" / "MERGE_HEAD").read_text(encoding="utf-8"), merge_head)

    def test_steering_parent_invalidates_descendants_and_keeps_new_commits(self):
        self.assertEqual(self.orc("run", self.review_plan(child=True)).returncode, 0)
        rid, st = self.state()
        branch = st["tasks"]["base"]["branch"]
        wt = Path(st["tasks"]["base"]["worktree"])
        self.assertEqual(self.orc("steer", rid, "base", "change base").returncode, 0)
        updated = (wt / "base.txt").read_text(encoding="utf-8")
        _, st = self.state()
        self.assertEqual(st["tasks"]["child"]["status"], "stale")
        p = self.orc("merge", rid, "child")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertFalse(self.state()[1]["tasks"]["base"].get("merged"))
        self.assertTrue(wt.exists())
        self.assertEqual(subprocess.check_output(["git", "show", f"{branch}:base.txt"], cwd=self.repo, text=True, encoding="utf-8", errors="replace"), updated)
        self.assertEqual(self.orc("merge", rid).returncode, 0)
        self.assertEqual((self.repo / "base.txt").read_text(encoding="utf-8"), updated)
        self.assertFalse((self.repo / "child.txt").exists())

    def test_steering_invalidates_transitive_readers_and_writers(self):
        plan = self.review_plan(child=True)
        body = (self.repo / plan).read_text(encoding="utf-8") + '''
            [[task]]
            id = "review"
            role = "reviewer"
            after = ["child"]
            why = "review child"
            brief = "review"
        '''
        (self.repo / plan).write_text(body, encoding="utf-8")
        self.assertEqual(self.orc("run", plan).returncode, 0)
        rid, _ = self.state()
        self.assertEqual(self.orc("steer", rid, "base", "change base").returncode, 0)
        _, st = self.state()
        self.assertEqual(st["tasks"]["child"]["status"], "stale")
        self.assertEqual(st["tasks"]["review"]["status"], "stale")
        self.assertNotEqual(self.orc("steer", rid, "child", "continue").returncode, 0)

    def test_merge_checks_parent_ancestry_even_without_a_stale_status(self):
        self.assertEqual(self.orc("run", self.review_plan(child=True)).returncode, 0)
        rid, st = self.state()
        wt = Path(st["tasks"]["base"]["worktree"])
        (wt / "new-parent.txt").write_text("new parent code", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
        subprocess.run(["git", "commit", "-qm", "parent changed outside orc"], cwd=wt, check=True)
        p = self.orc("merge", rid, "child")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertTrue(wt.exists())
        self.assertFalse(self.state()[1]["tasks"]["base"].get("merged"))

    def test_setup_preserves_malformed_settings_even_with_force(self):
        cfg = self.tmp / "claude-config"
        cfg.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(cfg)
        settings = cfg / "settings.json"
        malformed = '{"model":"opus","permissions":{"deny":["Bash(rm *)"]},'
        settings.write_text(malformed, encoding="utf-8")
        for args in ((), ("--force",)):
            with self.subTest(args=args):
                settings.write_text(malformed, encoding="utf-8")
                p = self.orc("setup", *args)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertEqual(settings.read_text(encoding="utf-8"), malformed)

    def error_after_final(self, predicate):
        self.fake_script(f'''
            import json, runpy, sys
            runpy.run_path({str(ROOT / "tests" / "fake_codex.py")!r}, run_name="__main__")
            if {predicate}:
                print(json.dumps({{"type": "turn.failed", "error": {{"message": "failure after final"}}}}))
                sys.exit(1)
        ''')

    def test_worker_error_with_final_json_does_not_pass(self):
        plan = self.review_plan()
        self.error_after_final("True")
        p = self.orc("run", plan)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        _, st = self.state()
        self.assertEqual(st["tasks"]["base"]["status"], "error")
        self.assertEqual(len(st["tasks"]["base"]["calls"]), 1)

    def test_readonly_error_with_final_json_does_not_pass(self):
        plan = self.plan("reader.toml", '''
            goal = "read"
            [[task]]
            id = "read"
            role = "reviewer"
            why = "read"
            brief = "read"
        ''')
        self.error_after_final("True")
        p = self.orc("run", plan)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(self.state()[1]["tasks"]["read"]["status"], "error")

    def test_verifier_error_with_pass_json_does_not_verify(self):
        plan = self.review_plan(verify="codex")
        self.error_after_final("'verdict.schema.json' in sys.argv[sys.argv.index('--output-schema') + 1]")
        p = self.orc("run", plan)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        r = self.state()[1]["tasks"]["base"]
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["verdicts"][0]["verdict"], "uncertain")
        self.assertEqual(self.orc("merge").returncode, 0)
        self.assertFalse((self.repo / "base.txt").exists())

    def test_repair_error_with_final_json_does_not_pass(self):
        plan = self.review_plan(brief="FAKE_BREAK_CHECK")
        self.error_after_final("'resume' in sys.argv")
        p = self.orc("run", plan)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(self.state()[1]["tasks"]["base"]["status"], "error")

    def test_steer_error_with_final_json_does_not_pass(self):
        self.assertEqual(self.orc("run", self.review_plan()).returncode, 0)
        rid, _ = self.state()
        self.error_after_final("'resume' in sys.argv")
        p = self.orc("steer", rid, "base", "change base")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(self.state()[1]["tasks"]["base"]["status"], "error")

    def test_quota_skipped_implementer_can_resume_with_existing_worktree(self):
        plan = self.review_plan()
        limits = Path(self.env["ORC_HOME"]) / "codex-limits.json"
        limits.parent.mkdir(parents=True)
        limits.write_text(json.dumps({"rate_limits": {"primary": {
            "used_percent": 95, "window_minutes": 300, "resets_at": 4102444800}}}), encoding="utf-8")
        self.assertEqual(self.orc("run", plan).returncode, 2)
        rid, st = self.state()
        self.assertEqual(st["tasks"]["base"]["status"], "skipped")
        limits.write_text("{}", encoding="utf-8")
        p = self.orc("resume", rid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.state()[1]["tasks"]["base"]["status"], "checks-passed")
        self.assertEqual(self.orc("merge", rid).returncode, 0)
        self.assertTrue((self.repo / "base.txt").exists())

    def test_partial_merge_keeps_resources_for_pending_child_and_reader(self):
        plan = self.review_plan(child=True)
        body = (self.repo / plan).read_text(encoding="utf-8") + '''
            [[task]]
            id = "review"
            role = "reviewer"
            after = ["base"]
            why = "read base"
            brief = "read base"
        '''
        (self.repo / plan).write_text(body, encoding="utf-8")
        self.assertEqual(self.orc("run", plan, "--only", "base").returncode, 0)
        rid, st = self.state()
        wt = Path(st["tasks"]["base"]["worktree"])
        self.assertEqual(self.orc("merge", rid, "base").returncode, 0)
        self.assertTrue(wt.exists())
        p = self.orc("resume", rid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        _, st = self.state()
        self.assertEqual(st["tasks"]["review"]["result"]["summary"], "fake worker in base")
        self.assertTrue((Path(st["tasks"]["child"]["worktree"]) / "base.txt").exists())
        self.assertEqual(self.orc("merge", rid).returncode, 0)
        self.assertFalse(wt.exists())
        self.assertTrue((self.repo / "child.txt").exists())

    def test_plan_rejects_invalid_numeric_execution_settings(self):
        for extra, field in [('max_parallel = 0', 'max_parallel'), ('max_parallel = -1', 'max_parallel'),
                             ('max_parallel = 1.5', 'max_parallel'), ('max_parallel = true', 'max_parallel'),
                             ('quota_stop = 101', 'quota_stop'), ('quota_stop = nan', 'quota_stop'),
                             ('quota_stop = -1', 'quota_stop')]:
            with self.subTest(extra=extra):
                p = self.orc("check", self.review_plan(extra=extra))
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(field, p.stdout)
                self.assertNotIn("Traceback", p.stderr)
        for extra, field in [('timeout_min = 0', 'timeout_min'), ('max_repairs = -1', 'max_repairs'),
                             ('timeout_min = "bad"', 'timeout_min'), ('max_repairs = 1.5', 'max_repairs')]:
            with self.subTest(extra=extra):
                plan = self.review_plan()
                with open(self.repo / plan, "a", encoding="utf-8") as f:
                    f.write(extra + "\n")
                p = self.orc("check", plan)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(field, p.stdout)
                self.assertNotIn("Traceback", p.stderr)

    def test_queued_call_rechecks_quota_after_acquiring_slot(self):
        with patch.dict(os.environ, self.env):
            mod = runpy.run_path(ORC[1], run_name="orc_test")
        home = Path(self.env["ORC_HOME"])
        home.mkdir()
        limits = home / "codex-limits.json"
        limits.write_text("{}", encoding="utf-8")
        waiting, release = threading.Event(), threading.Event()
        class HeldSlot:
            def __enter__(self):
                waiting.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release the slot")
            def __exit__(self, *args):
                pass
        records, errors = [], []
        def run_call():
            try:
                with patch.dict(os.environ, self.env):
                    records.append(mod["codex_exec"](model="gpt-6.1-sol", effort="high", sandbox="read-only",
                        cwd=self.repo, prompt="q", logdir=self.tmp / "queued", name="q", slots=HeldSlot()))
            except Exception as e:
                errors.append(e)
        worker = threading.Thread(target=run_call)
        worker.start()
        try:
            self.assertTrue(waiting.wait(5))
            limits.write_text(json.dumps({"rate_limits": {"primary": {
                "used_percent": 95, "window_minutes": 300, "resets_at": 4102444800}}}), encoding="utf-8")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(records[0].get("skipped"), records)
        self.assertFalse((Path(self.env["CODEX_HOME"]) / "fake-state.json").exists())

    def test_usage_snapshot_is_saved_before_a_slot_is_released(self):
        with patch.dict(os.environ, self.env):
            mod = runpy.run_path(ORC[1], run_name="orc_test")
        snapshots = []
        home = Path(self.env["ORC_HOME"])
        class ObservedSlot:
            def __enter__(self):
                pass
            def __exit__(self, *args):
                snapshots.append((home / "codex-limits.json").exists())
        with patch.dict(os.environ, self.env):
            rec = mod["codex_exec"](model="gpt-6.1-sol", effort="high", sandbox="workspace-write", cwd=self.repo,
                prompt="q", logdir=self.tmp / "usage", name="usage", schema="worker-result", slots=ObservedSlot())
        self.assertIsNone(rec["error"])
        self.assertEqual(snapshots, [True])

    # ---- follow-ups and history ----------------------------------------------------------------

    def asks(self):
        return [json.loads(l) for l in (self.repo / ".orc" / "asks" / "asks.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_ask_follow_up_resumes_the_same_thread(self):
        self.orc("statusline", stdin=json.dumps({"session_id": "s3", "workspace": {"project_dir": str(self.repo)}}))
        p = self.orc("ask", "--model", "luna", "--effort", "xhigh", "first question?")
        first = re.search(r"· ask (\S+)$", p.stdout.strip()).group(1)                 # the footer names the ask
        self.assertIn("resume=None", p.stdout)
        p = self.orc("ask", "--continue", first, "and a follow-up?")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        asks = self.asks()
        self.assertEqual(asks[1]["thread"], asks[0]["thread"])                          # same Codex thread
        self.assertIn(f"resume={asks[0]['thread']}", p.stdout)
        self.assertIn("model=gpt-6-luna effort=xhigh", p.stdout)                        # keeps the thread's model and effort
        self.assertIn(f"(follow-up to {first})", p.stdout)
        self.assertEqual((asks[1]["parent"], asks[1]["root"]), (first, first))
        prompt = (self.repo / ".orc" / "asks" / f"{asks[1]['id']}.prompt.md").read_text(encoding="utf-8")
        self.assertTrue(prompt.startswith("## Follow-up question\nand a follow-up?"))  # no repeated preamble
        p = self.orc("ask", "--continue", "last", "--model", "sol", "third?")        # `last`; explicit flags still win
        asks = self.asks()
        self.assertEqual((asks[2]["parent"], asks[2]["root"], asks[2]["thread"]), (asks[1]["id"], first, asks[0]["thread"]))
        self.assertIn("model=gpt-6.1-sol effort=xhigh", p.stdout)
        p = self.orc("ask", "--research", "what changed in X?")
        research = self.asks()[-1]["id"]
        p = self.orc("ask", "--continue", research, "dig into Y")
        self.assertIn("- fake research finding", p.stdout)                              # research follow-ups stay reports
        self.assertIn("Full report:", p.stdout)
        self.assertNotIn("long details", p.stdout)
        p = self.orc("ask", "--continue", "nope", "q")
        self.assertEqual(p.returncode, 1)
        self.assertIn("no ask or run task 'nope'", p.stderr)
        self.assertIn("| research ×2 |", self.orc("report").stdout)

        # history lists a conversation once, with its follow-ups; show replays it
        p = self.orc("show")
        self.assertIn(f"{first}  ", p.stdout)
        self.assertIn("first question?", p.stdout)
        self.assertIn("(+2 follow-ups)", p.stdout)
        self.assertNotIn("third?", p.stdout)
        self.assertIn("(+1 follow-up)", p.stdout)
        p = self.orc("show", "--grep", "THIRD")                                        # matches a follow-up, lists its root
        self.assertIn("first question?", p.stdout)
        self.assertNotIn("what changed", p.stdout)
        p = self.orc("show", asks[2]["id"])
        self.assertEqual(p.returncode, 0, p.stderr)
        for text in ("first question?", "and a follow-up?", "third?", f"Continue: `orc ask --continue {asks[2]['id']}"):
            self.assertIn(text, p.stdout)
        self.assertEqual(p.stdout.count("A: fake answer"), 3)

    def test_ask_a_finished_worker_about_its_work(self):
        self.plan("q.toml", """
            goal = "ask a worker"
            checks = ["true"]
            [[task]]
            id = "impl-a"
            role = "implementer"
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
        """)
        self.assertEqual(self.orc("run", "q.toml").returncode, 0)
        run_id, st = self.state()
        thread = st["tasks"]["impl-a"]["calls"][0]["thread"]
        p = self.orc("ask", "--continue", f"{run_id}/impl-a", "why this approach?")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"[cwd=impl-a sandbox=read-only resume={thread}]", p.stdout)     # its worktree, read-only
        prompt = (self.repo / ".orc" / "asks" / f"{self.asks()[-1]['id']}.prompt.md").read_text(encoding="utf-8")
        self.assertIn("not a new task", prompt)
        self.assertIn("plain prose, not JSON", prompt)
        self.orc("merge", run_id)                                                         # worktree removed after merge
        p = self.orc("ask", "--continue", f"{run_id}/impl-a", "and now?")
        self.assertIn("[cwd=repo sandbox=read-only", p.stdout)
        self.assertIn("contains your merged changes", (self.repo / ".orc" / "asks" / f"{self.asks()[-1]['id']}.prompt.md").read_text(encoding="utf-8"))
        p = self.orc("show")
        self.assertIn(f"→ {run_id}/impl-a", p.stdout)
        self.assertIn("impl-a checks-passed (merged)", p.stdout)
        p = self.orc("show", f"{run_id}/impl-a")
        self.assertIn(f"thread {thread}", p.stdout)
        self.assertIn("brief: b", p.stdout)
        self.assertIn(f'`continues = "{run_id}/impl-a"`', p.stdout)
        p = self.orc("show", run_id)
        self.assertIn("✓ impl-a", p.stdout)
        self.assertIn(f"orc show {run_id}/<task>", p.stdout)
        self.assertIn(f"Questions to {run_id}/impl-a", self.orc("show", self.asks()[-1]["id"]).stdout)

    FOLLOW = """
        goal = "follow-up work"
        checks = ["true"]
        [[task]]
        id = "more"
        role = "implementer"
        continues = "{ref}"
        why = "w"
        brief = "extend it"
        acceptance = ["ok"]
        verify = "checks"
    """

    def test_new_plan_continues_a_merged_workers_thread(self):
        self.plan("a.toml", """
            goal = "first"
            checks = ["true"]
            [[task]]
            id = "impl-a"
            role = "implementer"
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
        """)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, st = self.state()
        thread = st["tasks"]["impl-a"]["calls"][0]["thread"]
        self.assertEqual(self.orc("merge", run_a).returncode, 0)
        p = self.orc("steer", run_a, "impl-a", "more")
        self.assertIn(f'continues = "{run_a}/impl-a"', p.stderr)                          # steer points to the new route
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        p = self.orc("check", "b.toml")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"· ↻ {run_a}/impl-a |", p.stdout)
        p = self.orc("run", "b.toml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        run_b, st = self.state()
        more = st["tasks"]["more"]
        self.assertEqual(more["calls"][0]["thread"], thread)                               # warm thread, resumed
        self.assertEqual(more["continues"], f"{run_a}/impl-a")
        self.assertTrue((Path(more["worktree"]) / "impl-a.txt").exists())                  # fresh worktree from HEAD
        self.assertEqual(Path(more["worktree"]).name, "more")
        prompt = (self.repo / ".orc" / "runs" / run_b / "more" / "worker.prompt.md").read_text(encoding="utf-8")
        self.assertTrue(prompt.startswith(f"## Follow-up to your earlier work ({run_a}/impl-a)"))
        self.assertIn("are already in this checkout", prompt)
        self.assertIn("## Your task: more\nextend it", prompt)
        self.assertEqual(self.orc("merge", run_b).returncode, 0)
        self.assertTrue((self.repo / "more.txt").exists())

    ONE = """
        goal = "first"
        checks = ["true"]
        [[task]]
        id = "impl-a"
        role = "implementer"
        why = "w"
        brief = "b"
        acceptance = ["ok"]
        verify = "checks"
    """

    def test_continues_unmerged_work_and_rejects_bad_refs(self):
        self.plan("a.toml", self.ONE)
        self.orc("run", "a.toml")
        run_a, _ = self.state()
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        p = self.orc("run", "b.toml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        run_b, st = self.state()
        prompt = (self.repo / ".orc" / "runs" / run_b / "more" / "worker.prompt.md").read_text(encoding="utf-8")
        self.assertIn(f"NOT in this checkout (they are on orc/{run_a}/impl-a", prompt)
        for ref, err in ((f"{run_b}/nope", "no task nope"), ("20990101-000000/x", "no run 20990101-000000"),
                         ("last", "`continues` must be"), ("0101-000000-000", "`continues` must be"), ("bad ref!", "`continues` must be")):
            self.plan("c.toml", self.FOLLOW.replace("{ref}", ref))
            p = self.orc("check", "c.toml")
            self.assertEqual(p.returncode, 1, ref)
            self.assertIn(err, p.stdout)
        self.plan("d.toml", self.FOLLOW.replace("{ref}", f"{run_b}/more") + """
            [[task]]
            id = "again"
            role = "explorer"
            continues = "REF"
            why = "w"
            brief = "b"
        """.replace("REF", f"{run_b}/more"))
        p = self.orc("check", "d.toml")
        self.assertEqual(p.returncode, 1)
        self.assertIn("continue the same Codex thread", p.stdout)

    def test_check_creates_nothing(self):
        self.plan("c.toml", self.FOLLOW.replace("{ref}", "20990101-000000/x"))
        self.assertEqual(self.orc("check", "c.toml").returncode, 1)
        self.assertFalse((self.repo / ".orc").exists())

    def test_web_threads_never_resume_with_write_access(self):
        self.plan("a.toml", """
            goal = "web"
            checks = ["true"]
            [[task]]
            id = "web-x"
            role = "explorer"
            web = true
            why = "w"
            brief = "search"
            [[task]]
            id = "impl-a"
            role = "implementer"
            why = "w"
            brief = "b"
            acceptance = ["ok"]
            verify = "checks"
        """)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, st = self.state()
        for flag in ("--web", "--research"):                                             # no web pages into a worker's thread
            p = self.orc("ask", "--continue", f"{run_a}/impl-a", flag, "q")
            self.assertEqual(p.returncode, 1)
            self.assertIn("can't use --web or --research", p.stderr)
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/web-x").replace('role = "implementer"', 'role = "explorer"'))
        self.assertEqual(self.orc("run", "b.toml").returncode, 0)                          # a reader may continue it...
        run_b, _ = self.state()
        self.plan("c.toml", self.FOLLOW.replace("{ref}", f"{run_b}/more"))
        p = self.orc("check", "c.toml")                                                   # ...but can't launder it to a writer
        self.assertEqual(p.returncode, 1)
        self.assertIn("can't continue a thread that used web search", p.stdout)
        # a thread tainted any other way (e.g. by an older orc) is refused for steer and continues
        thread = st["tasks"]["impl-a"]["calls"][0]["thread"]
        (self.repo / ".orc" / "asks").mkdir(exist_ok=True)
        with open(self.repo / ".orc" / "asks" / "asks.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "0101-000000-000", "thread": thread, "web": True}) + "\n")
        p = self.orc("steer", run_a, "impl-a", "more")
        self.assertEqual(p.returncode, 1)
        self.assertIn("has seen web search results", p.stderr)
        self.plan("d.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        self.assertIn("used web search", self.orc("check", "d.toml").stdout)

    def test_a_continued_thread_moves_to_its_new_task(self):
        self.plan("a.toml", self.ONE)
        self.orc("run", "a.toml")
        run_a, st = self.state()
        thread = st["tasks"]["impl-a"]["calls"][0]["thread"]
        self.plan("b.toml", """
            goal = "gate, then continue"
            checks = ["! grep -rq BROKEN --include=*.txt ."]
            [[task]]
            id = "gate"
            role = "implementer"
            why = "w"
            brief = "FAKE_BREAK_UNTIL_STEER"
            acceptance = ["ok"]
            verify = "checks"
            [[task]]
            id = "more"
            role = "implementer"
            after = ["gate"]
            continues = "REF"
            why = "w"
            brief = "extend it"
            acceptance = ["ok"]
            verify = "checks"
        """.replace("REF", f"{run_a}/impl-a"))
        self.assertEqual(self.orc("run", "b.toml").returncode, 2)
        run_b, st = self.state()
        self.assertEqual((st["tasks"]["gate"]["status"], st["tasks"]["more"]["status"]), ("failed", "skipped"))
        self.orc("steer", run_b, "gate", "STEER_FIX")
        p = self.orc("resume", run_b)                                                     # the skipped task keeps its thread
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        _, st = self.state()
        self.assertEqual(st["tasks"]["more"]["calls"][0]["thread"], thread)
        self.assertTrue((Path(st["tasks"]["more"]["worktree"]) / "gate.txt").exists())
        self.assertIn("## Follow-up to your earlier work", (self.repo / ".orc" / "runs" / run_b / "more" / "worker.prompt.md").read_text(encoding="utf-8"))
        # continue again; the continued call fails its check, repairs on the same thread, then steers
        self.plan("c.toml", self.FOLLOW.replace("{ref}", f"{run_b}/more").replace("extend it", "FAKE_BREAK_UNTIL_STEER")
                  .replace('checks = ["true"]', 'checks = ["! grep -rq BROKEN --include=*.txt ."]'))
        self.assertEqual(self.orc("run", "c.toml").returncode, 2)
        run_c, st = self.state()
        self.assertEqual([c["name"] for c in st["tasks"]["more"]["calls"]], ["worker", "repair1"])
        self.assertEqual(self.orc("steer", run_c, "more", "STEER_FIX").returncode, 0)
        _, st = self.state()
        self.assertEqual({c["thread"] for c in st["tasks"]["more"]["calls"]}, {thread})
        self.assertEqual(st["tasks"]["more"]["status"], "checks-passed")
        # only the newest task may resume the thread now
        for args in (("steer", run_a, "impl-a", "x"), ("steer", run_b, "more", "x"), ("ask", "--continue", f"{run_a}/impl-a", "q")):
            p = self.orc(*args)
            self.assertEqual(p.returncode, 1, args)
            self.assertIn(f"continued by {run_c}/more", p.stderr)
        self.plan("d.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        self.assertIn(f"continued by {run_c}/more; use {run_c}/more instead", self.orc("check", "d.toml").stdout)
        self.assertIn(f"Its thread was continued by {run_c}/more", self.orc("show", f"{run_a}/impl-a").stdout)

    def test_worker_questions_follow_the_worker(self):
        self.plan("a.toml", self.ONE)
        self.orc("run", "a.toml")
        run_a, _ = self.state()
        self.orc("ask", "--continue", f"{run_a}/impl-a", "first?")
        first = self.asks()[-1]["id"]
        p = self.orc("ask", "--continue", "last", "second?")                              # still a question to the worker
        self.assertIn("[cwd=impl-a sandbox=read-only", p.stdout)
        x = self.asks()[-1]
        self.assertEqual((x["parent"], x["root"]), (first, f"{run_a}/impl-a"))
        prompt = (self.repo / ".orc" / "asks" / f"{x['id']}.prompt.md").read_text(encoding="utf-8")
        self.assertIn(f"your work on `{run_a}/impl-a`, not a new task", prompt)
        self.assertEqual(self.orc("ask", "--continue", first, "--web", "q").returncode, 1)
        self.orc("clean", run_a)
        p = self.orc("ask", "--continue", f"{run_a}/impl-a", "third?")
        self.assertIn("[cwd=repo", p.stdout)
        self.assertIn("your worktree was removed and your changes are not in it",
                      (self.repo / ".orc" / "asks" / f"{self.asks()[-1]['id']}.prompt.md").read_text(encoding="utf-8"))
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        self.assertEqual(self.orc("run", "b.toml").returncode, 0)
        run_b, _ = self.state()
        self.assertIn("NOT in this checkout (their branch was removed)",
                      (self.repo / ".orc" / "runs" / run_b / "more" / "worker.prompt.md").read_text(encoding="utf-8"))
        p = self.orc("show")                                                              # one conversation per worker
        self.assertEqual(p.stdout.count(f"→ {run_a}/impl-a"), 1)
        self.assertIn("first?", p.stdout)
        self.assertIn("(+2 follow-ups)", p.stdout)
        p = self.orc("show", "-n", "1")
        self.assertIn(f"{run_b}  ", p.stdout)
        self.assertIn("… 1 older runs (use -n)", p.stdout)
        p = self.orc("show", "--grep", "extend it")                                       # searches plans too
        self.assertIn(run_b, p.stdout)
        self.assertNotIn(f"{run_a}  ", p.stdout)
        self.assertNotIn("first?", p.stdout)
        p = self.orc("show", first)                                                       # earlier answers are clipped
        self.assertIn("A: fake answer", p.stdout)

    def test_last_is_per_session_and_bad_files_do_not_crash(self):
        status = lambda sid: json.dumps({"session_id": sid, "workspace": {"project_dir": str(self.repo)}})
        self.orc("statusline", stdin=status("one"))
        self.orc("ask", "q")
        self.orc("statusline", stdin=status("two"))
        p = self.orc("ask", "--continue", "last", "q2")
        self.assertEqual(p.returncode, 1)
        self.assertIn("no earlier ask in this Claude session", p.stderr)
        with open(self.repo / ".orc" / "asks" / "asks.jsonl", "a", encoding="utf-8") as f:
            f.write('"junk"\n[1]\nnot json\n')
        self.plan("a.toml", self.ONE)
        self.orc("run", "a.toml")
        run_a, _ = self.state()
        (self.repo / ".orc" / "runs" / run_a / "plan.toml").unlink()
        bad = self.repo / ".orc" / "runs" / "20200101-000000"
        bad.mkdir()
        (bad / "state.json").write_text("[]", encoding="utf-8")
        p = self.orc("show")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(run_a, p.stdout)
        self.assertIn("sol high  q\n", p.stdout)
        self.assertEqual(self.orc("show", f"{run_a}/impl-a").returncode, 0)
        for ref in ("20200101-000000", "20200101-000000/x", "nope"):
            p = self.orc("show", ref)
            self.assertEqual(p.returncode, 1, ref)
            self.assertNotIn("Traceback", p.stderr)


    def test_a_planned_continues_claims_the_thread_before_it_runs(self):
        self.plan("s.toml", """
            goal = "scout"
            [[task]]
            id = "s"
            role = "explorer"
            why = "w"
            brief = "look"
        """)
        self.orc("run", "s.toml")
        run0, st = self.state()
        thread = st["tasks"]["s"]["calls"][0]["thread"]
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run0}/s") + """
            [[task]]
            id = "x"
            role = "explorer"
            why = "w"
            brief = "other"
        """)
        self.assertEqual(self.orc("run", "b.toml", "--only", "x").returncode, 0)          # `more` hasn't run, but holds the thread
        run_b, st = self.state()
        self.assertNotIn("more", st["tasks"])
        self.plan("a.toml", self.FOLLOW.replace("{ref}", f"{run0}/s").replace('role = "implementer"', 'role = "explorer"\nweb = true'))
        p = self.orc("check", "a.toml")
        self.assertEqual(p.returncode, 1)
        self.assertIn(f"continued by {run_b}/more", p.stdout)
        self.assertIn(f"continued by {run_b}/more", self.orc("ask", "--continue", f"{run0}/s", "q").stderr)
        p = self.orc("resume", run_b)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        _, st = self.state()
        self.assertEqual(st["tasks"]["more"]["calls"][0]["thread"], thread)
        self.assertEqual(self.orc("steer", run_b, "more", "again").returncode, 0)          # the claimant owns it
        # a claim on a web plan entry taints the thread even before it runs; so does an unreadable plan
        self.plan("w.toml", self.FOLLOW.replace("{ref}", f"{run_b}/more").replace('role = "implementer"', 'role = "explorer"\nweb = true')
                  + '\n[[task]]\nid = "x"\nrole = "explorer"\nwhy = "w"\nbrief = "other"\n')
        self.assertEqual(self.orc("run", "w.toml", "--only", "x").returncode, 0)
        run_w, _ = self.state()
        self.plan("i.toml", self.FOLLOW.replace("{ref}", f"{run_w}/more"))
        self.assertIn("used web search", self.orc("check", "i.toml").stdout)
        (self.repo / ".orc" / "runs" / run_w / "plan.toml").write_text("not toml [", encoding="utf-8")
        self.assertIn("used web search", self.orc("check", "i.toml").stdout)

    def test_web_is_recorded_in_state_and_survives_a_lost_plan(self):
        self.plan("a.toml", """
            goal = "web"
            [[task]]
            id = "e"
            role = "explorer"
            web = true
            why = "w"
            brief = "search"
        """)
        self.orc("run", "a.toml")
        run_a, st = self.state()
        self.assertTrue(st["tasks"]["e"]["web"])
        (self.repo / ".orc" / "runs" / run_a / "plan.toml").unlink()
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/e"))
        self.assertIn("used web search", self.orc("check", "b.toml").stdout)
        p = self.orc("ask", "--continue", f"{run_a}/e", "what did you find?")            # questions never search the web
        self.assertIn("web=False", p.stdout)

    def test_a_running_task_cannot_be_resumed_elsewhere(self):
        self.plan("a.toml", self.ONE)
        self.orc("run", "a.toml")
        run_a, st = self.state()
        path = self.repo / ".orc" / "runs" / run_a / "state.json"
        st["tasks"]["impl-a"].update(status="running", pid=os.getpid())                  # a live orc process holds it
        path.write_text(json.dumps(st), encoding="utf-8")
        for args in (("ask", "--continue", f"{run_a}/impl-a", "q"), ("steer", run_a, "impl-a", "x")):
            p = self.orc(*args)
            self.assertEqual(p.returncode, 1, args)
            self.assertIn("is running right now", p.stderr)
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        self.assertIn("is running right now", self.orc("check", "b.toml").stdout)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        st["tasks"]["impl-a"]["pid"] = dead.pid                                          # its process died: usable again
        path.write_text(json.dumps(st), encoding="utf-8")
        self.assertEqual(self.orc("ask", "--continue", f"{run_a}/impl-a", "q").returncode, 0)

    def test_malformed_runs_do_not_break_other_commands(self):
        self.plan("a.toml", self.ONE)
        self.orc("run", "a.toml")
        run_a, _ = self.state()
        runs = self.repo / ".orc" / "runs"
        for name, state in (("20200101-000001", {"tasks": []}), ("20200101-000002", {"tasks": {"t": {"calls": None}}}),
                            ("20200101-000003", {"tasks": {"t": {"calls": [1, {"thread": "x"}, {"name": None, "thread": "y"}]}}}),
                            ("20200101-000004", {"tasks": {"t": "junk"}, "continues": ["x"]})):
            (runs / name).mkdir()
            (runs / name / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        for args in (("check", "b.toml"), ("show",), ("show", f"{run_a}/impl-a"), ("ask", "--continue", f"{run_a}/impl-a", "q"),
                     ("steer", run_a, "impl-a", "again")):
            p = self.orc(*args)
            self.assertEqual(p.returncode, 0, (args, p.stdout, p.stderr))

    def test_same_second_runs_sort_numerically(self):
        runs = self.repo / ".orc" / "runs"
        for name in ("20260930-101010", "20260930-101010-2", "20260930-101010-10"):
            (runs / name).mkdir(parents=True)
            (runs / name / "state.json").write_text(json.dumps({"id": name, "goal": f"goal {name}", "order": [], "tasks": {}}), encoding="utf-8")
        out = self.orc("show").stdout
        self.assertLess(out.index("goal 20260930-101010-10"), out.index("goal 20260930-101010-2"))
        self.assertIn("run 20260930-101010-10 ", self.orc("resume").stderr)                 # the latest run

    def wait_file(self, path, process):
        deadline = time.monotonic() + 5
        while not path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), f"process did not reach {path}; exit={process.poll()}")

    def test_concurrent_continuations_publish_only_one_claim(self):
        self.plan("a.toml", self.ONE)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, _ = self.state()
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a"))
        # Widen precisely the validation/persistence gap in separate orc processes.
        driver = self.tmp / "claim.py"
        driver.write_text(textwrap.dedent(f'''
            import runpy, sys, time
            from pathlib import Path
            from types import SimpleNamespace
            sys.stdout.reconfigure(encoding="utf-8")  # main() does this; cmd_run is called directly here
            mod = runpy.run_path({ORC[1]!r}, run_name="claim_test")
            original = mod["resolve_continues"]
            def slow_resolve(*args):
                result = original(*args)
                time.sleep(0.25)
                return result
            mod["cmd_run"].__globals__["resolve_continues"] = slow_resolve
            mod["Run"].execute = lambda *args: None
            Path(sys.argv[1]).touch()
            deadline = time.monotonic() + 5
            while not Path(sys.argv[2]).exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("test did not start claimants")
                time.sleep(0.01)
            sys.exit(mod["cmd_run"](SimpleNamespace(plan="b.toml", only=None)))
        '''))
        go = self.tmp / "go"
        processes = [subprocess.Popen([sys.executable, str(driver), str(self.tmp / f"ready{i}"), str(go)],
                        cwd=self.repo, env=self.env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for i in range(2)]
        try:
            for i, process in enumerate(processes):
                self.wait_file(self.tmp / f"ready{i}", process)
            go.touch()
            outputs = [p.communicate(timeout=10) for p in processes]
            self.assertEqual(sorted(p.returncode for p in processes), [0, 1], outputs)
            self.assertIn("continued by", "".join(out + err for out, err in outputs))
            states = [json.loads(p.read_text(encoding="utf-8")) for p in (self.repo / ".orc" / "runs").glob("*/state.json")]
            self.assertEqual(sum(bool(st.get("continues")) for st in states), 1)
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
                p.communicate(timeout=5)

    def test_resume_refuses_transferred_threads_for_writers_and_readers(self):
        self.plan("a.toml", self.ONE)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        source, st = self.state()
        for role in ("implementer", "explorer"):
            with self.subTest(role=role):
                follow = self.FOLLOW.replace('role = "implementer"', f'role = "{role}"')
                self.plan("b.toml", follow.replace("{ref}", f"{source}/impl-a") +
                          '\n[[task]]\nid="x"\nrole="explorer"\nwhy="w"\nbrief="other"\n')
                self.assertEqual(self.orc("run", "b.toml", "--only", "x").returncode, 0)
                run_b, _ = self.state()
                # B is filtered out but owns the claim; C transfers it before B's recovery.
                self.plan("c.toml", self.FOLLOW.replace("{ref}", f"{run_b}/more"))
                self.assertEqual(self.orc("run", "c.toml").returncode, 0)
                run_c, _ = self.state()
                before = json.loads((Path(self.env["CODEX_HOME"]) / "fake-state.json").read_text(encoding="utf-8"))["calls"]
                p = self.orc("resume", run_b)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn(f"continued by {run_c}/more", p.stdout)
                disk = json.loads((self.repo / ".orc" / "runs" / run_b / "state.json").read_text(encoding="utf-8"))
                self.assertEqual(disk["tasks"]["more"]["calls"], [])
                after = json.loads((Path(self.env["CODEX_HOME"]) / "fake-state.json").read_text(encoding="utf-8"))["calls"]
                self.assertEqual(before, after)
                # Use a new independent thread for the next role.
                self.assertEqual(self.orc("run", "a.toml").returncode, 0)
                source, _ = self.state()

    def test_active_worker_question_reserves_thread_and_releases_on_failure(self):
        self.plan("a.toml", self.ONE)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, _ = self.state()
        ref = f"{run_a}/impl-a"
        self.plan("b.toml", self.FOLLOW.replace("{ref}", ref))
        gate = self.tmp / "question"
        env = dict(self.env, ORC_FAKE_GATE=str(gate))
        process = subprocess.Popen(ORC + ["ask", "--continue", ref, "FAKE_HOLD why?"], cwd=self.repo,
                                   env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_file(gate.with_suffix(".ready"), process)
            for args in (("ask", "--continue", ref, "q"), ("steer", run_a, "impl-a", "q"),
                         ("check", "b.toml"), ("run", "b.toml")):
                p = self.orc(*args)
                self.assertEqual(p.returncode, 1, (args, p.stdout, p.stderr))
                self.assertIn("is running right now", p.stdout + p.stderr)
            # The next reserved question will fail to launch Codex.
            self.env["ORC_CODEX_BIN"] = str(self.tmp / "missing-codex")
        finally:
            gate.with_suffix(".release").touch()
            out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(self.orc("ask", "--continue", ref, "q").returncode, 1)
        self.env["ORC_CODEX_BIN"] = codex_bin(ROOT / "tests" / "fake_codex.py")
        self.assertEqual(self.orc("ask", "--continue", ref, "q").returncode, 0)
        self.assertEqual(self.orc("run", "b.toml").returncode, 0)

    def test_active_ask_conversation_reserves_thread(self):
        self.assertEqual(self.orc("ask", "q").returncode, 0)
        ref = self.asks()[-1]["id"]
        gate = self.tmp / "conversation"
        process = subprocess.Popen(ORC + ["ask", "--continue", ref, "FAKE_HOLD q"], cwd=self.repo,
                                   env=dict(self.env, ORC_FAKE_GATE=str(gate)), text=True, encoding="utf-8", errors="replace",
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_file(gate.with_suffix(".ready"), process)
            p = self.orc("ask", "--continue", ref, "q2")
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertIn("is running right now", p.stderr)
        finally:
            gate.with_suffix(".release").touch()
            out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(self.orc("ask", "--continue", ref, "q3").returncode, 0)

    def test_resume_waits_for_question_on_a_skipped_claimant(self):
        self.plan("a.toml", self.ONE)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, _ = self.state()
        self.plan("b.toml", self.FOLLOW.replace("{ref}", f"{run_a}/impl-a") +
                  '\n[[task]]\nid="x"\nrole="explorer"\nwhy="w"\nbrief="other"\n')
        self.assertEqual(self.orc("run", "b.toml", "--only", "x").returncode, 0)
        run_b, _ = self.state()
        gate = self.tmp / "recovery"
        process = subprocess.Popen(ORC + ["ask", "--continue", f"{run_b}/more", "FAKE_HOLD q"],
                                   cwd=self.repo, env=dict(self.env, ORC_FAKE_GATE=str(gate)), text=True, encoding="utf-8", errors="replace",
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_file(gate.with_suffix(".ready"), process)
            p = self.orc("resume", run_b)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("is running right now", p.stdout)
            _, st = self.state()
            self.assertEqual(st["tasks"]["more"]["status"], "skipped")
            self.assertEqual(st["tasks"]["more"]["calls"], [])
        finally:
            gate.with_suffix(".release").touch()
            out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, out + err)
        p = self.orc("resume", run_b)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_search_disabled_overrides_inherited_config_on_resumed_threads(self):
        config = Path(self.env["CODEX_HOME"]) / "config.toml"
        for mode in ("cached", "live"):
            with self.subTest(mode=mode):
                config.write_text(f'web_search = "{mode}"\n', encoding="utf-8")
                self.plan("a.toml", self.ONE)
                self.assertEqual(self.orc("run", "a.toml").returncode, 0)
                run_a, _ = self.state()
                p = self.orc("ask", "--continue", f"{run_a}/impl-a", "q")
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("web=False", p.stdout)
                self.assertEqual(self.orc("ask", "--continue", "last", "q2").returncode, 0)
                state = json.loads((Path(self.env["CODEX_HOME"]) / "fake-state.json").read_text(encoding="utf-8"))
                for call in state["invocations"][-3:]:
                    self.assertEqual(call["web_search"], "disabled", call)
                self.assertIsNotNone(state["invocations"][-1]["resume"])
        self.assertIn("web=True", self.orc("ask", "--web", "q").stdout)

    def test_reader_followup_preserves_dependency_checkout_and_old_state(self):
        self.plan("a.toml", self.ONE + '\n[[task]]\nid="review"\nrole="reviewer"\nafter=["impl-a"]\nwhy="w"\nbrief="review"\n')
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, st = self.state()
        cwd = st["tasks"]["impl-a"]["worktree"]
        self.assertEqual(st["tasks"]["review"]["cwd"], cwd)
        for legacy in (False, True):
            if legacy:
                del st["tasks"]["review"]["cwd"]
                (self.repo / ".orc" / "runs" / run_a / "state.json").write_text(json.dumps(st), encoding="utf-8")
            p = self.orc("ask", "--continue", f"{run_a}/review", "q")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("[cwd=impl-a sandbox=read-only", p.stdout)
        self.assertEqual(self.orc("clean", run_a).returncode, 0)
        self.assertIn("[cwd=repo sandbox=read-only", self.orc("ask", "--continue", f"{run_a}/review", "q").stdout)

    def test_show_rejects_malformed_selected_records_and_skips_unrelated_records(self):
        self.plan("a.toml", self.ONE)
        self.assertEqual(self.orc("run", "a.toml").returncode, 0)
        run_a, original = self.state()
        path = self.repo / ".orc" / "runs" / run_a / "state.json"
        mutations = [("tasks", ["junk"]), ("calls", ["junk"]), ("calls", [{"usage": [1]}]),
                     ("calls", [{"name": None}]), ("calls", [{"cost": "junk"}]), ("result", [1]),
                     ("result", {"findings": "junk"}), ("checks", [{"exit": 1, "tail": [1]}]),
                     ("verdicts", [{"issues": ["junk"]}])]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                st = copy.deepcopy(original)
                if key == "tasks":
                    st[key] = value
                else:
                    st["tasks"]["impl-a"][key] = value
                path.write_text(json.dumps(st), encoding="utf-8")
                p = self.orc("show", f"{run_a}/impl-a")
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("unreadable", p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                for args in (("show",), ("show", run_a)):
                    p = self.orc(*args)
                    self.assertIn(p.returncode, (0, 1))
                    self.assertNotIn("Traceback", p.stderr)
        original["tasks"]["bad"] = {"id": "bad", "status": "done", "calls": [1]}
        original["order"].append("bad")
        path.write_text(json.dumps(original), encoding="utf-8")
        for args in (("show",), ("show", run_a), ("show", f"{run_a}/impl-a")):
            p = self.orc(*args)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("impl-a", p.stdout)
        asks = self.repo / ".orc" / "asks"
        asks.mkdir(exist_ok=True)
        with open(asks / "asks.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "bad", "root": [], "question": [1]}) + "\n")
        self.assertEqual(self.orc("show").returncode, 0)


class PackagingTest(unittest.TestCase):
    def test_dist_skills_match_sources(self):
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "build_skills.py"), "--check"], text=True, encoding="utf-8", errors="replace", capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_plugin_layout(self):
        manifest = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(market["plugins"][0]["name"], manifest["name"])
        self.assertTrue((ROOT / market["plugins"][0]["source"] / "skills" / "orchestrate" / "SKILL.md").exists())
        skill = (ROOT / "plugin" / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/orc *)", skill)


if __name__ == "__main__":
    unittest.main()
