"""Where a job can happen: your local projects, your GitHub repos, a new project
or a one-off. Used by `mp-agent where`, the guided /agents flow in Cline and the
workshop's NEW JOB form.

Nothing here pushes anything anywhere. Getting a GitHub repo means cloning it,
or, when a clean local clone already exists, fast-forward pulling the latest.
"""
import json
import os
import re
import subprocess
import time

from . import gitops

SKIP_DIRS = {"node_modules", "vendor", ".venv", "venv", "worktrees"}


def protected(state_dir=None):
    """GitHub accounts ("owner") or repositories ("owner/repo") you have listed under
    "protected" in your own ~/.mp-agent/config.json: flagged wherever they are shown,
    and never pushed to or opened as a pull request."""
    state_dir = state_dir or os.environ.get("MP_HOME") or os.path.join(os.path.expanduser("~"), ".mp-agent")
    try:
        with open(os.path.join(state_dir, "config.json")) as fh:
            entries = json.load(fh).get("protected") or []
    except (OSError, ValueError, AttributeError):
        entries = []
    return [str(e).strip().strip("/").lower() for e in entries if str(e).strip().strip("/")]


PROTECTED_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$")


def set_protected(state_dir, entries):
    """Save the protected list (owner or owner/repo), keeping the rest of the config."""
    state_dir = state_dir or os.environ.get("MP_HOME") or os.path.join(os.path.expanduser("~"), ".mp-agent")
    path = os.path.join(state_dir, "config.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data["protected"] = sorted(dict.fromkeys(e.strip().strip("/").lower() for e in entries if e.strip().strip("/")))
    os.makedirs(state_dir, exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(path + ".tmp", path)
    return data["protected"]


def is_protected(remote, state_dir=None):
    name = (github_name(remote) or "").lower()
    return bool(name) and any(name == p or name.startswith(p + "/") for p in protected(state_dir))


def _git(path, *args):
    proc = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def local_repos(home, limit=15, depth=3, state_dir=None):
    """Git projects under the home folder, most recently worked on first."""
    found = []
    home = os.path.realpath(home)
    for root, dirs, _ in os.walk(home):
        rel_depth = root[len(home):].count(os.sep)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (root == home and d.startswith("."))]
        if ".git" in dirs:
            dirs.remove(".git")
            found.append(root)
            dirs[:] = []            # don't look for repos inside repos
        if rel_depth >= depth:
            dirs[:] = []
    repos = []
    for path in found:
        last = _git(path, "log", "-1", "--format=%ct")
        index = os.path.join(path, ".git", "index")
        touched = max(int(last or 0), int(os.path.getmtime(index)) if os.path.exists(index) else 0)
        remote = _git(path, "remote", "get-url", "origin")
        repos.append({
            "path": path, "name": os.path.relpath(path, home), "branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": gitops.is_dirty(path) if last else False, "touched": touched, "remote": remote,
            "github": github_name(remote), "protected": is_protected(remote, state_dir),
        })
    repos.sort(key=lambda r: r["touched"], reverse=True)
    return repos[:limit]


def github_name(remote):
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", remote or "")
    return m.group(1) if m else None


def github_repos(state_dir, limit=40, refresh=False):
    """Your repos and your organisations' repos, most recently pushed first (cached for 30 minutes)."""
    cache = os.path.join(state_dir, "github-repos.json")
    try:
        with open(cache) as fh:
            data = json.load(fh)
        if not refresh and time.time() - data.get("at", 0) < 1800:
            return data["repos"][:limit]
    except (OSError, ValueError, KeyError):
        pass
    def gh(*args):
        try:
            proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
            return proc.stdout if proc.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    owners = [o for o in (gh("api", "user", "-q", ".login").strip(),) if o]
    owners += [o for o in gh("api", "user/orgs", "-q", ".[].login").split() if o]
    repos = []
    for owner in owners:
        try:
            listed = json.loads(gh("repo", "list", owner, "--limit", "40", "--json",
                                   "nameWithOwner,pushedAt,isPrivate,description") or "[]")
        except ValueError:
            listed = []
        for r in listed:
            repos.append({"github": r["nameWithOwner"], "pushed": r.get("pushedAt") or "",
                          "private": r.get("isPrivate"), "description": (r.get("description") or "")[:80]})
    repos.sort(key=lambda r: r["pushed"], reverse=True)
    if repos:
        os.makedirs(state_dir, exist_ok=True)
        with open(cache + ".tmp", "w") as fh:
            json.dump({"at": time.time(), "repos": repos}, fh)
        os.replace(cache + ".tmp", cache)
    return repos[:limit]


def find_clone(name_with_owner, home):
    """A local clone of a GitHub repo, if there is one."""
    wanted = name_with_owner.lower()
    for repo in local_repos(home, limit=500):
        if (repo["github"] or "").lower() == wanted:
            return repo
    return None


def prepare_github(name_with_owner, home, say=print):
    """Get the latest of a GitHub repo locally and return its folder.
    Existing clean clone: fast-forward pull. No clone: clone into the home folder."""
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", name_with_owner or ""):
        raise gitops.SetupError(f"'{name_with_owner}' is not a GitHub repo name like owner/repo")
    existing = find_clone(name_with_owner, home)
    if existing:
        path = existing["path"]
        if gitops.is_dirty(path):
            raise gitops.SetupError(f"your copy of {name_with_owner} at {path} has uncommitted changes, so the latest "
                                    f"was not pulled. Commit or stash them first.")
        proc = subprocess.run(["git", "pull", "--ff-only", "-q"], cwd=path, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise gitops.SetupError(f"could not pull the latest {name_with_owner} into {path} (it may have local "
                                    f"commits that differ): {(proc.stderr or proc.stdout).strip()[-200:]}")
        say(f"pulled the latest {name_with_owner} into {path}")
        return path
    base = os.path.join(home, name_with_owner.split("/")[1])
    target, n = base, 2
    while os.path.exists(target):
        target = f"{base}-{name_with_owner.split('/')[0].lower()}" if n == 2 else f"{base}-{n}"
        n += 1
    proc = subprocess.run(["gh", "repo", "clone", name_with_owner, target, "--", "-q"], capture_output=True, text=True,
                          timeout=600)
    if proc.returncode != 0:
        raise gitops.SetupError(f"could not clone {name_with_owner}: {(proc.stderr or proc.stdout).strip()[-200:]}")
    say(f"cloned {name_with_owner} into {target}")
    return target
