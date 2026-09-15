"""The whole flow for one task.

    setup → plan → [wave 0: acceptance tests] → single unit | swarm waves → final gates

Everything happens on an integration branch in its own worktree; subtasks get
worktrees of their own, branched from the integration branch and merged back
after their wave. The user's checkout is never touched.
"""
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import gitops, memory, planner, rules
from .contract import Contract
from .waves import waves
from .worker import UnitSpec, Worker


def start_visualizer(run=None, open_window=True):
    if os.environ.get("MP_VIZ", "1") == "0":
        return
    try:
        subprocess.Popen(["mp-viz"] + ([] if open_window else ["--no-open"]), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def collect_screenshots(shots_dir, run_dir, since):
    """The browser saves every screenshot into one shared folder; move the ones
    taken during this run into the run's own folder, as proof of what was checked."""
    import shutil
    kept = []
    try:
        names = sorted(os.listdir(shots_dir))
    except OSError:
        return kept
    for name in names:
        source = os.path.join(shots_dir, name)
        if not name.lower().endswith((".png", ".jpg", ".jpeg")) or os.path.getmtime(source) < since:
            continue
        target_dir = os.path.join(run_dir, "screenshots")
        os.makedirs(target_dir, exist_ok=True)
        shutil.move(source, os.path.join(target_dir, name))
        kept.append(name)
    return kept


class Orchestrator:
    def __init__(self, ctx, task, repo, trees_root, check_override=None, use_planner=True, state_dir=None,
                 resume=None):
        self.state_dir = state_dir or os.path.dirname(trees_root)
        self.resume = resume    # {"dir", "plan", "units", "branch", "tree"} from a stopped run
        self.ctx, self.task, self.repo = ctx, task, repo
        self.trees_root, self.check_override, self.use_planner = trees_root, check_override, use_planner
        run = ctx.run
        # Where the work started from: the diff view compares against this commit and a
        # pull request targets this branch.
        self.base_commit = gitops.head(repo)
        self.base_branch = gitops.git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "main"
        if resume:
            self.branch, self.tree = resume["branch"], resume["tree"]
            earlier = resume.get("meta") or {}
            self.base_commit = earlier.get("base_commit") or self.base_commit
            self.base_branch = earlier.get("base_branch") or self.base_branch
        else:
            name, n = f"{run.stamp}-{run.slug}", 2
            while gitops.git(repo, "branch", "--list", f"mp/{name}", check=False).strip() or \
                    os.path.exists(os.path.join(trees_root, name)):
                name = f"{run.stamp}-{run.slug}-{n}"
                n += 1
            self.branch = f"mp/{name}"
            self.tree = os.path.join(trees_root, name)
        self.subtrees = []
        self.rules_loaded = False
        self.cline_refs_before = gitops.cline_refs(repo)   # the person's own Cline checkpoints stay

    def say(self, message):
        self.ctx.say(message)

    def load_rules(self):
        """The project's CLAUDE.md, AGENTS.md and the like, for every role (see rules.py)."""
        self.rules_loaded = True
        files, text = rules.collect(self.state_dir, self.repo, self.tree if os.path.isdir(self.tree) else None)
        self.ctx.project_rules = text
        self.ctx.run.write_json("rules.json", {"files": files, "enabled": rules.enabled(self.state_dir, self.repo)})
        if files:
            self.say(f"   project rules: {', '.join(files)}")
        elif not rules.enabled(self.state_dir, self.repo):
            self.say("   project rules: switched off for this project")

    def outcome(self, approved, text, **extra):
        if not approved and self.ctx.stop_reason == "stopped by you":
            text = "NOT approved: stopped by you"
        self.say("")
        self.say(text)
        return {"approved": approved, "outcome": text, "branch": self.branch, "project": self.repo,
                "task": self.task, "base_commit": self.base_commit, "base_branch": self.base_branch, **extra}

    def run(self):
        ctx, run = self.ctx, self.ctx.run
        run.write_text("repo", self.repo + "\n")
        self.say(f"task       {self.task}")
        self.say(f"repo       {self.repo}")
        self.say(f"branch     {self.branch}")
        self.say(f"workers    {ctx.worker.name}")
        if ctx.planner is not None:
            self.say(f"planner    {ctx.planner.name}")
        gates_line = "check"
        if ctx.options.review:
            gates_line += f" → quick review by {ctx.reviewer.name}"
        if ctx.options.panel_size > 0:
            gates_line += f" → panel of {ctx.panel.name}"
        if ctx.options.audit and ctx.judge is not None:
            gates_line += f" → audit by {ctx.judge.name}"
        self.say(f"limits     keeps going while it makes progress; escalates when stuck; safety net "
                 f"{ctx.options.budget_minutes:g} min / {ctx.options.max_calls} calls")
        self.say(f"gates      {gates_line}")
        self.say("")

        # --- plan -------------------------------------------------------------------------
        if self.resume:
            self.say(f"── resuming {os.path.basename(self.resume['dir'])} with its plan; settled work is kept")
            if not os.path.isdir(self.tree):
                if not gitops.branch_exists(self.repo, self.branch):
                    return self.finish(self.outcome(False, f"NOT approved: cannot resume, branch {self.branch} "
                                                           f"no longer exists", mode=None))
                gitops.worktree_attach(self.repo, self.tree, self.branch)
            plan = self.resume["plan"]
        elif self.use_planner and ctx.planner is not None:
            base = gitops.head(self.repo)
            gitops.worktree_add(self.repo, self.tree, self.branch, base)
            run.phase("planning")
            self.say(f"── planning with {ctx.planner.name}")
            self.load_rules()
            recalled = memory.recall(self.state_dir, self.repo)
            if recalled:
                self.say("   remembering what earlier runs on this project settled")
            plan, problem = planner.make_plan(ctx, self.task, self.tree, self.check_override, memory=recalled)
            if plan is None:
                return self.finish(self.outcome(False, f"NOT approved: {problem}", mode=None))
        else:
            base = gitops.head(self.repo)
            gitops.worktree_add(self.repo, self.tree, self.branch, base)
            plan = planner.single_plan(self.task, self.check_override or "./agent-check.sh")
            if planner.validate(plan, self.tree):
                return self.finish(self.outcome(False, "NOT approved: no planner and no usable check; add "
                                                       "agent-check.sh or pass --check", mode=None))
        if not self.rules_loaded:
            self.load_rules()
        run.write_json("plan.json", plan)
        contract = Contract.from_dict(plan["contract"])
        mode = plan["mode"]
        self.say(f"   plan: {mode} — {plan.get('summary', '')}".rstrip(" —"))
        for item in contract.done:
            self.say(f"   done: {item}")
        if mode == "swarm":
            for i, wave in enumerate(waves(plan["subtasks"]), 1):
                self.say(f"   wave {i}: {', '.join(wave)}")

        # --- wave 0: acceptance tests ---------------------------------------------------------
        frozen = []
        tests = plan.get("tests")
        if tests and self.settled("tests"):
            frozen = list(tests["files"])
            self.say(f"   tests: already settled; frozen: {', '.join(frozen)}")
            ctx.unit_state("tests", state="approved", phase="settled earlier")
        elif tests:
            self.say("── wave 0: acceptance tests")
            # The test writer is judged on the tests, not on the task: the task's own
            # contract says the code must work, which cannot be true yet, and judging
            # against it once sent a test writer round 19 times trying to implement
            # files it was not allowed to touch.
            # Only what a script can decide is theirs. Turning every done item into "a test
            # checks" once had reviewers demand scripted proof that a page was "visually
            # verified in a browser", and the writer invented a Playwright command for it.
            tests_contract = Contract(
                done=[f"the tests cover: {tests['goal']}",
                      f"a test checks each of these that a script can decide reliably: "
                      + "; ".join(f"({i}) {item}" for i, item in enumerate(contract.done, 1)),
                      f"the tests live only in: {', '.join(tests['files'])}",
                      "every test really checks what its message says (no test that can never fail)"],
                out_of_scope=["implementing the task itself, or changing any file other than the tests",
                              "the tests passing now: nothing is implemented yet, so they are expected to fail",
                              "tests for criteria a script cannot decide reliably (judgement or taste, how "
                              "descriptive or accurate wording is, that nothing was invented, looking at the result "
                              "in a browser or screenshots): the reviewers judge those on the finished work",
                              "tools that are not already installed in the project or on the machine",
                              "parsing edge cases the finished work is unlikely to contain (single-quoted "
                              "attributes, tags inside comments) unless the task asks for them",
                              *[f"a test for: {item}" for item in contract.out_of_scope]])
            spec = UnitSpec(
                name="tests",
                goal=("Write the acceptance tests for the task below, BEFORE anything is implemented. Write tests "
                      "only; do not implement the task. The tests are expected to fail until other agents do the "
                      f"work. Test goal: {tests['goal']}\n\nThe task they will test: {self.task}\n\nThe project "
                      f"check will be: `{plan['check']}`"),
                contract=self.carried("tests", tests_contract),
                check=None, owns=list(tests["files"]), review=True, panel=False, audit=False,
                start_at_check=bool(self.resume))
            result = Worker(ctx, spec, self.tree).run()
            if not result.approved:
                return self.finish(self.outcome(False, f"NOT approved: the acceptance tests were not settled "
                                                       f"({result.outcome})", mode=mode))
            frozen = list(tests["files"])
            self.say(f"   frozen: {', '.join(frozen)}")

        # --- the work ---------------------------------------------------------------------------
        if mode == "single":
            if self.settled("main"):
                return self.finish(self.outcome(True, self.approved_line(), mode=mode), success=True)
            spec = UnitSpec(name="main", goal=self.task, contract=self.carried("main", contract), check=plan["check"],
                            owns=None, frozen=frozen, review=True, panel=True, audit=True,
                            start_at_check=bool(self.resume))
            result = Worker(ctx, spec, self.tree).run()
            if not result.approved:
                return self.finish(self.outcome(False, result.outcome, mode=mode))
            return self.finish(self.outcome(True, self.approved_line(), mode=mode), success=True)

        stop = self.swarm(plan, contract, frozen)
        if stop:
            return self.finish(self.outcome(False, stop, mode=mode))

        if self.settled("final"):
            return self.finish(self.outcome(True, self.approved_line(), mode=mode), success=True)
        self.say("── final gates on the merged work")
        spec = UnitSpec(name="final", goal=self.task, contract=self.carried("final", contract), check=plan["check"],
                        owns=None,
                        frozen=frozen, review=True, panel=True, audit=True, start_at_check=True)
        result = Worker(ctx, spec, self.tree).run()
        if not result.approved:
            return self.finish(self.outcome(False, result.outcome, mode=mode))
        return self.finish(self.outcome(True, self.approved_line(), mode=mode), success=True)

    def settled(self, unit):
        return bool(self.resume) and (self.resume["units"].get(unit) or {}).get("state") == "approved"

    def carried(self, unit, contract):
        """On resume, keep the rulings and answers the stopped run had already added."""
        if self.resume:
            try:
                with open(os.path.join(self.resume["dir"], unit, "contract.json")) as fh:
                    for text in json.load(fh).get("amendments") or []:
                        if text not in contract.amendments:
                            contract.amendments.append(text)
            except (OSError, ValueError):
                pass
        return contract

    def approved_line(self):
        opts = self.ctx.options
        judges = [name for name, on in (("the reviewer", opts.review), ("the panel", opts.panel_size > 0)) if on]
        text = "approved: checks pass"
        if judges:
            text += f", {' and '.join(judges)} approved"
        if opts.audit and self.ctx.judge is not None:
            text += f", and {self.ctx.judge.name} approved it"
        return text

    def swarm(self, plan, contract, frozen):
        ctx = self.ctx
        subtasks = {s["id"]: s for s in plan["subtasks"]}
        order = waves(plan["subtasks"])
        for number, wave in enumerate(order, 1):
            reason = ctx.should_stop()
            if reason:
                return f"NOT approved: {reason}"
            self.say(f"── wave {number}/{len(order)}: {', '.join(wave)}")
            run_phase = f"wave {number}/{len(order)}: {', '.join(wave)}"
            ctx.run.phase(run_phase)
            base = gitops.head(self.tree)
            workers, to_merge = [], []
            for sid in wave:
                sub = subtasks[sid]
                path = f"{self.tree}-{sid}"
                branch = f"{self.branch}-{sid}"
                if self.settled(sid):
                    if gitops.branch_exists(self.repo, branch):
                        to_merge.append(sid)        # approved before the stop, not merged yet
                        self.say(f"   {sid}: already approved; merging it")
                    else:
                        self.say(f"   {sid}: already approved and merged")
                    ctx.unit_state(sid, state="approved", wave=number)
                    continue
                resumed = False
                if os.path.isdir(path):
                    resumed = True                   # its unfinished work is still on disk
                elif gitops.branch_exists(self.repo, branch):
                    gitops.worktree_attach(self.repo, path, branch)
                    resumed = True
                else:
                    gitops.worktree_add(self.repo, path, branch, base)
                self.subtrees.append((path, branch))
                spec = UnitSpec(
                    name=sid,
                    goal=f"{sub['goal']}\n\nThis is one part of a larger task, being built in parallel with other "
                         f"parts: {self.task}",
                    contract=Contract(list(sub.get("done") or []), list(sub.get("out_of_scope") or [])),
                    check=sub.get("check") or None, owns=list(sub["owns"]), frozen=frozen,
                    review=True, panel=False, audit=False, start_at_check=resumed)
                spec.contract = self.carried(sid, spec.contract)
                ctx.unit_state(sid, wave=number)
                workers.append(Worker(ctx, spec, path))

            def work(worker):
                result = worker.run()
                if not result.approved:
                    ctx.halt(f"subtask {worker.spec.name} stopped: {result.outcome}")
                return result

            with ThreadPoolExecutor(max_workers=max(1, ctx.options.workers)) as pool:
                results = list(pool.map(work, workers)) if workers else []
            failed = [r for r in results if not r.approved]
            if failed:
                return "NOT approved: " + "; ".join(f"{r.name}: {r.outcome}" for r in failed)

            self.say(f"── merging wave {number}")
            ctx.run.phase(f"merging wave {number}")
            for sid in [w.spec.name for w in workers] + to_merge:
                ok, output = gitops.merge(self.tree, f"{self.branch}-{sid}", f"mp-agent: merge subtask {sid}")
                self.say(f"   merged {sid}" if ok else f"   merge of {sid} failed")
                if not ok:
                    ctx.run.write_text(f"merge-wave-{number}.log", output)
                    return (f"NOT approved: merging subtask {sid} failed although ownership was "
                            f"disjoint; see merge-wave-{number}.log")
                if sid in to_merge:
                    gitops.branch_delete(self.repo, f"{self.branch}-{sid}")
            for path, branch in list(self.subtrees):
                gitops.worktree_remove(self.repo, path)
                gitops.branch_delete(self.repo, branch)
            self.subtrees.clear()

            merged_ids = [w.spec.name for w in workers] + to_merge
            checks = [subtasks[i].get("check") for i in merged_ids if subtasks[i].get("check")]
            if checks:
                combined = " && ".join(f"( {c} )" for c in dict.fromkeys(checks))
                status, output = gitops.run_check(self.tree, combined, ctx.options.check_timeout)
                ctx.run.write_text(f"integration-wave-{number}.log", output)
                self.say(f"   merged checks exit {status}")
                if status != 0:
                    spec = UnitSpec(
                        name=f"integrate-{number}",
                        goal=("Parts of this task were built in parallel and merged, and their checks now fail "
                              "together. Make the merged project pass without undoing the parts' work.\n\n"
                              f"The task: {self.task}"),
                        contract=Contract(list(contract.done), list(contract.out_of_scope)),
                        check=combined, owns=None, frozen=frozen, review=True, start_at_check=True)
                    result = Worker(ctx, spec, self.tree).run()
                    if not result.approved:
                        return f"NOT approved: integrating wave {number} failed: {result.outcome}"
        return None

    def drop_strays(self):
        """Folders an agent made beside the worktrees by guessing a longer path (it once
        wrote index.html into three of them). Real worktrees have a .git file; these do not."""
        import shutil
        try:
            names = os.listdir(self.trees_root)
        except OSError:
            return
        for name in names:
            path = os.path.join(self.trees_root, name)
            if (os.path.isdir(path) and not os.path.exists(os.path.join(path, ".git"))
                    and os.path.getmtime(path) >= self.ctx.run.started - 5):
                shutil.rmtree(path, ignore_errors=True)
                self.say(f"removed a stray folder an agent created: {path}")

    def finish(self, metadata, success=False):
        ctx = self.ctx
        gitops.drop_refs(self.repo, gitops.cline_refs(self.repo) - self.cline_refs_before)
        self.drop_strays()
        if success and not ctx.options.keep:
            gitops.worktree_remove(self.repo, self.tree)
            self.say(f"worktree removed; the work is on branch {self.branch}")
        else:
            self.say(f"worktree kept at {self.tree} (branch {self.branch})")
        for path, _ in self.subtrees:
            self.say(f"subtask worktree kept at {path}")
        from .providers import cline_usage, cost_line, summarize_costs
        totals = {}
        try:
            usage_file = os.path.join(ctx.run.dir, "usage.json")
            totals = {}
            if os.path.exists(usage_file):
                with open(usage_file) as fh:
                    totals = json.load(fh)
            totals.update(cline_usage(self.tree, ctx.run.started - 60))
            ctx.run.write_json("usage.json", totals)
            metadata["usage"] = totals
        except (OSError, ValueError):
            pass
        costs = summarize_costs(totals)
        metadata["costs"] = costs
        counters = ctx.run.counters
        self.say(f"cost: {cost_line(costs)}")
        calls = ", ".join(f"{int(counters[f'{role}_calls'])} {role}" for role in
                          ("worker", "reviewer", "panel", "planner", "judge") if counters.get(f"{role}_calls"))
        self.say(f"calls: {calls or 'none'}; {int(ctx.run.active_seconds())}s working "
                 f"({int(counters.get('queue_seconds', 0))}s of it queueing for the providers)"
                 + (f", {int(ctx.run.waiting_seconds)}s waiting for you" if ctx.run.waiting_seconds else ""))
        self.say(f"logs: {ctx.run.dir}")
        metadata["units"] = ctx.units
        shots = collect_screenshots(os.path.join(os.path.dirname(self.trees_root), "shots"), ctx.run.dir,
                                    ctx.run.started - 5)
        if shots:
            metadata["screenshots"] = shots
            self.say(f"screenshots: {len(shots)} kept with the run")
        try:
            remembered = memory.remember(self.state_dir, self.repo, ctx.run.dir, self.task, metadata["outcome"],
                                         list(ctx.decisions.items))
            if remembered:
                self.say(f"remembered for next time: {remembered}")
        except OSError:
            pass
        ctx.run.finish(metadata)
        return metadata
