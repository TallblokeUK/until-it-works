"""Your own gates: what they see, what refusing does, and what a broken one must not do."""
import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import tmpdir  # noqa: E402

from mp_agent import hooks  # noqa: E402


def a_hook(folder, event, body, where=".mp-agent/hooks"):
    path = os.path.join(folder, where, event)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class Finding(unittest.TestCase):
    def test_a_project_hook_and_a_personal_one_both_run_project_first(self):
        project, state = tmpdir("mp-project-"), tmpdir("mp-state-")
        a_hook(project, "plan", "#!/bin/sh\nexit 0\n")
        a_hook(state, "plan", "#!/bin/sh\nexit 0\n", where="hooks")
        found = hooks.find("plan", project, state)
        self.assertEqual([w for _, w in found], ["the project", "yours"])

    def test_a_file_that_is_not_executable_is_not_a_hook(self):
        project = tmpdir("mp-project-")
        path = a_hook(project, "plan", "#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o644)
        self.assertEqual(hooks.find("plan", project, None), [])

    def test_only_the_events_that_exist(self):
        project = tmpdir("mp-project-")
        a_hook(project, "whenever", "#!/bin/sh\nexit 0\n")
        self.assertEqual(hooks.find("whenever", project, None), [])


class Running(unittest.TestCase):
    def test_exit_zero_carries_on(self):
        project = tmpdir("mp-project-")
        a_hook(project, "unit-done", "#!/bin/sh\nexit 0\n")
        self.assertEqual(hooks.run("unit-done", {"unit": "main"}, project, None, say=lambda *a: None), "")

    def test_exit_two_refuses_and_its_words_come_back(self):
        project = tmpdir("mp-project-")
        a_hook(project, "unit-done", "#!/bin/sh\necho 'the formatter is unhappy: run black .'\nexit 2\n")
        said = hooks.run("unit-done", {"unit": "main"}, project, None, say=lambda *a: None)
        self.assertIn("the formatter is unhappy", said)

    def test_the_moment_arrives_as_json_on_stdin(self):
        project = tmpdir("mp-project-")
        out = os.path.join(project, "seen.json")
        a_hook(project, "plan", f"#!/bin/sh\ncat > {out}\nexit 0\n")
        hooks.run("plan", {"unit": "main", "contract": ["it works"], "mode": "single"}, project, None,
                  say=lambda *a: None)
        with open(out) as fh:
            seen = json.load(fh)
        self.assertEqual(seen["mode"], "single")
        self.assertEqual(seen["contract"], ["it works"])

    def test_a_broken_hook_cannot_stop_a_job(self):
        project = tmpdir("mp-project-")
        said = []
        a_hook(project, "job-done", "#!/bin/sh\necho 'kaboom' >&2\nexit 1\n")
        self.assertEqual(hooks.run("job-done", {}, project, None, say=said.append), "")
        self.assertIn("failed (exit 1)", " ".join(said))

    def test_one_that_hangs_is_given_up_on(self):
        project = tmpdir("mp-project-")
        said = []
        a_hook(project, "job-done", "#!/bin/sh\nsleep 30\n")
        self.assertEqual(hooks.run("job-done", {}, project, None, say=said.append, timeout=1), "")
        self.assertIn("took longer", " ".join(said))

    def test_a_refusal_with_nothing_to_say_still_says_something(self):
        project = tmpdir("mp-project-")
        a_hook(project, "unit-done", "#!/bin/sh\nexit 2\n")
        self.assertIn("said nothing", hooks.run("unit-done", {}, project, None, say=lambda *a: None))

    def test_the_project_hook_refusing_stops_before_yours_runs(self):
        project, state = tmpdir("mp-project-"), tmpdir("mp-state-")
        ran = os.path.join(state, "ran")
        a_hook(project, "job-done", "#!/bin/sh\necho no\nexit 2\n")
        a_hook(state, "job-done", f"#!/bin/sh\ntouch {ran}\nexit 0\n", where="hooks")
        self.assertIn("no", hooks.run("job-done", {}, project, state, say=lambda *a: None))
        self.assertFalse(os.path.exists(ran))


if __name__ == "__main__":
    unittest.main()


class InAJob(unittest.TestCase):
    """A hook refusing inside a real loop: the work goes back, and the job does not end approved."""

    def test_a_unit_hook_sends_the_work_back_and_is_satisfied_when_fixed(self):
        import tempfile
        from helpers import Script, approve, context, log, make_repo, write
        from mp_agent.orchestrator import Orchestrator
        repo = make_repo({"README": "x"})
        plan = ("```json\n" + json.dumps({
            "mode": "single", "why": "small", "summary": "one unit",
            "contract": {"done": ["done.txt says ok"], "out_of_scope": []},
            "check": "test -f done.txt", "tests": None, "subtasks": []}) + "\n```")
        # the gate: no job is done while TODO is in the tree
        a_hook(repo, "unit-done", "#!/bin/sh\nif [ -f TODO ]; then echo 'TODO is still there'; exit 2; fi\nexit 0\n")

        def implement(prompt, cwd):
            if "TODO is still there" in prompt:
                os.remove(os.path.join(cwd, "TODO"))
                return "tidied up"
            write(cwd, "done.txt", "ok\n")
            write(cwd, "TODO", "finish this\n")
            return "done, mostly"

        builder = Script("worker", implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script("judge", audit=approve), Script("planner", plan=lambda *a: plan))
        result = Orchestrator(ctx, "test task", repo, tempfile.mkdtemp(prefix="mp-test-trees-")).run()
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("hook says no: TODO is still there", log(ctx))
        self.assertGreaterEqual(builder.count("implement"), 2, "it should have been sent back once")

    def test_a_job_done_hook_can_refuse_the_whole_job(self):
        import tempfile
        from helpers import Script, approve, context, log, make_repo, write
        from mp_agent.orchestrator import Orchestrator
        repo = make_repo({"README": "x"})
        plan = ("```json\n" + json.dumps({
            "mode": "single", "why": "small", "summary": "one unit",
            "contract": {"done": ["done.txt says ok"], "out_of_scope": []},
            "check": "test -f done.txt", "tests": None, "subtasks": []}) + "\n```")
        a_hook(repo, "job-done", "#!/bin/sh\necho 'the changelog was not updated'\nexit 2\n")
        builder = Script("worker", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        ctx = context(builder, Script("judge", audit=approve), Script("planner", plan=lambda *a: plan))
        result = Orchestrator(ctx, "test task", repo, tempfile.mkdtemp(prefix="mp-test-trees-")).run()
        self.assertFalse(result["approved"], log(ctx))
        self.assertIn("the changelog was not updated", result["outcome"])
