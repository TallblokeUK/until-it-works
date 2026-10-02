"""Your own gates, run at the points where the loop decides something.

The loop's own judges are models. A hook is a program: it sees the same moment and can
refuse. That makes it the place for the rules a model should not be asked to weigh —
"nothing is approved while the formatter is unhappy", "no new dependency without a line in
DEPENDENCIES.md", "never mark this unit done on a Friday".

A hook is an executable file named after the moment it runs at:

    <project>/.mp-agent/hooks/<event>      this project's own gates, in your checkout
    ~/.mp-agent/hooks/<event>              yours, for every project

They are read from your checkout and never from the worktree the agents work in, so a job
cannot install or edit the gate that judges it. They run with the worktree as their working
directory, so they see the work in progress; where it is also arrives in the JSON.

The events:

    plan        the plan is written, before anything is built
    unit-done   a unit is about to be called approved
    job-done    the whole job is about to be called approved

It is given the moment as JSON on stdin and answers with its exit code:

    0           fine, carry on
    2           no: whatever it printed goes back as feedback, and the work continues
    anything    the hook itself is broken; it is logged and ignored, because a hook that
                cannot run must not be able to stop a job

Nothing here asks a model anything. A hook that needs judgement should ask for it itself.
"""
import json
import os
import subprocess

EVENTS = ("plan", "unit-done", "job-done")
TIMEOUT = 120


def find(event, project, state_dir):
    """The hooks for this moment, project first: [(path, where)]."""
    if event not in EVENTS:
        return []
    found = []
    for base, where in ((os.path.join(project or "", ".mp-agent", "hooks"), "the project"),
                        (os.path.join(state_dir or "", "hooks"), "yours")):
        path = os.path.join(base, event)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            found.append((path, where))
    return found


def run(event, moment, project, state_dir, cwd=None, say=print, timeout=TIMEOUT):
    """Run the hooks for this moment. project: where the gates live (your checkout).
    cwd: where they run (the worktree). Returns "" to carry on, or why it was refused."""
    for path, where in find(event, project, state_dir):
        name = os.path.basename(path)
        try:
            proc = subprocess.run([path], input=json.dumps(moment, default=str), capture_output=True,
                                  text=True, timeout=timeout, cwd=cwd or project or None,
                                  env={**os.environ, "MP_EVENT": event})
        except subprocess.TimeoutExpired:
            say(f"   the {where} {name} hook took longer than {timeout}s; carrying on without it")
            continue
        except OSError as exc:
            say(f"   the {where} {name} hook could not run ({exc}); carrying on without it")
            continue
        if proc.returncode == 0:
            continue
        if proc.returncode == 2:
            said = (proc.stdout + "\n" + proc.stderr).strip() or "the hook refused it and said nothing"
            say(f"   the {where} {name} hook says no: {said.splitlines()[0][:160]}")
            return said
        # any other code is the hook being broken, which must not be able to stop a job
        say(f"   the {where} {name} hook failed (exit {proc.returncode}); carrying on without it")
    return ""
