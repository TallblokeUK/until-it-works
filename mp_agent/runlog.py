"""The run folder: the only interface between a run and anyone watching it.

mp-status, mp-viz and `mp-agent answer` all read or write these files, so the
line formats in run.log are part of the interface, not decoration.

    ~/.mp-agent/runs/<stamp>-<slug>/
        task.md, repo, pid, phase, run.log
        plan.json, contract.json, decisions.json, units.json
        question.json / answer.json   while waiting for the person
        metadata.json                 when finished
        <unit>/pass-NN/...            prompts, outputs, checks, diffs
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

from .gitops import slugify


class Run:
    def __init__(self, root, task, stamp=None, echo=True):
        self.stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = slugify(task)
        self.slug, n = base, 2
        # Two runs of the same task in the same second must not share a folder,
        # branch or worktree.
        while True:
            self.dir = os.path.join(root, f"{self.stamp}-{self.slug}")
            try:
                os.makedirs(self.dir)
                break
            except FileExistsError:
                self.slug = f"{base}-{n}"
                n += 1
        self.echo = echo
        self._lock = threading.Lock()
        self.counters = {}
        self.started = time.time()
        self.waiting_seconds = 0.0
        self.write_text("task.md", task + "\n")
        self.write_text("pid", str(os.getpid()))

    def path(self, *parts):
        p = os.path.join(self.dir, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def write_text(self, name, text):
        with open(self.path(name), "w") as fh:
            fh.write(text)

    def read_text(self, name, default=""):
        try:
            with open(os.path.join(self.dir, name)) as fh:
                return fh.read()
        except OSError:
            return default

    def write_json(self, name, obj):
        tmp = self.path(name + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(obj, fh, indent=2)
        os.replace(tmp, self.path(name))

    def say(self, message):
        with self._lock:
            with open(self.path("run.log"), "a") as fh:
                fh.write(message + "\n")
            if self.echo:
                print(message, flush=True)

    def activity(self, event):
        """A tool an agent just used (docs lookup, browser, files), for the workshop."""
        event = {**event, "ts": round(time.time(), 2)}
        with self._lock:
            with open(self.path("activity.jsonl"), "a") as fh:
                fh.write(json.dumps(event) + "\n")

    def phase(self, text):
        self.write_text("phase", text + "\n")

    def count(self, key, n=1):
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    def active_seconds(self):
        return time.time() - self.started - self.waiting_seconds

    def finish(self, metadata):
        metadata = {**metadata, "run": self.dir, "counters": self.counters,
                    "seconds": int(time.time() - self.started),
                    "waiting_seconds": int(self.waiting_seconds)}
        self.write_json("metadata.json", metadata)
        self.phase("finished")
        try:
            os.remove(os.path.join(self.dir, "pid"))
        except OSError:
            pass
