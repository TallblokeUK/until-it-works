"""Housekeeping: what `mp-agent tidy` and the workshop's TIDY button clear up.

It only ever touches mp-agent's own leftovers. Your projects and their
branches are never changed here; those change only through Keep, PR or
Discard. Old runs are archived (moved), never deleted.

Every item is planned first (nothing changes), then applied only if chosen.
"""
import glob
import json
import os
import shutil
import subprocess
import time
from datetime import datetime

from . import gitops

SHOT_AGE = 3600          # screenshots older than this were never collected by a run


def _alive(run_dir):
    try:
        with open(os.path.join(run_dir, "pid")) as fh:
            os.kill(int(fh.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def _project_of(run_dir):
    for name in ("repo",):
        try:
            with open(os.path.join(run_dir, name)) as fh:
                return fh.read().strip()
        except OSError:
            pass
    return None


def plan(state_dir):
    runs_dir = os.path.join(state_dir, "runs")
    trees_dir = os.path.join(state_dir, "worktrees")
    live = {os.path.basename(r) for r in glob.glob(os.path.join(runs_dir, "*")) if _alive(r)}
    items = []

    worktrees = [p for p in sorted(glob.glob(os.path.join(trees_dir, "*")))
                 if os.path.isdir(p) and not any(os.path.basename(p) == r or os.path.basename(p).startswith(r + "-")
                                                 for r in live)]
    items.append({"id": "worktrees", "label": "leftover worktrees from stopped runs (their branches are kept)",
                  "count": len(worktrees), "details": [os.path.basename(p) for p in worktrees]})

    now = time.time()
    shots = [p for p in glob.glob(os.path.join(state_dir, "shots", "*"))
             if os.path.isfile(p) and now - os.path.getmtime(p) > SHOT_AGE]
    items.append({"id": "shots", "label": "stray screenshots no run collected", "count": len(shots),
                  "details": [os.path.basename(p) for p in shots][:20]})

    try:
        with open(os.path.join(state_dir, "queue.json")) as fh:
            history = json.load(fh).get("history") or []
    except (OSError, ValueError):
        history = []
    items.append({"id": "queue_history", "label": "the queue's record of jobs it already started",
                  "count": len(history), "details": [h.get("label", "")[:60] for h in history][-10:]})

    log = os.path.join(state_dir, "last-start.log")
    size = os.path.getsize(log) if os.path.exists(log) else 0
    items.append({"id": "start_log", "label": "the launch log", "count": 1 if size else 0,
                  "details": [f"{size // 1024} KB"] if size else []})

    memories = []
    for path in glob.glob(os.path.join(state_dir, "projects", "*", "memory.md")):
        try:
            with open(path) as fh:
                first = fh.readline()
        except OSError:
            continue
        project = first.split(" for ", 1)[1].strip() if " for " in first else ""
        if project and not os.path.isdir(project):
            memories.append({"id": os.path.basename(os.path.dirname(path)), "project": project})
    items.append({"id": "memories", "label": "memories for projects that no longer exist", "count": len(memories),
                  "details": [m["project"] for m in memories]})

    gone = []
    for run in sorted(glob.glob(os.path.join(runs_dir, "*"))):
        if not os.path.isdir(run) or os.path.basename(run) in live:
            continue
        project = _project_of(run)
        if project is None or not os.path.isdir(project):
            gone.append(os.path.basename(run))
    items.append({"id": "orphan_runs", "label": "runs whose project no longer exists (archived, not deleted)",
                  "count": len(gone), "details": gone[-20:]})

    finished = [os.path.basename(r) for r in sorted(glob.glob(os.path.join(runs_dir, "*")))
                if os.path.isdir(r) and os.path.basename(r) not in live and os.path.basename(r) not in gone]
    items.append({"id": "all_runs", "label": "every other finished run too, for a fresh HISTORY (archived, not deleted)",
                  "count": len(finished), "details": finished[-20:], "optional": True})
    return {"items": items, "live": sorted(live)}


def apply(state_dir, chosen, say=lambda m: None):
    """Do the chosen items. Returns {item id: what was done}."""
    current = {i["id"]: i for i in plan(state_dir)["items"]}
    runs_dir = os.path.join(state_dir, "runs")
    done = {}
    archive = os.path.join(state_dir, f"runs-archive-{datetime.now().strftime('%Y-%m-%d')}")

    def archive_runs(names):
        os.makedirs(archive, exist_ok=True)
        moved = 0
        for name in names:
            source = os.path.join(runs_dir, name)
            if os.path.isdir(source) and not _alive(source):
                shutil.move(source, os.path.join(archive, name))
                moved += 1
        return moved

    for item_id in chosen:
        item = current.get(item_id)
        if not item or not item["count"]:
            continue
        say(item_id)
        if item_id == "worktrees":
            for name in item["details"]:
                path = os.path.join(state_dir, "worktrees", name)
                repo = None
                try:
                    with open(os.path.join(path, ".git")) as fh:
                        repo = fh.read().split("gitdir:", 1)[1].strip().split("/.git/worktrees/")[0]
                except (OSError, IndexError):
                    pass
                if repo and os.path.isdir(repo):
                    gitops.worktree_remove(repo, path)
                    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
                shutil.rmtree(path, ignore_errors=True)
            done[item_id] = f"removed {item['count']} worktree(s)"
        elif item_id == "shots":
            now = time.time()
            removed = 0
            for path in glob.glob(os.path.join(state_dir, "shots", "*")):
                if os.path.isfile(path) and now - os.path.getmtime(path) > SHOT_AGE:
                    os.remove(path)
                    removed += 1
            done[item_id] = f"deleted {removed} screenshot(s)"
        elif item_id == "queue_history":
            path = os.path.join(state_dir, "queue.json")
            with open(path) as fh:
                data = json.load(fh)
            data["history"] = []
            with open(path, "w") as fh:
                json.dump(data, fh, indent=2)
            done[item_id] = "cleared"
        elif item_id == "start_log":
            open(os.path.join(state_dir, "last-start.log"), "w").close()
            done[item_id] = "emptied"
        elif item_id == "memories":
            removed = 0
            for path in glob.glob(os.path.join(state_dir, "projects", "*", "memory.md")):
                with open(path) as fh:
                    first = fh.readline()
                project = first.split(" for ", 1)[1].strip() if " for " in first else ""
                if project and not os.path.isdir(project):
                    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
                    removed += 1
            done[item_id] = f"removed {removed} memory file(s)"
        elif item_id == "orphan_runs":
            gone = [n for n in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, n))
                    and not os.path.isdir(_project_of(os.path.join(runs_dir, n)) or "")]
            done[item_id] = f"archived {archive_runs(gone)} run(s) to {archive}"
        elif item_id == "all_runs":
            names = [n for n in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, n))]
            done[item_id] = f"archived {archive_runs(names)} run(s) to {archive}"
    return done
