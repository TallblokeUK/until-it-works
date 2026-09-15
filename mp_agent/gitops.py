"""Everything mp-agent does to git: project setup, worktrees, snapshots,
ownership enforcement and merging.

Git is the harness's undo button. Every unit of work happens in a worktree on
its own branch, so the user's checkout is never touched and throwing work away
is deleting a branch.
"""
import fnmatch
import hashlib
import os
import re
import subprocess
from pathlib import Path

IDENTITY = ["-c", "user.name=mp-agent", "-c", "user.email=mp-agent@localhost"]


class SetupError(Exception):
    """The project cannot be used as it stands; the message says why and what to do."""


class GitError(Exception):
    pass


def git(cwd, *args, check=True, input_bytes=None):
    """Run git and return stdout as text. Raises GitError on failure when check is set."""
    proc = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, input=input_bytes,
                          capture_output=True)
    out = proc.stdout.decode(errors="replace")
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.decode(errors='replace').strip()}")
    return out


def git_bytes(cwd, *args):
    proc = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def slugify(text, limit=40):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "task"


def toplevel(path):
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def has_commits(repo):
    return subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo,
                          capture_output=True).returncode == 0


# Byproducts of running code, not work anyone did. They never count as an
# ownership violation, and a project mp-agent creates starts by ignoring them.
JUNK_DIRS = ("__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "node_modules/", ".venv/")
JUNK_SUFFIXES = (".pyc", ".pyo")
DEFAULT_GITIGNORE = "__pycache__/\n*.py[co]\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.venv/\nnode_modules/\n"
JUNK_PATHSPEC = ["--", ".", ":(exclude,glob)**/*.py[co]"] + [f":(exclude,glob)**/{d}**" for d in JUNK_DIRS]


def is_junk(path):
    return path.endswith(JUNK_SUFFIXES) or any(("/" + path).find("/" + d) != -1 for d in JUNK_DIRS)


def is_dirty(repo):
    """Uncommitted work, ignoring cache files that merely running the code leaves behind."""
    for line in git(repo, "status", "--porcelain").splitlines():
        path = line[3:].strip().strip('"')
        if path and not is_junk(path):
            return True
    return False


FORBIDDEN = ("/", "/etc", "/usr", "/var", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev", "/opt", "/root")


def refuse_dangerous(path, home):
    real = os.path.realpath(path)
    if real == os.path.realpath(home):
        raise SetupError(f"refusing to run in your home directory ({real})")
    for bad in FORBIDDEN:
        if real == bad or (bad != "/" and real.startswith(bad + "/")):
            raise SetupError(f"refusing to run in a system path ({real})")


def setup_project(folder, task, init=False, projects_root=None, home=None, say=print):
    """Return the repository root to work in, creating or initialising it if allowed.

    - no folder: a new directory under projects_root, named after the task
    - a new or empty folder: git initialised silently
    - a folder with files but no git: refused unless init is set
    - uncommitted changes: refused, as ever
    """
    home = home or os.path.expanduser("~")
    projects_root = projects_root or os.path.join(home, "mp-projects")
    if folder is None:
        base = os.path.join(projects_root, slugify(task))
        folder, n = base, 2
        while os.path.exists(folder) and os.listdir(folder):
            folder = f"{base}-{n}"
            n += 1
        say(f"project    new folder {folder}")
    folder = os.path.abspath(os.path.expanduser(folder))
    refuse_dangerous(folder, home)
    os.makedirs(folder, exist_ok=True)

    top = toplevel(folder)
    if top is None:
        entries = [e for e in os.listdir(folder) if e not in (".DS_Store",)]
        if entries and not init:
            raise SetupError(
                f"{folder} has files but is not a git repository. mp-agent works on git branches "
                f"so it can never damage your files; rerun with --init to let it set up git there.")
        git(folder, "init", "-q", "-b", "main")
        if not os.path.exists(os.path.join(folder, ".gitignore")):
            with open(os.path.join(folder, ".gitignore"), "w") as fh:
                fh.write(DEFAULT_GITIGNORE)
        git(folder, "add", "-A")
        git(folder, "commit", "-q", "--allow-empty", "-m", "mp-agent: starting point")
        say(f"project    set up git in {folder}")
        top = folder
    refuse_dangerous(top, home)
    if not has_commits(top):
        git(top, "add", "-A")
        git(top, "commit", "-q", "--allow-empty", "-m", "mp-agent: starting point")
    if is_dirty(top):
        raise SetupError(f"{top} has uncommitted changes; commit or stash them first")
    return top


def head(tree):
    return git(tree, "rev-parse", "HEAD").strip()


def worktree_add(repo, path, branch, base):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, path, base)


def worktree_attach(repo, path, branch):
    """Check out an existing branch into a worktree (used when resuming)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    git(repo, "worktree", "prune", check=False)
    git(repo, "worktree", "add", "-q", path, branch)


def branch_exists(repo, branch):
    return bool(git(repo, "branch", "--list", branch, check=False).strip())


def worktree_detached(repo, path):
    """A throwaway checkout of this worktree's HEAD, on no branch."""
    worktree_remove(repo, path)
    git(repo, "worktree", "add", "-q", "--detach", path, "HEAD")


def adopt_paths(tree, commit, paths, message):
    """Bring these paths from another commit into this worktree and commit only them,
    leaving any other uncommitted work as it is. Returns the paths that changed."""
    changed = [p for p in git(tree, "diff", "--name-only", "HEAD", commit, "--", *paths).splitlines() if p]
    if not changed:
        return []
    git(tree, "checkout", commit, "--", *changed)
    git(tree, "commit", "-q", "-m", message, "--", *changed)
    stage_all(tree)
    return changed


CLINE_REFS = "refs/cline/checkpoints"


def cline_refs(repo):
    """Cline saves a checkpoint ref in the project on every call. They would keep
    every attempt reachable (a later run could read an earlier one's work)."""
    return set(git(repo, "for-each-ref", "--format=%(refname)", CLINE_REFS, check=False).split())


def drop_refs(repo, refs):
    for ref in refs:
        git(repo, "update-ref", "-d", ref, check=False)
    return len(refs)


def worktree_remove(repo, path):
    git(repo, "worktree", "remove", "--force", path, check=False)


def branch_delete(repo, branch):
    git(repo, "branch", "-D", branch, check=False)


def stage_all(tree):
    """Stage the work, never the byproducts of running it (cache files are left
    untracked even in projects without a .gitignore)."""
    git(tree, "add", "-A", *JUNK_PATHSPEC)


def staged_diff(tree, binary=False):
    """The whole change in this worktree against its HEAD, untracked files included,
    cache junk excluded (running the tests is not a change to the work)."""
    stage_all(tree)
    args = ["diff", "--cached"] + (["--binary"] if binary else [])
    return git_bytes(tree, *args)


def diff_hash(tree):
    return hashlib.sha1(staged_diff(tree, binary=True)).hexdigest()


def changed_files(tree):
    stage_all(tree)
    return [p for p in git(tree, "diff", "--cached", "--name-only").splitlines() if p]


def snapshot(tree):
    """Capture the implementer's version before anyone else is let near it."""
    return staged_diff(tree, binary=True)


def restore(tree, snap):
    """Put the tree back to exactly the snapshot. Returns True if anything had changed."""
    if staged_diff(tree, binary=True) == snap:
        return False
    git(tree, "reset", "-q", "--hard", "HEAD")
    git(tree, "clean", "-fdq")
    if snap:
        git(tree, "apply", "--index", "--binary", "-", input_bytes=snap)
    return True


def _matches(path, rule):
    rule = rule.strip()
    if rule.startswith("./"):
        rule = rule[2:]
    if rule.endswith("/"):
        return path.startswith(rule)
    if any(ch in rule for ch in "*?["):
        return fnmatch.fnmatch(path, rule)
    return path == rule


def owned(path, owns):
    return owns is None or any(_matches(path, r) for r in owns)


def ownership_violations(tree, owns, frozen):
    """Files changed in this worktree that the unit may not touch."""
    bad = []
    for path in changed_files(tree):
        if is_junk(path):
            continue
        if any(_matches(path, f) for f in (frozen or [])) or not owned(path, owns):
            bad.append(path)
    return bad


def revert_paths(tree, paths):
    """Undo changes to specific paths: restore tracked files, delete new ones."""
    tracked = set(git(tree, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    for path in paths:
        if path in tracked:
            git(tree, "reset", "-q", "HEAD", "--", path, check=False)
            git(tree, "checkout", "HEAD", "--", path, check=False)
        else:
            git(tree, "rm", "-q", "--cached", "--", path, check=False)
            target = Path(tree) / path
            if target.is_file() or target.is_symlink():
                target.unlink()
    stage_all(tree)


def commit_all(tree, message):
    stage_all(tree)
    if not git(tree, "diff", "--cached", "--name-only").strip():
        return None
    git(tree, "commit", "-q", "-m", message)
    return head(tree)


def merge(tree, branch, message):
    """Merge a branch into the worktree's branch. Returns (ok, output); aborts on conflict."""
    proc = subprocess.run(["git", *IDENTITY, "merge", "--no-ff", "-m", message, branch],
                          cwd=tree, capture_output=True)
    out = (proc.stdout + proc.stderr).decode(errors="replace")
    if proc.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=tree, capture_output=True)
        return False, out
    return True, out


def run_check(tree, command, timeout=600):
    """Run the check command in the worktree. Returns (status, output)."""
    try:
        proc = subprocess.run(["bash", "-c", command], cwd=tree, capture_output=True, timeout=timeout,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return proc.returncode, (proc.stdout + proc.stderr).decode(errors="replace")
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"") + (exc.stderr or b"")
        return 124, partial.decode(errors="replace") + f"\n[check timed out after {timeout}s]"


def listing(repo, limit=300):
    files = git(repo, "ls-files").splitlines()
    more = len(files) - limit
    return "\n".join(files[:limit]) + (f"\n… and {more} more" if more > 0 else "")
