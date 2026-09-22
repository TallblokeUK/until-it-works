"""The Jev pre-gate: what it asks, what it does with the answer, and what it does when it fails.

Nothing here touches the network. The pre-gate's whole promise is that the panel is never
weakened by it, so most of these tests are about the failure paths.
"""
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mp_agent import fastjudge  # noqa: E402


class Questions(unittest.TestCase):
    def test_only_the_lenses_whose_evidence_is_in_the_prompt(self):
        asked = fastjudge.questions(["edges", "truth", "rehearsal", "look"])
        self.assertEqual(sorted(asked), ["edges", "truth"])
        # rehearsal predicts another model; look needs the rendered page. Neither is in the diff.
        self.assertNotIn("rehearsal", asked)
        self.assertNotIn("look", asked)

    def test_each_question_is_a_noul_phrased_so_high_means_a_problem(self):
        for name, q in fastjudge.questions(["edges", "truth"]).items():
            self.assertEqual(q["type"], "noul", name)
            self.assertEqual(sorted(q["criteria"]), ["false", "true"], name)
            self.assertTrue(q["instructions"].strip(), name)

    def test_the_criteria_carry_the_lens_words(self):
        edges = fastjudge.questions(["edges"])["edges"]
        for word in ("boundary", "whitespace", "error path", "out of scope"):
            self.assertIn(word, (edges["instructions"] + edges["criteria"]["true"] +
                                 edges["criteria"]["false"]).lower(), word)
        truth = fastjudge.questions(["truth"])["truth"]
        for word in ("docstring", "error message", "exact", "validated"):
            self.assertIn(word, (truth["instructions"] + truth["criteria"]["true"] +
                                 truth["criteria"]["false"]).lower(), word)

    def test_the_state_is_named_fields_not_one_blob(self):
        state = fastjudge.state_of(goal="g", contract="c", decisions="d", diff="x", checks="ok")
        self.assertEqual(sorted(state), ["checks", "contract", "decisions", "diff", "goal"])


class Asking(unittest.TestCase):
    def reply(self, answers):
        class Fake:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"model": "jev-1.13.0", "answers": answers}).encode()
        return lambda request, timeout=None: Fake()

    def test_it_reads_the_probability_out_of_each_noul(self):
        answers = {"edges": {"type": "noul", "noul": 0.02}, "truth": {"type": "noul", "noul": 0.71}}
        probs = fastjudge.ask({"goal": "g"}, fastjudge.questions(["edges", "truth"]), "k",
                              opener=self.reply(answers))
        self.assertEqual(probs, {"edges": 0.02, "truth": 0.71})

    def test_it_sends_the_key_as_a_bearer_token_and_never_in_the_body(self):
        sent = {}

        def opener(request, timeout=None):
            sent["headers"] = {k.lower(): v for k, v in request.headers.items()}
            sent["body"] = json.loads(request.data)
            return self.reply({"edges": {"type": "noul", "noul": 0.1}})(request)

        fastjudge.ask({"goal": "g"}, fastjudge.questions(["edges"]), "secret-key", opener=opener)
        self.assertEqual(sent["headers"]["authorization"], "Bearer secret-key")
        self.assertNotIn("secret-key", json.dumps(sent["body"]))
        self.assertEqual(sent["body"]["model"], fastjudge.MODEL)
        self.assertEqual(sorted(sent["body"]), ["model", "questions", "state"])

    def test_an_answer_that_is_missing_or_malformed_is_simply_not_there(self):
        probs = fastjudge.ask({"goal": "g"}, fastjudge.questions(["edges", "truth"]), "k",
                              opener=self.reply({"edges": {"type": "noul"}, "truth": {"type": "noul", "noul": 0.4}}))
        self.assertEqual(probs, {"truth": 0.4})

    def test_every_failure_gives_nothing_rather_than_raising(self):
        def http(request, timeout=None):
            raise urllib.error.HTTPError("u", 500, "boom", {}, None)

        def refused(request, timeout=None):
            raise OSError("connection refused")

        class Garbage:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"<html>not json</html>"

        for opener in (http, refused, lambda request, timeout=None: Garbage()):
            self.assertEqual(fastjudge.ask({"goal": "g"}, fastjudge.questions(["edges"]), "k", opener=opener), {})
        self.assertEqual(fastjudge.ask({"goal": "g"}, fastjudge.questions(["edges"]), "", opener=http), {})


class Deciding(unittest.TestCase):
    NAMES = ["edges", "truth", "rehearsal"]

    def test_a_confidently_clean_lens_is_skipped(self):
        advice = fastjudge.advise(self.NAMES, {"edges": 0.02, "truth": 0.03}, threshold=0.9, skipping=True)
        self.assertEqual(advice.convene, ["rehearsal"])
        self.assertEqual(sorted(advice.skipped), ["edges", "truth"])

    def test_uncertainty_convenes_the_member(self):
        advice = fastjudge.advise(self.NAMES, {"edges": 0.5, "truth": 0.11}, threshold=0.9, skipping=True)
        self.assertEqual(advice.convene, self.NAMES)      # 0.11 > 0.10, so truth is not clean enough
        self.assertEqual(advice.skipped, [])

    def test_a_flagged_problem_convenes_the_member(self):
        advice = fastjudge.advise(self.NAMES, {"edges": 0.97}, threshold=0.9, skipping=True)
        self.assertEqual(advice.convene, self.NAMES)

    def test_no_answers_at_all_convenes_everyone(self):
        advice = fastjudge.advise(self.NAMES, {}, threshold=0.9, skipping=True)
        self.assertEqual(advice.convene, self.NAMES)
        self.assertEqual(advice.skipped, [])

    def test_watching_without_skipping_convenes_everyone_but_remembers(self):
        advice = fastjudge.advise(self.NAMES, {"edges": 0.01}, threshold=0.9, skipping=False)
        self.assertEqual(advice.convene, self.NAMES)
        self.assertEqual(advice.skipped, [])
        self.assertEqual(advice.would_skip, ["edges"])    # what the bench measures

    def test_the_threshold_moves_the_line(self):
        loose = fastjudge.advise(self.NAMES, {"edges": 0.2}, threshold=0.7, skipping=True)
        self.assertEqual(loose.skipped, ["edges"])
        tight = fastjudge.advise(self.NAMES, {"edges": 0.2}, threshold=0.95, skipping=True)
        self.assertEqual(tight.skipped, [])

    def test_it_can_never_empty_the_panel(self):
        advice = fastjudge.advise(["edges", "truth"], {"edges": 0.0, "truth": 0.0}, threshold=0.9, skipping=True)
        self.assertTrue(advice.convene, "at least one member must always read the change")


class Recording(unittest.TestCase):
    def test_it_says_what_it_thought_and_what_it_cost(self):
        advice = fastjudge.advise(["edges", "truth"], {"edges": 0.02, "truth": 0.6}, threshold=0.9, skipping=True)
        text = "\n".join(advice.lines)
        self.assertIn("edges", text)
        self.assertIn("0.02", text)
        self.assertIn("skipped", text)
        self.assertIn("0.6", text)

    def test_agreement_is_recorded_for_the_members_that_did_run(self):
        advice = fastjudge.advise(["edges", "truth"], {"edges": 0.02, "truth": 0.6}, threshold=0.9, skipping=False)
        # Jev: edges 0.02 (nothing there), truth 0.6 (something there).
        # The panel approved edges and objected on truth, so it agreed with both.
        agreed = advice.agreement({"edges": False, "truth": True})
        self.assertEqual(agreed, {"edges": True, "truth": True})
        # now the miss that matters: the member objected where Jev was confidently clean
        self.assertEqual(advice.agreement({"edges": True, "truth": True}), {"edges": False, "truth": True})
        self.assertEqual(advice.agreement({}), {})              # nothing ran, nothing to compare


if __name__ == "__main__":
    unittest.main()


class InThePanel(unittest.TestCase):
    """The pre-gate inside gates.panel(): who is convened, and what happens when Jev fails."""

    def panel_with(self, monkeyed, settings, size=3, brief=""):
        sys.path.insert(0, os.path.dirname(__file__))
        from helpers import Script, approve, context, make_repo
        from mp_agent import gates
        from mp_agent.contract import Contract
        from mp_agent.worker import UnitSpec, Worker

        members = Script("panel", panel=approve)
        ctx = context(Script("worker"), Script("judge"), Script("planner"), reviewer=members, panel=members)
        ctx.fastjudge = settings
        ctx.design_brief = brief
        tree = make_repo()
        spec = UnitSpec(name="main", goal="g", contract=Contract(["it works"], []), check=None, owns=None)
        worker = Worker(ctx, spec, tree)
        real_ask = gates.fastjudge.ask
        gates.fastjudge.ask = monkeyed
        try:
            return gates.pre_gate(ctx, worker, [n for n in gates.LENSES if n != "look" or brief][:size + 1],
                                  "a diff", "checks passed"), ctx
        finally:
            gates.fastjudge.ask = real_ask

    def test_a_confident_clean_lens_is_skipped_when_switched_on(self):
        advice, _ = self.panel_with(lambda *a, **k: {"edges": 0.01, "truth": 0.02},
                                    {"key": "k", "threshold": 0.9, "skipping": True})
        self.assertNotIn("edges", advice.convene)
        self.assertIn("rehearsal", advice.convene)

    def test_while_only_watching_everyone_is_still_convened(self):
        advice, _ = self.panel_with(lambda *a, **k: {"edges": 0.01},
                                    {"key": "k", "threshold": 0.9, "skipping": False})
        self.assertIn("edges", advice.convene)
        self.assertEqual(advice.would_skip, ["edges"])

    def test_no_key_means_the_panel_is_untouched_and_nothing_is_asked(self):
        asked = []
        advice, _ = self.panel_with(lambda *a, **k: asked.append(1) or {}, {"threshold": 0.9, "skipping": True})
        self.assertEqual(asked, [])
        self.assertIn("edges", advice.convene)

    def test_a_failed_call_means_the_panel_is_untouched(self):
        advice, _ = self.panel_with(lambda *a, **k: {}, {"key": "k", "threshold": 0.9, "skipping": True})
        self.assertIn("edges", advice.convene)
        self.assertIn("truth", advice.convene)
        self.assertEqual(advice.skipped, [])

    def test_it_is_given_the_contract_and_the_diff_as_named_fields(self):
        seen = {}

        def spy(state, asked, key, timeout=None, opener=None):
            seen.update(state=state, asked=asked, key=key)
            return {}

        self.panel_with(spy, {"key": "k", "threshold": 0.9, "skipping": True})
        self.assertEqual(sorted(seen["state"]), ["checks", "contract", "decisions", "diff", "goal"])
        self.assertIn("it works", seen["state"]["contract"])
        self.assertEqual(seen["state"]["diff"], "a diff")
        self.assertEqual(sorted(seen["asked"]), ["edges", "truth"])


class TheCurve(unittest.TestCase):
    """Reading watch-mode runs back: one dataset answers the question at every threshold."""

    def a_run(self, folder, passes):
        """passes: [{lens: (probability, objected)}] — one dict per pass."""
        for i, lenses in enumerate(passes, 1):
            where = os.path.join(folder, "main", f"pass-{i:02d}")
            os.makedirs(where, exist_ok=True)
            with open(os.path.join(where, "pre-gate.json"), "w") as fh:
                json.dump({"probabilities": {k: p for k, (p, _) in lenses.items()}}, fh)
            for lens, (_, objected) in lenses.items():
                with open(os.path.join(where, f"panel-{lens}.md"), "w") as fh:
                    fh.write("Some findings.\n\nVERDICT: " + ("CHANGES REQUIRED" if objected else "APPROVED") + "\n")
        return folder

    def test_it_pairs_each_probability_with_what_the_member_said(self):
        import tempfile
        folder = self.a_run(tempfile.mkdtemp(prefix="mp-pregate-"), [
            {"edges": (0.04, False), "truth": (0.9, True)},
            {"edges": (0.30, True)},
        ])
        rows = fastjudge.readings(folder)
        self.assertEqual(sorted((r["lens"], r["probability"], r["objected"]) for r in rows),
                         [("edges", 0.04, False), ("edges", 0.3, True), ("truth", 0.9, True)])

    def test_the_curve_counts_the_skips_and_the_misses_at_each_threshold(self):
        rows = [{"lens": "edges", "probability": 0.04, "objected": False},
                {"lens": "truth", "probability": 0.08, "objected": True},     # the one that matters
                {"lens": "edges", "probability": 0.30, "objected": False},
                {"lens": "truth", "probability": 0.95, "objected": True}]
        curve = {row["threshold"]: row for row in fastjudge.curve(rows, thresholds=(0.9, 0.7))}
        self.assertEqual((curve[0.9]["skipped"], curve[0.9]["missed"]), (2, 1))     # p <= 0.10
        self.assertEqual((curve[0.7]["skipped"], curve[0.7]["missed"]), (3, 1))     # p <= 0.30
        self.assertEqual(curve[0.9]["members"], 4)

    def test_it_says_how_far_apart_the_two_populations_are(self):
        rows = [{"lens": "edges", "probability": 0.1, "objected": False},
                {"lens": "edges", "probability": 0.2, "objected": False},
                {"lens": "truth", "probability": 0.8, "objected": True},
                {"lens": "truth", "probability": 0.9, "objected": True}]
        apart = fastjudge.separation(rows)
        self.assertAlmostEqual(apart["approved"], 0.15)
        self.assertAlmostEqual(apart["objected"], 0.85)
        self.assertAlmostEqual(apart["gap"], 0.7)

    def test_nothing_to_read_is_not_an_error(self):
        import tempfile
        self.assertEqual(fastjudge.readings(tempfile.mkdtemp(prefix="mp-pregate-empty-")), [])
        self.assertEqual(fastjudge.curve([]), [])
        self.assertEqual(fastjudge.separation([]), {"approved": None, "objected": None, "gap": None})


class Replaying(unittest.TestCase):
    """Reading a stored panel prompt back into the five fields, so a wording change can be
    checked against objections that already happened."""

    PROMPT = """# The requirement

Add top_words(text, n) to wordcount.py

## The contract

C1 the n most common words, most common first
C2 ties in alphabetical order

## Decisions so far

(none yet)

## The change, as a diff against the starting point

```diff
+def top_words(text, n):
+    return []
```

## Check output (it passed)

```
OK (3 tests)
```

The files are on disk in the current directory. Read whatever you need, but change nothing."""

    def test_the_five_fields_come_back_out(self):
        state = fastjudge.state_from_prompt(self.PROMPT)
        self.assertEqual(sorted(state), ["checks", "contract", "decisions", "diff", "goal"])
        self.assertEqual(state["goal"], "Add top_words(text, n) to wordcount.py")
        self.assertIn("C2 ties in alphabetical order", state["contract"])
        self.assertIn("+def top_words", state["diff"])
        self.assertIn("OK (3 tests)", state["checks"])

    def test_a_prompt_it_cannot_read_gives_empty_fields_rather_than_raising(self):
        state = fastjudge.state_from_prompt("no headings at all")
        self.assertEqual(set(state.values()), {""})
        self.assertEqual(sorted(fastjudge.state_from_prompt("")), ["checks", "contract", "decisions", "diff", "goal"])

    def test_it_finds_the_passes_that_kept_both_halves(self):
        import tempfile
        runs = tempfile.mkdtemp(prefix="mp-runs-")
        where = os.path.join(runs, "20260921T000000Z-a-job", "main", "pass-01")
        os.makedirs(where)
        with open(os.path.join(where, "panel-prompt.md"), "w") as fh:
            fh.write(self.PROMPT)
        with open(os.path.join(where, "panel-edges.md"), "w") as fh:
            fh.write("VERDICT: CHANGES REQUIRED\n")
        with open(os.path.join(where, "panel-rehearsal.md"), "w") as fh:
            fh.write("VERDICT: APPROVED\n")       # not a covered lens: ignored
        with open(os.path.join(where, "panel-truth.md"), "w") as fh:
            fh.write("no verdict line here")      # abstained: settles nothing
        found = fastjudge.past_passes(runs)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][2], {"edges": True})
        self.assertEqual(fastjudge.past_passes(tempfile.mkdtemp(prefix="mp-empty-")), [])
