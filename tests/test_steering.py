"""Saying something to a job while it runs, and being asked before it commits to things.

Two halves of the same idea: a job you can work with in phases rather than watch in one
shot. Steering is guidance, never a rewrite of the contract; a checkpoint is the loop
stopping to show you what it is about to build on.
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import Script, approve, context, log, make_repo, wait_for, write  # noqa: E402


class Steering(unittest.TestCase):
    def ctx(self):
        return context(Script("worker"), Script("judge"), Script("planner"))

    def test_nothing_said_is_nothing_added(self):
        ctx = self.ctx()
        self.assertEqual(ctx.steer(), "")

    def test_what_you_said_is_handed_over_once(self):
        ctx = self.ctx()
        ctx.say_to_job("the colours are wrong, use the palette in brand.css")
        said = ctx.steer()
        self.assertIn("brand.css", said)
        self.assertEqual(ctx.steer(), "", "it must not be handed over twice")

    def test_several_notes_arrive_together_in_order(self):
        ctx = self.ctx()
        ctx.say_to_job("first thing")
        ctx.say_to_job("second thing")
        said = ctx.steer()
        self.assertLess(said.index("first thing"), said.index("second thing"))

    def test_it_reaches_the_builder_and_the_reviewers_know_about_it(self):
        from mp_agent.contract import Contract
        from mp_agent.worker import UnitSpec, Worker
        ctx = self.ctx()
        ctx.say_to_job("do not bother with the CSV export")
        worker = Worker(ctx, UnitSpec(name="main", goal="g", contract=Contract(["x"], []), check=None, owns=None),
                        make_repo())
        prompt = worker.implementer_prompt()
        self.assertIn("do not bother with the CSV export", prompt)
        self.assertIn("never overrides the contract", prompt.lower())
        # and it is on the decisions log, so a reviewer does not object to work you asked for
        self.assertIn("do not bother with the CSV export", ctx.decisions.render("main"))

    def test_it_is_guidance_and_cannot_quietly_become_the_contract(self):
        from mp_agent.contract import Contract
        from mp_agent.worker import UnitSpec, Worker
        ctx = self.ctx()
        ctx.say_to_job("actually drop requirement C1")
        spec = UnitSpec(name="main", goal="g", contract=Contract(["C1 thing must work"], []), check=None, owns=None)
        Worker(ctx, spec, make_repo()).implementer_prompt()
        self.assertEqual(spec.contract.done, ["C1 thing must work"])       # untouched
        self.assertEqual(spec.contract.amendments, [])


class Checkpoints(unittest.TestCase):
    def test_which_ones_are_asked_for(self):
        from mp_agent import models
        from helpers import tmpdir
        state = tmpdir("mp-checkpoints-")
        self.assertEqual(models.load_checkpoints(state), [])          # off until asked for
        models.save_extra(state, {"checkpoints": ["plan", "design", "wave"]})
        self.assertEqual(models.load_checkpoints(state), ["plan", "design", "wave"])
        models.save_extra(state, {"checkpoints": ["plan", "nonsense"]})
        self.assertEqual(models.load_checkpoints(state), ["plan"])     # only the ones that exist


if __name__ == "__main__":
    unittest.main()


class CheckpointsInAJob(unittest.TestCase):
    """The loop stopping at the plan, and doing as it is told."""

    def setup(self, answer_with, plans):
        import threading
        from mp_agent.orchestrator import Orchestrator
        repo = make_repo({"README": "x"})
        asked = {}
        builder = Script("worker", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        lead = Script("planner", plan=lambda *a: plans.pop(0))
        ctx = context(builder, Script("judge", audit=approve), lead, checkpoints=("plan",))

        def person():
            # after a re-plan the checkpoint shows the new plan and asks again, which is the point
            # of it; so answer once as asked, and wave the rest through.
            for turn in range(4):
                if not wait_for(os.path.join(ctx.run.dir, "question.json"), timeout=20):
                    return
                with open(os.path.join(ctx.run.dir, "question.json")) as fh:
                    question = json.load(fh)
                if not asked:
                    asked.update(question)
                tmp = os.path.join(ctx.run.dir, "answer.json.tmp")
                with open(tmp, "w") as fh:
                    json.dump({"answer": answer_with if turn == 0 else "go"}, fh)
                os.replace(tmp, os.path.join(ctx.run.dir, "answer.json"))
                for _ in range(200):                      # wait for the job to take it
                    if not os.path.exists(os.path.join(ctx.run.dir, "question.json")):
                        break
                    time.sleep(0.05)
        threading.Thread(target=person, daemon=True).start()
        import tempfile
        result = Orchestrator(ctx, "test task", repo, tempfile.mkdtemp(prefix="mp-test-trees-")).run()
        return result, ctx, asked

    def a_plan(self, summary="one unit", check="test -f done.txt"):
        return ("```json\n" + json.dumps({
            "mode": "single", "why": "small", "summary": summary,
            "contract": {"done": ["done.txt says ok"], "out_of_scope": ["unicode"]},
            "check": check, "tests": None, "subtasks": []}) + "\n```")

    def test_it_shows_the_plan_and_waits(self):
        result, ctx, asked = self.setup("go", [self.a_plan()])
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("Done means", asked["question"])
        self.assertIn("done.txt says ok", asked["question"])
        self.assertIn("Out of scope", asked["question"])
        self.assertIn("test -f done.txt", asked["question"])          # the check, before anything runs
        self.assertEqual([c["answer"] for c in asked["choices"]], ["go", "stop"])

    def test_asking_for_a_change_plans_again_with_your_words(self):
        plans = [self.a_plan(summary="first go"), self.a_plan(summary="with your change")]
        result, ctx, asked = self.setup("please also handle an empty file", plans)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("planning again with what you asked for", log(ctx))
        self.assertIn("with your change", log(ctx))
        said = [c[1] for c in ctx.planner.calls if c[0] == "plan"][-1]
        self.assertIn("please also handle an empty file", said)

    def test_a_change_that_begins_with_go_is_still_a_change(self):
        # "go up to PB as well" is not permission to carry on, however it starts
        plans = [self.a_plan(summary="first go"), self.a_plan(summary="with your change")]
        result, ctx, asked = self.setup("go up to PB and EB as well, not just TB", plans)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("planning again with what you asked for", log(ctx))
        said = [c[1] for c in ctx.planner.calls if c[0] == "plan"][-1]
        self.assertIn("go up to PB and EB as well", said)

    def test_a_bare_go_carries_on(self):
        result, ctx, asked = self.setup("go", [self.a_plan()])
        self.assertTrue(result["approved"], log(ctx))
        self.assertNotIn("planning again", log(ctx))

    def test_stop_means_stop(self):
        result, ctx, asked = self.setup("stop", [self.a_plan()])
        self.assertFalse(result["approved"])
        self.assertIn("stopped by you", result["outcome"])

    def test_without_the_checkpoint_it_never_asks(self):
        import tempfile, threading
        from mp_agent.orchestrator import Orchestrator
        repo = make_repo({"README": "x"})
        builder = Script("worker", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        ctx = context(builder, Script("judge", audit=approve), Script("planner", plan=lambda *a: self.a_plan()))
        result = Orchestrator(ctx, "test task", repo, tempfile.mkdtemp(prefix="mp-test-trees-")).run()
        self.assertTrue(result["approved"], log(ctx))
        self.assertFalse(os.path.exists(os.path.join(ctx.run.dir, "question.json")))
