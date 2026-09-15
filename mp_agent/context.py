"""Shared state for one run, and the escalation ladder.

When a unit stops making progress it does not just give up:
    1. ruling    the planner settles what is going round in circles
    2. upgrade   offer a stronger worker model (or switch to one automatically, if set up so)
    3. re-plan   the planner rewrites the unit's goal, contract and approach
    4. ask       the run pauses, notifies the person, and waits for `mp-agent answer`
"""
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import planner
from .providers import billing_of, classify, error_line


@dataclass
class Options:
    workers: int = 3
    patience: int = 3
    churn: int = 8
    panel_size: int = 3
    review: bool = True
    audit: bool = True
    ask: bool = True
    keep: bool = False
    budget_minutes: float = 180.0
    max_calls: int = 800
    check_timeout: int = 600
    answer_poll: float = 3.0
    max_rulings_per_unit: int = 6
    max_test_repairs: int = 2
    max_usd: float = 0.0           # spending cap on real money (not Claude on Max), 0 = none
    shape: str = "auto"            # auto (the planner decides), solo or swarm
    auto_rejudge: int = 1          # times a crashed reviewer is simply run again before asking


def notify(title, message):
    from . import desktop
    desktop.notify(title, message)


class Context:
    """The agents for each role, the run, and what the units share.

    worker, judge and planner are required (judge and planner may be None when the
    audit or planning is switched off); reviewer and panel default to the worker."""

    def __init__(self, run, worker, judge, planner_agent, decisions, options, notifier=notify,
                 reviewer=None, panel=None):
        self.run, self.worker, self.judge, self.planner = run, worker, judge, planner_agent
        self.reviewer, self.panel = reviewer or worker, panel or worker
        self.project_rules = ""        # the project's own instructions (rules.py), for every role
        # upgrading the workers when they are stuck (set by the CLI; tests may leave them unset)
        self.upgrade = {"mode": "never", "to": "", "max": 0}
        self.make_worker = None        # spec -> a ready worker agent
        self.model_options = []        # what is installed, for offering upgrades
        self.roles = {}                # the chosen spec for each role
        self.upgrades = []             # what was upgraded, for the log and HISTORY
        self.switches = []             # models swapped out because they could not be used
        self.make_agent = None         # spec -> a ready agent for a role that is not the workers'
        self.decisions, self.options, self.notifier = decisions, options, notifier
        self.stop = threading.Event()
        self.stop_reason = None
        self._units_lock = threading.Lock()
        self._question_lock = threading.Lock()
        self.units = {}
        self.usage = None              # providers.Usage, for the spending cap
        self._spend_lock = threading.Lock()

    def say(self, message):
        self.run.say(message)

    def halt(self, reason):
        if not self.stop.is_set():
            self.stop_reason = reason
            self.stop.set()

    def should_stop(self):
        if self.stop.is_set():
            return self.stop_reason or "stopped"
        if self.run.active_seconds() > self.options.budget_minutes * 60:
            self.halt(f"the {self.options.budget_minutes:g} minute safety budget ran out")
        calls = sum(v for k, v in self.run.counters.items() if k.endswith("_calls"))
        if calls >= self.options.max_calls:
            self.halt(f"the safety limit of {self.options.max_calls} model calls was reached")
        return self.stop_reason if self.stop.is_set() else None

    def spent_usd(self):
        """Real money so far: models billed to an API key (subscriptions and local models are not)."""
        if self.usage is None:
            return 0.0
        self.usage.refresh(force=True)
        return round(sum(float(v.get("usd") or 0) for k, v in self.usage.totals().items()
                         if billing_of(k, v) == "api"), 4)

    @staticmethod
    def dollars(amount):
        """$0.004 for small sums, $1.50 for larger ones."""
        return f"${amount:.3f}" if amount < 1 else f"${amount:.2f}"

    def check_spend(self, unit):
        """Returns True to carry on. Over the cap, ask whether to raise it."""
        if not self.options.max_usd:
            return True
        with self._spend_lock:
            spent = self.spent_usd()
            if spent < self.options.max_usd:
                return True
            cap = self.options.max_usd
            self.say(f"   spending cap reached: {self.dollars(spent)} of {self.dollars(cap)}")
            answer = self.ask_person(unit, f"This job has spent {self.dollars(spent)} (the cap is {self.dollars(cap)}). "
                                         f"Reply with a new cap such as '{max(1, round(cap * 2))}' to carry on, or 'stop'.")
            if answer:
                number = re.search(r"\d+(?:\.\d+)?", answer)
                if number and float(number.group()) > spent:
                    self.options.max_usd = float(number.group())
                    self.say(f"   spending cap raised to {self.dollars(self.options.max_usd)}")
                    return True
                if answer.strip().lower().split()[0] in ("yes", "y", "continue", "carry", "ok", "go", "raise"):
                    self.options.max_usd = max(cap * 2, spent + 1)
                    self.say(f"   spending cap raised to {self.dollars(self.options.max_usd)}")
                    return True
            self.halt(f"stopped at the spending cap ({self.dollars(spent)})")
            return False

    def unit_state(self, name, **fields):
        with self._units_lock:
            self.units.setdefault(name, {}).update(fields)
            self.run.write_json("units.json", self.units)

    # --- escalation --------------------------------------------------------------

    def escalate(self, worker, why, feedback):
        """Returns extra feedback to continue with, or None if the unit must stop."""
        spec = worker.spec
        if self.planner is not None and worker.escalations == 0:
            worker.escalations = 1
            worker.say("   asking the planner for a ruling")
            amendments = planner.ruling(self, worker, why, feedback)
            repaired = worker.repair_tests()
            if amendments or repaired:
                labels = []
                for text in amendments:
                    ident = spec.contract.amend(text)
                    labels.append(f"{ident}: {text}")
                    worker.say(f"   ruling {ident}: {text}")
                worker.contract_changed()
                ruled = ("The planner ruled on what has been going round in circles. The contract now includes:\n"
                         + "\n".join(f"- {l}" for l in labels) + "\nFollow these rulings exactly.") if labels else ""
                return "\n\n".join(t for t in (ruled, repaired) if t)
            if getattr(worker, "upgrade_hint", ""):
                upgraded = self.offer_upgrade(worker, why, feedback, worker.upgrade_hint)
                if upgraded:
                    return upgraded
            advice = getattr(worker, "ruling_advice", "")
            if advice:
                # No rule changes, but it says how to get unstuck: try that before a re-plan.
                worker.say(f"   the planner made no amendment but gave advice: {advice.splitlines()[0][:140]}")
                return ("The planner looked at why this keeps failing. Nothing in the contract changes, and this "
                        "is its advice; follow it:\n\n" + advice)
            worker.say("   the planner made no amendment")
        if worker.escalations <= 1 and not getattr(worker, "upgrade_offered", False):
            upgraded = self.offer_upgrade(worker, why, feedback)
            if upgraded:
                return upgraded
        if self.planner is not None and worker.escalations <= 1:
            worker.escalations = 2
            worker.say("   still stuck; asking the planner to re-plan this unit")
            data = planner.replan(self, worker, why, feedback)
            if data:
                spec.goal = str(data.get("goal") or spec.goal)
                spec.contract.done = list(data.get("done") or spec.contract.done)
                spec.contract.out_of_scope = list(data.get("out_of_scope") or spec.contract.out_of_scope)
                spec.guidance = str(data.get("guidance") or "")
                worker.say(f"   re-planned: {spec.guidance[:160]}")
                worker.contract_changed()
                return "The planner has re-planned this work. Follow the new goal, contract and guidance above."
            worker.say("   the planner could not re-plan it")
        worker.escalations = 3
        q = planner.question(self, worker, why, feedback)
        answer = self.ask_person(spec.name, q)
        if answer is None:
            return None
        ident = spec.contract.amend(f"The person who asked for this work decided: {answer} (asked: {q})")
        worker.say(f"   recorded as {ident}")
        worker.escalations = 0
        worker.contract_changed()
        return f"The person who asked for this work answered a question about it. {ident}: {answer}"

    def offer_upgrade(self, worker, why, feedback, hint=""):
        """Stuck because the workers can't do it, rather than because the task is unclear:
        offer (or, if set up so, make) a switch to a stronger worker model for the rest of the job.
        Returns feedback to continue with, or "" to carry on up the ladder."""
        from . import models
        worker.upgrade_offered = True
        settings = self.upgrade
        mine = getattr(worker, "agent_spec", None) or self.roles.get("worker") or getattr(self.worker, "name", "")
        if self.upgrades and self.roles.get("worker") and mine != self.roles["worker"] and settings["mode"] != "never":
            # Another part already got stuck and the job's workers were upgraded: this part takes the same ones.
            return self.switch_worker(worker, mine, self.roles["worker"], "already chosen for this job", record=False)
        if settings["mode"] == "never" or self.make_worker is None or len(self.upgrades) >= max(settings["max"], 0):
            return ""
        current_spec = mine
        exclude = {self.roles.get("planner"), self.roles.get("judge")}
        choices = models.upgrade_options(current_spec, self.model_options, exclude=exclude)
        if not choices:
            return ""
        label = lambda spec: models.label_for(spec, self.model_options)
        latest = next((l.strip() for l in (feedback or "").splitlines() if l.strip() and not l.startswith("#")), "")[:200]
        if settings["mode"] == "auto":
            wanted = next((c["spec"] for c in choices if c["spec"] == settings["to"]), choices[0]["spec"])
            return self.switch_worker(worker, current_spec, wanted, "automatically")
        if not self.options.ask:
            return ""
        options_text = "; ".join(f"'upgrade {c['spec']}' for {c['label']}" for c in choices)
        question = (f"The workers ({label(current_spec)}) are stuck on {worker.spec.name}: {hint or why}."
                    + (f" The latest objection: {latest}" if latest else "")
                    + f" Upgrade the workers for the rest of this job? Reply {options_text}; 'keep' to keep trying "
                      "as they are; or 'stop'.")
        self.question_choices = ([{"label": f"USE {c['label'].split(' (')[0].upper()}", "answer": f"upgrade {c['spec']}",
                                   "detail": c["label"]} for c in choices]
                                 + [{"label": "KEEP TRYING", "answer": "keep"}, {"label": "STOP", "answer": "stop"}])
        answer = self.ask_person(worker.spec.name, question)
        self.question_choices = None
        if answer is None:
            return ""
        words = answer.strip().split()
        if words and words[0].lower() == "stop":
            self.halt("stopped by you")
            return ""
        if words and words[0].lower() == "upgrade" and len(words) > 1:
            try:
                wanted = models.resolve(" ".join(words[1:]), self.model_options)
            except models.ChoiceError as exc:
                worker.say(f"   not upgraded: {exc}")
                return ""
            if wanted in exclude:
                worker.say(f"   not upgraded: {wanted} is the planner or the judge, and a model may not judge its own work")
                return ""
            return self.switch_worker(worker, current_spec, wanted, "as you chose")
        worker.say("   keeping the current workers")
        return ""

    def switch_worker(self, worker, old, new, how, record=True):
        with self._units_lock:
            if record or self.worker is None or self.roles.get("worker") != new:
                self.worker = self.make_worker(new)
                self.roles["worker"] = new
            agent = self.worker
            if record:
                self.upgrades.append({"unit": worker.spec.name, "from": old, "to": new, "pass": worker.passes,
                                      "how": how})
                self.run.write_json("upgrades.json", self.upgrades)
        if hasattr(worker, "agent"):
            worker.agent, worker.agent_spec = agent, new
        worker.say(f"   upgraded the workers from {old} to {new} ({how}), "
                   + ("for the rest of this job" if record else "the same as the rest of this job"))
        worker.progress.reset()
        return (f"The work is now being done by a stronger model ({new}), because the previous workers kept getting "
                "stuck. Everything decided so far still stands: the contract, the rulings and the decisions log. Look "
                "at the latest feedback afresh and fix the real cause.")

    ROLE_ORDER = ("worker", "reviewer", "panel", "planner", "judge")

    def roles_using(self, who, worker=None):
        """The roles whose model is the agent named who."""
        hit = [role for role in self.ROLE_ORDER if getattr(getattr(self, role, None), "name", None) == who]
        if worker is not None and getattr(getattr(worker, "agent", None), "name", None) == who and "worker" not in hit:
            hit.insert(0, "worker")
        return hit

    def role_spec(self, role):
        spec = self.roles.get(role)
        return spec or (self.roles.get("worker") if role in ("reviewer", "panel") else None)

    def switch_choices(self, who, worker=None):
        """(roles, current spec, [options]) for offering another model in place of one that cannot
        be used, or None when there is nothing to offer."""
        from . import models
        hit = self.roles_using(who, worker)
        if not hit or self.make_agent is None or self.make_worker is None or not self.model_options:
            return None
        current = self.role_spec(hit[0])
        if not current:
            return None
        # a model may never judge its own work: builders stay apart from the planner and judge
        builders = {"worker", "reviewer", "panel"}
        if builders & set(hit):
            exclude = {self.role_spec(r) for r in ("planner", "judge") if r not in hit}
        else:
            exclude = {self.role_spec(r) for r in builders if r not in hit}
        choices = models.switch_options(current, self.model_options, exclude=exclude - {None})
        return (hit, current, choices) if choices else None

    def switch_model(self, hit, old, wanted, worker=None, why="out of credit or usage"):
        """Use another model for the roles in hit, for the rest of the job."""
        from . import models
        try:
            new = models.resolve(wanted, self.model_options)
        except models.ChoiceError as exc:
            self.say(f"   not switched: {exc}")
            return False
        with self._units_lock:
            for role in hit:
                if role == "worker":
                    self.worker = self.make_worker(new)
                    if worker is not None and hasattr(worker, "agent"):
                        worker.agent, worker.agent_spec = self.worker, new
                else:
                    setattr(self, role, self.make_agent(new))
                self.roles[role] = new
            self.switches.append({"roles": list(hit), "from": old, "to": new, "why": why})
            self.run.write_json("switches.json", self.switches)
        names = {"worker": "workers", "reviewer": "quick reviewer", "panel": "panel", "planner": "planner",
                 "judge": "judge"}
        what = " and ".join(names[r] for r in hit)
        self.say(f"   switched the {what} from {old} to {new} ({old} is {why}), for the rest of this job")
        return True

    def provider_trouble(self, unit, who, reply, worker=None):
        """A model could not be reached or refused to work, and backing off did not
        fix it. Nobody's work is at fault, so rather than end the run, ask a person
        whether to keep going, or (when it is out of credit or usage) to use another
        model instead. Returns True to try the same step again."""
        kind = classify(reply.status, reply.text)
        what = {
            "fatal": "cannot continue (out of credit or usage, or not logged in)",
            "rate": "is still rate limited after backing off for several minutes",
            "transient": "keeps failing with server errors",
            "missing": "cannot be started",
            "timeout": "keeps timing out",
        }.get(kind, "keeps failing")
        detail = error_line(reply.text)
        self.say(f"   provider trouble: {who} {what}: {detail}")
        if not self.options.ask:
            return False
        offer = self.switch_choices(who, worker) if kind in ("fatal", "rate", "failed") else None
        question = (f"{who} {what} ({detail}). Sort it out if it needs you (credit, login, network), then reply "
                    f"'retry' to carry on, or 'stop' to end the run.")
        if offer:
            hit, current, choices = offer
            from . import models
            question += (" Or use another model for the rest of this job: "
                         + "; ".join(f"'switch {c['spec']}' for {c['label']}" for c in choices) + ".")
            self.question_choices = ([{"label": f"SWITCH TO {c['label'].split(' (')[0].upper()}",
                                       "answer": f"switch {c['spec']}", "detail": c["label"]} for c in choices]
                                     + [{"label": "RETRY", "answer": "retry"}, {"label": "STOP", "answer": "stop"}])
        try:
            answer = self.ask_person(unit, question)
        finally:
            self.question_choices = None
        words = (answer or "").strip().split()
        if offer and words and words[0].lower() == "switch" and len(words) > 1:
            return self.switch_model(offer[0], offer[1], " ".join(words[1:]), worker)
        return bool(words) and words[0].lower().strip(".,!") in (
            "retry", "yes", "y", "continue", "carry", "go", "ok", "wait", "resume")

    @staticmethod
    def _read_answer(path):
        """The answer once the file is complete, else None. mp-agent and the workshop write it in
        one step, but something writing it by hand can be caught halfway: wait for the rest."""
        try:
            with open(path) as fh:
                return str(json.load(fh).get("answer") or "").strip()
        except (OSError, ValueError, AttributeError):
            return None

    def ask_person(self, unit, question):
        if not self.options.ask:
            self.say(f"   needs a decision but asking is off: {question}")
            return None
        with self._question_lock:
            run = self.run
            answer_path = os.path.join(run.dir, "answer.json")
            if os.path.exists(answer_path):
                os.remove(answer_path)
            run.write_json("question.json", {"unit": unit, "question": question,
                                             "choices": getattr(self, "question_choices", None) or [],
                                             "asked_at": datetime.now(timezone.utc).isoformat()})
            run.phase(f"waiting for you: {question}")
            self.say(f"   waiting for you: {question}")
            self.say('   answer with: mp-agent answer "..."')
            self.notifier("mp-agent needs you", question)
            started = time.time()
            try:
                answer = None
                while answer is None:
                    if self.stop.is_set():
                        return None
                    answer = self._read_answer(answer_path)
                    if answer is None:
                        time.sleep(self.options.answer_poll)
            finally:
                run.waiting_seconds += time.time() - started
                for name in ("question.json", "answer.json"):
                    try:
                        os.remove(os.path.join(run.dir, name))
                    except OSError:
                        pass
            self.say(f"   you answered: {answer}")
            return answer or None
