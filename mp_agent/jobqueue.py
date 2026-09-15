"""Jobs waiting their turn.

Queue several jobs (say, before you stop for the day) and they run one after
another; the provider pacing keeps them inside the rate limits. The workshop
server works through the queue whenever nothing is running, and stays up while
anything is still queued.

    ~/.mp-agent/queue.json   {"jobs": [...waiting...], "history": [...started or failed...]}
"""
import fcntl
import json
import os
import time
import uuid


def _path(state_dir):
    return os.path.join(state_dir, "queue.json")


class _Locked:
    def __init__(self, state_dir):
        self.state_dir = state_dir

    def __enter__(self):
        os.makedirs(self.state_dir, exist_ok=True)
        self.fh = open(_path(self.state_dir) + ".lock", "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        try:
            with open(_path(self.state_dir)) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("jobs", [])
        self.data.setdefault("history", [])
        return self.data

    def __exit__(self, *exc):
        if exc[0] is None:
            self.data["history"] = self.data["history"][-50:]
            tmp = _path(self.state_dir) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, _path(self.state_dir))
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def add(state_dir, task, args, label=""):
    job = {"id": uuid.uuid4().hex[:8], "task": task, "args": list(args), "label": label or task[:80],
           "added": time.time()}
    with _Locked(state_dir) as data:
        data["jobs"].append(job)
    return job


def listing(state_dir):
    with _Locked(state_dir) as data:
        return {"jobs": list(data["jobs"]), "history": list(data["history"])}


def remove(state_dir, job_id):
    with _Locked(state_dir) as data:
        before = len(data["jobs"])
        data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
        return len(data["jobs"]) < before


def take_next(state_dir):
    """Remove and return the first waiting job, or None."""
    with _Locked(state_dir) as data:
        if not data["jobs"]:
            return None
        return data["jobs"].pop(0)


def record(state_dir, job, outcome, run_dir=None):
    with _Locked(state_dir) as data:
        data["history"].append({**job, "started": time.time(), "outcome": outcome, "run": run_dir})
