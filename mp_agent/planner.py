"""The planner's jobs: the plan, rulings, re-plans and the question for the person.

The planner decides what the work is and what "done" means. It never edits
code and it is never the one that says the work is finished.
"""
import json
import os
import re

from . import gitops
from .providers import PROVIDER_TROUBLE, acting, classify, tagged
from .waves import CycleError, waves

PLAN_SYSTEM = """You are the planner for an automated coding system. Other agents (the workers, named under "The team") do the implementation, many times over; checks, reviewers and a final auditor judge their work against the contract you write. You decide the plan and the contract. You never write code.

Read the project as much as you need (you have read-only tools), then reply with ONLY a JSON object inside a ```json fence, shaped like this:

{
  "mode": "single" or "swarm",
  "summary": "one or two sentences on the approach",
  "contract": {
    "done": ["concrete, checkable statement", "..."],
    "out_of_scope": ["things reviewers must not demand", "..."]
  },
  "check": "one shell command run from the project root; exit 0 means the whole task is acceptable",
  "tests": null or {"goal": "what the acceptance tests must cover", "files": ["relative/path", "..."]},
  "subtasks": [
    {"id": "lowercase-id", "goal": "...", "owns": ["relative/file.py", "relative/dir/"],
     "depends_on": ["other-id"], "done": ["..."], "out_of_scope": ["..."],
     "check": "command that validates just this subtask"}
  ]
}

Rules:
- Prefer "single". Choose "swarm" only when the work splits into at least two parts that change different files and could be built independently; "subtasks" is ignored for "single".
- contract.done: every statement must be checkable. contract.out_of_scope: be generous. List the edge cases, extra validation and hardening a picky reviewer might invent that this task does not need; reviewers are told these are never reasons to block.
- check: use the project's existing checks when they cover the task. If nothing checks this task yet, set "tests" and make "check" run them.
- tests: acceptance tests written FIRST from the contract, then frozen so implementers cannot change them. Test only what the contract asks, and only what a script can decide reliably; say in tests.goal which criteria those are. Criteria needing judgement or a look in a browser are left to the reviewers. Check structured files (HTML, JSON, YAML) with a real parser that is already installed (python3's standard library has html.parser and json) rather than grep, which reviewers can always find another hole in. If "check" runs a script that does not exist yet (for example ./agent-check.sh), include that script in tests.files.
- subtasks: "owns" lists files, or directories ending in "/", relative to the project root. No two subtasks may own overlapping paths, and no subtask may own a tests file. No globs, no "..", no absolute paths. depends_on names subtasks whose merged work this one needs.
- Every file any subtask will need to create must be owned by exactly one subtask, including shared scaffolding such as package __init__.py files, config files and entry points. Give shared scaffolding to one subtask in the earliest wave (or to a small "scaffold" subtask the others depend on); a worker that writes a file nobody owns has that change thrown away.
- Keep the plan as small as the task allows."""

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def extract_json(text):
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.S)
    candidates = fenced[::-1]
    if not candidates:
        start, end = (text or "").find("{"), (text or "").rfind("}")
        if start != -1 and end > start:
            candidates = [text[start:end + 1]]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    return None


def safe_relative(path):
    return (isinstance(path, str) and path.strip() and not os.path.isabs(path)
            and ".." not in path.split("/") and not any(c in path for c in "*?["))


def _norm(path):
    return path[2:] if path.startswith("./") else path


def overlaps(a, b):
    a, b = _norm(a), _norm(b)
    if a == b or a.rstrip("/") == b.rstrip("/"):
        return True
    if a.endswith("/") and b.startswith(a):
        return True
    return b.endswith("/") and a.startswith(b)


def validate(plan, repo):
    """Everything wrong with a plan, as sentences the planner can act on. Empty list = valid."""
    if not isinstance(plan, dict):
        return ["the reply did not contain a JSON object"]
    errors = []
    mode = plan.get("mode")
    if mode not in ("single", "swarm"):
        errors.append('"mode" must be "single" or "swarm"')
    contract = plan.get("contract") or {}
    if not isinstance(contract.get("done"), list) or not contract.get("done"):
        errors.append("contract.done must list at least one checkable statement")
    check = plan.get("check")
    if not isinstance(check, str) or not check.strip():
        errors.append('"check" must be a shell command')
    tests = plan.get("tests")
    test_files = []
    if tests:
        test_files = tests.get("files") if isinstance(tests, dict) else None
        if not isinstance(test_files, list) or not test_files or not all(safe_relative(f) for f in test_files):
            errors.append("tests.files must be a non-empty list of relative paths without globs or ..")
            test_files = []
        if not isinstance(tests, dict) or not str(tests.get("goal") or "").strip():
            errors.append("tests.goal must say what the acceptance tests cover")
    if isinstance(check, str) and check.strip().startswith("./"):
        script = _norm(check.split()[0])
        if not os.path.exists(os.path.join(repo, script)) and script not in [_norm(f) for f in test_files]:
            errors.append(f"check runs {script}, which does not exist; add it to tests.files or use another command")

    if mode == "swarm":
        subs = plan.get("subtasks")
        if not isinstance(subs, list) or len(subs) < 2:
            errors.append('"swarm" needs at least two subtasks; use "single" otherwise')
            subs = subs if isinstance(subs, list) else []
        ids = [s.get("id") for s in subs if isinstance(s, dict)]
        for s in subs:
            if not isinstance(s, dict):
                errors.append("every subtask must be an object")
                continue
            sid = s.get("id")
            if not isinstance(sid, str) or not ID_RE.match(sid):
                errors.append(f"subtask id {sid!r} must be lowercase letters, digits and dashes")
            if ids.count(sid) > 1:
                errors.append(f"subtask id {sid!r} is used more than once")
            if not str(s.get("goal") or "").strip():
                errors.append(f"subtask {sid} needs a goal")
            owns = s.get("owns")
            if not isinstance(owns, list) or not owns or not all(safe_relative(p) for p in owns):
                errors.append(f"subtask {sid} must own a non-empty list of relative paths (no globs, no ..)")
            if not isinstance(s.get("done"), list) or not s.get("done"):
                errors.append(f"subtask {sid} needs a non-empty done list")
            for dep in s.get("depends_on") or []:
                if dep == sid:
                    errors.append(f"subtask {sid} depends on itself")
                elif dep not in ids:
                    errors.append(f"subtask {sid} depends on unknown subtask {dep!r}")
            for path in owns if isinstance(owns, list) else []:
                if any(overlaps(path, t) for t in test_files):
                    errors.append(f"subtask {sid} owns {path}, which is a frozen tests file")
        for i, a in enumerate(subs):
            for b in subs[i + 1:]:
                if not (isinstance(a, dict) and isinstance(b, dict)):
                    continue
                for pa in a.get("owns") or []:
                    for pb in b.get("owns") or []:
                        if isinstance(pa, str) and isinstance(pb, str) and overlaps(pa, pb):
                            errors.append(f"subtasks {a.get('id')} and {b.get('id')} both own {pa} / {pb}")
        if not errors:
            try:
                waves(subs)
            except CycleError as exc:
                errors.append(str(exc))
    return errors


def project_notes(repo):
    notes = []
    for name in ("agent-check.sh", "package.json", "composer.json", "pyproject.toml", "pytest.ini",
                 "Makefile", "go.mod", "Cargo.toml"):
        if os.path.exists(os.path.join(repo, name)):
            notes.append(name)
    files = gitops.listing(repo)
    return (f"Project root: {repo}\n"
            f"Check-related files present: {', '.join(notes) if notes else 'none'}\n\n"
            f"Tracked files:\n{files if files.strip() else '(the project is empty)'}")


def make_plan(ctx, task, repo, check_override=None, attempts=3, memory=""):
    """Ask the planner for a valid plan. Returns (plan, None) or (None, reason)."""
    prompt = f"# The task\n\n{task}\n\n# The project\n\n{project_notes(repo)}"
    team = [(label, getattr(ctx, attr, None)) for label, attr in
            (("workers", "worker"), ("quick reviewer", "reviewer"), ("panel", "panel"), ("final judge", "judge"))]
    prompt += "\n\n# The team\n\n" + "\n".join(f"- {label}: {agent.name}" for label, agent in team if agent) + (
        "\n\nA fast, cheap worker can afford many passes and a swarm of parallel subtasks; a slow or expensive "
        "one is better given a single, clearly specified unit.")
    if getattr(ctx, "project_rules", ""):
        prompt += ("\n\n" + ctx.project_rules + "\n\nWrite the contract so the work keeps to these rules; put a rule "
                   "in the contract only when this task is likely to run into it.")
    if memory.strip():
        prompt += ("\n\n# What earlier runs on this project settled\n\nThese were decided by rulings, by the person "
                   "who asks for the work, or by implementers. Keep to them unless this task says otherwise, and "
                   "carry any that apply into the contract.\n\n" + memory)
    if check_override:
        prompt += f"\n\n# Required check\n\nThe user requires this check command: `{check_override}`. Use it as \"check\"."
    errors = []
    attempt = 0
    while attempt < attempts:
        attempt += 1
        ask = prompt if not errors else (
            prompt + "\n\n# Your previous plan was rejected\n\nFix every one of these and reply with the whole plan again:\n"
            + "\n".join(f"- {e}" for e in errors))
        ctx.run.write_text(f"plan/attempt-{attempt}-prompt.md", ask)
        with acting("planner", "plan"):
            reply = ctx.planner.ask(PLAN_SYSTEM, ask, repo)
        ctx.run.count("planner_calls")
        ctx.run.write_text(f"plan/attempt-{attempt}-reply.md", reply.text)
        if not reply.ok:
            if classify(reply.status, reply.text) in PROVIDER_TROUBLE and \
                    ctx.provider_trouble("plan", ctx.planner.name, reply):
                attempt -= 1
                continue
            return None, f"the planner could not run (exit {reply.status})"
        plan = extract_json(reply.text)
        if plan is not None and check_override:
            plan["check"] = check_override
        errors = validate(plan, repo)
        if not errors:
            return plan, None
        ctx.say(f"   plan rejected ({len(errors)} problem(s)); asking again")
    return None, "the planner could not produce a valid plan: " + "; ".join(errors)


def single_plan(task, check):
    return {"mode": "single", "summary": "no planner", "contract": {"done": [task], "out_of_scope": []},
            "check": check, "tests": None, "subtasks": []}


RULING_SYSTEM = """You wrote the plan and contract for this work. The agents doing it have stopped making progress, or have raised a question the contract does not settle. Decide.

Reply with one or more lines of the form:
AMENDMENT: <a clear, checkable rule that settles it>
Then a short reason. Rule for the smallest contract that meets the task; when in doubt, put things out of scope rather than demand more. If nothing needs to change in the contract, reply with a single line: NO AMENDMENT

Frozen tests can be wrong too. Read them. If a frozen test would fail on correct work (a bug in the test itself), or demands something the contract does not ask for, also write:
TEST FIX: <file>: <exactly what is wrong in the test and what it should check instead>
Only for a real fault in the test; never to excuse work that does not meet the contract. The harness has the test repaired and reviewed, and the implementer keeps the fixed test."""

REPLAN_SYSTEM = """You wrote the plan and contract for this work. The unit below is stuck even after a ruling. Re-plan it: a clearer goal, a tighter contract and concrete guidance on the approach the implementer should take. It must still own only the same files.

Reply with ONLY a JSON object in a ```json fence:
{"goal": "...", "done": ["..."], "out_of_scope": ["..."], "guidance": "concrete approach, in a few sentences"}"""

QUESTION_SYSTEM = """The automated agents on this task are stuck even after a ruling and a re-plan, so the person who asked for the work is asked to decide. They are not reviewing the code and should not need to read it: ask only what they can answer from knowing what they want.

- Ask about intent, priorities or trade-offs in plain English (what the result should do, which of two behaviours they prefer, whether something matters to them), never about code, tests, scripts, regexes or file contents.
- If the agents are stuck on something technical that the person cannot judge, do not pass the technical question on. Say in one plain sentence what is not working yet, then offer the choices: keep trying, accept it as it is, or stop.
- Short, answerable in a sentence, with the options numbered.

Reply with one line: QUESTION: <the question>"""


def situation(worker, why, feedback):
    spec = worker.spec
    reverts = getattr(worker, "reverts", 0)
    allowed = ", ".join(spec.owns) if spec.owns is not None else "any file except frozen ones"
    return (f"# The unit\n\n{spec.name}: {spec.goal}\n\nIt may change only: {allowed}"
            + (f"\nFrozen files: {', '.join(spec.frozen)}" if spec.frozen else "")
            + (f"\nIt has tried to change files outside that {reverts} time(s); the harness reverted those changes, "
               "so they never reached the disk. That is usually the reason it seems not to be making progress."
               if reverts else "")
            + f"\n\n{spec.contract.render()}\n\n{worker.ctx.decisions.render(spec.name)}\n\n"
            + (f"{worker.ctx.project_rules}\n\n" if getattr(worker.ctx, "project_rules", "") else "")
            + f"# Why it is stuck\n\n{why}\n\n# The latest feedback it was given\n\n{feedback[-6000:]}")


def ruling(ctx, worker, why, feedback):
    reply = ctx.planner.ask(RULING_SYSTEM, situation(worker, why, feedback), worker.tree)
    ctx.run.count("planner_calls")
    ctx.run.write_text(worker.pass_file("ruling.md"), reply.text)
    # a frozen test the planner found at fault; Worker.repair_tests acts on these
    worker.test_fixes = tagged(reply.text, "TEST FIX") if reply.ok and worker.spec.frozen else []
    # the reasoning often says exactly how to get unstuck even when no rule changes
    worker.ruling_advice = _advice(reply.text) if reply.ok else ""
    return tagged(reply.text, "AMENDMENT") if reply.ok else []


def _advice(text):
    lines = [l for l in (text or "").splitlines()
             if not re.match(r"^[\s*_#>-]*(AMENDMENT|TEST FIX):", l) and l.strip().strip("*_") != "NO AMENDMENT"]
    return "\n".join(lines).strip()[:3000]


def replan(ctx, worker, why, feedback):
    prompt = situation(worker, why, feedback)
    reply = ctx.planner.ask(REPLAN_SYSTEM, prompt, worker.tree)
    ctx.run.count("planner_calls")
    ctx.run.write_text(worker.pass_file("replan.md"), reply.text)
    data = extract_json(reply.text) if reply.ok else None
    if reply.ok and data is None:
        # one stray bracket once threw away a good re-plan
        reply = ctx.planner.ask(REPLAN_SYSTEM, prompt + "\n\n# Your previous reply was not valid JSON\n\n"
                                "Reply again with ONLY the JSON object, checked for valid syntax.\n\n"
                                + reply.text[-6000:], worker.tree)
        ctx.run.count("planner_calls")
        ctx.run.write_text(worker.pass_file("replan-retry.md"), reply.text)
        data = extract_json(reply.text) if reply.ok else None
    if not data or not data.get("done"):
        return None
    return data


def question(ctx, worker, why, feedback):
    if ctx.planner is None:
        return f"{worker.spec.name} is stuck ({why}). What should it do?"
    reply = ctx.planner.ask(QUESTION_SYSTEM, situation(worker, why, feedback), worker.tree)
    ctx.run.count("planner_calls")
    found = tagged(reply.text, "QUESTION") if reply.ok else []
    return found[0] if found else f"{worker.spec.name} is stuck ({why}). What should it do?"

