"""One unit of work: implement → check → review (→ panel → audit), until approved.

A unit is the whole task in single mode, a subtask in a swarm, the acceptance
tests in wave 0, an integration repair, or the final gates on the merged work.
The model never decides completion; the unit is approved only when every gate
it has agrees. It keeps going while it is making progress and escalates when
it is not.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import gates, gitops
from .contract import Contract
from .progress import Progress
from .providers import PROVIDER_TROUBLE, acting, classify, tagged

IMPLEMENTER_SYSTEM = """You are an implementer in an automated engineering loop, working inside a scratch git worktree.

Rules that are not negotiable:
- Work to the contract. Make every "done" item true. Do not build anything listed as out of scope, or anything the contract does not ask for.
- Make the smallest coherent change. Do not restructure code you were not asked to change.
- Change only the paths you are allowed to change. Changes anywhere else are reverted automatically.
- Never make a check pass by weakening it: do not delete, skip, loosen or comment out tests, lint rules or assertions, and do not swallow failures with broad exception handlers. Frozen files cannot be changed at all; if you believe a frozen test is wrong, write a line `DISPUTE: <which test and why>` and leave it as it is.
- Never bend the product to suit a test that is wrong (duplicate or fake elements, hidden text, odd names, special cases that exist only for the test). Build it correctly and DISPUTE the test; a wrong test gets repaired.
- Work only inside this worktree. No sudo, no system packages, no network unless the task needs it.
- Read the real error output before changing anything. Run the validation command yourself before you finish, but understand that your opinion does not end the loop.
- If you have MCP tools: use Context7 to check a library or framework's current API before relying on memory, and use the Playwright browser when the task involves how a web page looks or behaves (take a screenshot of what you checked).

When "What is wrong right now" contains reviewer findings, deal with every one and record each on its own line at the end of your reply:
- `DECISION: <what you did about it and why>` when you changed the code for it, or
- `DECLINE: <the finding> — <the contract line or decision it conflicts with>` when it asks for something out of scope or contradicts an earlier decision. Do not change the code for a declined finding.

Finish with a short plain-text summary of what you changed."""


@dataclass
class UnitSpec:
    name: str
    goal: str
    contract: Contract
    check: str = None
    owns: list = None            # None: any path except frozen ones
    frozen: list = field(default_factory=list)
    review: bool = True
    panel: bool = False
    audit: bool = False
    start_at_check: bool = False  # judge what is already there before implementing anything
    guidance: str = ""


@dataclass
class UnitResult:
    name: str
    approved: bool
    outcome: str
    passes: int
    commit: str = None


def utc_now():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


class Worker:
    def __init__(self, ctx, spec, tree):
        self.ctx, self.spec, self.tree = ctx, spec, tree
        self.escalations = 0
        self.rulings = 0
        self.declines = []
        self.feedback = ""
        self.passes = 0
        self.progress = Progress(ctx.options.patience, ctx.options.churn)
        self.settled_questions = set()
        self.rejudge = False          # judge the same tree again without implementing (a judge had crashed)
        self.notes = ""               # this pass's harness notes (reverted files, rulings) for any failed round
        self.judge_crashes = 0
        self.test_fixes = []          # set by planner.ruling when a frozen test is at fault
        self.test_repairs = 0
        self.tests_repaired = False    # a repair happened since the last gate: the check must run again
        self.ruling_advice = ""       # the planner's reasoning from its last ruling

    # --- plumbing ---------------------------------------------------------------------

    @property
    def prefix(self):
        return "" if self.spec.name in ("main", "final") else f"[{self.spec.name}] "

    def say(self, message):
        self.ctx.say(self.prefix + message)

    def phase(self, text):
        self.ctx.run.phase(self.prefix + text)
        self.ctx.unit_state(self.spec.name, phase=text, passes=self.passes)

    def pass_file(self, name):
        return f"{self.spec.name}/pass-{self.passes:02d}/{name}"

    def contract_changed(self):
        self.ctx.run.write_json(f"{self.spec.name}/contract.json", self.spec.contract.to_dict())

    def result(self, approved, outcome, commit=None):
        self.ctx.unit_state(self.spec.name, state="approved" if approved else "stopped", outcome=outcome)
        return UnitResult(self.spec.name, approved, outcome, self.passes, commit)

    def scope_text(self):
        s = self.spec
        if s.owns is None:
            text = "You may change any file in the project"
            text += (", except these frozen files: " + ", ".join(s.frozen)) if s.frozen else "."
        else:
            text = "You may only create or change these paths: " + ", ".join(s.owns)
            if s.frozen:
                text += ". These files are frozen and must not change: " + ", ".join(s.frozen)
        return "## What you may change\n\n" + text

    def implementer_prompt(self):
        s = self.spec
        parts = [f"# Task\n\n{s.goal}", s.contract.render(), self.ctx.decisions.render(s.name), self.scope_text()]
        if getattr(self.ctx, "project_rules", ""):
            parts.append(self.ctx.project_rules.replace("# Project rules", "## Project rules", 1))
        if s.check:
            parts.append(f"## Validation\n\nThe work is validated by running `{s.check}` from the project root. "
                         "It must exit 0. The harness runs it after you finish; its result, not your opinion, decides.")
        else:
            parts.append("## Validation\n\nThere is no automated check for this unit; reviewers judge it against the contract.")
        if s.guidance:
            parts.append(f"## Guidance from the planner\n\n{s.guidance}")
        if self.feedback:
            parts.append(f"## What is wrong right now\n\n{self.feedback}")
        return "\n\n".join(parts)

    # --- the loop ------------------------------------------------------------------------

    def absorb(self, text):
        """Record the implementer's DECISION / DECLINE lines; return its DISPUTE lines."""
        for line in tagged(text, "DECISION"):
            self.ctx.decisions.add(line, self.spec.name, "decision")
        self.declines = tagged(text, "DECLINE")
        for line in self.declines:
            self.ctx.decisions.add(line, self.spec.name, "decline")
        return tagged(text, "DISPUTE")

    def next_round(self, feedback):
        """End this pass unapproved. Returns a UnitResult if the unit must stop, else None."""
        if self.notes and self.notes not in feedback:
            feedback = self.notes + feedback
        self.feedback = feedback
        self.progress.round_failed()
        why = self.progress.reason()
        if not why:
            return None
        self.say(f"   no progress: {why}")
        extra = self.ctx.escalate(self, why, feedback)
        if extra is None:
            return self.result(False, f"NOT approved: stuck ({why}) and no decision was available")
        self.feedback = f"{feedback}\n\n{extra}"
        self.progress.reset()
        return None

    def settle(self, questions):
        """Put genuine ambiguities to the planner. Returns feedback text if the contract changed."""
        fresh = [q for q in questions if q not in self.settled_questions]
        if not fresh or self.ctx.planner is None or self.rulings >= self.ctx.options.max_rulings_per_unit:
            return ""
        self.settled_questions.update(fresh)
        self.rulings += 1
        self.say(f"   {len(fresh)} open question(s) for the planner")
        from . import planner
        amendments = planner.ruling(self.ctx, self, "a reviewer raised a question the contract does not settle",
                                    "Open questions:\n" + "\n".join(f"- {q}" for q in fresh))
        labels = []
        for text in amendments:
            ident = self.spec.contract.amend(text)
            labels.append(f"{ident}: {text}")
            self.say(f"   ruling {ident}: {text}")
        text = ""
        if labels:
            self.contract_changed()
            text = ("The planner settled open questions; the contract now includes:\n"
                    + "\n".join(f"- {l}" for l in labels) + "\nMake sure the code complies.")
        repaired = self.repair_tests()
        return "\n\n".join(t for t in (text, repaired) if t)

    def repair_tests(self):
        """The planner found a frozen test at fault: have it fixed and reviewed on its own,
        away from the implementer's work, then bring only the test files across.
        Returns feedback for the implementer, or "" if nothing was repaired."""
        fixes, self.test_fixes = self.test_fixes, []
        s, ctx = self.spec, self.ctx
        if not fixes or not s.frozen:
            return ""
        if self.test_repairs >= ctx.options.max_test_repairs:
            self.say("   a frozen test is still said to be wrong, but it has been repaired enough times already")
            return ""
        self.test_repairs += 1
        self.say(f"   the planner says a frozen test is wrong; repairing it: {fixes[0][:140]}")
        scratch = f"{self.tree.rstrip('/')}-testfix-{self.test_repairs}"
        gitops.worktree_detached(self.tree, scratch)
        try:
            spec = UnitSpec(
                name=f"{s.name}-testfix",
                goal=("Repair the frozen acceptance tests below. They are wrong in the way described; fix exactly "
                      "that and keep everything else they check. Change nothing but the tests.\n\n"
                      + "\n".join(f"- {f}" for f in fixes)),
                contract=Contract(
                    done=[*[f"fixed: {f}" for f in fixes],
                          "every other test still checks what it checked before, no more loosely"],
                    out_of_scope=["implementing the task itself (it is not in this checkout)",
                                  "the tests passing here: the work they test is not in this checkout",
                                  "improving tests beyond the faults described"]),
                check=None, owns=list(s.frozen), review=True, panel=False, audit=False)
            result = Worker(ctx, spec, scratch).run()
            if not result.approved or not result.commit:
                self.say(f"   the test repair was not approved ({result.outcome}); the test stays as it was")
                return ""
            changed = gitops.adopt_paths(self.tree, result.commit, s.frozen,
                                         f"mp-agent [{s.name}]: repair frozen tests")
        finally:
            gitops.worktree_remove(self.tree, scratch)
        if not changed:
            return ""
        self.say(f"   frozen test repaired: {', '.join(changed)}")
        self.tests_repaired = True
        self.progress.reset()
        return ("A frozen test was wrong and has been repaired (" + ", ".join(changed) + "): "
                + "; ".join(fixes) + "\nRun the validation again against the repaired test, and undo anything "
                "you changed in the product only to satisfy the old, wrong test.")

    def gate(self, name, result):
        """Log a gate's verdict and turn it into (stop_result, go_round_again)."""
        if self.ctx.stop.is_set():
            return self.result(False, f"NOT approved: {self.ctx.stop_reason}"), False
        for line in result.lines:
            self.say(line)
        if result.failed:
            if result.trouble is not None:
                who, reply = result.trouble
                if (classify(reply.status, reply.text) not in PROVIDER_TROUBLE
                        and self.judge_crashes < self.ctx.options.auto_rejudge):
                    self.judge_crashes += 1
                    self.say(f"   {name} could not run (exit {reply.status}); running it again")
                    self.rejudge = True
                    return None, True
                if self.ctx.provider_trouble(self.spec.name, who, reply):
                    self.rejudge = True
                    return None, True
            return self.result(False, f"NOT approved: {result.detail}"), False
        self.judge_crashes = 0
        ruled = self.settle(result.ambiguities)
        if not result.approved:
            intro = {
                "review": "Validation passes, but an independent reviewer found problems with your change.",
                "panel": "Validation passes and the first reviewer approved, but a pre-audit panel found problems.",
                "audit": "The checks pass and the reviewers approved, but the final auditor found problems.",
            }[name]
            fb = (f"{intro} Deal with each one (fix it, or DECLINE it citing the contract or a decision), "
                  f"then make validation pass again.\n\n{result.findings}")
            if ruled:
                fb += f"\n\n{ruled}"
            return self.next_round(fb), True
        if ruled:
            if self.tests_repaired:
                self.tests_repaired = False
                return self.next_round(ruled), True
            # An approving judge asked a question; the answer is on the contract for any
            # later gate. Going round again for it once cost two full audits on a page
            # that had already passed.
            self.say("   ruling recorded; the approval stands")
        return None, False

    def run(self):
        ctx, s, run = self.ctx, self.spec, self.ctx.run
        self.contract_changed()
        ctx.unit_state(s.name, goal=s.goal, state="working", owns=s.owns)
        skip_implement = s.start_at_check
        model_failures = 0
        self.rejudge = False
        while True:
            reason = ctx.should_stop()
            if reason:
                return self.result(False, f"NOT approved: {reason}")
            if not ctx.check_spend(s.name):
                return self.result(False, f"NOT approved: {ctx.stop_reason}")
            self.passes += 1
            self.say(f"── pass {self.passes} ({utc_now()})")
            notes = ""
            rejudge, self.rejudge = self.rejudge, False

            if not skip_implement and not rejudge:
                self.phase(f"pass {self.passes}: implementing")
                if ctx.stop.is_set():
                    return self.result(False, f"NOT approved: {ctx.stop_reason}")
                prompt = self.implementer_prompt()
                run.write_text(self.pass_file("prompt.md"), prompt)
                with acting("implementer", s.name):
                    reply = ctx.worker.ask(IMPLEMENTER_SYSTEM, prompt, self.tree)
                run.count("worker_calls")
                run.write_text(self.pass_file("output.log"), reply.text)
                self.say(f"   model {int(reply.seconds)}s (exit {reply.status})")
                if ctx.stop.is_set():
                    return self.result(False, f"NOT approved: {ctx.stop_reason}")
                if not reply.ok and classify(reply.status, reply.text) in PROVIDER_TROUBLE:
                    # The provider, not the model's work: same pass again once someone says so.
                    if ctx.provider_trouble(s.name, ctx.worker.name, reply):
                        self.passes -= 1
                        continue
                    return self.result(False, f"NOT approved: {ctx.worker.name} was unavailable "
                                              f"({classify(reply.status, reply.text)})")
                if not reply.ok:
                    model_failures += 1
                    if model_failures >= 3:
                        return self.result(False, f"NOT approved: the model run failed {model_failures} times in a "
                                                  f"row (last exit {reply.status})")
                    stop = self.next_round(f"Your previous attempt did not finish (exit {reply.status}). "
                                           f"Its last output:\n\n{reply.text[-2000:]}")
                    if stop:
                        return stop
                    continue
                model_failures = 0
                disputes = self.absorb(reply.text)
                bad = gitops.ownership_violations(self.tree, s.owns, s.frozen)
                if bad:
                    gitops.revert_paths(self.tree, bad)
                    self.say(f"   changed files it may not touch ({', '.join(bad[:5])}); reverted")
                    self.reverts = getattr(self, "reverts", 0) + 1
                    notes += (f"You changed files you may not touch, so those changes were reverted and are NOT on "
                              f"disk: {', '.join(bad)}. Only change: "
                              f"{', '.join(s.owns) if s.owns is not None else 'files that are not frozen'}. "
                              + ("This has now happened several times: stop trying to change those files; if the "
                                 "task seems to need it, say so in your summary instead. "
                                 if self.reverts >= 2 else "") + "\n\n")
                if disputes:
                    self.say(f"   disputes a frozen test: {disputes[0][:120]}")
                    ruled = self.settle([f"The implementer disputes a frozen test: {d}" for d in disputes])
                    if ruled:
                        notes += ruled + "\n\n"
            skip_implement = False
            self.notes = notes

            if not rejudge:
                self.progress.record_tree(gitops.diff_hash(self.tree))
            diff = gitops.staged_diff(self.tree).decode(errors="replace")
            run.write_text(self.pass_file("diff.patch"), diff)

            validation = None
            if s.check:
                self.phase(f"pass {self.passes}: running check")
                status, output = gitops.run_check(self.tree, s.check, ctx.options.check_timeout)
                run.write_text(self.pass_file("validation.log"), output)
                self.say(f"   check exit {status}, {len(output.splitlines())} lines of output")
                if status != 0:
                    stop = self.next_round("The validation command failed. This is its output. Diagnose "
                                           "the real cause and fix it; do not weaken the check.\n\n" + output[-6000:])
                    if stop:
                        return stop
                    continue
                validation = output
            else:
                self.say("   no automated check for this unit")

            if not ctx.check_spend(s.name):
                return self.result(False, f"NOT approved: {ctx.stop_reason}")
            if s.review and ctx.options.review:
                self.say("   validation passed; independent review" if validation is not None
                         else "   independent review")
                self.phase(f"pass {self.passes}: fast review")
                stop, again = self.gate("review", gates.review(ctx, self, diff, validation))
                if stop:
                    return stop
                if again:
                    continue

            if s.panel and ctx.options.panel_size > 0:
                if not ctx.check_spend(s.name):
                    return self.result(False, f"NOT approved: {ctx.stop_reason}")
                size = min(ctx.options.panel_size, len(gates.LENSES))
                self.say(f"   pre-audit panel: {size} lens(es), {ctx.panel.name}")
                self.phase(f"pass {self.passes}: pre-audit panel")
                stop, again = self.gate("panel", gates.panel(ctx, self, diff, validation, size))
                if stop:
                    return stop
                if again:
                    continue

            if s.audit and ctx.options.audit and ctx.judge is not None:
                if not ctx.check_spend(s.name):
                    return self.result(False, f"NOT approved: {ctx.stop_reason}")
                self.say(f"   confident; final review by {ctx.judge.name}")
                self.phase(f"pass {self.passes}: final audit ({ctx.judge.name})")
                stop, again = self.gate("audit", gates.audit(ctx, self, diff, validation))
                if stop:
                    return stop
                if again:
                    continue

            commit = gitops.commit_all(self.tree, f"mp-agent [{s.name}]: {s.goal.splitlines()[0][:72]}")
            self.say(f"   {s.name} approved after {self.passes} pass(es)")
            return self.result(True, "approved", commit)
