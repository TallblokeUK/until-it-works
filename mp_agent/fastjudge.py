"""A cheap, calibrated look at a change before the panel is convened.

The panel exists because a `VERDICT: APPROVED` line parsed out of prose carries no
confidence: four cheap opinions agreeing is a stand-in for a number nobody has. A System
One model (TypeSafe's Jev) returns that number directly — a probability, calibrated, per
question — so a lens it is confident has nothing to say need not be convened at all.

What it is not: a replacement for anybody. Jev returns a probability, not an argument, and
the findings a panel member writes are what the worker reads on the next pass. This only
ever decides whether to *ask* a member, never what the answer is.

Two lenses are covered, and the two that are left out are left out on purpose:

    edges, truth   the diff, the contract and the check output are the whole of the
                   evidence, and Jev is given all of it
    rehearsal      asks what another model would refuse: a judgment about a model's future
                   behaviour, with no ground truth to be calibrated against
    look           needs the rendered page — screenshots, measurements — which no diff
                   contains. A model cannot be calibrated about evidence it never saw, so
                   it would read confidently clean exactly when the rendering is wrong.

Every failure path ends the same way: the panel runs exactly as it would have. No key, no
network, a bad reply, a timeout — the member list is left alone.
"""
import glob
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
COVERED = ("edges", "truth")
TIMEOUT = 20.0

# Taken from the lens text in gates.py, which is load-bearing: these are the words the panel
# member is given, narrowed to a single yes/no judgment. High means there is a problem.
QUESTIONS = {
    "edges": {
        "type": "noul",
        "instructions": "This change mishandles an input that the contract covers and the tests do not exercise.",
        "criteria": {
            "true": "There is an input within the scope of the contract — an empty or boundary value, zero, a "
                    "negative, a repeat, an unusual ordering, whitespace, a very large value, or an error path — "
                    "for which this code gives a wrong result, raises an unhandled error, or fails silently.",
            "false": "Every input within the scope of the contract is handled, including the empty and boundary "
                     "cases the tests do not reach. Inputs the contract puts out of scope do not count, however "
                     "badly they are handled.",
        },
    },
    "truth": {
        "type": "noul",
        "instructions": "Something in this change states, in words, behaviour the code does not have.",
        "criteria": {
            "true": "A comment, docstring, name, error message or log line describes something other than what the "
                    "code does; or text carried in from elsewhere describes a different situation; or a word such "
                    "as 'exact', 'safe', 'validated' or 'all' claims more than the code lives up to.",
            "false": "Every comment, docstring, name, message and claim in the change matches what the code "
                     "actually does.",
        },
    },
}


def questions(names):
    """The questions worth asking for these lenses: only the ones whose evidence is in the prompt."""
    return {name: QUESTIONS[name] for name in names if name in COVERED}


def state_of(goal, contract, decisions, diff, checks):
    """What the judgment is made on, as named fields rather than one blob."""
    return {"goal": goal, "contract": contract, "decisions": decisions, "diff": diff, "checks": checks}


def ask(state, asked, key, timeout=TIMEOUT, opener=None):
    """{lens: probability of a problem}. Anything at all going wrong gives {}, and {} means
    "convene everyone", so a failure can only ever cost time, never scrutiny."""
    if not key or not asked:
        return {}
    body = json.dumps({"state": state, "model": MODEL, "questions": asked}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        answers = payload.get("answers") or {}
    except Exception:            # HTTP, network, timeout, malformed JSON: all the same answer
        return {}
    out = {}
    for name in asked:
        value = (answers.get(name) or {}).get("noul")
        if isinstance(value, (int, float)) and 0 <= value <= 1:
            out[name] = float(value)
    return out


@dataclass
class Advice:
    convene: list                      # the members to actually run
    skipped: list = field(default_factory=list)
    would_skip: list = field(default_factory=list)      # what it would have skipped, while only watching
    probabilities: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)

    def agreement(self, objected):
        """{lens: did Jev and the member that ran agree}. objected: {lens: the member objected}.
        Only for members that ran — a skipped member has nothing to agree with."""
        out = {}
        for name, p in self.probabilities.items():
            if name in objected:
                out[name] = (p > 0.5) == bool(objected[name])
        return out


def advise(names, probabilities, threshold=0.9, skipping=True):
    """Which members to convene. A lens is skipped only when Jev is confident there is nothing
    there — and never the last one: some member reads every pass, whatever Jev says."""
    clean = 1.0 - max(0.0, min(1.0, threshold))
    confident = [n for n in names if n in probabilities and probabilities[n] <= clean]
    if len(confident) >= len(names):          # never leave the panel empty
        confident = confident[:-1]
    skipped = confident if skipping else []
    advice = Advice(convene=[n for n in names if n not in skipped], skipped=list(skipped),
                    would_skip=[] if skipping else list(confident), probabilities=dict(probabilities))
    for name in names:
        if name not in probabilities:
            continue
        p = probabilities[name]
        if name in skipped:
            what = "skipped"
        elif name in advice.would_skip:
            what = "would have been skipped (watching only)"
        else:
            what = "convened"
        advice.lines.append(f"   pre-gate {name}: {p:g} problem, {what}")
    return advice


# ---- reading it back ------------------------------------------------------------------
# In watch mode every member still runs, so each pass leaves a probability beside what the
# member actually said. That pairing answers the question at every threshold at once: how
# many members a threshold would have skipped, and how many of those would have objected.

VERDICT_LINE = re.compile(r"^[\s*_#>]*VERDICT:\s*(APPROVED|CHANGES REQUIRED)[\s*_]*$", re.M)
THRESHOLDS = (0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.6)


def readings(run_dir):
    """[{lens, probability, objected, pass}] for every member that ran with a probability beside it."""
    rows = []
    for path in sorted(glob.glob(os.path.join(run_dir, "*", "pass-*", "pre-gate.json"))):
        where = os.path.dirname(path)
        try:
            with open(path) as fh:
                probabilities = (json.load(fh) or {}).get("probabilities") or {}
        except (OSError, ValueError):
            continue
        for lens, probability in probabilities.items():
            try:
                with open(os.path.join(where, f"panel-{lens}.md")) as fh:
                    said = VERDICT_LINE.search(fh.read())
            except OSError:
                continue                      # the member was skipped: nothing to compare with
            if not said:
                continue                      # no verdict: it abstained, which settles nothing
            rows.append({"lens": lens, "probability": float(probability),
                         "objected": said.group(1) == "CHANGES REQUIRED",
                         "pass": os.path.basename(where), "unit": os.path.basename(os.path.dirname(where))})
    return rows


def curve(rows, thresholds=THRESHOLDS):
    """What each threshold would have done: members skipped, and the ones that would have objected."""
    if not rows:
        return []                      # no readings, no curve: there is nothing to say
    out = []
    for threshold in thresholds:
        clean = 1.0 - threshold
        skipped = [r for r in rows if r["probability"] <= clean]
        out.append({"threshold": threshold, "members": len(rows), "skipped": len(skipped),
                    "missed": len([r for r in skipped if r["objected"]]),
                    "saved": round(len(skipped) / len(rows), 3) if rows else 0.0})
    return out


def separation(rows):
    """How far apart the two populations sit. A pre-gate is only worth having if the members
    that objected scored higher than the members that approved."""
    approved = [r["probability"] for r in rows if not r["objected"]]
    objected = [r["probability"] for r in rows if r["objected"]]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    a, o = mean(approved), mean(objected)
    return {"approved": a, "objected": o, "gap": round(o - a, 3) if a is not None and o is not None else None}


# ---- replaying what already happened -------------------------------------------------
# Every past pass stored the prompt the panel read and what each member said. Feeding those
# prompts back gives a population of real objections without running a single job, which is
# how a change to the wording above is checked before it is trusted.

HEADING = re.compile(r"^#{1,2} (.+)$", re.M)


def state_from_prompt(prompt):
    """The five fields, recovered from a stored panel prompt."""
    found, spots = {}, [(m.start(), m.group(1).strip().lower()) for m in HEADING.finditer(prompt or "")]
    for i, (start, title) in enumerate(spots):
        end = spots[i + 1][0] if i + 1 < len(spots) else len(prompt)
        found[title] = prompt[start:end].split("\n", 1)[-1].strip()
    pick = lambda *names: next((v for k, v in found.items() for n in names if n in k), "")
    return state_of(goal=pick("requirement"), contract=pick("contract"), decisions=pick("decisions"),
                    diff=pick("the change"), checks=pick("check output"))


def past_passes(runs_dir):
    """[(folder, prompt, {lens: objected})] for passes whose prompt and verdicts were both kept."""
    out = []
    for path in sorted(glob.glob(os.path.join(runs_dir, "*", "*", "pass-*", "panel-prompt.md"))):
        where = os.path.dirname(path)
        verdicts = {}
        for member in sorted(glob.glob(os.path.join(where, "panel-*.md"))):
            lens = os.path.basename(member)[len("panel-"):-len(".md")]
            if lens not in COVERED:
                continue
            try:
                with open(member, errors="replace") as fh:
                    said = VERDICT_LINE.search(fh.read())
            except OSError:
                continue
            if said:
                verdicts[lens] = said.group(1) == "CHANGES REQUIRED"
        if not verdicts:
            continue
        try:
            with open(path, errors="replace") as fh:
                out.append((where, fh.read(), verdicts))
        except OSError:
            continue
    return out
