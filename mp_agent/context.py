"""Shared state for one run, and the escalation ladder.

When a unit stops making progress it does not just give up:
    1. ruling    the planner settles what is going round in circles
    2. re-plan   the planner rewrites the unit's goal, contract and approach
    3. ask       the run pauses, notifies the person, and waits for `mp-agent answer`
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
            advice = getattr(worker, "ruling_advice", "")
            if advice:
                # No rule changes, but it says how to get unstuck: try that before a re-plan.
                worker.say(f"   the planner made no amendment but gave advice: {advice.splitlines()[0][:140]}")
                return ("The planner looked at why this keeps failing. Nothing in the contract changes, and this "
                        "is its advice; follow it:\n\n" + advice)
            worker.say("   the planner made no amendment")
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

    def provider_trouble(self, unit, who, reply):
        """A model could not be reached or refused to work, and backing off did not
        fix it. Nobody's work is at fault, so rather than end the run, ask a person
        whether to keep going. Returns True to try the same step again."""
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
        answer = self.ask_person(unit, f"{who} {what} ({detail}). Sort it out if it needs you (credit, login, "
                                     f"network), then reply 'retry' to carry on, or 'stop' to end the run.")
        return bool(answer) and answer.strip().lower().split()[0].strip(".,!") in (
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
