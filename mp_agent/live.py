"""What somebody watching a job needs to know, small enough to redraw every second.

The workshop reads the run folder itself and builds a large payload for a web page: the
whole log, every recent tool call, the plan. A status line in a terminal wants none of
that, and a watcher outside this process should not have to know the folder's layout to
find out whether a job is waiting for an answer.

So: one snapshot, from one place. `mp-agent status --json` prints it, which is what the
Claude Code mod reads, and anything else that wants to watch a job can read the same
thing without growing its own copy of the rules for which run is the live one.
"""
import json
import os
import time

HOME = os.path.expanduser("~")
RUNS = os.environ.get("MP_RUNS", os.path.join(HOME, ".mp-agent", "runs"))


def alive(run):
    """Whether the job in this folder is still running. The pid file is written by it."""
    try:
        with open(os.path.join(run, "pid")) as fh:
            os.kill(int(fh.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def _read(path, limit=None):
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
        return text if limit is None else text[-limit:]
    except OSError:
        return ""


def _json(run, name):
    try:
        return json.loads(_read(os.path.join(run, name)) or "null")
    except ValueError:
        return None


def runs():
    """Every run folder, newest first."""
    try:
        found = sorted((os.path.join(RUNS, d) for d in os.listdir(RUNS)), key=os.path.getmtime, reverse=True)
    except OSError:
        return []
    return [r for r in found if os.path.isdir(r)]


def pick(wanted=None):
    """The run to show: the one asked for, else the live one, else the most recent."""
    if wanted:
        chosen = os.path.join(RUNS, os.path.basename(wanted))
        if os.path.isdir(chosen):
            return chosen
    found = runs()
    return next((r for r in found if alive(r)), None) or (found[0] if found else None)


def snapshot(wanted=None):
    """The small picture of one run. Always a dict: {"run": None} when there is nothing."""
    run = pick(wanted)
    if not run:
        return {"run": None, "live": False, "units": [], "question": None}
    live = alive(run)
    meta = _json(run, "metadata.json") or {}
    units = _json(run, "units.json") or {}
    usage = _json(run, "usage.json") or {}
    task = os.path.join(run, "task.md")
    started = os.path.getmtime(task) if os.path.exists(task) else time.time()
    return {
        "run": os.path.basename(run),
        "live": live,
        "task": _read(task).strip(),
        "phase": _read(os.path.join(run, "phase")).strip(),
        # A question is only a question while somebody can still answer it.
        "question": (_json(run, "question.json") if live else None) or None,
        "paused": os.path.exists(os.path.join(run, "pause")),
        "alone": os.path.exists(os.path.join(run, "alone")),
        "units": [{"name": name, "state": (u or {}).get("state") or "",
                   "phase": (u or {}).get("phase") or "", "passes": int((u or {}).get("passes") or 0),
                   "wave": (u or {}).get("wave")}
                  for name, u in units.items()],
        "elapsed": int((time.time() if live else os.path.getmtime(run)) - started),
        "calls": sum(int((m or {}).get("calls") or 0) for m in usage.values()),
        "usd": round(sum(float((m or {}).get("usd") or 0) for m in usage.values()), 4),
        "approved": meta.get("approved"),
        "outcome": meta.get("outcome") or "",
        "project": meta.get("project") or "",
        "waiting": [os.path.basename(r) for r in runs()
                    if alive(r) and os.path.exists(os.path.join(r, "question.json"))],
    }


def answering(wanted=None):
    """The run folder an answer belongs to, or "" when nothing is waiting."""
    if wanted:
        chosen = os.path.join(RUNS, os.path.basename(wanted))
        return chosen if os.path.isdir(chosen) else ""
    waiting = [r for r in runs() if alive(r) and os.path.exists(os.path.join(r, "question.json"))]
    return waiting[0] if len(waiting) == 1 else ""


def live_run(wanted=None):
    """The folder of the job that is running, or "" if none is."""
    if wanted:
        chosen = os.path.join(RUNS, os.path.basename(wanted))
        return chosen if os.path.isdir(chosen) and alive(chosen) else ""
    return next((r for r in runs() if alive(r)), "")
