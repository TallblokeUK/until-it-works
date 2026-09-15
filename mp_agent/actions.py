"""What a person does with a finished run: keep the work or throw it away.

Used by `mp-agent keep|discard` and by the workshop's buttons. Keeping merges
the run's branch into whatever branch the project has checked out; it refuses
if the project has uncommitted changes and backs out cleanly on a conflict, so
it can never leave the project half-merged.
"""
import glob
import json
import os
import re
import subprocess

from . import gitops


class ActionError(Exception):
    pass


def load(run_dir):
    path = os.path.join(run_dir, "metadata.json")
    if not os.path.exists(path):
        raise ActionError("that run has not finished yet")
    with open(path) as fh:
        meta = json.load(fh)
    if not meta.get("branch") or not meta.get("project"):
        raise ActionError("that run has no branch to act on")
    return meta


def _record(run_dir, meta, resolution, detail):
    meta["resolution"] = {"action": resolution, "detail": detail}
    tmp = os.path.join(run_dir, "metadata.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(meta, fh, indent=2)
    os.replace(tmp, os.path.join(run_dir, "metadata.json"))


def _branch_exists(project, branch):
    return bool(gitops.git(project, "branch", "--list", branch).strip())


def describe(run_dir):
    """What keeping would do, for a confirmation message."""
    meta = load(run_dir)
    project, branch = meta["project"], meta["branch"]
    target = gitops.git(project, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "the current branch"
    stat = gitops.git(project, "diff", "--shortstat", f"HEAD...{branch}", check=False).strip()
    return {"project": project, "branch": branch, "into": target, "change": stat or "no changes",
            "resolution": meta.get("resolution")}


def keep(run_dir):
    meta = load(run_dir)
    project, branch = meta["project"], meta["branch"]
    if meta.get("resolution"):
        raise ActionError(f"already {meta['resolution']['action']}")
    if not os.path.isdir(project):
        raise ActionError(f"the project folder {project} no longer exists")
    if not _branch_exists(project, branch):
        raise ActionError(f"the branch {branch} no longer exists")
    if gitops.is_dirty(project):
        raise ActionError(f"{project} has uncommitted changes; commit or stash them, then keep again")
    into = gitops.git(project, "rev-parse", "--abbrev-ref", "HEAD").strip()
    ok, output = gitops.merge(project, branch, f"mp-agent: {meta.get('task', branch)[:72]}")
    if not ok:
        raise ActionError(f"merging into {into} would conflict, so nothing was changed. {output.strip()[-300:]}")
    gitops.branch_delete(project, branch)
    detail = f"merged into {into} in {project}"
    _record(run_dir, meta, "kept", detail)
    return detail


def discard(run_dir):
    meta = load(run_dir)
    project, branch = meta["project"], meta["branch"]
    if meta.get("resolution"):
        raise ActionError(f"already {meta['resolution']['action']}")
    worktree = os.path.join(os.path.dirname(os.path.dirname(run_dir)), "worktrees", os.path.basename(run_dir))
    if os.path.isdir(project):
        for path in [worktree] + [f"{worktree}-{u}" for u in (meta.get("units") or {})]:
            if os.path.isdir(path):
                gitops.worktree_remove(project, path)
        if _branch_exists(project, branch):
            gitops.branch_delete(project, branch)
    detail = f"deleted branch {branch}"
    _record(run_dir, meta, "discarded", detail)
    return detail


MAX_DIFF_BYTES = 400_000


def _base_for(meta):
    project, branch = meta["project"], meta["branch"]
    base = meta.get("base_commit")
    if base and subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=project,
                               capture_output=True).returncode == 0:
        return base
    # runs recorded before base commits were: where the branch left what is checked out now
    return gitops.git(project, "merge-base", "HEAD", branch, check=False).strip()


def judge_notes(run_dir):
    """The final judge's last reply, for reading beside the diff."""
    audits = sorted(glob.glob(os.path.join(run_dir, "*", "pass-*", "audit.md")), key=os.path.getmtime)
    if not audits:
        return ""
    with open(audits[-1], errors="replace") as fh:
        return fh.read()[-6000:]


def changes(run_dir):
    """Files changed by a run and the diff, against where the run started."""
    meta = load(run_dir)
    project, branch = meta["project"], meta["branch"]
    if not os.path.isdir(project):
        raise ActionError(f"the project folder {project} no longer exists")
    if not _branch_exists(project, branch):
        resolution = (meta.get("resolution") or {}).get("action")
        raise ActionError(f"the branch {branch} is gone" + (f" (it was {resolution})" if resolution else ""))
    base = _base_for(meta)
    if not base:
        raise ActionError("could not work out where this run started")
    stat = gitops.git(project, "diff", "--numstat", base, branch, check=False)
    files = []
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files.append({"path": parts[2], "added": parts[0], "removed": parts[1]})
    diff = gitops.git(project, "diff", base, branch, check=False)
    truncated = len(diff.encode()) > MAX_DIFF_BYTES
    if truncated:
        diff = diff.encode()[:MAX_DIFF_BYTES].decode(errors="ignore") + "\n[diff truncated]"
    return {"project": project, "branch": branch, "base": base[:12], "files": files, "diff": diff,
            "truncated": truncated, "judge": judge_notes(run_dir)}


def _pr_body(run_dir, meta):
    def read_json(name, default):
        try:
            with open(os.path.join(run_dir, name)) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default
    plan = read_json("plan.json", {}) or {}
    contract = plan.get("contract") or {}
    lines = [meta.get("task", "").strip(), "", f"**Outcome:** {meta.get('outcome', '')}", ""]
    if contract.get("done"):
        lines += ["### What done means (the contract)"] + [f"- {d}" for d in contract["done"]] + [""]
    amendments = []
    for path in sorted(glob.glob(os.path.join(run_dir, "*", "contract.json"))):
        try:
            with open(path) as fh:
                amendments += json.load(fh).get("amendments") or []
        except (OSError, ValueError):
            pass
    if amendments:
        lines += ["### Settled during the run"] + [f"- {a}" for a in dict.fromkeys(amendments)] + [""]
    decisions = [d for d in read_json("decisions.json", []) if d.get("kind") == "decision"][-12:]
    if decisions:
        lines += ["### Implementation decisions"] + [f"- {d['text']}" for d in decisions] + [""]
    notes = judge_notes(run_dir)
    if notes:
        lines += ["<details><summary>The final judge's notes</summary>", "", notes.strip()[-3500:], "", "</details>", ""]
    costs = meta.get("costs") or {}
    roles = read_json("roles.json", {})
    who = (f" Built by {roles['worker']}, reviewed by {roles.get('reviewer') or roles['worker']} and a panel of "
           f"{roles.get('panel') or roles['worker']}, judged by {roles.get('judge')}." if roles.get("worker") else "")
    billed = costs.get("billed_usd", costs.get("mercury_usd", 0)) or 0
    lines += [f"_Made by mp-agent: {plan.get('mode', 'single')} run, {meta.get('seconds', 0)}s, ${billed:.3f} billed."
              f"{who} Checked by tests and every judge — still read the diff._"]
    return "\n".join(lines)


def pull_request(run_dir, base_branch=None, state_dir=None):
    """Push the run's branch and open a GitHub pull request. Never for a repository
    listed as protected in your config."""
    from .where import github_name, is_protected
    meta = load(run_dir)
    project, branch = meta["project"], meta["branch"]
    if meta.get("resolution"):
        raise ActionError(f"already {meta['resolution']['action']}")
    if not meta.get("approved"):
        raise ActionError("only approved runs can become pull requests")
    if not os.path.isdir(project) or not _branch_exists(project, branch):
        raise ActionError(f"the branch {branch} is no longer in {project}")
    remote = gitops.git(project, "remote", "get-url", "origin", check=False).strip()
    repo = github_name(remote)
    if not repo:
        raise ActionError("this project has no GitHub remote called origin, so there is nowhere to open a PR")
    if is_protected(remote, state_dir):
        raise ActionError(f"{repo} is listed as protected in your config; nothing was pushed")
    base = base_branch or meta.get("base_branch") or "main"
    title = re.sub(r"\s+", " ", meta.get("task", branch)).strip()[:72]
    push = subprocess.run(["git", "push", "-u", "origin", f"{branch}:{branch}"], cwd=project, capture_output=True,
                          text=True, timeout=300)
    if push.returncode != 0:
        raise ActionError(f"pushing {branch} failed: {(push.stderr or push.stdout).strip()[-300:]}")
    created = subprocess.run(["gh", "pr", "create", "--repo", repo, "--head", branch, "--base", base, "--title", title,
                              "--body", _pr_body(run_dir, meta)], cwd=project, capture_output=True, text=True,
                             timeout=120)
    if created.returncode != 0:
        raise ActionError(f"the branch was pushed, but opening the PR failed: "
                          f"{(created.stderr or created.stdout).strip()[-300:]}")
    url = created.stdout.strip().splitlines()[-1]
    _record(run_dir, meta, "pull request", url)
    return url
