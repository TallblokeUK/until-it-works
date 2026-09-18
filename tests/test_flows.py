"""End-to-end flows through the real orchestrator, worker, gates and git, with
scripted agents in place of the real models."""
import json
import os
import re
import tempfile
import threading
import unittest

from helpers import Script, approve, context, log, make_repo, sh, tmpdir, wait_for, write

from mp_agent.orchestrator import Orchestrator
from mp_agent.providers import Reply

CHECK = "test -f done.txt && grep -q ok done.txt"


def answer(ctx, body):
    """What `mp-agent answer` does: write the answer in one step, never half a file."""
    tmp = os.path.join(ctx.run.dir, "answer.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(body, fh)
    os.replace(tmp, os.path.join(ctx.run.dir, "answer.json"))


def plan_reply(plan):
    return "Here is the plan.\n```json\n" + json.dumps(plan) + "\n```"


def single(check=CHECK, **extra):
    return {"mode": "single", "summary": "one unit", "contract": {"done": ["done.txt says ok"],
            "out_of_scope": ["unicode"]}, "check": check, "tests": None, "subtasks": [], **extra}


def orchestrate(ctx, repo):
    trees = tmpdir("mp-test-trees-")
    return Orchestrator(ctx, "test task", repo, trees).run()


def on_branch(repo, result, path):
    return sh(repo, "git", "show", f"{result['branch']}:{path}")


class SingleMode(unittest.TestCase):
    def test_happy_path(self):
        repo = make_repo({"README": "x"})
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        auditor = Script(audit=approve)
        lead = Script(plan=lambda *a: plan_reply(single()))
        ctx = context(builder, auditor, lead)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertEqual(on_branch(repo, result, "done.txt"), "ok\n")
        self.assertEqual(builder.count("panel"), 3)
        self.assertRegex(log(ctx), r"(?m)^approved: checks pass, the reviewer and the panel approved, and fake approved it$")
        self.assertFalse(gitops_dirty(repo))

    def test_reviewer_panel_and_judge_can_each_be_their_own_model(self):
        repo = make_repo({"README": "x"})
        builder = Script("builder", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done")
        reviewer = Script("quick", review=approve)
        panel = Script("lenses", panel=approve)
        judge = Script("judge", audit=approve)
        ctx = context(builder, judge, Script(plan=lambda *a: plan_reply(single())), reviewer=reviewer, panel=panel,
                      panel_size=2)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertEqual((builder.count("implement"), builder.count("review"), builder.count("panel")), (1, 0, 0))
        self.assertEqual((reviewer.count("review"), panel.count("panel"), judge.count("audit")), (1, 2, 1))
        self.assertIn("pre-audit panel: 2 lens(es), lenses", log(ctx))
        self.assertIn("gates      check → quick review by quick → panel of lenses → audit by judge", log(ctx))
        self.assertEqual(ctx.run.counters["reviewer_calls"], 1)
        self.assertEqual(ctx.run.counters["panel_calls"], 2)
        plan_prompt = next(c[1] for c in ctx.planner.calls if c[0] == "plan")
        self.assertIn("- workers: builder\n- quick reviewer: quick\n- panel: lenses\n- final judge: judge", plan_prompt)

    def test_every_role_is_given_the_project_rules(self):
        repo = make_repo({"README": "x", "CLAUDE.md": "Always write British English.", ".cursorrules": "No semicolons."})
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve, panel=approve)
        judge = Script(audit=approve)
        lead = Script(plan=lambda *a: plan_reply(single()))
        ctx = context(builder, judge, lead)
        self.assertTrue(orchestrate(ctx, repo)["approved"], log(ctx))
        prompts = {"plan": lead.calls[0][1], "implement": builder.calls[0][1],
                   "review": next(c[1] for c in builder.calls if c[0] == "review"),
                   "panel": next(c[1] for c in builder.calls if c[0].startswith("panel")), "audit": judge.calls[0][1]}
        for role, prompt in prompts.items():
            self.assertIn("Always write British English.", prompt, role)
            self.assertIn("No semicolons.", prompt, role)
        self.assertIn("project rules: CLAUDE.md, .cursorrules", log(ctx))

    def test_cline_checkpoint_refs_from_the_run_are_dropped(self):
        repo = make_repo({"README": "x"})
        sh(repo, "git", "update-ref", "refs/cline/checkpoints/mine/1", "HEAD")
        strays = []

        def implement(prompt, cwd):
            write(cwd, "done.txt", "ok\n")
            sh(cwd, "git", "update-ref", "refs/cline/checkpoints/run/1", "HEAD")
            write(cwd + "-guessed-longer-name", "index.html", "stray")
            strays.append(cwd + "-guessed-longer-name")
            return "done"

        builder = Script(implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))
        self.assertTrue(orchestrate(ctx, repo)["approved"], log(ctx))
        self.assertEqual(sh(repo, "git", "for-each-ref", "--format=%(refname)", "refs/cline").split(),
                         ["refs/cline/checkpoints/mine/1"])
        self.assertIn("removed a stray folder an agent created", log(ctx))
        self.assertFalse(os.path.exists(strays[0]))

    def test_objection_decision_then_approval(self):
        repo = make_repo()
        state = {"reviews": 0}

        def implement(prompt, cwd):
            if "found problems" in prompt:
                write(cwd, "done.txt", "ok\n")
                return "fixed\nDECISION: wrote ok on its own line"
            write(cwd, "done.txt", "ok")
            return "first try"

        def review(prompt, cwd):
            state["reviews"] += 1
            return approve() if state["reviews"] > 1 else "C1 needs a newline\nVERDICT: CHANGES REQUIRED"

        builder = Script(implement=implement, review=review, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("reviewer asked for changes", log(ctx))
        decisions = json.loads(ctx.run.read_text("decisions.json"))
        self.assertEqual(decisions[0]["text"], "wrote ok on its own line")
        # the next reviewer sees the decision
        last_review = [c for c in builder.calls if c[0] == "review"][-1][1]
        self.assertIn("D1 (decided): wrote ok on its own line", last_review)

    def test_reviewer_vandalism_is_restored(self):
        repo = make_repo()

        def vandal(prompt, cwd):
            write(cwd, "done.txt", "vandalised\n")
            return approve()

        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=vandal, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("restoring the implementer's version", log(ctx))
        self.assertEqual(on_branch(repo, result, "done.txt"), "ok\n")


class Escalation(unittest.TestCase):
    def test_stall_gets_a_ruling_that_settles_it(self):
        repo = make_repo()

        def review(prompt, cwd):
            if "A1: trailing newline is not required" in prompt:
                return approve()
            return "needs a trailing newline\nVERDICT: CHANGES REQUIRED"

        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok") or "same every time",
                         review=review, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(single()),
                        ruling=lambda *a: "AMENDMENT: trailing newline is not required\nbecause C1 says ok")
        ctx = context(builder, Script(audit=approve), lead, patience=2)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("no progress: 2 passes in a row changed nothing", log(ctx))
        self.assertIn("ruling A1: trailing newline is not required", log(ctx))

    def test_stuck_asks_the_person_and_resumes_on_answer(self):
        repo = make_repo()

        def review(prompt, cwd):
            if "decided: yes, plain ok is fine" in prompt:
                return approve()
            return "unclear\nVERDICT: CHANGES REQUIRED"

        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok") or "same",
                         review=review, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(single()), ruling=lambda *a: "NO AMENDMENT",
                        replan=lambda *a: "cannot", question=lambda *a: "QUESTION: Is plain ok fine?")
        ctx = context(builder, Script(audit=approve), lead, patience=2)

        def person():
            if wait_for(os.path.join(ctx.run.dir, "question.json")):
                answer(ctx, {"answer": "yes, plain ok is fine"})

        threading.Thread(target=person, daemon=True).start()
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("waiting for you: Is plain ok fine?", log(ctx))
        self.assertIn("you answered: yes, plain ok is fine", log(ctx))
        self.assertFalse(os.path.exists(os.path.join(ctx.run.dir, "question.json")))

    def test_question_from_an_approving_auditor_does_not_cost_another_round(self):
        repo = make_repo()
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        auditor = Script(audit=lambda *a: "Fine.\nAMBIGUITY: must ok be lowercase?\nVERDICT: APPROVED")
        lead = Script(plan=lambda *a: plan_reply(single()), ruling=lambda *a: "AMENDMENT: lowercase ok is fine")
        ctx = context(builder, auditor, lead)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("ruling A1: lowercase ok is fine", log(ctx))
        self.assertIn("ruling recorded; the approval stands", log(ctx))
        self.assertEqual(builder.count("implement"), 1)
        self.assertEqual(auditor.count("audit"), 1)

    def stuck_setup(self, mode, ruling="NO AMENDMENT", answer_with=None, replan=None):
        repo = make_repo()
        weak = Script("weak", implement=lambda p, cwd: write(cwd, "done.txt", "ko\n") or "same mistake", review=approve,
                      panel=approve)
        strong = Script("claude:opus", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "fixed properly",
                        review=approve, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(single()), ruling=lambda *a: ruling,
                      replan=replan or (lambda *a: self.fail("should have upgraded before re-planning")),
                      question=lambda *a: "QUESTION: ?")
        ctx = context(weak, Script(audit=approve), lead, patience=2)
        ctx.upgrade = {"mode": mode, "to": "", "max": 1}
        made = []
        ctx.make_worker = lambda spec: made.append(spec) or strong
        ctx.model_options = [{"spec": "claude:sonnet", "label": "Claude Sonnet"}, {"spec": "claude:opus", "label": "Claude Opus"},
                             {"spec": "claude:haiku", "label": "Claude Haiku"}]
        ctx.roles = {"worker": "cline:inception:mercury-2.5", "planner": "claude:sonnet", "judge": "claude:sonnet"}
        if answer_with:
            def person():
                if wait_for(os.path.join(ctx.run.dir, "question.json")):
                    with open(os.path.join(ctx.run.dir, "question.json")) as fh:
                        self.asked = json.load(fh)
                    answer(ctx, {"answer": answer_with})
            threading.Thread(target=person, daemon=True).start()
        return repo, ctx, made, strong

    def test_stuck_workers_are_upgraded_automatically_when_set_up_so(self):
        repo, ctx, made, strong = self.stuck_setup("auto")
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertEqual(made, ["claude:opus"])                  # the strongest available that is not the judge
        self.assertIn("upgraded the workers from cline:inception:mercury-2.5 to claude:opus (automatically)", log(ctx))
        self.assertEqual(on_branch(repo, result, "done.txt"), "ok\n")
        self.assertEqual(json.loads(ctx.run.read_text("metadata.json"))["upgrades"][0]["to"], "claude:opus")

    def test_the_person_is_offered_stronger_workers_and_chooses(self):
        repo, ctx, made, strong = self.stuck_setup("ask", answer_with="upgrade opus")
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertEqual(made, ["claude:opus"])
        labels = [c["label"] for c in self.asked["choices"]]
        self.assertIn("KEEP TRYING", labels)
        self.assertTrue(any(c["answer"] == "upgrade claude:opus" for c in self.asked["choices"]))
        self.assertFalse(any("sonnet" in c["answer"] for c in self.asked["choices"]))    # the judge is never offered
        self.assertIn("Upgrade the workers for the rest of this job?", self.asked["question"])

    def test_planner_saying_upgrade_offers_it_straight_after_the_ruling(self):
        repo, ctx, made, strong = self.stuck_setup("auto", ruling="NO AMENDMENT\nUPGRADE: they keep writing ko instead of ok")
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertEqual(made, ["claude:opus"])
        self.assertNotIn("the planner made no amendment but gave advice", log(ctx))

    def test_never_upgrading_goes_on_to_the_re_plan(self):
        replans = []
        repo, ctx, made, strong = self.stuck_setup(
            "never", replan=lambda *a: replans.append(1) or '```json\n{"goal": "g", "done": ["done.txt says ok"], "guidance": "x"}\n```')
        ctx.options.ask = False
        orchestrate(ctx, repo)
        self.assertEqual(made, [])
        self.assertTrue(replans)

    def test_ruling_without_amendment_passes_its_advice_on_before_asking(self):
        repo = make_repo()

        def implement(prompt, cwd):
            if "write ok on its own line" in prompt:
                write(cwd, "done.txt", "ok\n")
                return "followed the advice"
            write(cwd, "done.txt", "ko\n")
            return "same every time"

        lead = Script(plan=lambda *a: plan_reply(single()),
                        ruling=lambda *a: "NO AMENDMENT\n\nThe contract is fine; write ok on its own line.",
                        replan=lambda *a: self.fail("should not re-plan"),
                        question=lambda *a: self.fail("should not ask"))
        builder = Script(implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), lead, patience=2)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("the planner made no amendment but gave advice: The contract is fine", log(ctx))

    def test_invalid_replan_json_is_asked_for_again(self):
        repo = make_repo()
        replies = ['```json\n{"goal": "g", "done": ["done.txt says ok"], "guidance": "x"]}\n```',
                   '```json\n{"goal": "g", "done": ["done.txt says ok"], "guidance": "write ok, newline"}\n```']

        def implement(prompt, cwd):
            write(cwd, "done.txt", "ok\n" if "write ok, newline" in prompt else "ko\n")
            return "done"

        lead = Script(plan=lambda *a: plan_reply(single()), ruling=lambda *a: "NO AMENDMENT",
                        replan=lambda *a: replies.pop(0))
        builder = Script(implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), lead, patience=2)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertEqual(lead.count("replan"), 2)
        self.assertIn("re-planned: write ok, newline", log(ctx))

    def test_no_ask_stops_honestly(self):
        repo = make_repo()
        builder = Script(implement=lambda p, cwd: "did nothing", review=approve, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(single()), ruling=lambda *a: "NO AMENDMENT",
                        replan=lambda *a: "no", question=lambda *a: "QUESTION: help?")
        ctx = context(builder, Script(audit=approve), lead, patience=2, ask=False)
        result = orchestrate(ctx, repo)
        self.assertFalse(result["approved"])
        self.assertRegex(result["outcome"], r"^NOT approved: stuck")


class ProviderTrouble(unittest.TestCase):
    def test_rate_limited_implementer_asks_then_resumes(self):
        repo = make_repo()
        state = {"calls": 0}

        def implement(prompt, cwd):
            state["calls"] += 1
            if state["calls"] == 1:
                return Reply("Error: Rate limit reached: input token limit exceeded", 1)
            write(cwd, "done.txt", "ok\n")
            return "done"

        builder = Script(implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))

        def person():
            if wait_for(os.path.join(ctx.run.dir, "question.json")):
                answer(ctx, {"answer": "retry"})

        threading.Thread(target=person, daemon=True).start()
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("provider trouble: fake is still rate limited", log(ctx))
        self.assertNotIn("no progress", log(ctx))

    def test_crashed_reviewer_is_run_again_without_reimplementing(self):
        repo = make_repo()
        state = {"reviews": 0}

        def review(prompt, cwd):
            state["reviews"] += 1
            return Reply("TypeError: cline crashed", 1) if state["reviews"] == 1 else approve()

        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=review, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("review could not run (exit 1); running it again", log(ctx))
        self.assertEqual(builder.count("implement"), 1)

    def test_out_of_usage_auditor_stops_with_the_reason_when_asking_is_off(self):
        repo = make_repo()
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        auditor = Script(audit=lambda *a: Reply("Claude AI usage limit reached|1789500000", 1))
        ctx = context(builder, auditor, Script(plan=lambda *a: plan_reply(single())), ask=False)
        result = orchestrate(ctx, repo)
        self.assertFalse(result["approved"])
        self.assertIn("provider trouble: fake cannot continue (out of credit or usage", log(ctx))
        self.assertIn("NOT approved: the final reviewer could not run", result["outcome"])


class ProjectMemory(unittest.TestCase):
    def test_rulings_are_remembered_and_given_to_the_next_plan(self):
        repo = make_repo()
        state = tmpdir("mp-test-state-")

        def review(prompt, cwd):
            if "A1: trailing newline is not required" in prompt:
                return approve()
            return "needs a trailing newline\nVERDICT: CHANGES REQUIRED"

        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok") or "same", review=review, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(single()),
                        ruling=lambda *a: "AMENDMENT: trailing newline is not required")
        ctx = context(builder, Script(audit=approve), lead, patience=2)
        trees = os.path.join(state, "worktrees")
        self.assertTrue(Orchestrator(ctx, "first task", repo, trees).run()["approved"], log(ctx))
        self.assertIn("remembered for next time", log(ctx))

        lead2 = Script(plan=lambda *a: plan_reply(single()))
        builder2 = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                          panel=approve)
        ctx2 = context(builder2, Script(audit=approve), lead2)
        self.assertTrue(Orchestrator(ctx2, "second task", repo, trees).run()["approved"], log(ctx2))
        plan_prompt = lead2.calls[0][1]
        self.assertIn("What earlier runs on this project settled", plan_prompt)
        self.assertIn("trailing newline is not required", plan_prompt)


class Resume(unittest.TestCase):
    def resume(self, old_ctx, repo, trees, builder, lead=None):
        from mp_agent.cli import load_resume
        info = load_resume(old_ctx.run.dir, trees)
        ctx = context(builder, Script(audit=approve), lead or Script())
        ctx.decisions.items = list(info["decisions"])
        return ctx, Orchestrator(ctx, "test task", repo, trees, resume=info).run()

    def test_single_unit_carries_on_from_the_code_on_disk(self):
        repo, trees = make_repo(), tmpdir("mp-test-trees-")
        holder = {}

        def stop_while_reviewing(prompt, cwd):
            holder["ctx"].halt("stopped by you")
            return approve()

        first = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done\nDECISION: wrote ok",
                       review=stop_while_reviewing, panel=approve)
        ctx = context(first, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))
        holder["ctx"] = ctx
        result = Orchestrator(ctx, "test task", repo, trees).run()
        self.assertEqual(result["outcome"], "NOT approved: stopped by you")

        second = Script(implement=lambda p, cwd: self.fail("should judge the existing work first"),
                        review=approve, panel=approve)
        ctx2, result2 = self.resume(ctx, repo, trees, second)
        self.assertTrue(result2["approved"], log(ctx2))
        self.assertIn("resuming", log(ctx2))
        self.assertEqual(sh(repo, "git", "show", f"{result['branch']}:done.txt"), "ok\n")
        self.assertEqual(ctx2.decisions.items[0]["text"], "wrote ok")

    def test_swarm_keeps_approved_subtasks_and_finishes_the_rest(self):
        repo, trees = make_repo(), tmpdir("mp-test-trees-")
        holder = {}

        def implement(prompt, cwd):
            name = re.search(r"# Task\n\nwrite (\w)\.txt", prompt)
            if name and name.group(1) == "b" and "stop-b" in holder:
                holder["ctx"].halt("stopped by you")
                return "stopped"
            if name:
                write(cwd, f"{name.group(1)}.txt", name.group(1))
                return "wrote"
            return "nothing to do"

        holder["stop-b"] = True
        first = Script(implement=implement, review=approve, panel=approve)
        ctx = context(first, Script(audit=approve), Script(plan=lambda *a: plan_reply(Swarm.PLAN)), workers=1)
        holder["ctx"] = ctx
        result = Orchestrator(ctx, "test task", repo, trees).run()
        self.assertFalse(result["approved"])
        self.assertEqual(ctx.units["a"]["state"], "approved")

        holder.pop("stop-b")
        second = Script(implement=implement, review=approve, panel=approve)
        ctx2, result2 = self.resume(ctx, repo, trees, second)
        self.assertTrue(result2["approved"], log(ctx2))
        text = log(ctx2)
        self.assertIn("a: already approved and merged", text)      # merged when its wave ended, though b stopped
        self.assertNotIn("[a] ── pass", text)
        for name in "abc":
            self.assertEqual(sh(repo, "git", "show", f"{result['branch']}:{name}.txt"), name)


class KeepAndDiscard(unittest.TestCase):
    def finished(self):
        repo = make_repo({"README": "x"})
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())))
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        return repo, ctx.run.dir, result

    def test_keep_merges_and_records(self):
        from mp_agent import actions
        repo, run_dir, result = self.finished()
        self.assertIn("merged into main", actions.keep(run_dir))
        self.assertEqual(sh(repo, "cat", "done.txt"), "ok\n")
        self.assertEqual(sh(repo, "git", "branch", "--list", result["branch"]).strip(), "")
        with self.assertRaises(actions.ActionError):
            actions.keep(run_dir)

    def test_keep_refuses_a_dirty_project(self):
        from mp_agent import actions
        repo, run_dir, _ = self.finished()
        write(repo, "README", "local edit")
        with self.assertRaisesRegex(actions.ActionError, "uncommitted"):
            actions.keep(run_dir)

    def test_conflict_changes_nothing(self):
        from mp_agent import actions
        repo, run_dir, _ = self.finished()
        write(repo, "done.txt", "different\n")
        sh(repo, "git", "add", "-A")
        sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "clash")
        with self.assertRaisesRegex(actions.ActionError, "conflict"):
            actions.keep(run_dir)
        self.assertEqual(sh(repo, "cat", "done.txt"), "different\n")
        self.assertFalse(gitops_dirty(repo))

    def test_discard_deletes_the_branch(self):
        from mp_agent import actions
        repo, run_dir, result = self.finished()
        actions.discard(run_dir)
        self.assertEqual(sh(repo, "git", "branch", "--list", result["branch"]).strip(), "")


class NewTools(unittest.TestCase):
    def finished(self, remote=None):
        repo = make_repo({"README": "x"})
        if remote:
            sh(repo, "git", "remote", "add", "origin", remote)
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done",
                         review=approve, panel=approve)
        ctx = context(builder, Script(audit=lambda *a: "Looks right; one remark.\nVERDICT: APPROVED"),
                      Script(plan=lambda *a: plan_reply(single())))
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        return repo, ctx.run.dir, result

    def test_changes_lists_files_diff_and_judge_notes(self):
        from mp_agent import actions
        repo, run_dir, result = self.finished()
        info = actions.changes(run_dir)
        self.assertEqual([f["path"] for f in info["files"]], ["done.txt"])
        self.assertIn("+ok", info["diff"])
        self.assertIn("one remark", info["judge"])

    def test_pull_request_refuses_protected_repos_and_repos_without_github(self):
        from mp_agent import actions
        state = tmpdir("mp-state-")
        write(state, "config.json", json.dumps({"protected": ["keepout/db-web"]}))
        _, run_dir, _ = self.finished(remote="https://github.com/keepout/db-web.git")
        with self.assertRaisesRegex(actions.ActionError, "listed as protected"):
            actions.pull_request(run_dir, state_dir=state)
        _, run_dir, _ = self.finished()
        with self.assertRaisesRegex(actions.ActionError, "no GitHub remote"):
            actions.pull_request(run_dir)

    def test_spending_cap_asks_and_can_be_raised(self):
        class FakeUsage:
            def refresh(self, force=False):
                pass

            def totals(self):
                return {"inception/mercury-2.5": {"usd": 1.5}, "claude-sonnet-5": {"usd": 9.0}}

        repo = make_repo()
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                         panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())), max_usd=1.0)
        ctx.usage = FakeUsage()

        def person():
            if wait_for(os.path.join(ctx.run.dir, "question.json")):
                answer(ctx, {"answer": "raise it to 5"})

        threading.Thread(target=person, daemon=True).start()
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("spending cap reached: $1.50 of $1.00", log(ctx))     # Claude on Max not counted
        self.assertIn("spending cap raised to $5.00", log(ctx))

    def test_spending_cap_is_checked_before_the_review_stages_too(self):
        spend = {"usd": 0.0}

        class GrowingUsage:
            def refresh(self, force=False):
                pass

            def totals(self):
                return {"inception/mercury-2.5": {"usd": spend["usd"]}}

        def implement(prompt, cwd):
            spend["usd"] = 0.5          # the implementer spends past the cap within its first pass
            write(cwd, "done.txt", "ok\n")
            return "done"

        repo = make_repo()
        builder = Script(implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())), max_usd=0.1,
                      ask=False)
        ctx.usage = GrowingUsage()
        result = orchestrate(ctx, repo)
        self.assertFalse(result["approved"])
        self.assertEqual(builder.count("review"), 0)
        self.assertIn("spending cap", result["outcome"])

    def test_spending_cap_stops_when_told(self):
        class FakeUsage:
            def refresh(self, force=False):
                pass

            def totals(self):
                return {"inception/mercury-2.5": {"usd": 3.0}}

        repo = make_repo()
        builder = Script(implement=lambda p, cwd: "never gets here", review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(single())), max_usd=1.0,
                      ask=False)
        ctx.usage = FakeUsage()
        result = orchestrate(ctx, repo)
        self.assertFalse(result["approved"])
        self.assertIn("spending cap", result["outcome"])


class Swarm(unittest.TestCase):
    PLAN = {
        "mode": "swarm", "summary": "three parts",
        "contract": {"done": ["a, b and c exist"], "out_of_scope": []},
        "check": "test -f a.txt && test -f b.txt && test -f c.txt",
        "tests": None,
        "subtasks": [
            {"id": "a", "goal": "write a.txt", "owns": ["a.txt"], "done": ["a.txt"], "check": "test -f a.txt"},
            {"id": "b", "goal": "write b.txt", "owns": ["b.txt"], "done": ["b.txt"], "check": "test -f b.txt"},
            {"id": "c", "goal": "write c.txt from a and b", "owns": ["c.txt"], "done": ["c.txt"],
             "depends_on": ["a", "b"], "check": "test -f c.txt && test -f a.txt"},
        ],
    }

    def implement(self, prompt, cwd):
        goal = re.search(r"# Task\n\nwrite (\w)\.txt", prompt)
        if goal:
            name = goal.group(1)
            write(cwd, f"{name}.txt", name)
            if name == "b":
                write(cwd, "a.txt", "b trampling a")   # not b's file: must be reverted
            return f"wrote {name}"
        return "nothing to do"

    def test_waves_merge_and_final_gates(self):
        repo = make_repo()
        builder = Script(implement=self.implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(self.PLAN)), workers=2)
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        text = log(ctx)
        self.assertIn("── wave 1/2: a, b", text)
        self.assertIn("── wave 2/2: c", text)
        self.assertIn("[b]    changed files it may not touch (a.txt); reverted", text)
        self.assertIn("── final gates on the merged work", text)
        self.assertEqual(on_branch(repo, result, "a.txt"), "a")
        self.assertEqual(on_branch(repo, result, "c.txt"), "c")
        self.assertEqual(sh(repo, "git", "worktree", "list").count("\n"), 1)

    def test_failed_subtask_stops_the_run(self):
        repo = make_repo()
        builder = Script(implement=lambda p, cwd: "never writes anything", review=approve, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(self.PLAN), ruling=lambda *a: "NO AMENDMENT",
                        replan=lambda *a: "no", question=lambda *a: "QUESTION: ?")
        ctx = context(builder, Script(audit=approve), lead, patience=2, ask=False)
        result = orchestrate(ctx, repo)
        self.assertFalse(result["approved"])
        self.assertIn("NOT approved", result["outcome"])


    def test_a_stopped_part_does_not_stop_the_others(self):
        repo = make_repo()

        def implement(prompt, cwd):
            if re.search(r"# Task\n\nwrite a\.txt", prompt):
                write(cwd, "a.txt", "a")
                return "wrote a"
            return "b never manages it"

        builder = Script(implement=implement, review=approve, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(self.PLAN), ruling=lambda *a: "NO AMENDMENT",
                      replan=lambda *a: "no", question=lambda *a: "QUESTION: ?")
        ctx = context(builder, Script(audit=approve), lead, patience=2, ask=False, workers=2)
        result = orchestrate(ctx, repo)
        text = log(ctx)
        self.assertFalse(result["approved"])
        self.assertEqual(ctx.units["a"]["state"], "approved", text)
        self.assertIn("   b stopped: NOT approved", text)
        self.assertIn("a approved and merged", result["outcome"])
        self.assertEqual(on_branch(repo, result, "a.txt"), "a")          # the finished part is kept
        self.assertNotIn("── wave 2/2", text)                             # c needs b, so it never starts


class Shape(unittest.TestCase):
    def test_the_persons_choice_of_solo_is_enforced(self):
        repo = make_repo({"README": "x"})
        replies = [dict(Swarm.PLAN, why="three parts"), single(why="one small change")]
        lead = Script(plan=lambda *a: plan_reply(replies.pop(0)))
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                         panel=approve)
        ctx = context(builder, Script(audit=approve), lead, shape="solo")
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        prompts = [c[1] for c in lead.calls if c[0] == "plan"]
        self.assertEqual(len(prompts), 2)
        self.assertIn("The person chose a solo run", prompts[0])
        self.assertIn('the person chose a solo run: "mode" must be "single"', prompts[1])
        self.assertIn("why solo (you chose it): one small change", log(ctx))

    def test_the_planner_says_why_when_it_decides(self):
        repo = make_repo({"README": "x"})
        lead = Script(plan=lambda *a: plan_reply(single(why="a one-line fix")))
        builder = Script(implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                         panel=approve)
        ctx = context(builder, Script(audit=approve), lead)
        self.assertTrue(orchestrate(ctx, repo)["approved"], log(ctx))
        self.assertIn("why solo: a one-line fix", log(ctx))
        prompt = next(c[1] for c in lead.calls if c[0] == "plan")
        self.assertNotIn("Required shape", prompt)
        self.assertIn("parts are built at the same time", prompt)


class SwarmUpgrades(unittest.TestCase):
    def test_an_upgrade_is_for_the_stuck_part_and_later_work_and_others_adopt_it_when_stuck(self):
        from mp_agent.contract import Contract
        from mp_agent.worker import UnitSpec, Worker
        weak, strong = Script("weak"), Script("claude:opus")
        ctx = context(weak, Script(audit=approve), Script())
        ctx.upgrade = {"mode": "auto", "to": "", "max": 1}
        made = []
        ctx.make_worker = lambda spec: made.append(spec) or strong
        ctx.model_options = [{"spec": "claude:opus", "label": "Claude Opus"}, {"spec": "claude:sonnet", "label": "Claude Sonnet"}]
        ctx.roles = {"worker": "cline:inception:mercury-2.5", "planner": "claude:sonnet", "judge": "claude:sonnet"}
        tree = make_repo()
        part = lambda name: Worker(ctx, UnitSpec(name=name, goal="g", contract=Contract(["x"], []), check=None,
                                                 owns=[f"{name}.txt"]), tree)
        a, b = part("a"), part("b")
        self.assertTrue(ctx.offer_upgrade(b, "stuck", ""))
        self.assertIs(b.agent, strong)
        self.assertIs(a.agent, weak)                         # a was doing fine and keeps its workers
        self.assertIs(part("c").agent, strong)               # work that starts later gets the new ones
        self.assertTrue(ctx.offer_upgrade(a, "stuck", ""))  # a gets stuck too: same upgrade, not a second one
        self.assertIs(a.agent, strong)
        self.assertEqual(made, ["claude:opus"])
        self.assertEqual(len(ctx.upgrades), 1)
        self.assertIn("[a]    upgraded the workers from cline:inception:mercury-2.5 to claude:opus (already chosen",
                      log(ctx))


class OutOfCredit(unittest.TestCase):
    def test_a_model_out_of_credit_can_be_switched_for_every_role_it_plays(self):
        repo = make_repo({"README": "x"})
        broke = Reply("error: Free tier limit reached. Please upgrade to a paid plan to continue using the service.",
                      1, 0.01)
        builder = Script("mercury", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done")
        reviewer = Script("mercury", review=lambda *a: broke)
        panel = Script("mercury", panel=lambda *a: broke)
        fresh = Script("luna", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                       panel=approve)
        ctx = context(builder, Script("sonnet", audit=approve), Script(plan=lambda *a: plan_reply(single())),
                      reviewer=reviewer, panel=panel)
        ctx.roles = {"worker": "cline:inception:mercury-2.5", "reviewer": None, "panel": None,
                     "planner": "claude:sonnet", "judge": "claude:sonnet"}
        ctx.model_options = [{"spec": s, "label": s} for s in
                             ("claude:sonnet", "claude:opus", "codex:gpt-5.6-luna", "cline:inception:mercury-coder")]
        made = []
        ctx.make_worker = lambda spec: made.append(("worker", spec)) or fresh
        ctx.make_agent = lambda spec: made.append(("agent", spec)) or fresh
        asked = {}

        def person():
            if wait_for(os.path.join(ctx.run.dir, "question.json")):
                with open(os.path.join(ctx.run.dir, "question.json")) as fh:
                    asked.update(json.load(fh))
                answer(ctx, {"answer": "switch codex:gpt-5.6-luna"})
        threading.Thread(target=person, daemon=True).start()
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        offered = [c["answer"] for c in asked["choices"]]
        self.assertIn("switch codex:gpt-5.6-luna", offered)
        self.assertIn("switch claude:opus", offered)
        self.assertNotIn("switch claude:sonnet", offered)                  # the judge may not review its own work
        self.assertNotIn("switch cline:inception:mercury-coder", offered)  # the same account is out of credit too
        self.assertEqual(offered[-2:], ["retry", "stop"])
        self.assertEqual(sorted(made), [("agent", "codex:gpt-5.6-luna"), ("agent", "codex:gpt-5.6-luna"),
                                        ("worker", "codex:gpt-5.6-luna")])
        self.assertIn("switched the workers and quick reviewer and panel from cline:inception:mercury-2.5 to "
                      "codex:gpt-5.6-luna", log(ctx))
        self.assertEqual(result.get("switches", [{}])[0].get("to"), "codex:gpt-5.6-luna")


class Unattended(unittest.TestCase):
    def broken(self):
        return Reply("ERROR: You've hit your usage limit. Purchase more credits.", 1, 0.01)

    def setup(self, unattended):
        repo = make_repo({"README": "x"})
        first = Script("mercury", implement=lambda p, cwd: self.broken())
        fresh = Script("luna", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                       panel=approve)
        ctx = context(first, Script("sonnet", audit=approve), Script(plan=lambda *a: plan_reply(single())),
                      reviewer=fresh, panel=fresh, ask=False, unattended=unattended)
        ctx.roles = {"worker": "cline:inception:mercury-2.5", "reviewer": None, "panel": None,
                     "planner": "claude:sonnet", "judge": "claude:sonnet"}
        ctx.model_options = [{"spec": s, "label": s} for s in ("claude:sonnet", "codex:gpt-5.6-luna")]
        ctx.make_worker = ctx.make_agent = lambda spec: fresh
        return repo, ctx

    def test_nobody_watching_switches_models_and_carries_on(self):
        repo, ctx = self.setup("switch")
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("nobody is watching, so carrying on with another model", log(ctx))
        self.assertEqual(ctx.roles["worker"], "codex:gpt-5.6-luna")
        self.assertEqual(result["switches"][0]["to"], "codex:gpt-5.6-luna")

    def test_it_can_be_told_to_stop_instead(self):
        repo, ctx = self.setup("stop")
        result = orchestrate(ctx, repo)
        self.assertFalse(result["approved"])
        self.assertIn("unavailable", result["outcome"])
        self.assertEqual(ctx.switches, [])


class CarryOnWithoutMe(unittest.TestCase):
    """The workshop's CARRY ON WITHOUT ME writes a file in the run folder while the job runs."""

    def test_a_running_job_settles_provider_trouble_by_itself_once_told_to(self):
        repo = make_repo({"README": "x"})
        broken = Reply("ERROR: You've hit your usage limit. Purchase more credits.", 1, 0.01)
        first = Script("mercury", implement=lambda p, cwd: broken)
        fresh = Script("luna", implement=lambda p, cwd: write(cwd, "done.txt", "ok\n") or "done", review=approve,
                       panel=approve)
        ctx = context(first, Script("sonnet", audit=approve), Script(plan=lambda *a: plan_reply(single())),
                      reviewer=fresh, panel=fresh)
        ctx.roles = {"worker": "cline:inception:mercury-2.5", "reviewer": None, "panel": None,
                     "planner": "claude:sonnet", "judge": "claude:sonnet"}
        ctx.model_options = [{"spec": s, "label": s} for s in ("claude:sonnet", "codex:gpt-5.6-luna")]
        ctx.make_worker = ctx.make_agent = lambda spec: fresh
        self.assertFalse(ctx.alone())
        with open(os.path.join(ctx.run.dir, "alone"), "w") as fh:      # the button
            fh.write("x")
        self.assertTrue(ctx.alone())
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("nobody is watching, so carrying on with another model", log(ctx))

    def test_a_real_question_still_waits_for_you(self):
        repo = make_repo({"README": "x"})
        asked = {}

        def implement(prompt, cwd):
            # once the person has answered, the work is put right; until then it keeps missing
            write(cwd, "done.txt", "ok\n" if asked.get("it") else "no\n")
            return "answered" if asked.get("it") else "same mistake"

        lead = Script(plan=lambda *a: plan_reply(single()), ruling=lambda *a: "NO AMENDMENT",
                      replan=lambda *a: "no", question=lambda *a: "QUESTION: which way round should it be?")
        builder = Script("mercury", implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script("sonnet", audit=approve), lead, patience=2)
        ctx.upgrade = {"mode": "never", "to": "", "max": 0}
        with open(os.path.join(ctx.run.dir, "alone"), "w") as fh:      # the button
            fh.write("x")

        def person():
            if wait_for(os.path.join(ctx.run.dir, "question.json"), timeout=20):
                asked["it"] = True
                answer(ctx, {"answer": "the second way round"})
        threading.Thread(target=person, daemon=True).start()
        result = orchestrate(ctx, repo)
        self.assertTrue(asked.get("it"), "a real question should still have been asked")
        self.assertIn("carrying on without you, but this one needs you", log(ctx))
        self.assertTrue(result["approved"], log(ctx))


class WaveZero(unittest.TestCase):
    def test_tests_written_first_then_frozen(self):
        repo = make_repo()
        plan = single(check="./agent-check.sh",
                      tests={"goal": "done.txt says ok", "files": ["agent-check.sh"]})

        def implement(prompt, cwd):
            if "Write the acceptance tests" in prompt:
                write(cwd, "agent-check.sh", "#!/bin/sh\ngrep -q ok done.txt\n")
                os.chmod(os.path.join(cwd, "agent-check.sh"), 0o755)
                return "tests written"
            write(cwd, "done.txt", "ok\n")
            write(cwd, "agent-check.sh", "#!/bin/sh\nexit 0\n")   # weakening the frozen test
            return "implemented"

        builder = Script(implement=implement, review=approve, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(plan)))
        result = orchestrate(ctx, repo)
        self.assertTrue(result["approved"], log(ctx))
        self.assertIn("frozen: agent-check.sh", log(ctx))
        self.assertIn("changed files it may not touch (agent-check.sh); reverted", log(ctx))
        self.assertIn("grep -q ok done.txt", on_branch(repo, result, "agent-check.sh"))
        tests_review = next(c[1] for c in builder.calls if c[0] == "review" and "acceptance tests" in c[1])
        self.assertIn("a script can decide reliably: (1) done.txt says ok", tests_review)
        self.assertIn("looking at the result in a browser", tests_review)
        self.assertIn("expected to fail", tests_review)

    def test_a_wrong_frozen_test_is_repaired_not_worked_around(self):
        repo = make_repo()
        plan = single(check="./agent-check.sh", tests={"goal": "done.txt says ok", "files": ["agent-check.sh"]})
        rulings = []

        def implement(prompt, cwd):
            if "Write the acceptance tests" in prompt:      # a bug: can never pass on correct work
                write(cwd, "agent-check.sh", "#!/bin/sh\ngrep -q '#ok' done.txt\n")
                os.chmod(os.path.join(cwd, "agent-check.sh"), 0o755)
                return "tests written"
            if "Repair the frozen acceptance tests" in prompt:
                self.assertFalse(os.path.exists(os.path.join(cwd, "done.txt")))   # away from the work
                write(cwd, "agent-check.sh", "#!/bin/sh\ngrep -q ok done.txt\n")
                write(cwd, "done.txt", "not mine to touch\n")
                return "repaired"
            write(cwd, "done.txt", "ok\n")
            return "implemented\nDISPUTE: agent-check.sh looks for '#ok', which correct work never contains"

        def ruling(prompt, cwd):
            rulings.append(prompt)
            return "NO AMENDMENT\nTEST FIX: agent-check.sh: it greps for '#ok'; it should grep for ok"

        builder = Script(implement=implement, review=approve, panel=approve)
        lead = Script(plan=lambda *a: plan_reply(plan), ruling=ruling)
        ctx = context(builder, Script(audit=approve), lead)
        result = orchestrate(ctx, repo)
        text = log(ctx)
        self.assertTrue(result["approved"], text)
        self.assertEqual(len(rulings), 1)
        self.assertIn("the planner says a frozen test is wrong; repairing it", text)
        self.assertIn("[main-testfix]    changed files it may not touch (done.txt); reverted", text)
        self.assertIn("frozen test repaired: agent-check.sh", text)
        self.assertIn("grep -q ok done.txt", on_branch(repo, result, "agent-check.sh"))
        self.assertEqual(on_branch(repo, result, "done.txt"), "ok\n")
        self.assertEqual(sh(repo, "git", "worktree", "list").count("\n"), 1)

    def test_reviewers_of_tests_are_not_told_the_code_must_already_work(self):
        from mp_agent.worker import Worker  # noqa: F401  (keeps the import path honest)
        repo = make_repo()
        plan = single(check="./agent-check.sh", tests={"goal": "done.txt says ok", "files": ["agent-check.sh"]})
        seen = []

        def implement(prompt, cwd):
            if "Write the acceptance tests" in prompt:
                write(cwd, "agent-check.sh", "#!/bin/sh\ngrep -q ok done.txt\n")
                os.chmod(os.path.join(cwd, "agent-check.sh"), 0o755)
                return "tests"
            write(cwd, "done.txt", "ok\n")
            return "done"

        def review(prompt, cwd):
            seen.append(prompt)
            return approve()

        builder = Script(implement=implement, review=review, panel=approve)
        ctx = context(builder, Script(audit=approve), Script(plan=lambda *a: plan_reply(plan)))
        self.assertTrue(orchestrate(ctx, repo)["approved"], log(ctx))
        first = seen[0]
        self.assertNotIn("C1: done.txt says ok", first)
        self.assertIn("implementing the task itself", first)


def gitops_dirty(repo):
    return bool(sh(repo, "git", "status", "--porcelain").strip())


if __name__ == "__main__":
    unittest.main()
