"""Asking a question about a run, and getting an answer back.

Steering is one-way: you say something and the builders read it next pass. This is the
other direction — you ask, and something that has read the whole run answers you. "Why did
it drop that requirement?", "what is left?", "what did the judge object to?".

It never touches the work. A separate read-only agent is given the run's own papers (the
task, the contract, the decisions, what the reviewers said, the diff so far) and answers in
plain words. The job carries on beside it, unaware; nothing it says becomes an instruction.
To change what the job is doing, say it — that is what steering is for.
"""
import glob
import json
import os

SYSTEM = """You answer questions about a coding job that is running (or has finished). You are given its
papers: what was asked for, the contract it is being judged against, what has been decided, what the
reviewers said, and the change so far.

Answer the question and nothing else. Be specific and quote the papers — a contract line, a reviewer's
words, a file and line. If the papers do not say, say that plainly rather than guessing; "the log does not
say why" is a good answer when it is the true one.

You are not running the job and cannot change it. Do not write code, do not propose a patch unless you are
asked for one, and never say you have done something. Keep it to a few sentences unless the question needs
more."""

PAPERS = 14_000


def _read(path, limit=6000):
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    return text if len(text) <= limit else text[-limit:]


def _latest(run_dir, name):
    """The newest file of that name across the run's units and passes."""
    found = sorted(glob.glob(os.path.join(run_dir, "*", "pass-*", name)))
    return _read(found[-1]) if found else ""


def papers(run_dir):
    """Everything the answerer is given about the run, as one document."""
    parts = []
    task = _read(os.path.join(run_dir, "task.md"), 4000)
    if task:
        parts.append(f"# What was asked for\n\n{task}")
    try:
        with open(os.path.join(run_dir, "plan.json")) as fh:
            plan = json.load(fh)
        contract = plan.get("contract") or {}
        lines = [f"summary: {plan.get('summary', '')}", "", "done means:"]
        lines += [f"  C{i}: {item}" for i, item in enumerate(contract.get("done") or [], 1)]
        lines += ["", "out of scope:"] + [f"  O{i}: {item}" for i, item in enumerate(contract.get("out_of_scope") or [], 1)]
        lines += ["", f"checked by: {plan.get('check', '')}", f"built as: {plan.get('mode', '')}"]
        parts.append("# The plan and the contract\n\n" + "\n".join(lines))
    except (OSError, ValueError):
        pass
    decisions = _read(os.path.join(run_dir, "decisions.json"), 4000)
    if decisions.strip() not in ("", "[]"):
        parts.append(f"# Decisions made during the run\n\n{decisions}")
    for name, heading in (("review.md", "What the quick reviewer last said"),
                          ("panel-edges.md", "The panel's edges lens"),
                          ("panel-truth.md", "The panel's truth lens"),
                          ("panel-look.md", "The panel's design lens"),
                          ("audit.md", "What the final judge last said")):
        text = _latest(run_dir, name)
        if text.strip():
            parts.append(f"# {heading}\n\n{text[-4000:]}")
    diff = _latest(run_dir, "diff.patch")
    if diff.strip():
        parts.append(f"# The change so far\n\n```diff\n{diff[-6000:]}\n```")
    log = _read(os.path.join(run_dir, "run.log"), 6000)
    if log:
        parts.append(f"# The run's log (the end of it)\n\n```\n{log}\n```")
    text = "\n\n".join(parts)
    return text if len(text) <= PAPERS else text[-PAPERS:]


def who_answers(run_dir, fallback="claude:sonnet"):
    """The model that answers: the run's own planner, which read the project already."""
    try:
        with open(os.path.join(run_dir, "roles.json")) as fh:
            roles = json.load(fh)
        return roles.get("planner") or roles.get("judge") or fallback
    except (OSError, ValueError):
        return fallback


def ask(run_dir, question, agent, cwd=None):
    """The answer, as text. Returns (answer, problem)."""
    question = (question or "").strip()
    if not question:
        return None, "ask something"
    prompt = f"{papers(run_dir)}\n\n# The question\n\n{question}"
    reply = agent.ask(SYSTEM, prompt, cwd or os.getcwd())
    if not reply.ok:
        return None, f"the model could not run (exit {reply.status})"
    return (reply.text or "").strip(), None
