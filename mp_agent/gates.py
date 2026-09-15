"""The judges: the quick reviewer, the pre-audit panel and the final judge (the auditor).

All of them judge against the contract and the decisions log, and all of them
are guarded: they run as agents with auto-approval, so the implementer's
version is snapshotted before they look and restored if anything changed.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import gitops
from .providers import acting, tagged, verdict

RULES = """Judge the change against the contract below, not against your own idea of what it should be.
- A blocking defect must either break a contract line (cite it, e.g. C2 or A1) or be wrong behaviour for an input that is in scope, and you must name that input.
- Anything listed out of scope, and anything the contract does not ask for, is never a reason to block. Mention it as a remark if you must.
- The decisions log records what earlier rounds settled. Do not reopen a decision unless you can show it breaks the contract.
- If the implementer declined a finding by citing the contract or a decision, accept that unless the citation is wrong.
- If the contract genuinely does not settle something that matters, do not guess: write a line `AMBIGUITY: <the question>`. An ambiguity on its own is not a defect.
- If a frozen test is itself wrong (it would fail on correct work, or the change bends the product to satisfy it), write `AMBIGUITY: frozen test <which> seems wrong: <why>` so it can be repaired; do not ask for the product to be bent further.
- Style, naming, formatting, structure and how you would have written it are not defects.
- If you have MCP tools, use Context7 to confirm a library's current API before calling its use a defect, and the Playwright browser when judging web page behaviour (take a screenshot of what you checked, as proof).
- Do not modify, create or delete any file. Reading files and running throwaway commands that change nothing are fine (for Python, set PYTHONDONTWRITEBYTECODE=1).

End your reply with exactly one of these lines, alone on the line:
VERDICT: APPROVED
VERDICT: CHANGES REQUIRED"""


def open_questions(text):
    """AMBIGUITY lines that really ask something ("AMBIGUITY: none, the contract is clear" does not)."""
    return [q for q in tagged(text, "AMBIGUITY")
            if not re.match(r"(none|n/?a|no ambiguit|nothing)\b", q.strip("*_ ").lower())]


REVIEWER_SYSTEM = (
    "You are an independent senior code reviewer. You did not write this change and have no history with it. "
    "Do not assume it is correct because its checks pass. Look for a misread requirement, unhandled in-scope "
    "cases, regressions, security problems, data loss, poor error handling, and changes that satisfy a test "
    "without solving the problem.\n\n" + RULES)

PANEL_SYSTEM = (
    "You are one member of a pre-audit panel. The change has passed its checks and a first reviewer; a "
    "stronger, slower auditor reads it after you. Your job is to catch, cheaply, what that auditor would catch. "
    "You did not write this code.\n\n" + RULES)

LENSES = {
    "edges": "Your lens: inputs the tests do not exercise. Within the scope of the contract, try empty and "
             "boundary values, zero, negatives, repeats, ordering, whitespace, very large values and the error "
             "paths. Where you can, prove a failure by running a tiny throwaway snippet against the code.",
    "truth": "Your lens: things that are untrue. Comments, docstrings, names, error messages and log text that do "
             "not match what the code does; code or comments copied from elsewhere that describe a different "
             "situation; words like \"exact\", \"safe\", \"validated\" or \"all\" the code does not live up to.",
    "rehearsal": "Your lens: rehearse the final auditor. Read the change the way a careful senior reviewer would "
                 "and predict what it would refuse against this contract. If you are confident it would "
                 "approve, approve.",
}

AUDITOR_SYSTEM = (
    "You are the last reviewer before this change is kept. A faster model wrote it; its checks pass, a reviewer "
    "and a panel of the same kind approved it. You are here because agreement between cheap opinions is not "
    "evidence. Look for what a quick reader misses: a contract line met in the letter but not the substance, an "
    "in-scope case nobody tested, a regression, a security or data-loss risk, a comment or name that states "
    "something untrue, and anything copied from elsewhere that does not apply here.\n\n" + RULES)

MAX_DIFF_CHARS = 40_000


@dataclass
class GateResult:
    approved: bool
    findings: str = ""
    ambiguities: list = field(default_factory=list)
    failed: bool = False
    detail: str = ""
    lines: list = field(default_factory=list)   # log lines describing individual verdicts
    trouble: tuple = None                        # (agent name, Reply) when the judge itself could not run


def review_prompt(goal, contract, decisions, unit, diff, validation, declines):
    parts = [f"# The requirement\n\n{goal}", contract.render(), decisions.render(unit)]
    if declines:
        parts.append("## Findings the implementer declined last pass\n\n" + "\n".join(f"- {d}" for d in declines))
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n[diff truncated; read the files on disk for the rest]"
    parts.append(f"## The change, as a diff against the starting point\n\n```diff\n{diff}\n```")
    if validation is not None:
        parts.append(f"## Check output (it passed)\n\n```\n{validation[-4000:]}\n```")
    else:
        parts.append("## Check output\n\nThere is no automated check for this unit; judge it against the contract.")
    parts.append("The files are on disk in the current directory. Read whatever you need, but change nothing.")
    return "\n\n".join(parts)


def findings_of(text, limit=6000):
    cleaned = re.sub(r"^[\s*_#>]*VERDICT:.*$", "", text or "", flags=re.M).strip()
    return cleaned[-limit:]


def _ask(agent, system, prompt, tree, say, label, count):
    """One verdict-bearing call. A reply without a verdict is asked once more:
    a reviewer that wandered off has not objected to anything."""
    reply = agent.ask(system, prompt, tree)
    count()
    if reply.ok and verdict(reply.text) is None:
        say(f"   {label} gave no verdict; asking again")
        reply = agent.ask(system, prompt, tree)
        count()
    return reply


def _guarded(tree, say, fn):
    snap = gitops.snapshot(tree)
    try:
        return fn()
    finally:
        if gitops.restore(tree, snap):
            say("   a reviewer changed files; restoring the implementer's version")


def review(ctx, worker, diff, validation):
    spec, run = worker.spec, ctx.run
    prompt = review_prompt(spec.goal, spec.contract, ctx.decisions, spec.name, diff, validation, worker.declines)
    def reviewing():
        with acting("reviewer", spec.name):
            return _ask(ctx.reviewer, REVIEWER_SYSTEM, prompt, worker.tree, worker.say, "reviewer",
                        lambda: run.count("reviewer_calls"))
    reply = _guarded(worker.tree, worker.say, reviewing)
    run.write_text(worker.pass_file("review-prompt.md"), prompt)
    run.write_text(worker.pass_file("review.md"), reply.text)
    if not reply.ok:
        return GateResult(False, failed=True, detail=f"the reviewer could not run (exit {reply.status})",
                          trouble=(ctx.reviewer.name, reply))
    v = verdict(reply.text)
    ambiguities = open_questions(reply.text)
    if v == "APPROVED":
        return GateResult(True, ambiguities=ambiguities, lines=["   reviewer approved"])
    line = "   reviewer asked for changes" if v else "   reviewer gave no verdict line"
    return GateResult(False, findings=findings_of(reply.text), ambiguities=ambiguities, lines=[line])


def panel(ctx, worker, diff, validation, size):
    spec, run = worker.spec, ctx.run
    names = list(LENSES)[:max(0, min(size, len(LENSES)))]
    prompt = review_prompt(spec.goal, spec.contract, ctx.decisions, spec.name, diff, validation, worker.declines)
    run.write_text(worker.pass_file("panel-prompt.md"), prompt)

    def member(name):
        # Members share one worktree and run at once, so they are not guarded
        # one by one; the whole panel is guarded below.
        with acting(f"panel:{name}", spec.name):
            return name, _ask(ctx.panel, PANEL_SYSTEM + "\n\n" + LENSES[name], prompt, worker.tree, worker.say,
                              f"panel {name}", lambda: run.count("panel_calls"))

    def convene():
        with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
            return list(pool.map(member, names))

    results = _guarded(worker.tree, worker.say, convene)
    broken = [reply for _, reply in results if not reply.ok]
    if broken and len(broken) == len(results):
        # Every member failed to run: that is the provider, not a verdict.
        return GateResult(False, failed=True, detail=f"the panel could not run (exit {broken[0].status})",
                          trouble=(ctx.panel.name, broken[0]))
    lines, objections, ambiguities = [], [], []
    for name, reply in results:
        run.write_text(worker.pass_file(f"panel-{name}.md"), reply.text)
        v = verdict(reply.text) if reply.ok else None
        ambiguities += open_questions(reply.text)
        if v == "APPROVED":
            lines.append(f"   panel {name}: approved")
        elif v == "CHANGES REQUIRED":
            lines.append(f"   panel {name}: objected")
            objections.append(f"### From the {name} reviewer\n\n{findings_of(reply.text, 3000)}")
        else:
            lines.append(f"   panel {name}: no verdict (abstains)")
    if objections:
        lines.append("   panel asked for changes")
        return GateResult(False, findings="\n\n".join(objections), ambiguities=ambiguities, lines=lines)
    lines.append("   panel approved")
    return GateResult(True, ambiguities=ambiguities, lines=lines)


def audit(ctx, worker, diff, validation):
    spec, run = worker.spec, ctx.run
    prompt = review_prompt(spec.goal, spec.contract, ctx.decisions, spec.name, diff, validation, worker.declines)
    run.write_text(worker.pass_file("audit-prompt.md"), prompt)
    def auditing():
        with acting("judge", spec.name):
            return _ask(ctx.judge, AUDITOR_SYSTEM, prompt, worker.tree, worker.say, "auditor",
                        lambda: run.count("judge_calls"))
    reply = _guarded(worker.tree, worker.say, auditing)
    run.write_text(worker.pass_file("audit.md"), reply.text)
    head = f"   auditor {int(reply.seconds)}s (exit {reply.status})"
    if not reply.ok:
        return GateResult(False, failed=True, lines=[head], trouble=(ctx.judge.name, reply),
                          detail=f"the final reviewer could not run (exit {reply.status})")
    v = verdict(reply.text)
    ambiguities = open_questions(reply.text)
    if v == "APPROVED":
        return GateResult(True, ambiguities=ambiguities, lines=[head])
    if v is None:
        return GateResult(False, failed=True, lines=[head], detail="the final reviewer never gave a verdict")
    return GateResult(False, findings=findings_of(reply.text), ambiguities=ambiguities,
                      lines=[head, "   final reviewer asked for changes"])
