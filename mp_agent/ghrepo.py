"""Putting a finished project on GitHub, once you have decided you want it there.

mp-agent never creates a repository or pushes on its own: a job's work stays on a branch in
a folder on this machine until a person says otherwise. This is that step, made explicit —
it says what it is about to do, to which account, and refuses rather than guesses.

Two situations, and they are different:

    no remote yet     a project the agents started (mp-agent start --new). A repository is
                      created and the current branch pushed.
    a remote already  nothing is created; the branch is pushed to what is already there,
                      and `mp-agent pr` opens a pull request if that is what you want.

Private unless you ask otherwise, and never a repository that is protected here.
"""
import os
import re
import subprocess

from . import gitops, where

# Anything that looks like a credential in a tracked file stops the push: a private
# repository is still a copy of the secret somewhere it was not before.
SECRETS = [
    r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-(proj-)?[A-Za-z0-9]{32,}", r"sk-or-v1-[a-f0-9]{32,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{40,}", r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}", r"xox[baprs]-[A-Za-z0-9-]{10,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"apikey_[A-Za-z0-9]{20,}",
]
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
SKIP = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".lock")


def _run(cmd, cwd=None, timeout=300):
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def secrets_in(project, run=_run):
    """Tracked files that look like they carry a credential. Empty is the happy answer."""
    code, listing, _ = run(["git", "ls-files"], cwd=project)
    if code != 0:
        return []
    found = []
    for name in listing.split("\n"):
        if not name.strip() or name.endswith(SKIP):
            continue
        path = os.path.join(project, name)
        try:
            if os.path.getsize(path) > 2_000_000:
                continue
            with open(path, errors="replace") as fh:
                text = fh.read(400_000)
        except OSError:
            continue
        for pattern in SECRETS:
            if re.search(pattern, text):
                found.append(name)
                break
    return sorted(set(found))


def plan(project, state_dir=None, run=_run):
    """What putting this project on GitHub would do, and anything in the way."""
    project = os.path.realpath(os.path.expanduser(project))
    out = {"project": project, "name": os.path.basename(project), "remote": None, "branch": None,
           "stoppers": [], "warnings": [], "action": None}
    if not os.path.isdir(os.path.join(project, ".git")):
        out["stoppers"].append("this folder is not a git repository")
        return out
    code, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project)
    out["branch"] = branch.strip() or None
    code, remotes, _ = run(["git", "remote", "-v"], cwd=project)
    first = next((l.split() for l in remotes.splitlines() if l.strip()), None)
    if first and len(first) > 1:
        out["remote"] = first[1]
        out["action"] = "push"
        if where.is_protected(first[1], state_dir):
            out["stoppers"].append(f"{first[1]} is protected here, so mp-agent never pushes to it")
    else:
        out["action"] = "create"
    code, status, _ = run(["git", "status", "--porcelain"], cwd=project)
    untracked = [l[3:] for l in status.splitlines() if l.startswith("??")]
    if untracked:
        out["warnings"].append(f"{len(untracked)} untracked file(s) will not be included "
                               f"(for example {untracked[0]})")
    leaks = secrets_in(project, run)
    if leaks:
        out["stoppers"].append("these tracked files look like they carry a credential: " + ", ".join(leaks[:5]))
    if not out["branch"]:
        out["stoppers"].append("this repository has no branch checked out")
    return out


def create(project, name, owner=None, private=True, run=_run):
    """Make the repository and push the current branch. Returns (url, problem)."""
    if not NAME_RE.match(name or ""):
        return None, "a repository name is letters, digits, dots, dashes and underscores"
    full = f"{owner}/{name}" if owner else name
    code, out, err = run(["gh", "repo", "create", full, "--private" if private else "--public",
                          "--source", ".", "--push"], cwd=project, timeout=600)
    if code != 0:
        return None, (err or out).strip()[-300:] or "gh could not create it"
    url = next((w for w in (out + err).split() if w.startswith("https://github.com/")), None)
    return url or f"https://github.com/{full}", None


def push(project, run=_run):
    """Push the current branch to the remote that is already there. Returns (detail, problem)."""
    code, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project)
    branch = branch.strip()
    code, out, err = run(["git", "push", "-u", "origin", branch], cwd=project, timeout=600)
    if code != 0:
        return None, (err or out).strip()[-300:]
    return f"pushed {branch} to origin", None
