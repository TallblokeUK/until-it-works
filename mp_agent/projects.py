"""The projects the workshop knows about, from the runs that have happened in them.

For each project: how many runs, the newest, whether one is running or waiting for
an answer, and how many approved results still wait for you to KEEP or DISCARD.
The workshop's project picker filters the run tabs, HISTORY and QUEUE with this.
"""
import json
import os


def _read(path, default=""):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def project_of(run_dir):
    """The project a run worked in, as a real path, or None."""
    repo = _read(os.path.join(run_dir, "repo")).strip()
    if not repo:
        try:
            repo = json.loads(_read(os.path.join(run_dir, "metadata.json"), "{}")).get("project") or ""
        except ValueError:
            repo = ""
    return os.path.realpath(repo) if repo else None


def same(a, b):
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


def list_projects(runs_dir, alive):
    """Every project with at least one run, most recently active first."""
    found = {}
    try:
        names = os.listdir(runs_dir)
    except OSError:
        return []
    for name in names:
        run = os.path.join(runs_dir, name)
        if not os.path.isdir(run):
            continue
        path = project_of(run)
        if not path:
            continue
        entry = found.setdefault(path, {"path": path, "name": os.path.basename(path) or path, "runs": 0, "last": 0.0,
                                        "live": False, "needs_you": False, "to_decide": 0, "exists": os.path.isdir(path)})
        entry["runs"] += 1
        entry["last"] = max(entry["last"], os.path.getmtime(run))
        is_live = alive(run)
        entry["live"] = entry["live"] or is_live
        entry["needs_you"] = entry["needs_you"] or (is_live and os.path.exists(os.path.join(run, "question.json")))
        if not is_live:
            try:
                meta = json.loads(_read(os.path.join(run, "metadata.json"), "{}"))
            except ValueError:
                meta = {}
            if meta.get("approved") and not meta.get("resolution") and not meta.get("oneoff") \
                    and not meta.get("resumed_as"):
                entry["to_decide"] += 1
    # two folders with the same name get their parent folder added, so the picker can tell them apart
    by_name = {}
    for entry in found.values():
        by_name.setdefault(entry["name"], []).append(entry)
    for entries in by_name.values():
        if len(entries) > 1:
            for entry in entries:
                entry["name"] = os.path.join(os.path.basename(os.path.dirname(entry["path"])), entry["name"])
    # a folder that is gone (a selftest's, or a project you deleted) is not something to switch to;
    # its runs still show under all projects
    return sorted((e for e in found.values() if e["exists"]), key=lambda e: -e["last"])
