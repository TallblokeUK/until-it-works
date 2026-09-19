import json
import subprocess
import sys
import glob
import os
import tempfile
import unittest

from helpers import make_repo, sh, tmpdir, write

from mp_agent import gitops, planner
from mp_agent.contract import Contract, Decisions
from mp_agent.progress import Progress
from mp_agent.providers import Pacer, Reply, Retrying, tagged, verdict
from mp_agent.waves import CycleError, waves


class GitOps(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir("mp-home-")

    def test_new_folder_gets_git(self):
        folder = os.path.join(self.home, "fresh")
        repo = gitops.setup_project(folder, "t", home=self.home, say=lambda *_: None)
        self.assertEqual(os.path.realpath(repo), os.path.realpath(folder))
        with open(os.path.join(repo, ".gitignore")) as fh:
            self.assertIn("__pycache__/", fh.read())
        self.assertTrue(gitops.has_commits(repo))

    def test_no_folder_creates_one_under_projects(self):
        repo = gitops.setup_project(None, "Build a CSV tool", home=self.home, say=lambda *_: None)
        self.assertIn(os.path.join("mp-projects", "build-a-csv-tool"), repo)

    def test_loose_files_need_init(self):
        folder = os.path.join(self.home, "loose")
        write(folder, "notes.txt", "hi")
        with self.assertRaises(gitops.SetupError):
            gitops.setup_project(folder, "t", home=self.home, say=lambda *_: None)
        repo = gitops.setup_project(folder, "t", init=True, home=self.home, say=lambda *_: None)
        self.assertIn("notes.txt", sh(repo, "git", "ls-files"))

    def test_refuses_home_and_dirty(self):
        with self.assertRaises(gitops.SetupError):
            gitops.setup_project(self.home, "t", home=self.home, say=lambda *_: None)
        repo = make_repo({"a.txt": "a"})
        write(repo, "a.txt", "changed")
        with self.assertRaises(gitops.SetupError):
            gitops.setup_project(repo, "t", home=self.home, say=lambda *_: None)

    def test_snapshot_restore_including_binary_and_new_files(self):
        repo = make_repo({"a.txt": "a\n", "b.bin": "x"})
        write(repo, "a.txt", "implementer\n")
        with open(os.path.join(repo, "b.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02")
        write(repo, "c.txt", "new\n")
        snap = gitops.snapshot(repo)
        write(repo, "a.txt", "reviewer vandalism\n")
        os.remove(os.path.join(repo, "c.txt"))
        write(repo, "d.txt", "junk")
        self.assertTrue(gitops.restore(repo, snap))
        self.assertEqual(gitops.snapshot(repo), snap)
        self.assertFalse(gitops.restore(repo, snap))
        write(repo, "__pycache__/x.cpython-314.pyc", "cache")   # running tests is not a change
        self.assertEqual(gitops.snapshot(repo), snap)
        self.assertFalse(gitops.restore(repo, snap))

    def test_cache_files_do_not_make_a_repo_dirty(self):
        repo = make_repo({"calc.py": "a"})
        write(repo, "__pycache__/calc.cpython-314.pyc", "cache")
        self.assertFalse(gitops.is_dirty(repo))
        write(repo, "calc.py", "b")
        self.assertTrue(gitops.is_dirty(repo))

    def test_cache_files_are_never_committed(self):
        repo = make_repo({"calc.py": "a"})
        write(repo, "calc.py", "b")
        write(repo, "__pycache__/calc.cpython-314.pyc", "cache")
        write(repo, "pkg/__pycache__/x.cpython-314.pyc", "cache")
        gitops.commit_all(repo, "work")
        committed = sh(repo, "git", "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(committed, ["calc.py"])

    def test_ownership(self):
        repo = make_repo({"src/a.py": "a", "tests/t.py": "t"})
        write(repo, "src/a.py", "changed")
        write(repo, "src/new.py", "new")
        write(repo, "other.py", "nope")
        write(repo, "tests/t.py", "weakened")
        write(repo, "tests/__pycache__/t.cpython-314.pyc", "junk")
        write(repo, "src/__pycache__/a.cpython-314.pyc", "junk")
        bad = gitops.ownership_violations(repo, ["src/"], ["tests/t.py"])
        self.assertEqual(sorted(bad), ["other.py", "tests/t.py"])
        gitops.revert_paths(repo, bad)
        self.assertEqual(sorted(p for p in gitops.changed_files(repo) if not gitops.is_junk(p)),
                         ["src/a.py", "src/new.py"])
        self.assertFalse(os.path.exists(os.path.join(repo, "other.py")))


class Plans(unittest.TestCase):
    def plan(self, **over):
        base = {"mode": "swarm", "contract": {"done": ["x"]}, "check": "true", "tests": None,
                "subtasks": [{"id": "a", "goal": "g", "owns": ["a.py"], "done": ["a"]},
                             {"id": "b", "goal": "g", "owns": ["b/"], "done": ["b"], "depends_on": ["a"]}]}
        base.update(over)
        return base

    def test_valid(self):
        self.assertEqual(planner.validate(self.plan(), make_repo()), [])

    def test_overlap_cycle_and_frozen_tests(self):
        repo = make_repo()
        p = self.plan(subtasks=[{"id": "a", "goal": "g", "owns": ["b/x.py"], "done": ["a"], "depends_on": ["b"]},
                                {"id": "b", "goal": "g", "owns": ["b/"], "done": ["b"], "depends_on": ["a"]}])
        errors = " ".join(planner.validate(p, repo))
        self.assertIn("both own", errors)
        p = self.plan(tests={"goal": "t", "files": ["b/test_b.py"]})
        self.assertIn("frozen tests file", " ".join(planner.validate(p, repo)))
        p = self.plan(subtasks=[{"id": "a", "goal": "g", "owns": ["a.py"], "done": ["a"], "depends_on": ["b"]},
                                {"id": "b", "goal": "g", "owns": ["b.py"], "done": ["b"], "depends_on": ["a"]}])
        self.assertIn("cycle", " ".join(planner.validate(p, repo)))

    def test_missing_check_script_must_be_in_tests(self):
        repo = make_repo()
        p = self.plan(mode="single", check="./agent-check.sh")
        self.assertIn("does not exist", " ".join(planner.validate(p, repo)))
        p["tests"] = {"goal": "t", "files": ["agent-check.sh", "test_x.py"]}
        self.assertEqual(planner.validate(p, repo), [])

    def test_extract_json(self):
        self.assertEqual(planner.extract_json('blah\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertIsNone(planner.extract_json("no json here"))

    def test_waves(self):
        self.assertEqual(waves([{"id": "c", "depends_on": ["a", "b"]}, {"id": "a"}, {"id": "b"}]),
                         [["a", "b"], ["c"]])
        with self.assertRaises(CycleError):
            waves([{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}])


class Parsing(unittest.TestCase):
    def test_verdict_takes_last_and_tolerates_markdown(self):
        self.assertEqual(verdict("VERDICT: CHANGES REQUIRED\n...\n**VERDICT: APPROVED**"), "APPROVED")
        self.assertIsNone(verdict("I think it is fine"))

    def test_ambiguity_none_is_not_a_question(self):
        from mp_agent.gates import open_questions
        text = "AMBIGUITY: none — the contract is fully satisfied\n**AMBIGUITY:** N/A\nAMBIGUITY: is 0 a valid id?"
        self.assertEqual(open_questions(text), ["is 0 a valid id?"])

    def test_tagged(self):
        text = "summary\n- DECISION: rejected spaces inside a part\nDECLINE: unicode — O2\n"
        self.assertEqual(tagged(text, "DECISION"), ["rejected spaces inside a part"])
        self.assertEqual(tagged(text, "DECLINE"), ["unicode — O2"])

    def test_contract_and_decisions_render(self):
        c = Contract(["parses 1h30m"], ["unicode digits"])
        self.assertEqual(c.amend("spaces inside a part are rejected"), "A1")
        text = c.render()
        self.assertIn("C1: parses 1h30m", text)
        self.assertIn("O1: unicode digits", text)
        self.assertIn("A1: spaces inside a part are rejected", text)
        d = Decisions()
        d.add("kept regex", "main")
        self.assertIn("D1 (decided): kept regex", d.render("main"))


class ProgressTests(unittest.TestCase):
    def test_no_change(self):
        p = Progress(patience=2, churn=99)
        for h in ["a", "a", "a"]:
            p.record_tree(h)
        self.assertIn("changed nothing", p.reason())

    def test_revisit(self):
        p = Progress(patience=9, churn=99)
        for h in ["a", "b", "a", "b"]:
            p.record_tree(h)
        self.assertIn("returning", p.reason())

    def test_churn_and_reset(self):
        p = Progress(patience=9, churn=3)
        for i in range(3):
            p.record_tree(str(i))
            p.round_failed()
        self.assertIn("without approval", p.reason())
        p.reset()
        self.assertIsNone(p.reason())


class Classification(unittest.TestCase):
    def test_real_error_strings(self):
        from mp_agent.providers import classify, error_line
        self.assertEqual(classify(1, "Error: Rate limit reached: input token limit exceeded"), "rate")
        self.assertEqual(classify(1, "error: The server had an error while processing your request."), "transient")
        self.assertEqual(classify(1, "Credit balance is too low"), "fatal")
        self.assertEqual(classify(1, "Claude AI usage limit reached|1789500000"), "fatal")
        self.assertEqual(classify(1, "You've hit your usage limit. Visit chatgpt.com/codex/settings/usage to "
                                     "purchase more credits or try again at Sep 19th, 2026 6:05 PM."), "fatal")
        self.assertEqual(classify(127, ""), "missing")
        self.assertEqual(classify(124, "..."), "timeout")
        self.assertEqual(classify(1, "AssertionError: 401 != 200"), "failed")
        self.assertEqual(error_line("working...\nerror: hook dispatch failed\nError: Rate limit reached\ndone"),
                         "Error: Rate limit reached")

    def test_fatal_is_not_retried(self):
        calls = []

        class Broke:
            name = pace_key = "broke"

            def ask(self, *a):
                calls.append(1)
                return Reply("Credit balance is too low", 1)

        said = []
        agent = Retrying(Broke(), Pacer(tmpdir(), 0), said.append, sleep=lambda *_: None)
        self.assertFalse(agent.ask("s", "p", "/tmp").ok)
        self.assertEqual(len(calls), 1)
        self.assertIn("cannot continue", said[0])


class Costs(unittest.TestCase):
    def test_usage_counts_only_this_runs_sessions_and_splits_by_billing(self):
        import json as j
        from mp_agent.providers import Usage, cost_line, summarize_costs
        root = tmpdir()
        for sid, cwd, costs in [("a", "/trees/run1", [0.001, 0.002]), ("b", "/trees/run1-reader", [0.004]),
                                ("c", "/trees/run2", [9.0])]:
            os.makedirs(os.path.join(root, sid))
            with open(os.path.join(root, sid, f"{sid}.json"), "w") as fh:
                j.dump({"cwd": cwd, "provider": "inception", "model": "mercury-2.5"}, fh)
            with open(os.path.join(root, sid, f"{sid}.messages.json"), "w") as fh:
                j.dump({"messages": [{"metrics": {"cost": c}} for c in costs]}, fh)
        tracker = Usage(lambda totals: None, "/trees/run1", 0, sessions=root)
        tracker.add({"claude-opus-5": {"usd": 0.5}}, "subscription")
        tracker.add({"llama3": {"usd": 0.0}}, "local")
        tracker.refresh(force=True)
        costs = summarize_costs(tracker.totals())
        self.assertAlmostEqual(costs["billed_usd"], 0.007)
        self.assertAlmostEqual(costs["subscription_usd"], 0.5)
        self.assertEqual(costs["by_model"]["llama3"]["billing"], "local")
        line = cost_line(costs)
        self.assertIn("$0.007 billed to API keys (inception/mercury-2.5 $0.007)", line)
        self.assertIn("$0.50 API-equivalent on subscriptions, not billed (claude-opus-5)", line)
        # records written before billing was tracked: only Claude was on a subscription
        self.assertEqual(summarize_costs({"claude-sonnet-5": {"usd": 1.0}, "x/y": {"usd": 2.0}})["billed_usd"], 2.0)


class TokenUsage(unittest.TestCase):
    def test_cline_usage_per_model_and_claude_reports_add_up(self):
        import json as j
        from mp_agent.providers import Usage, cline_usage
        root = tmpdir()
        os.makedirs(os.path.join(root, "s1"))
        with open(os.path.join(root, "s1", "s1.json"), "w") as fh:
            j.dump({"cwd": "/trees/run1", "provider": "inception", "model": "mercury-2.5"}, fh)
        with open(os.path.join(root, "s1", "s1.messages.json"), "w") as fh:
            j.dump({"messages": [
                {"modelInfo": {"id": "mercury-2.5", "provider": "inception"},
                 "metrics": {"inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 5, "cost": 0.001}},
                {"modelInfo": {"id": "mercury-2.5", "provider": "inception"},
                 "metrics": {"inputTokens": 50, "outputTokens": 5, "cost": 0.0005}}]}, fh)
        usage = cline_usage("/trees/run1", 0, sessions=root)
        self.assertEqual(usage["inception/mercury-2.5"]["calls"], 1)
        self.assertEqual(usage["inception/mercury-2.5"]["input"], 150)
        written = []
        tracker = Usage(written.append, "/trees/run1", 0, sessions=root)
        tracker.add({"claude-sonnet-5": {"input": 2, "output": 14, "cache_read": 18531, "cache_write": 22597,
                                         "usd": 0.094}})
        tracker.refresh(force=True)
        totals = written[-1]
        self.assertEqual(totals["claude-sonnet-5"]["cache_write"], 22597)
        self.assertEqual(totals["inception/mercury-2.5"]["output"], 15)


class ModelChoice(unittest.TestCase):
    OPTIONS = [{"spec": "claude:opus", "label": "Claude Opus (Claude Code, Max plan)"},
               {"spec": "claude:sonnet", "label": "Claude Sonnet (Claude Code, Max plan)"},
               {"spec": "codex:gpt-5.5", "label": "GPT-5.5 (Codex)"},
               {"spec": "codex:gpt-5.6-sol", "label": "GPT-5.6-Sol (Codex)"},
               {"spec": "qwen:deepseek-v4-pro", "label": "DeepSeek V4 Pro (Qwen Code)"},
               {"spec": "cline:inception:mercury-2.5", "label": "Mercury 2.5 (Cline, inception)"}]

    def test_panel_size_and_panel_model_are_separate_flags(self):
        from mp_agent import cli
        args = cli.parser().parse_args(["--panel", "1", "--panel-model", "haiku", "--reviewer", "same", "a task"])
        self.assertEqual(args.panel, 1)
        self.assertEqual(cli.given_roles(args), {"reviewer": "same", "panel": "haiku"})

    def test_short_names_resolve_and_ambiguity_is_refused(self):
        from mp_agent import models
        with_antigravity = self.OPTIONS + [{"spec": "antigravity:claude-opus-4-6-thinking",
                                            "label": "Claude Opus 4.6 (Thinking) (Antigravity)"}]
        self.assertEqual(models.resolve("opus", with_antigravity), "claude:opus")
        self.assertEqual(models.resolve("opus", self.OPTIONS), "claude:opus")
        self.assertEqual(models.resolve("GPT-5.5", self.OPTIONS), "codex:gpt-5.5")
        self.assertEqual(models.resolve("deepseek pro", self.OPTIONS), "qwen:deepseek-v4-pro")
        with self.assertRaisesRegex(models.ChoiceError, "could mean"):
            models.resolve("gpt", self.OPTIONS)
        with self.assertRaisesRegex(models.ChoiceError, "no available model"):
            models.resolve("llama", self.OPTIONS)

    def test_judge_and_planner_must_not_be_the_worker(self):
        from mp_agent import models
        self.assertTrue(models.check_independent({"worker": "claude:opus", "planner": "claude:opus",
                                                  "judge": "codex:gpt-5.5"}))
        self.assertFalse(models.check_independent({"worker": "cline:inception:mercury-2.5",
                                                   "planner": "claude:sonnet", "judge": "claude:opus"}))

    def test_config_round_trip_keeps_defaults(self):
        from mp_agent import models
        state = tmpdir()
        self.assertEqual(models.load_config(state), models.BUILTIN)
        models.save_config(state, {"judge": "claude:opus", "reviewer": "claude:haiku"})
        self.assertEqual(models.load_config(state)["judge"], "claude:opus")
        self.assertEqual(models.load_config(state)["worker"], models.BUILTIN["worker"])
        self.assertEqual(models.effective(models.load_config(state))["panel"], models.BUILTIN["worker"])
        models.save_config(state, {"reviewer": ""})
        self.assertEqual(models.load_config(state)["reviewer"], "")

    def test_presets_pick_from_what_is_available_and_keep_the_judge_independent(self):
        from mp_agent import models
        found = {p["id"]: p for p in models.presets(self.OPTIONS + [{"spec": "codex:gpt-5.6-luna", "label": "Luna"},
                                                                     {"spec": "claude:haiku", "label": "Haiku"}])}
        self.assertEqual(found["fast"]["roles"]["worker"], "cline:inception:mercury-2.5")
        self.assertEqual(found["mixed"]["roles"]["worker"], "codex:gpt-5.6-luna")
        self.assertEqual(found["mixed"]["roles"]["planner"], "claude:opus")
        openai = found["openai"]["roles"]
        self.assertEqual(openai["worker"], "codex:gpt-5.6-luna")
        self.assertNotEqual(openai["judge"], openai["worker"])
        # the reviewing stays at the workers' level: Haiku measured worst of every worker (docs/benchmarks.md)
        self.assertEqual(found["claude"]["roles"]["reviewer"], "claude:sonnet")
        only_claude = {p["id"]: p for p in models.presets([o for o in self.OPTIONS if o["spec"].startswith("claude")])}
        self.assertIsNone(only_claude["fast"]["roles"])
        self.assertIn("a worker", only_claude["fast"]["missing"][0])
        state = tmpdir()
        roles, problem = models.apply_preset(state, "fast", self.OPTIONS)
        self.assertIsNone(problem)
        self.assertEqual(models.load_tuning(state)["panel_size"], 3)
        _, problem = models.apply_preset(state, "claude", self.OPTIONS)
        self.assertIsNone(problem)
        saved = models.load_config(state)
        self.assertEqual(saved["reviewer"], "claude:sonnet")     # the workers' own model, reviewing with fresh eyes
        self.assertEqual(saved["worker"], "claude:sonnet")
        self.assertEqual(models.load_tuning(state)["panel_size"], 1)
        self.assertIn("needs", models.apply_preset(state, "fast", self.OPTIONS[:2])[1])

    def test_opencode_adapter_reads_its_json_events(self):
        import json as j
        import stat
        from unittest import mock
        from mp_agent.providers import make_agent
        bin_dir, seen = tmpdir(), tempfile.mktemp()
        fake = os.path.join(bin_dir, "opencode")
        events = [{"type": "step_start", "part": {}},
                  {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "a.py"}}}},
                  {"type": "text", "part": {"text": "Looks right."}},
                  {"type": "text", "part": {"text": "VERDICT: APPROVED"}},
                  {"type": "step_finish", "part": {"cost": 0.002, "tokens": {"input": 120, "output": 30, "reasoning": 5,
                                                                             "cache": {"read": 40, "write": 0}}}}]
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\n"
                     f"printf '%s\\n' \"$@\" > {seen}\n"
                     f"echo \"CONFIG=$OPENCODE_CONFIG_CONTENT\" >> {seen}\n"
                     + "".join(f"echo '{j.dumps(e)}'\n" for e in events))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            judge = make_agent("opencode:ollama/qwen3")
            reply = judge.ask("SYSTEM", "PROMPT", tmpdir())
            with open(seen) as fh:
                judge_args = fh.read()
            worker = make_agent("opencode:openrouter/qwen/qwen3-coder", worker=True)
            worker.ask("SYSTEM", "PROMPT", tmpdir())
            with open(seen) as fh:
                worker_args = fh.read()
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "Looks right.\nVERDICT: APPROVED")
        self.assertEqual(reply.usage["ollama/qwen3"]["output"], 35)
        self.assertEqual(judge.billing, "local")
        self.assertEqual(worker.billing, "api")
        self.assertIn('"edit": "deny"', judge_args)
        self.assertNotIn("--auto", judge_args)
        self.assertIn("--auto", worker_args)
        self.assertIn("openrouter/qwen/qwen3-coder", worker_args)

    def test_worker_and_judge_get_different_permissions(self):
        from mp_agent.providers import make_agent
        self.assertIn("Edit", make_agent("claude:haiku", worker=True).tools)
        self.assertNotIn("Edit", make_agent("claude:opus").tools)
        self.assertTrue(make_agent("codex:gpt-5.5", worker=True).worker)
        self.assertFalse(make_agent("codex:gpt-5.5").worker)


class Antigravity(unittest.TestCase):
    def test_json_status_decides_and_quota_is_fatal(self):
        from unittest import mock
        from mp_agent import providers
        ok = providers.Reply('{"status":"SUCCESS","response":"fine\\nVERDICT: APPROVED",'
                             '"usage":{"input_tokens":10,"output_tokens":2,"thinking_tokens":1}}', 0, 1.0)
        quota = providers.Reply('{"status":"ERROR","response":"","error":"API error: RESOURCE_EXHAUSTED (code 429): '
                                'Individual quota reached. Please upgrade your subscription."}', 0, 1.0)
        agent = providers.make_agent("antigravity:gemini-3.1-pro-high")
        with mock.patch.object(providers, "run_cli", return_value=ok) as run:
            reply = agent.ask("system", "prompt", "/tmp/project")
            cmd = run.call_args[0][0]
        self.assertEqual(providers.verdict(reply.text), "APPROVED")
        self.assertEqual(reply.usage["gemini-3.1-pro-high"]["output"], 3)
        self.assertIn("plan", cmd)
        self.assertIn("/tmp/project", cmd)
        with mock.patch.object(providers, "run_cli", return_value=quota):
            reply = agent.ask("system", "prompt", "/tmp/project")
        self.assertFalse(reply.ok)
        self.assertEqual(providers.classify(reply.status, reply.text), "fatal")
        worker = providers.make_agent("antigravity:gpt-oss-120b-medium", worker=True)
        with mock.patch.object(providers, "run_cli", return_value=ok) as run:
            worker.ask("system", "prompt", "/tmp/project")
            self.assertIn("--dangerously-skip-permissions", run.call_args[0][0])


class ToolActivity(unittest.TestCase):
    def test_categories_and_details(self):
        from mp_agent.providers import activity_detail, categorize
        self.assertEqual(categorize("context7__query-docs"), "docs")
        self.assertEqual(categorize("mcp__context7__resolve-library-id"), "docs")
        self.assertEqual(categorize("playwright__browser_navigate"), "browser")
        self.assertEqual(categorize("fetch_web_content"), "web")
        self.assertEqual(categorize("read_files"), "files")
        self.assertEqual(categorize("run_commands"), "terminal")
        self.assertEqual(categorize("editor"), "edit")
        self.assertEqual(activity_detail('{"query":"GET route","libraryName":"FastAPI"}'), "FastAPI")
        self.assertEqual(activity_detail('{"url":"https://example.com"}'), "https://example.com")

    def test_cline_tool_lines_are_reported_for_the_calling_actor(self):
        from unittest import mock
        from mp_agent import providers
        events = []
        agent = providers.make_agent("cline:inception:mercury-2.5")
        agent.on_activity = events.append

        def fake_run(cmd, cwd, timeout, stdin_text=None, env=None, on_line=None):
            for line in ["thinking...", '[context7__resolve-library-id] {"libraryName":"FastAPI"}',
                         '[playwright__browser_navigate] {"url":"https://example.com"}', "done"]:
                on_line(line)
            return providers.Reply("done", 0, 1.0)

        with mock.patch.object(providers, "run_cli", side_effect=fake_run), providers.acting("reviewer", "reader"):
            agent.ask("s", "p", "/tmp")
        self.assertEqual([(e["role"], e["unit"], e["category"], e["detail"]) for e in events],
                         [("reviewer", "reader", "docs", "FastAPI"), ("reviewer", "reader", "browser", "https://example.com")])

    def test_claude_loads_only_our_mcp_servers(self):
        from unittest import mock
        from mp_agent import providers
        from mp_agent import mcp
        tools = mcp.Tools({"docs": {"command": "npx", "args": ["-y", "docs"]}}, tmpdir())
        path = tools.claude_file
        agent = providers.make_agent("claude:opus", mcp=tools)
        result = '{"type":"result","result":"VERDICT: APPROVED","total_cost_usd":0.1,"modelUsage":{}}'
        with mock.patch.object(providers, "run_cli", return_value=providers.Reply(result, 0, 1.0)) as run:
            reply = agent.ask("s", "p", "/tmp")
            cmd = run.call_args[0][0]
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--mcp-config") + 1], path)
        self.assertEqual(reply.text, "VERDICT: APPROVED")


class Places(unittest.TestCase):
    def test_local_repos_found_newest_first_and_github_names_parsed(self):
        from mp_agent import where
        home = tmpdir("mp-home-")
        for name, remote in (("alpha", "git@github.com:someone/alpha.git"), ("work/beta", "https://github.com/keepout/beta"),
                             (".hidden/gamma", "")):
            path = os.path.join(home, name)
            os.makedirs(path)
            sh(path, "git", "init", "-q", "-b", "main")
            if remote:
                sh(path, "git", "remote", "add", "origin", remote)
            write(path, "f.txt", name)
            sh(path, "git", "add", "-A")
            sh(path, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
        write(os.path.join(home, "alpha"), "f.txt", "changed")
        state = tmpdir("mp-state-")
        write(state, "config.json", json.dumps({"protected": ["KeepOut"]}))
        repos = where.local_repos(home, state_dir=state)
        names = [r["name"] for r in repos]
        self.assertIn("alpha", names)
        self.assertIn(os.path.join("work", "beta"), names)
        self.assertNotIn(os.path.join(".hidden", "gamma"), names)
        alpha = next(r for r in repos if r["name"] == "alpha")
        beta = next(r for r in repos if r["name"].endswith("beta"))
        self.assertTrue(alpha["dirty"])
        self.assertEqual(alpha["github"], "someone/alpha")
        self.assertFalse(alpha["protected"])
        self.assertTrue(beta["protected"])
        self.assertEqual(where.find_clone("someone/alpha", home)["name"], "alpha")
        with self.assertRaises(gitops.SetupError):
            where.prepare_github("not a repo name", home)


class QueueAndHistory(unittest.TestCase):
    def test_queue_order_remove_and_history(self):
        from mp_agent import jobqueue
        state = tmpdir()
        a = jobqueue.add(state, "first task", ["--new"])
        b = jobqueue.add(state, "second task", ["--repo", "/x"])
        c = jobqueue.add(state, "third task", ["--oneoff"])
        self.assertTrue(jobqueue.remove(state, b["id"]))
        self.assertEqual(jobqueue.take_next(state)["id"], a["id"])
        jobqueue.record(state, a, "Started: first task", "/runs/1")
        data = jobqueue.listing(state)
        self.assertEqual([j["id"] for j in data["jobs"]], [c["id"]])
        self.assertEqual(data["history"][0]["run"], "/runs/1")

    def test_history_counts_gates_and_projects(self):
        import json as j
        from mp_agent import history
        runs = tmpdir()
        run = os.path.join(runs, "20260915T000000Z-x")
        os.makedirs(run)
        with open(os.path.join(run, "plan.json"), "w") as fh:
            j.dump({"mode": "single"}, fh)
        with open(os.path.join(run, "metadata.json"), "w") as fh:
            j.dump({"approved": True, "project": "/p/demo", "seconds": 60, "costs": {"mercury_usd": 0.01},
                    "units": {"main": {"passes": 2}}, "task": "t"}, fh)
        with open(os.path.join(run, "run.log"), "w") as fh:
            fh.write("── pass 1\n   panel edges: objected\n   panel asked for changes\n── pass 2\n"
                     "   panel approved\n   final reviewer asked for changes\n")
        s = history.summarize(runs)
        self.assertEqual(s["gates"]["panel_objections"], 1)
        self.assertEqual(s["gates"]["judge_after_panel"], 1)
        self.assertEqual(s["lenses"], {"edges": 1})
        self.assertEqual(s["projects"][0]["project"], "demo")


class StartArguments(unittest.TestCase):
    def test_start_keeps_the_spending_cap_out_of_the_task(self):
        from unittest import mock
        from mp_agent import cli
        home = tmpdir("mp-home-")
        launched = {}

        def fake_launch(command, cwd):
            launched["command"] = command
            return "/runs/x"

        with mock.patch.object(cli, "HOME", home), mock.patch.object(cli, "launch", side_effect=fake_launch), \
                mock.patch.object(cli, "choose_models", return_value=({"worker": "w", "planner": "p", "judge": "j", "reviewer": "", "panel": "r", "designer": ""}, [], None)), \
                mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.read.return_value = "Create notes.txt containing the word hello."
            self.assertEqual(cli.start(["--oneoff", "--name", "cap-test", "--max-usd", "0.001"]), 0)
        command = launched["command"]
        self.assertEqual(command[-1], "Create notes.txt containing the word hello.")
        self.assertEqual(command[command.index("--max-usd") + 1], "0.001")
        self.assertEqual(command[command.index("--reviewer") + 1], "same")
        self.assertEqual(command[command.index("--panel-model") + 1], "r")
        self.assertEqual(command[command.index("--designer") + 1], "same")
        # a flag start does not know is passed on whole, never read as the start of --panel-model
        with mock.patch.object(cli, "HOME", home), mock.patch.object(cli, "launch", side_effect=fake_launch), \
                mock.patch.object(cli, "choose_models", return_value=({"worker": "w", "planner": "p", "judge": "j", "reviewer": "", "panel": ""}, [], None)) as chose, \
                mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.read.return_value = "Create notes.txt containing the word hello."
            self.assertEqual(cli.start(["--oneoff", "--name", "panel-test", "--panel", "1"]), 0)
        self.assertEqual(chose.call_args[0][0], {})
        command = launched["command"]
        self.assertEqual(command[command.index("--panel") + 1], "1")


class Tidy(unittest.TestCase):
    def test_plans_then_tidies_only_leftovers_and_archives_runs(self):
        import json as j
        from mp_agent import tidy
        state = tmpdir("mp-state-")
        project = make_repo({"a.txt": "a"})
        for name, repo in (("20260915T000001Z-gone", "/nonexistent/project"), ("20260915T000002Z-here", project)):
            run = os.path.join(state, "runs", name)
            os.makedirs(run)
            with open(os.path.join(run, "repo"), "w") as fh:
                fh.write(repo)
        os.makedirs(os.path.join(state, "projects", "gone-1"))
        with open(os.path.join(state, "projects", "gone-1", "memory.md"), "w") as fh:
            fh.write("# What mp-agent runs have settled for /nonexistent/project\n")
        os.makedirs(os.path.join(state, "shots"))
        old_shot = os.path.join(state, "shots", "page-old.png")
        open(old_shot, "w").close()
        os.utime(old_shot, (0, 0))
        open(os.path.join(state, "shots", "page-new.png"), "w").close()
        with open(os.path.join(state, "queue.json"), "w") as fh:
            j.dump({"jobs": [{"id": "x"}], "history": [{"label": "done"}]}, fh)

        items = {i["id"]: i for i in tidy.plan(state)["items"]}
        self.assertEqual(items["orphan_runs"]["count"], 1)
        self.assertEqual(items["memories"]["count"], 1)
        self.assertEqual(items["shots"]["count"], 1)
        self.assertTrue(os.path.isdir(os.path.join(state, "runs", "20260915T000001Z-gone")))   # planning changes nothing

        tidy.apply(state, ["orphan_runs", "memories", "shots", "queue_history"])
        self.assertFalse(os.path.exists(os.path.join(state, "runs", "20260915T000001Z-gone")))
        self.assertTrue(os.path.isdir(os.path.join(state, "runs", "20260915T000002Z-here")))
        archived = glob.glob(os.path.join(state, "runs-archive-*", "20260915T000001Z-gone"))
        self.assertEqual(len(archived), 1)
        self.assertFalse(os.path.exists(os.path.join(state, "projects", "gone-1")))
        self.assertTrue(os.path.exists(os.path.join(state, "shots", "page-new.png")))
        with open(os.path.join(state, "queue.json")) as fh:
            q = j.load(fh)
        self.assertEqual((len(q["jobs"]), len(q["history"])), (1, 0))       # waiting jobs are never touched
        self.assertTrue(os.path.isdir(project))                               # projects are never touched


class Pacing(unittest.TestCase):
    def test_learns_from_refusals_and_recovers(self):
        pacer = Pacer(tmpdir(), floor=3)
        self.assertEqual(pacer.interval("inception"), 3)
        self.assertEqual(pacer.refused("inception"), 20)
        self.assertEqual(pacer.refused("inception"), 40)
        for _ in range(Pacer.NARROW_AFTER):
            pacer.succeeded("inception")
        self.assertEqual(pacer.interval("inception"), 30)
        other = Pacer(pacer.directory, floor=3)          # a separate run shares what was learned
        self.assertEqual(other.interval("inception"), 30)
        self.assertEqual(other.interval("claude"), 3)    # providers are separate

    def test_waits_are_recorded(self):
        waited = []
        pacer = Pacer(tmpdir(), floor=0.3, record=waited.append)
        pacer.wait_turn("k")
        pacer.wait_turn("k")
        self.assertEqual(len(waited), 1)
        self.assertGreater(waited[0], 0.1)


class Retries(unittest.TestCase):
    def test_transient_rate_and_missing_cli_are_retried(self):
        replies = [Reply("error: The server had an error while processing your request.", 1),
                   Reply("Rate limit reached: input token limit exceeded", 1),
                   Reply("", 127),
                   Reply("fine", 0)]

        class Flaky:
            name = pace_key = "flaky"

            def ask(self, *a):
                return replies.pop(0)

        said, slept = [], []
        agent = Retrying(Flaky(), Pacer(tmpdir(), 0, sleep=lambda *_: None), said.append, retries=4,
                         sleep=slept.append)
        self.assertTrue(agent.ask("s", "p", "/tmp").ok)
        self.assertEqual(slept, [30, 120, 120])   # transient, then rate (2nd step), then missing (3rd step)
        self.assertTrue(any("provider error" in s for s in said))

    def test_real_failure_is_not_retried(self):
        class Broken:
            name = pace_key = "broken"
            calls = 0

            def ask(self, *a):
                Broken.calls += 1
                return Reply("TypeError: something in the model's own work", 1)

        agent = Retrying(Broken(), Pacer(tmpdir(), 0), lambda *_: None, sleep=lambda *_: None)
        self.assertFalse(agent.ask("s", "p", "/tmp").ok)
        self.assertEqual(Broken.calls, 1)


if __name__ == "__main__":
    unittest.main()


class Keys(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        self.state = tmpdir("mp-keys-")
        self.patch = mock.patch.dict(os.environ, {"MP_KEYS_BACKEND": "file"})
        self.patch.start()
        for name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.patch.stop()

    def test_stored_keys_are_never_shown_and_only_reach_the_environment(self):
        import json as j
        import stat
        from mp_agent import keys
        saved = keys.set_key(self.state, "openrouter", "  sk-or-v1-abcdefghij1234\n")
        self.assertEqual(saved["last4"], "1234")
        status = keys.status(self.state)
        self.assertNotIn("abcdefghij", j.dumps(status))
        self.assertTrue(next(p for p in status["providers"] if p["id"] == "openrouter")["stored"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, "keys.json")).st_mode), 0o600)
        self.assertEqual(keys.environment(self.state), {"OPENROUTER_API_KEY": "sk-or-v1-abcdefghij1234"})
        self.assertEqual(keys.environment(self.state, only={"google"}), {})
        os.environ["OPENROUTER_API_KEY"] = "mine"                   # the person's own variable wins
        self.assertEqual(keys.environment(self.state), {})
        del os.environ["OPENROUTER_API_KEY"]
        with self.assertRaises(keys.KeyError_):
            keys.set_key(self.state, "openrouter", "short")
        with self.assertRaises(keys.KeyError_):
            keys.set_key(self.state, "nosuch", "abcdefghijklmnop")
        self.assertTrue(keys.remove_key(self.state, "openrouter"))
        self.assertEqual(keys.environment(self.state), {})
        self.assertFalse(next(p for p in keys.status(self.state)["providers"] if p["id"] == "openrouter")["stored"])

    def test_the_keyring_gets_the_key_on_stdin_not_the_command_line(self):
        from unittest import mock
        from mp_agent import keys
        calls = []

        def fake_run(cmd, stdin_text=None, timeout=15):
            calls.append((cmd, stdin_text))
            return subprocess.CompletedProcess(cmd, 0, "sk-secret-value-99\n" if "lookup" in cmd else "", "")

        import subprocess
        with mock.patch.object(keys, "_run", side_effect=fake_run):
            keys.SecretService().set("openai", "sk-secret-value-99")
            self.assertEqual(keys.SecretService().get("openai"), "sk-secret-value-99")
            keys.MacKeychain().set("openai", 'sk-with"quote')
        self.assertNotIn("sk-secret-value-99", " ".join(calls[0][0]))
        self.assertEqual(calls[0][1], "sk-secret-value-99")
        self.assertEqual(calls[2][0], ["security", "-i"])
        self.assertIn('-w "sk-with\\"quote"', calls[2][1])

    def test_claude_code_gets_a_key_only_when_api_billing_is_chosen(self):
        from mp_agent.providers import make_agent
        self.assertEqual(make_agent("claude:opus").billing, "subscription")
        paid = make_agent("claude:opus", claude_api_key="sk-ant-xyz")
        self.assertEqual((paid.billing, paid.api_key), ("api", "sk-ant-xyz"))
        gemini = make_agent("gemini:default", key_env={"GEMINI_API_KEY": "g-key"})
        self.assertEqual(gemini.key_env, {"GEMINI_API_KEY": "g-key"})


class Setup(unittest.TestCase):
    def test_scan_says_what_is_ready_and_what_to_do_next(self):
        from unittest import mock
        from mp_agent import models, setup
        state = tmpdir("mp-setup-")
        options = [{"spec": "claude:opus", "label": "Opus"}, {"spec": "claude:sonnet", "label": "Sonnet"},
                   {"spec": "cline:inception:mercury-2.5", "label": "Mercury"}]
        which = {"claude": "/bin/claude", "cline": "/bin/cline", "gemini": "/bin/gemini"}
        with mock.patch.object(models, "available", return_value=options), \
                mock.patch.object(setup.shutil, "which", side_effect=lambda c: which.get(c)), \
                mock.patch.dict(os.environ, {"MP_KEYS_BACKEND": "file"}):
            info = setup.scan(state)
        tools = {t["id"]: t for t in info["tools"]}
        self.assertEqual((tools["claude"]["ready"], tools["claude"]["models"]), (True, 2))
        self.assertEqual((tools["gemini"]["installed"], tools["gemini"]["ready"]), (True, False))
        self.assertFalse(tools["opencode"]["installed"])
        self.assertTrue(info["ready"])
        self.assertTrue(info["first_time"])
        self.assertEqual([p["id"] for p in info["presets"] if p["roles"]], ["fast", "claude"])
        models.save_config(state, {"worker": "codex:gpt-5.5"})
        with mock.patch.object(models, "available", return_value=options), \
                mock.patch.dict(os.environ, {"MP_KEYS_BACKEND": "file"}):
            info = setup.scan(state)
        self.assertFalse(info["ready"])
        self.assertIn("the worker (codex:gpt-5.5)", info["ready_detail"])

    def test_model_test_is_one_read_only_call(self):
        from unittest import mock
        from mp_agent import setup
        seen = {}

        class Fake:
            def ask(self, system, prompt, cwd):
                seen["worker"] = made["worker"]
                return Reply("READY", 0, 0.1) if "good" in made["spec"] else Reply("Error: invalid api key", 1, 0.1)

        made = {}

        def fake_make(spec, timeout, worker=False, **kw):
            made.update(spec=spec, worker=worker)
            return Fake()

        state = tmpdir("mp-setup-")
        with mock.patch.object(setup, "make_agent", side_effect=fake_make), \
                mock.patch.dict(os.environ, {"MP_KEYS_BACKEND": "file"}):
            good = setup.test_model(state, "x:good")
            bad = setup.test_model(state, "x:bad")
        self.assertTrue(good["ok"])
        self.assertFalse(seen["worker"])
        self.assertEqual((bad["ok"], bad["kind"]), (False, "fatal"))
        self.assertIn("invalid api key", bad["problem"])


class WorkshopServer(unittest.TestCase):
    def test_page_files_are_served_from_the_page_folder_only(self):
        import socket
        import time as t
        import urllib.error
        import urllib.request
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        home, site = tmpdir("mp-viz-home-"), tmpdir("mp-viz-page-")
        write(site, "viz/index.html", "<p>hi</p>")
        write(site, "viz/eggs/cat.js", "window.cat = 1;")
        write(site, "secret.js", "nope")
        env = {**os.environ, "MP_VIZ_PORT": str(port), "MP_HOME": home, "MP_RUNS": os.path.join(home, "runs"),
               "MP_KEYS_BACKEND": "file", "MP_VIZ_PAGE": os.path.join(site, "viz", "index.html")}
        viz = os.path.join(os.path.dirname(__file__), "..", "bin", "mp-viz")
        server = subprocess.Popen([sys.executable, viz, "--no-open"], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)

        def get(path):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
                    return r.status, r.headers.get("Content-Type"), r.read().decode()
            except urllib.error.HTTPError as exc:
                return exc.code, None, ""
            except OSError:
                return None, None, ""

        try:
            for _ in range(50):
                if get("/")[0] == 200:
                    break
                t.sleep(0.1)
            status, kind, body = get("/eggs/cat.js")
            self.assertEqual((status, body), (200, "window.cat = 1;"))
            self.assertIn("javascript", kind)
            self.assertEqual(get("/../secret.js")[0], 404)
            self.assertEqual(get("/%2e%2e/secret.js")[0], 404)
            self.assertEqual(get("/missing.js")[0], 404)
        finally:
            server.terminate()
            server.wait(timeout=10)

    def test_host_origin_and_keys_guards(self):
        import socket
        import subprocess
        import time as t
        import urllib.error
        import urllib.request
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        home = tmpdir("mp-viz-home-")
        env = {**os.environ, "MP_VIZ_PORT": str(port), "MP_HOME": home, "MP_RUNS": os.path.join(home, "runs"),
               "MP_KEYS_BACKEND": "file"}
        env.pop("OPENROUTER_API_KEY", None)
        viz = os.path.join(os.path.dirname(__file__), "..", "bin", "mp-viz")
        server = subprocess.Popen([sys.executable, viz, "--no-open"], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"

        def call(path, body=None, headers=None):
            data = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(base + path, data=data, headers=headers or {})
            try:
                with urllib.request.urlopen(request, timeout=60) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode()

        try:
            for _ in range(50):
                try:
                    if call("/api/runs")[0] == 200:
                        break
                except OSError:
                    t.sleep(0.1)
            good = {"Content-Type": "application/json", "Origin": base}
            self.assertEqual(call("/api/runs", headers={"Host": f"evil.example:{port}"})[0], 403)
            self.assertEqual(call("/api/keys/set", {"provider": "openrouter", "key": "sk-or-abcdefgh5678"},
                                  {**good, "Host": f"evil.example:{port}"})[0], 403)
            self.assertEqual(call("/api/keys/set", {"provider": "openrouter", "key": "sk-or-abcdefgh5678"},
                                  {"Content-Type": "application/json", "Origin": "https://evil.example"})[0], 403)
            status, text = call("/api/keys/set", {"provider": "openrouter", "key": "sk-or-abcdefgh5678"}, good)
            self.assertEqual(status, 200, text)
            self.assertNotIn("abcdefgh", text)
            self.assertIn("5678", text)
            with open(os.path.join(home, "keys.json")) as fh:
                self.assertIn("sk-or-abcdefgh5678", fh.read())
            status, text = call("/api/setup")
            self.assertEqual(status, 200, text)
            self.assertNotIn("abcdefgh", text)
            self.assertTrue(next(p for p in json.loads(text)["keys"]["providers"] if p["id"] == "openrouter")["stored"])
            self.assertEqual(call("/api/keys/remove", {"provider": "openrouter"}, good)[0], 200)
            self.assertEqual(call("/api/keys/set", {"provider": "../../x", "key": "abcdefghijk"}, good)[0], 400)
            self.assertEqual(json.loads(call("/api/projects")[1]), [])
            self.assertEqual(json.loads(call("/api/runs?project=/nowhere")[1]), [])
            # a folder no run has worked in is never opened, whatever the page asks
            self.assertEqual(call("/api/open-folder", {"project": home}, good)[0], 404)
            self.assertEqual(call("/api/projects/open", {"path": "/etc"}, good)[0], 400)      # only inside home
            self.assertEqual(call("/api/repo?project=/etc")[0], 404)                          # only known projects
        finally:
            server.terminate()
            server.wait(timeout=10)


class Desktop(unittest.TestCase):
    def test_mac_and_linux_commands(self):
        from unittest import mock
        from mp_agent import desktop
        launched = []
        with mock.patch.object(desktop, "_quiet", side_effect=lambda cmd, **kw: launched.append(cmd) or True):
            with mock.patch.object(desktop, "MAC", True):
                desktop.notify('Title "x"', "it's done; rm -rf /")
                desktop.open_folder("/Users/me/project")
            with mock.patch.object(desktop, "MAC", False):
                desktop.notify("Title", "done")
                desktop.open_folder("/home/me/project")
        mac_notify, mac_open, linux_notify, linux_open = launched
        self.assertEqual(mac_notify[0], "osascript")
        self.assertEqual(mac_notify[-2:], ['Title "x"', "it's done; rm -rf /"])     # arguments, not script text
        self.assertNotIn("rm -rf", " ".join(mac_notify[:-2]))
        self.assertEqual(mac_open, ["open", "/Users/me/project"])
        self.assertEqual(linux_notify[0], "notify-send")
        self.assertEqual(linux_open, ["xdg-open", "/home/me/project"])

    def test_screen_size_on_a_mac_and_window_size(self):
        from unittest import mock
        from mp_agent import desktop
        retina = "Displays:\n  Color LCD:\n    Resolution: 3024 x 1964 Retina\n    UI Looks like: 1512 x 982 @ 120.00Hz\n"
        external = "Displays:\n  DELL:\n    Resolution: 2560 x 1440 (QHD)\n"
        for text, expected in ((retina, (1512, 982)), (external, (2560, 1440))):
            with mock.patch.object(desktop, "MAC", True), \
                    mock.patch.object(desktop.subprocess, "run",
                                      return_value=subprocess.CompletedProcess([], 0, text, "")):
                self.assertEqual(desktop.screen_size(), expected)
        self.assertEqual(desktop.window_size((2560, 1440)), (2000, 1296))
        self.assertEqual(desktop.window_size((1512, 982)), (1285, 883))
        with mock.patch.object(desktop, "screen_size", return_value=None):
            self.assertEqual(desktop.window_size(), (1600, 1000))

    def test_command_of_a_running_process(self):
        from mp_agent import desktop
        self.assertIn("python", desktop.command_of(os.getpid()).lower())
        self.assertEqual(desktop.command_of(99999999), "")


class Launchers(unittest.TestCase):
    def test_each_tool_gets_its_own_format_and_foreign_files_are_left_alone(self):
        from unittest import mock
        from mp_agent import launchers
        claude = launchers.render("claude")
        self.assertTrue(claude.startswith("---\ndescription: "))
        self.assertIn("allowed-tools: Bash(mp-agent:*)", claude)
        self.assertIn("The task is: $ARGUMENTS", claude)
        gemini = launchers.render("gemini")
        self.assertIn("The task is: {{args}}", gemini)
        try:
            import tomllib
        except ImportError:                                  # Python before 3.11
            tomllib = None
        if tomllib:
            parsed = tomllib.loads(gemini)
            self.assertIn("mp-agent start", parsed["prompt"])
            self.assertTrue(parsed["description"])
        codex = launchers.render("codex")
        self.assertIn("name: mp-agent", codex)
        self.assertNotIn("$ARGUMENTS", codex)                # Codex skills do not substitute it
        self.assertIn("The task is the text that follows", launchers.render("cline"))
        home = tmpdir("mp-launch-")
        os.makedirs(os.path.join(home, ".claude", "commands"))
        os.makedirs(os.path.join(home, ".gemini"))
        write(os.path.join(home, ".claude", "commands"), "mp-agent.md", "my own command\n")
        with mock.patch.object(launchers.shutil, "which", return_value=None):
            done = {tool: what for tool, _, what in launchers.install(home)}
        self.assertIn("left alone", done["claude"])
        self.assertEqual(done["gemini"], "installed")
        self.assertNotIn("codex", done)                      # not installed: nothing written
        with open(os.path.join(home, ".claude", "commands", "mp-agent.md")) as fh:
            self.assertEqual(fh.read(), "my own command\n")
        with mock.patch.object(launchers.shutil, "which", return_value=None):
            self.assertEqual({t: w for t, _, w in launchers.install(home)}["gemini"], "up to date")


class Answers(unittest.TestCase):
    def test_a_half_written_answer_is_waited_for_not_a_crash(self):
        from mp_agent.context import Context
        folder = tmpdir("mp-answer-")
        path = os.path.join(folder, "answer.json")
        self.assertIsNone(Context._read_answer(path))            # not there yet
        write(folder, "answer.json", "")
        self.assertIsNone(Context._read_answer(path))            # created, nothing in it yet
        write(folder, "answer.json", '{"answer": "ye')
        self.assertIsNone(Context._read_answer(path))            # halfway through
        write(folder, "answer.json", '{"answer": " yes "}')
        self.assertEqual(Context._read_answer(path), "yes")


class AppIcon(unittest.TestCase):
    def test_icon_is_a_real_png_and_each_platform_gets_its_launcher(self):
        import struct
        import zlib
        from mp_agent import appicon
        data = appicon.png(64)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (64, 64))
        idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
        self.assertEqual(len(zlib.decompress(idat)), 64 * (64 * 4 + 1))
        home = tmpdir("mp-app-")
        written = appicon.install(home, "/opt/uiw/bin/mp-viz", path_value="/usr/bin:/it's/here", platform="linux")
        desktop = next(p for p in written if p.endswith(".desktop"))
        with open(desktop) as fh:
            entry = fh.read()
        self.assertIn("Name=Until It Works(hop)", entry)
        self.assertIn("Terminal=false", entry)
        launcher = appicon.paths(home, "linux")["launcher"]
        self.assertTrue(os.access(launcher, os.X_OK))
        with open(launcher) as fh:
            script = fh.read()
        self.assertIn("--window", script)
        out = subprocess.run(["sh", "-c", script.replace("exec ", "echo PATH=$PATH; : ", 1)], capture_output=True, text=True)
        self.assertIn("PATH=/usr/bin:/it's/here", out.stdout)             # a quote in PATH survives
        mac_home = tmpdir("mp-app-mac-")
        mac = appicon.install(mac_home, "/opt/uiw/bin/mp-viz", path_value="/usr/bin", platform="darwin")
        app = appicon.paths(mac_home, "darwin")["app"]
        self.assertIn(app, mac)
        self.assertTrue(os.access(os.path.join(app, "Contents", "MacOS", "launch"), os.X_OK))
        with open(os.path.join(app, "Contents", "Info.plist")) as fh:
            self.assertIn("<key>CFBundleExecutable</key><string>launch</string>", fh.read())


class Protected(unittest.TestCase):
    def test_protect_add_remove_keeps_the_rest_of_the_config(self):
        from unittest import mock
        from mp_agent import cli, where
        state = tmpdir("mp-protect-")
        write(state, "config.json", json.dumps({"max_usd": 3, "protected": ["someone"]}))
        with mock.patch.object(cli, "STATE", state):
            self.assertEqual(cli.protect_command(["add", "Other/Repo"]), 0)
            self.assertEqual(cli.protect_command(["add", "not valid!"]), 1)
            self.assertEqual(where.protected(state), ["other/repo", "someone"])
            self.assertEqual(cli.protect_command(["remove", "someone"]), 0)
            self.assertEqual(cli.protect_command(["remove", "nobody"]), 1)
        with open(os.path.join(state, "config.json")) as fh:
            self.assertEqual(json.load(fh), {"max_usd": 3, "protected": ["other/repo"]})
        self.assertTrue(where.is_protected("git@github.com:other/repo.git", state))
        self.assertFalse(where.is_protected("git@github.com:other/another.git", state))


class ClineHub(unittest.TestCase):
    def test_a_hub_whose_folder_was_removed_is_restarted_and_a_healthy_one_is_left(self):
        from mp_agent import providers
        alive = tmpdir("mp-hub-")
        gone = os.path.join(tmpdir("mp-hub-"), "removed-worktree")
        processes = [(111, f"node .cline --cline-hub-daemon --cwd {gone} --host 127.0.0.1 --port 1"),
                     (222, f"node .cline --cline-hub-daemon --cwd {alive} --host 127.0.0.1 --port 2"),
                     (333, "cline --cwd /x something else")]
        self.assertEqual(providers.stale_cline_hubs(lambda: processes), [111])


class Projects(unittest.TestCase):
    def test_projects_count_runs_and_results_waiting_for_a_decision(self):
        import json as j
        from mp_agent import history, projects
        runs = tmpdir("mp-proj-runs-")
        shop, blog = tmpdir("shop-"), tmpdir("blog-")
        gone = os.path.join(tmpdir(), "deleted")

        def run(name, project, meta, live=False):
            d = os.path.join(runs, name)
            os.makedirs(d)
            write(d, "repo", project + "\n")
            write(d, "plan.json", "{}")
            write(d, "metadata.json", j.dumps({"project": project, "units": {}, **meta}))
            if live:
                write(d, "question.json", "{}")
            return d

        run("r1", shop, {"approved": True, "resolution": {"action": "kept"}})
        run("r2", shop, {"approved": True})                       # waiting for KEEP or DISCARD
        run("r3", shop, {"approved": False})
        run("r4", blog, {"approved": True, "oneoff": True})        # a one-off keeps itself
        live = run("r5", blog, {}, live=True)
        run("r6", gone, {"approved": True})
        found = projects.list_projects(runs, alive=lambda d: d == live)
        by_path = {p["path"]: p for p in found}
        self.assertNotIn(os.path.realpath(gone), by_path)              # gone folders are not offered
        s, b = by_path[os.path.realpath(shop)], by_path[os.path.realpath(blog)]
        self.assertEqual((s["runs"], s["to_decide"], s["live"]), (3, 1, False))
        self.assertEqual((b["runs"], b["to_decide"], b["live"], b["needs_you"]), (2, 0, True, True))
        self.assertEqual(history.summarize(runs, project=shop)["totals"]["runs"], 3)
        self.assertEqual(history.summarize(runs)["totals"]["runs"], 6)

    def test_two_folders_with_the_same_name_are_told_apart(self):
        from mp_agent import projects
        runs = tmpdir("mp-proj-runs-")
        a = os.path.join(tmpdir("clientA-"), "site")
        b = os.path.join(tmpdir("clientB-"), "site")
        for i, path in enumerate((a, b)):
            os.makedirs(path)
            d = os.path.join(runs, f"r{i}")
            os.makedirs(d)
            write(d, "repo", path)
        names = sorted(p["name"] for p in projects.list_projects(runs, alive=lambda d: False))
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(n.endswith("/site") for n in names))


class ProjectRules(unittest.TestCase):
    def test_finds_every_kind_of_instruction_file_once_and_respects_the_switch(self):
        from mp_agent import rules
        repo = tmpdir("mp-rules-")
        write(repo, "CLAUDE.md", "Use tabs.")
        write(repo, "AGENTS.md", "Use tabs.")                              # an identical copy is given once
        write(repo, ".cursor/rules/php.mdc", "PHP must pass phpcs.")
        write(repo, ".clinerules/style.md", "No jQuery.")
        write(repo, ".github/copilot-instructions.md", "British English.")
        write(repo, "rules.md", "x" * (rules.PER_FILE + 500))
        outside = tmpdir("mp-outside-")
        write(outside, "secret.md", "not the project's")
        os.symlink(os.path.join(outside, "secret.md"), os.path.join(repo, "CONVENTIONS.md"))
        found = dict(rules.find(repo))
        self.assertEqual(sorted(found), sorted(["CLAUDE.md", ".cursor/rules/php.mdc", ".clinerules/style.md",
                                                ".github/copilot-instructions.md", "rules.md"]))
        self.assertIn("left out", found["rules.md"])
        text = rules.render(rules.find(repo))
        self.assertTrue(text.startswith("# Project rules"))
        self.assertIn("never override the rules of this loop", text)
        state = tmpdir("mp-rules-state-")
        self.assertTrue(rules.collect(state, repo)[1])
        rules.set_enabled(state, repo, False)
        self.assertEqual(rules.collect(state, repo), ([], ""))
        rules.set_enabled(state, repo, True)
        self.assertEqual(len(rules.collect(state, repo)[0]), 5)
        self.assertEqual(rules.render([]), "")


class RepoInfo(unittest.TestCase):
    def fake_gh(self, permissions):
        def run(cmd, cwd=None, timeout=15):
            if cmd[:3] == ["gh", "auth", "status"]:
                return 0, json.dumps({"hosts": {"github.com": [{"login": "me", "active": True}]}}), ""
            if cmd[:3] == ["gh", "api", "user/orgs"]:
                return 0, "my-org\n", ""
            if cmd[:3] == ["gh", "repo", "view"]:
                owner = cmd[3].split("/")[0]
                return 0, json.dumps({"isPrivate": True, "isFork": False, "viewerPermission": permissions.get(cmd[3], "READ"),
                                      "defaultBranchRef": {"name": "main"}, "owner": {"login": owner}}), ""
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            return proc.returncode, proc.stdout, proc.stderr
        return run

    def test_remotes_are_explained_and_a_push_to_someone_elses_repo_is_warned_about(self):
        from mp_agent import repoinfo
        repoinfo._CACHE.clear()
        repo = make_repo({"a.txt": "a"})
        sh(repo, "git", "remote", "add", "origin", "https://someone:ghp_secret123@github.com/client/site.git")
        sh(repo, "git", "remote", "add", "mine", "git@github.com:me/site.git")
        sh(repo, "git", "remote", "add", "work", "git@github.com:my-org/site.git")
        state = tmpdir("mp-repo-state-")
        info = repoinfo.inspect(repo, state, run=self.fake_gh({"client/site": "WRITE", "me/site": "ADMIN"}))
        remotes = {r["name"]: r for r in info["remotes"]}
        self.assertEqual((remotes["mine"]["yours"], remotes["work"]["yours"], remotes["origin"]["yours"]), (True, True, False))
        self.assertIn("belongs to client", remotes["origin"]["warning"])
        self.assertIn("you can push to it (write)", info["warnings"][0])
        self.assertNotIn("ghp_secret123", json.dumps(info))                       # credentials never reach the page
        write(state, "config.json", json.dumps({"protected": ["client"]}))
        repoinfo._CACHE.clear()
        info = repoinfo.inspect(repo, state, run=self.fake_gh({"client/site": "WRITE"}))
        origin = next(r for r in info["remotes"] if r["name"] == "origin")
        self.assertTrue(origin["protected"])
        self.assertNotIn("warning", origin)
        self.assertIn("a git push you run yourself", origin["note"])
        write(repo, "b.txt", "uncommitted")
        repoinfo._CACHE.clear()
        self.assertTrue(any("uncommitted" in w for w in repoinfo.inspect(repo, state, run=self.fake_gh({}))["warnings"]))
        plain = tmpdir("mp-not-git-")
        self.assertIn("not a git repository", repoinfo.inspect(plain, state, run=self.fake_gh({}))["warnings"][0])

    def test_folder_browser_stays_inside_home_and_marks_git_projects(self):
        from mp_agent import repoinfo
        home = tmpdir("mp-browse-home-")
        os.makedirs(os.path.join(home, "code", "shop", ".git"))
        os.makedirs(os.path.join(home, ".hidden"))
        top = repoinfo.browse(home, home)
        self.assertEqual([f["name"] for f in top["folders"]], ["code"])
        self.assertIsNone(top["parent"])
        inner = repoinfo.browse(os.path.join(home, "code"), home)
        self.assertEqual(inner["folders"][0], {"name": "shop", "path": os.path.join(os.path.realpath(home), "code", "shop"), "git": True})
        self.assertEqual(repoinfo.browse("/etc", home)["path"], os.path.realpath(home))     # outside home: back to home
        self.assertIn(".hidden", [f["name"] for f in repoinfo.browse(home, home, show_hidden=True)["folders"]])

    def test_opened_folders_join_the_project_list(self):
        from mp_agent import projects
        state, runs, folder = tmpdir(), tmpdir(), tmpdir("opened-")
        projects.open_project(state, folder)
        listed = projects.list_projects(runs, alive=lambda d: False, extra=projects.opened(state))
        self.assertEqual([(p["path"], p["runs"]) for p in listed], [(os.path.realpath(folder), 0)])


class McpServers(unittest.TestCase):
    def test_one_list_with_scopes_and_every_tool_gets_it_its_own_way(self):
        from unittest import mock
        from mp_agent import mcp, providers
        state, shots = tmpdir("mp-mcp-"), "/shots"
        shop = tmpdir("shop-")
        mcp.add(state, "sentry", mcp.parse_spec('npx -y "@sentry/mcp-server" --org acme', env_keys=["SENTRY_TOKEN"]))
        mcp.add(state, "db", mcp.parse_spec(url="https://mcp.example/db"), project=shop)
        mcp.switch_builtin(state, "playwright", False)
        self.assertEqual(sorted(mcp.servers_for(state, shots)), ["context7", "sentry"])
        self.assertEqual(sorted(mcp.servers_for(state, shots, shop)), ["context7", "db", "sentry"])
        self.assertEqual(mcp.servers_for(state, shots)["sentry"]["args"], ["-y", "@sentry/mcp-server", "--org", "acme"])
        with self.assertRaises(ValueError):
            mcp.add(state, "Bad Name!", {"command": "x"})
        with self.assertRaises(ValueError):
            mcp.parse_spec(url="ftp://nope")
        servers = mcp.servers_for(state, shots, shop)
        with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok-real-secret"}, clear=False):
            os.environ.pop("CONTEXT7_API_KEY", None)
            tools = mcp.Tools(servers, tmpdir(), codex_own=["node_repl"])
        with open(tools.claude_file) as fh:
            claude = json.load(fh)["mcpServers"]
        self.assertEqual(claude["sentry"]["env"], {"SENTRY_TOKEN": "${SENTRY_TOKEN}"})    # a reference, never the key
        self.assertEqual(claude["context7"]["env"], {})                                  # no key set: nothing referenced
        self.assertEqual(claude["db"], {"type": "http", "url": "https://mcp.example/db"})
        for path in (tools.claude_file, tools.gemini_file):
            with open(path) as fh:
                self.assertNotIn("tok-real-secret", fh.read())
        self.assertIn("mcp_servers.node_repl.enabled=false", tools.codex)
        self.assertIn('mcp_servers.sentry.env_vars=["SENTRY_TOKEN"]', tools.codex)
        self.assertEqual(tools.opencode["sentry"]["environment"], {"SENTRY_TOKEN": "{env:SENTRY_TOKEN}"})

        calls = []
        with mock.patch.object(providers, "run_cli", side_effect=lambda cmd, cwd, t, **kw: calls.append((cmd, kw)) or
                               providers.Reply("fine", 0, 0.1)):
            providers.make_agent("qwen:m", mcp=tools).ask("S", "THE PROMPT", "/tmp")
            providers.make_agent("gemini:default", mcp=tools).ask("S", "THE PROMPT", "/tmp")
            providers.make_agent("opencode:openrouter/x").ask("S", "P", "/tmp")
            providers.make_agent("opencode:openrouter/x", mcp=tools).ask("S", "P", "/tmp")
            providers.make_agent("codex:gpt", mcp=tools).ask("S", "P", "/tmp")
        qwen, gemini, opencode_plain, opencode, codex = calls
        self.assertTrue(qwen[0][-1].endswith("THE PROMPT"))                                # not swallowed by the list flag
        self.assertNotIn("--safe-mode", qwen[0])
        self.assertEqual(gemini[1]["env"]["GEMINI_CLI_SYSTEM_SETTINGS_PATH"], tools.gemini_file)
        self.assertNotIn("mcp", json.loads(opencode_plain[1]["env"]["OPENCODE_CONFIG_CONTENT"]))
        config = json.loads(opencode[1]["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual((sorted(config["mcp"]), config["permission"]), (["context7", "db", "sentry"], {"edit": "deny"}))
        self.assertEqual(codex[0][:3], ["codex", "exec", "-c"])
        self.assertTrue(mcp.remove(state, "db", project=shop))
        self.assertNotIn("db", mcp.servers_for(state, shots, shop))

    def test_cline_gets_missing_servers_added_and_keeps_its_own(self):
        from unittest import mock
        from mp_agent import mcp
        home = tmpdir("mp-cline-home-")
        write(os.path.join(home, ".cline", "data", "settings"), "cline_mcp_settings.json",
              json.dumps({"mcpServers": {"context7": {}, "mine": {}}}))
        ran = []
        with mock.patch.dict(os.environ, {"HOME": home}):
            added = mcp.sync_cline({"context7": {"command": "npx"}, "sentry": {"command": "npx", "args": ["-y", "s"]},
                                    "remote": {"url": "https://x"}},
                                   run=lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0))
        self.assertEqual(added, ["sentry"])
        self.assertEqual(ran, [["cline", "mcp", "install", "sentry", "--yes", "--", "npx", "-y", "s"]])


class PageCheck(unittest.TestCase):
    def test_a_page_that_throws_fails_and_a_good_one_passes(self):
        from mp_agent import pagecheck
        if not pagecheck.find_browser():
            self.skipTest("no Chromium-family browser here")
        folder = tmpdir("mp-page-")
        write(folder, "good.html", "<p id=x></p><script>document.getElementById('x').textContent = location.search;</script>")
        write(folder, "bad.html", "<script>\nconst ok = 1;\nif (location.search.includes('boom')) missingThing.go();\n</script>")
        said = []
        good = pagecheck.check(os.path.join(folder, "good.html"), ["", "?a=1"], 1500, said.append)
        if good == 2:
            self.skipTest(f"the browser here would not run: {said}")
        self.assertEqual(good, 0, said)
        self.assertEqual(pagecheck.check(os.path.join(folder, "bad.html"), ["?fine=1"], 1500, said.append), 0)
        self.assertEqual(pagecheck.check(os.path.join(folder, "bad.html"), ["?boom=1"], 1500, said.append), 1)
        self.assertTrue(any("missingThing is not defined" in s and "line 3" in s for s in said), said)


class ShapeFlags(unittest.TestCase):
    def test_solo_and_swarm_flags(self):
        from mp_agent import cli
        self.assertEqual(cli.parser().parse_args(["x", "--swarm"]).shape, "swarm")
        self.assertEqual(cli.parser().parse_args(["x", "--solo"]).shape, "solo")
        self.assertIsNone(cli.parser().parse_args(["x"]).shape)

    def test_a_swarm_needs_the_planner(self):
        import contextlib, io
        from mp_agent import cli
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["x", "--swarm", "--no-plan"]), 2)
        self.assertIn("a swarm needs the planner", err.getvalue())


class SwitchModels(unittest.TestCase):
    def test_free_tier_limits_are_out_of_credit(self):
        from mp_agent.providers import classify
        self.assertEqual(classify(1, "error: Free tier limit reached. Please upgrade to a paid plan."), "fatal")

    def test_switch_options_leave_the_account_and_stay_close_in_strength(self):
        from mp_agent import models
        self.assertEqual(models.account("cline:inception:mercury-2.5"), "cline:inception")
        self.assertEqual(models.account("opencode:openrouter/deepseek/deepseek-v4-pro"), "opencode:openrouter")
        self.assertEqual(models.account("claude:opus"), "claude")
        options = [{"spec": s, "label": s} for s in ("claude:fable", "claude:sonnet", "claude:haiku", "codex:gpt-5.6-terra",
                                                    "codex:gpt-5.6-sol")]
        picks = [o["spec"] for o in models.switch_options("claude:opus", options, limit=5)]
        self.assertEqual(picks[0], "codex:gpt-5.6-sol")        # the nearest from another account, stronger side first
        self.assertNotIn("claude:sonnet", picks)
        self.assertEqual(models.switch_options("claude:opus", [], limit=3), [])


class Bench(unittest.TestCase):
    def test_a_line_up_is_read_from_what_a_person_types(self):
        from mp_agent import bench
        combo = bench.parse_combo("name=fast,worker=cline:inception:mercury-2.5,judge=claude:sonnet")
        self.assertEqual(combo["name"], "fast")
        self.assertEqual(combo["roles"], {"worker": "cline:inception:mercury-2.5", "judge": "claude:sonnet"})
        self.assertEqual(bench.parse_combo("worker=claude:sonnet")["name"], "w:sonnet")
        for bad in ("", "worker", "critic=claude:sonnet"):
            with self.assertRaises(ValueError):
                bench.parse_combo(bad)

    def test_every_bench_task_has_hidden_tests_that_the_starting_files_fail(self):
        from mp_agent import bench
        tasks = bench.listing()
        self.assertTrue(tasks, "there are no bench tasks")
        for entry in tasks:
            task = bench.load_task(entry["name"])
            self.assertTrue(entry["about"], f"{task['name']} has no about.txt")
            self.assertTrue(os.path.isdir(task["hidden"]), f"{task['name']} has no hidden tests")
            folder = tmpdir("mp-bench-")
            bench.prepare(task, folder)
            sh(folder, "git", "checkout", "-q", "-b", "start")
            result = bench.grade(folder, "start", task["hidden"])
            self.assertGreater(result["ran"], 0, f"{task['name']}: the hidden tests did not run: {result['detail']}")
            self.assertFalse(result["passed"], f"{task['name']}: the hidden tests pass before any work is done")

    def test_the_summary_counts_what_matters(self):
        from mp_agent import bench
        rows = [
            {"combo": "a", "works": True, "approved": True, "seconds": 600, "passes": 2, "counters": {"worker_calls": 4},
             "costs": {"billed_usd": 0.5}, "upgrades": []},
            {"combo": "a", "works": False, "approved": True, "seconds": 1200, "passes": 5,
             "counters": {"worker_calls": 9}, "costs": {"billed_usd": 1.0}, "upgrades": [{}]},
            {"combo": "b", "works": True, "approved": False, "seconds": 300, "passes": 1, "counters": {},
             "costs": {}, "upgrades": []},
            {"combo": "b", "error": "the job did not run"},
        ]
        summary = {s["combo"]: s for s in bench.summarize(rows)}
        self.assertEqual(summary["a"]["false_approvals"], 1)
        self.assertEqual(summary["a"]["works"], 1)
        self.assertEqual(summary["a"]["median_minutes"], 15.0)
        self.assertEqual(summary["b"]["false_rejections"], 1)
        self.assertEqual(summary["b"]["failed_to_run"], 1)
        self.assertEqual(summary["b"]["works_rate"], 0.5)
        self.assertIn("line-up", bench.table(bench.summarize(rows)))


class ModelProblems(unittest.TestCase):
    def test_a_problem_is_remembered_then_cleared_when_it_works_again(self):
        from mp_agent import models
        state = tmpdir("mp-problems-")
        options = [{"spec": "codex:gpt-5.6-luna", "label": "GPT-5.6-Luna (Codex)"}]
        self.assertIsNone(models.with_problems(state, options)[0].get("problem"))
        models.note_problem(state, "codex:gpt-5.6-luna", "You've hit your usage limit")
        self.assertIn("usage limit", models.with_problems(state, options)[0]["problem"])
        self.assertIn("⚠", models.with_problems(state, options)[0]["label"])
        self.assertTrue(models.clear_problem(state, "codex:gpt-5.6-luna"))
        self.assertIsNone(models.with_problems(state, options)[0].get("problem"))
        self.assertFalse(models.clear_problem(state, "codex:gpt-5.6-luna"))


class BenchSuggest(unittest.TestCase):
    ROWS = [
        {"combo": "cheap", "roles": {"worker": "cline:inception:mercury-2.5"}, "works": True, "approved": True,
         "seconds": 300, "passes": 3, "counters": {}, "costs": {"billed_usd": 0.2}, "upgrades": []},
        {"combo": "cheap", "roles": {"worker": "cline:inception:mercury-2.5"}, "works": True, "approved": True,
         "seconds": 420, "passes": 2, "counters": {}, "costs": {"billed_usd": 0.2}, "upgrades": []},
        {"combo": "plan", "roles": {"worker": "claude:sonnet", "judge": "claude:opus"}, "works": True,
         "approved": True, "seconds": 600, "passes": 1, "counters": {}, "costs": {}, "upgrades": []},
        {"combo": "plan", "roles": {"worker": "claude:sonnet", "judge": "claude:opus"}, "works": True,
         "approved": True, "seconds": 660, "passes": 1, "counters": {}, "costs": {}, "upgrades": []},
        {"combo": "flaky", "roles": {"worker": "antigravity:gemini-3.8-flash-high"}, "works": False,
         "approved": False, "seconds": 120, "passes": 1, "counters": {}, "costs": {}, "upgrades": []},
        {"combo": "flaky", "roles": {"worker": "antigravity:gemini-3.8-flash-high"}, "works": False,
         "approved": False, "seconds": 120, "passes": 1, "counters": {}, "costs": {}, "upgrades": []},
    ]

    def test_it_recommends_by_what_a_person_wants(self):
        from mp_agent import bench
        picks = {p["for"]: p for p in bench.suggest(self.ROWS)}
        self.assertEqual(picks["the quickest"]["combo"], "cheap")
        self.assertEqual(picks["no API bills"]["combo"], "plan")      # cheap is quicker, but it bills
        self.assertEqual(picks["the steadiest"]["combo"], "plan")     # one pass, not three
        self.assertEqual(picks["the quickest"]["billed_per_run"], 0.2)
        avoid = [p for p in bench.suggest(self.ROWS) if p["for"] == "avoid"]
        self.assertEqual([p["combo"] for p in avoid], ["flaky"])
        self.assertEqual(avoid[0]["why"], "no run finished")

    def test_a_line_up_that_never_finished_recommends_nothing(self):
        from mp_agent import bench
        only_failures = [r for r in self.ROWS if not r["works"]]
        self.assertEqual([p["for"] for p in bench.suggest(only_failures)], [])
        self.assertEqual(bench.suggest([]), [])

    def test_the_measured_presets_carry_their_numbers(self):
        from mp_agent import models
        found = {p["id"]: p for p in models.presets([{"spec": s, "label": s} for s in
                 ("claude:opus", "claude:sonnet", "cline:inception:mercury-2.5", "codex:gpt-5.6-luna",
                  "codex:gpt-5.6-sol")])}
        for pid in ("fast", "claude", "openai"):
            m = found[pid]["measured"]
            self.assertEqual(m["runs"], 6, pid)
            self.assertGreater(m["minutes"], 0, pid)
        self.assertIsNone(found["mixed"]["measured"])      # never run exactly as this preset builds it


class BenchVoidRuns(unittest.TestCase):
    def test_a_run_the_provider_would_not_serve_is_void_not_a_failure(self):
        from mp_agent import bench
        void = {"combo": "g", "outcome": "NOT approved: antigravity:gemini-3.8-flash-high was unavailable (fatal)"}
        self.assertTrue(bench.is_void(void))
        self.assertTrue(bench.is_void({"combo": "g", "outcome": "NOT approved: the reviewer could not run (exit 1)"}))
        self.assertFalse(bench.is_void({"combo": "g", "outcome": "NOT approved: the 30 minute safety budget ran out"}))
        self.assertFalse(bench.is_void({"combo": "g", "works": True, "outcome": "approved: checks pass"}))
        rows = [dict(void, void=True),
                {"combo": "g", "works": True, "approved": True, "seconds": 600, "passes": 1, "counters": {},
                 "costs": {}, "upgrades": []}]
        summary = bench.summarize(rows)[0]
        self.assertEqual((summary["runs"], summary["works"], summary["void"]), (1, 1, 1))
        self.assertIn("void", bench.table(bench.summarize(rows)))


class ProjectTeam(unittest.TestCase):
    def test_a_project_keeps_its_own_models_and_can_forget_them(self):
        from mp_agent import models
        state, project = tmpdir("mp-state-"), tmpdir("mp-project-")
        self.assertEqual(models.project_config(state, project), {})
        models.save_project_config(state, project, {"worker": "claude:haiku", "judge": "claude:opus", "nonsense": "x"})
        kept = models.project_config(state, project)
        self.assertEqual(kept, {"worker": "claude:haiku", "judge": "claude:opus"})
        self.assertEqual(models.project_config(state, tmpdir("mp-other-")), {})   # only that project
        self.assertIn(os.path.realpath(project), models.projects_with_roles(state))
        models.save_project_config(state, project, {})
        self.assertEqual(models.project_config(state, project), {})
        self.assertEqual(models.projects_with_roles(state), {})

    def test_the_general_config_is_untouched_by_a_project_team(self):
        from mp_agent import models
        state, project = tmpdir("mp-state-"), tmpdir("mp-project-")
        models.save_config(state, {"worker": "claude:sonnet", "judge": "claude:opus", "planner": "claude:opus",
                                   "reviewer": "", "panel": ""})
        models.save_project_config(state, project, {"worker": "claude:haiku"})
        self.assertEqual(models.load_config(state)["worker"], "claude:sonnet")
        merged = {**models.load_config(state), **models.project_config(state, project)}
        self.assertEqual(merged["worker"], "claude:haiku")      # how a job started there chooses
        self.assertEqual(merged["judge"], "claude:opus")


class DesignBrief(unittest.TestCase):
    BRIEF = """# Design brief

## The direction
A darkroom timer: one instrument panel on a near-black ground, a safelight amber as the only colour.

## Colour
- Ground #101114 (dominates)
- Chalk #f3efe6 for text
- Safelight #ff7a2f, the one accent
- short form #abc for the hairline

## Type
Monospace digits, tabular figures.
"""

    def test_the_direction_and_the_palette_are_pulled_out_for_the_workshop(self):
        from mp_agent import design
        self.assertTrue(design.direction(self.BRIEF).startswith("A darkroom timer"))
        self.assertEqual(design.palette(self.BRIEF),
                         ["#101114", "#f3efe6", "#ff7a2f", "#aabbcc"])       # #abc becomes #aabbcc
        self.assertEqual(design.direction("no sections here"), "")
        self.assertEqual(design.palette(""), [])

    def test_every_role_sees_the_brief_under_its_own_heading(self):
        from mp_agent import design
        section = design.section(self.BRIEF)
        self.assertTrue(section.startswith("# Design brief"))
        self.assertIn("### The direction", section)          # one level down, inside a bigger prompt
        self.assertIn("never overrides the contract", section)
        self.assertEqual(design.section(""), "")

    def test_who_gets_a_designer(self):
        from mp_agent import design
        self.assertTrue(design.wanted({"design": True}))
        self.assertFalse(design.wanted({"design": False}))
        self.assertFalse(design.wanted({}))                               # a plan that never mentions it
        self.assertTrue(design.wanted({"design": False}, "on"))           # --design
        self.assertFalse(design.wanted({"design": True}, "off"))          # --no-design

    def test_the_designer_is_chosen_for_the_skill_it_can_load(self):
        from mp_agent import models
        options = [{"spec": s, "label": s} for s in
                   ("cline:inception:mercury-2.5", "codex:gpt-6-astra", "claude:sonnet", "claude:opus")]
        self.assertEqual(models.pick_designer(options, avoid=("cline:inception:mercury-2.5",)), "claude:opus")
        self.assertEqual(models.pick_designer(options, avoid=("claude:opus", "claude:sonnet")), "codex:gpt-6-astra")
        self.assertTrue(models.designs_with_a_skill("claude:opus"))
        self.assertFalse(models.designs_with_a_skill("codex:gpt-6-astra"))
        problems = [{"spec": "claude:opus", "label": "x", "problem": "out of credit"}, {"spec": "claude:sonnet", "label": "y"}]
        self.assertEqual(models.pick_designer(problems), "claude:sonnet")   # not one that cannot run

    def test_the_look_lens_only_appears_when_there_is_a_brief(self):
        from mp_agent import gates
        self.assertIn("look", gates.LENSES)
        self.assertIn("Not this", gates.LENSES["look"])
