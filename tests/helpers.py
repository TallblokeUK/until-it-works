import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mp_agent import gates, planner, worker  # noqa: E402
from mp_agent.context import Context, Options  # noqa: E402
from mp_agent.contract import Decisions  # noqa: E402
from mp_agent.providers import Agent, Reply  # noqa: E402
from mp_agent.runlog import Run  # noqa: E402


# Every temporary folder a test makes lives under one root that is deleted when the run ends.
# Without this the suite left thousands of folders behind in /tmp, and a day of running it
# used up the filesystem's inodes: writes failed everywhere with "no space left on device".
ROOT = tempfile.mkdtemp(prefix="mp-tests-")
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)


def tmpdir(prefix="mp-test-"):
    return tempfile.mkdtemp(prefix=prefix, dir=ROOT)


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo(files=None, check_ok=True):
    root = tmpdir("mp-test-repo-")
    sh(root, "git", "init", "-q", "-b", "main")
    for path, content in (files or {}).items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
    sh(root, "git", "add", "-A")
    sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    return root


def role(system):
    if system == worker.IMPLEMENTER_SYSTEM:
        return "implement"
    if system == gates.REVIEWER_SYSTEM:
        return "review"
    if system.startswith(gates.PANEL_SYSTEM):
        for name, lens in gates.LENSES.items():
            if system.endswith(lens):
                return f"panel:{name}"
    if system == gates.AUDITOR_SYSTEM:
        return "audit"
    if system == planner.PLAN_SYSTEM:
        return "plan"
    if system == planner.RULING_SYSTEM:
        return "ruling"
    if system == planner.REPLAN_SYSTEM:
        return "replan"
    if system == planner.QUESTION_SYSTEM:
        return "question"
    raise AssertionError("unknown system prompt: " + system[:80])


class Script(Agent):
    """A fake agent: handlers[role](prompt, cwd) -> text (or Reply). Records every call."""

    def __init__(self, name="fake", **handlers):
        self.name, self.pace_key = name, name
        self.handlers = handlers
        self.calls = []
        self.lock = threading.Lock()

    def ask(self, system, prompt, cwd):
        r = role(system)
        key = r.split(":")[0] if r not in self.handlers else r
        with self.lock:
            self.calls.append((r, prompt, cwd))
        handler = self.handlers.get(r) or self.handlers.get(key)
        if handler is None:
            raise AssertionError(f"{self.name} has no handler for {r}")
        out = handler(prompt, cwd)
        return out if isinstance(out, Reply) else Reply(out, 0, 0.01)

    def count(self, r):
        return sum(1 for c in self.calls if c[0] == r or c[0].startswith(r + ":"))


def approve(*_):
    return "Looks right.\nVERDICT: APPROVED"


def write(cwd, path, content):
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


def context(builder, judge=None, planner_agent=None, reviewer=None, panel=None, **opts):
    runs = tmpdir("mp-test-runs-")
    run = Run(runs, "test task", echo=False)
    options = Options(answer_poll=0.05, **opts)
    decisions = Decisions(save=lambda items: run.write_json("decisions.json", items))
    return Context(run, builder, judge, planner_agent, decisions, options, notifier=lambda *a: None,
                   reviewer=reviewer, panel=panel)


def log(ctx):
    return ctx.run.read_text("run.log")


def wait_for(path, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.02)
    return False
