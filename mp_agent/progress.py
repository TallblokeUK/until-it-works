"""Deciding whether a unit is getting anywhere.

The loop is meant to keep going until the work is right, so a pass ceiling is
the wrong brake: it stops runs that were converging. What should stop a loop
is evidence that it is not converging. Three kinds:

- no change:   the implementer left the tree exactly as it was
- revisit:     the tree went back to a state it was in before (A → B → A),
               which is what oscillating reviewers produce
- churn:       many rounds in a row ending in an objection or a failing check,
               even though the code keeps changing (the endless-nitpick spiral)

Being stalled does not end a run; it escalates (ruling, re-plan, ask the person).
"""


class Progress:
    def __init__(self, patience=3, churn=8):
        self.patience, self.churn = patience, churn
        self.reset()

    def reset(self):
        self.seen = []
        self.no_change = 0
        self.revisits = 0
        self.streak = 0

    def record_tree(self, tree_hash):
        if self.seen and tree_hash == self.seen[-1]:
            self.no_change += 1
        else:
            self.no_change = 0
            if tree_hash in self.seen:
                self.revisits += 1
        self.seen.append(tree_hash)

    def round_failed(self):
        self.streak += 1

    def reason(self):
        """Why this unit counts as stalled, or None if it is still making progress."""
        if self.no_change >= self.patience:
            return f"{self.no_change} passes in a row changed nothing"
        if self.revisits >= 2:
            return "the code keeps returning to versions it had already tried"
        if self.streak >= self.churn:
            return f"{self.streak} rounds in a row without approval"
        return None
