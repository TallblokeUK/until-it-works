"""What a local folder is connected to: its git remotes, the GitHub repositories they
point at, your permission on each, and plain-English warnings when a push from here
would change someone else's repository.

Also the folder browser behind OPEN A FOLDER, and which GitHub account and
organisations `gh` is signed in with. Nothing here fetches, pushes or changes anything.
"""
import json
import os
import re
import subprocess
import time

from . import where

_CACHE = {}
CACHE_SECONDS = 300


def _run(cmd, cwd=None, timeout=15):
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _cached(key, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    _CACHE[key] = (time.time(), value)
    return value


def github_account(run=_run):
    """{"login", "orgs"} for the account gh is signed in with, or {"login": None, "problem"}."""
    def look():
        code, out, err = run(["gh", "auth", "status", "--json", "hosts"])
        if code != 0:
            return {"login": None, "orgs": [], "problem": "gh is not installed or not signed in (run: gh auth login)"}
        try:
            accounts = json.loads(out).get("hosts", {}).get("github.com") or []
        except ValueError:
            accounts = []
        active = next((a for a in accounts if a.get("active")), accounts[0] if accounts else {})
        code, out, _ = run(["gh", "api", "user/orgs", "--jq", ".[].login"])
        orgs = [o for o in out.split() if o] if code == 0 else []
        return {"login": active.get("login"), "orgs": orgs, "accounts": [a.get("login") for a in accounts]}
    return _cached("account", look)


def github_repo(name, run=_run):
    def look():
        code, out, err = run(["gh", "repo", "view", name, "--json",
                              "nameWithOwner,isPrivate,isFork,parent,viewerPermission,defaultBranchRef,pushedAt,owner,url"])
        if code != 0:
            return {"problem": "GitHub did not answer for this repository (no access, or it no longer exists)"}
        try:
            data = json.loads(out)
        except ValueError:
            return {"problem": "GitHub's answer could not be read"}
        parent = data.get("parent") or {}
        return {"private": data.get("isPrivate"), "fork": data.get("isFork"),
                "parent": (f"{(parent.get('owner') or {}).get('login')}/{parent.get('name')}" if parent else None),
                "permission": (data.get("viewerPermission") or "").lower() or None,
                "default_branch": (data.get("defaultBranchRef") or {}).get("name"),
                "pushed_at": data.get("pushedAt"), "url": data.get("url")}
    return _cached("repo:" + name.lower(), look)


def inspect(path, state_dir=None, run=_run, use_github=True):
    """Everything the REPO panel shows for one folder."""
    path = os.path.realpath(os.path.expanduser(path))
    out = {"path": path, "exists": os.path.isdir(path), "is_git": False, "remotes": [], "warnings": []}
    if not out["exists"]:
        out["warnings"].append("This folder does not exist.")
        return out
    code, top, _ = run(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if code != 0:
        out["warnings"].append("This folder is not a git repository yet. A job can still run here: it sets up git "
                               "first (NEW JOB asks before doing that).")
        return out
    out.update(is_git=True, top=top.strip())
    _, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    out["branch"] = branch.strip() or None
    _, status, _ = run(["git", "status", "--porcelain"], cwd=path)
    out["uncommitted"] = len([l for l in status.splitlines() if l.strip()])
    code, counts, _ = run(["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=path)
    if code == 0 and len(counts.split()) == 2:
        ahead, behind = (int(n) for n in counts.split())
        _, tracking, _ = run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=path)
        out["tracking"] = {"branch": tracking.strip(), "ahead": ahead, "behind": behind}
    account = github_account(run) if use_github else {"login": None, "orgs": []}
    out["account"] = account
    mine = {n.lower() for n in [account.get("login") or "", *account.get("orgs", [])] if n}
    _, remotes, _ = run(["git", "remote", "-v"], cwd=path)
    seen = set()
    for line in remotes.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] in seen:
            continue
        seen.add(parts[0])
        name, url = parts[0], parts[1]
        github = where.github_name(url)
        entry = {"name": name, "url": _without_credentials(url), "github": github}
        if github:
            owner = github.split("/")[0]
            entry["owner"] = owner
            entry["yours"] = owner.lower() in mine if mine else None
            entry["protected"] = where.is_protected(url, state_dir)
            if use_github:
                entry["info"] = github_repo(github, run)
            permission = (entry.get("info") or {}).get("permission")
            if entry["yours"] is False:
                if entry["protected"]:
                    entry["note"] = (f"{github} belongs to {owner}. It is protected here, so mp-agent never pushes to "
                                     "it or opens a pull request"
                                     + (f"; but you can push to it ({permission}), so a git push you run yourself "
                                        "from this folder would still change their repository."
                                        if permission in ("admin", "maintain", "write") else "."))
                elif permission in ("admin", "maintain", "write"):
                    warning = (f"'{name}' points at {github}, which belongs to {owner}, not you or one of your "
                               f"organisations, and you can push to it ({permission}). A push from this folder would "
                               f"change their repository.")
                    entry["warning"] = warning
                    out["warnings"].append(warning)
                elif permission:
                    entry["note"] = f"{github} belongs to {owner}; you can only read it, so pushes from here will fail."
            if (entry.get("info") or {}).get("fork") and entry["info"].get("parent"):
                entry["note"] = (entry.get("note", "") + f" A fork of {entry['info']['parent']}.").strip()
        out["remotes"].append(entry)
    if not out["remotes"]:
        out["warnings"].append("No remotes: this project is only on this computer (nothing can be pushed or opened as "
                               "a pull request until you add one).")
    if out["uncommitted"]:
        out["warnings"].append(f"{out['uncommitted']} uncommitted change(s): jobs only start from a clean state, so "
                               "commit them first.")
    return out


def _without_credentials(url):
    """https://user:token@github.com/... must never reach the page."""
    return re.sub(r"(https?://)[^/@]+@", r"\1", url)


def browse(path, home, show_hidden=False):
    """The folders inside path (which must be inside your home folder), for OPEN A FOLDER."""
    home = os.path.realpath(home)
    path = os.path.realpath(os.path.expanduser(path or home))
    if path != home and not path.startswith(home + os.sep):
        path = home
    folders = []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError:
        names = []
    for name in names:
        full = os.path.join(path, name)
        if (name.startswith(".") and not show_hidden) or not os.path.isdir(full) or name in ("node_modules", "__pycache__"):
            continue
        folders.append({"name": name, "path": full, "git": os.path.exists(os.path.join(full, ".git"))})
    return {"path": path, "home": home, "parent": None if path == home else os.path.dirname(path),
            "git": os.path.exists(os.path.join(path, ".git")), "folders": folders}
