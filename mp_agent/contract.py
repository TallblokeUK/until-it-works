"""The contract a unit of work is judged against, and the decisions log.

Without these, reviewers oscillate: on 2026-09-15 a panel objected that
whitespace handling was too loose, later that it was too strict, then demanded
Unicode digits and None handling the task never asked for. A contract fixes
what "done" means and what is out of scope; the decisions log stops a fresh
reviewer from reopening what an earlier round settled.
"""
import json
import threading
from dataclasses import dataclass, field


@dataclass
class Contract:
    done: list
    out_of_scope: list = field(default_factory=list)
    amendments: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        return cls(list(data.get("done") or []), list(data.get("out_of_scope") or []),
                   list(data.get("amendments") or []))

    def to_dict(self):
        return {"done": self.done, "out_of_scope": self.out_of_scope, "amendments": self.amendments}

    def amend(self, text):
        self.amendments.append(text)
        return f"A{len(self.amendments)}"

    def render(self):
        lines = ["## Contract", "", "Done means all of these are true:"]
        lines += [f"- C{i}: {item}" for i, item in enumerate(self.done, 1)] or ["- (nothing listed)"]
        lines += ["", "Out of scope (never a reason to block):"]
        lines += [f"- O{i}: {item}" for i, item in enumerate(self.out_of_scope, 1)] or ["- (nothing listed)"]
        if self.amendments:
            lines += ["", "Amendments (rulings made during this run; they override anything above):"]
            lines += [f"- A{i}: {item}" for i, item in enumerate(self.amendments, 1)]
        return "\n".join(lines)


class Decisions:
    """Run-wide log of how objections were resolved. Thread-safe; persisted on every change."""

    def __init__(self, save=None):
        self.items = []
        self._lock = threading.Lock()
        self._save = save

    def add(self, text, unit, kind="decision"):
        with self._lock:
            ident = f"D{len(self.items) + 1}"
            self.items.append({"id": ident, "unit": unit, "kind": kind, "text": text})
            if self._save:
                self._save(self.items)
            return ident

    def render(self, unit=None):
        with self._lock:
            items = list(self.items)
        if not items:
            return "## Decisions so far\n\n(none yet)"
        lines = ["## Decisions so far",
                 "",
                 "Settled during this run. Do not reopen one unless you can show it breaks the contract."]
        for item in items:
            label = "declined" if item["kind"] == "decline" else "decided"
            where = f" [{item['unit']}]" if item["unit"] != unit else ""
            lines.append(f"- {item['id']}{where} ({label}): {item['text']}")
        return "\n".join(lines)

    def to_json(self):
        with self._lock:
            return json.dumps(self.items, indent=2)
