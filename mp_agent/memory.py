"""What earlier runs on a project settled, handed to the planner next time.

Rulings, the person's answers and the implementers' decisions are exactly the
things a fresh planner would otherwise have to rediscover (or get wrong, and
have the reviewers argue about again). They are kept outside the project, one
small file per project, newest last, trimmed from the front.

    ~/.mp-agent/projects/<name>-<hash>/memory.md
"""
import glob
import hashlib
import json
import os
from datetime import datetime, timezone

MAX_BYTES = 20_000
PROMPT_CHARS = 6_000


def path_for(state_dir, project):
    real = os.path.realpath(project)
    tag = hashlib.sha1(real.encode()).hexdigest()[:8]
    return os.path.join(state_dir, "projects", f"{os.path.basename(real) or 'project'}-{tag}", "memory.md")


def recall(state_dir, project):
    try:
        with open(path_for(state_dir, project)) as fh:
            text = fh.read()
    except OSError:
        return ""
    return text[-PROMPT_CHARS:]


def remember(state_dir, project, run_dir, task, outcome, decisions):
    amendments = []
    for contract_file in sorted(glob.glob(os.path.join(run_dir, "*", "contract.json"))):
        unit = os.path.basename(os.path.dirname(contract_file))
        try:
            with open(contract_file) as fh:
                for text in json.load(fh).get("amendments") or []:
                    amendments.append((unit, text))
        except (OSError, ValueError):
            continue
    decided = [d for d in decisions if d.get("kind") == "decision"][-10:]
    if not amendments and not decided:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [f"## {stamp} — {task.strip().splitlines()[0][:120]}", f"Outcome: {outcome}"]
    if amendments:
        lines.append("Settled (rulings and answers):")
        lines += [f"- {text}" + (f" [{unit}]" if unit not in ("main", "final") else "") for unit, text in amendments]
    if decided:
        lines.append("Implementation decisions:")
        lines += [f"- {d['text']}" for d in decided]
    entry = "\n".join(lines) + "\n\n"
    target = path_for(state_dir, project)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with open(target) as fh:
            existing = fh.read()
    except OSError:
        existing = f"# What mp-agent runs have settled for {os.path.realpath(project)}\n\n"
    text = existing + entry
    if len(text.encode()) > MAX_BYTES:
        header, _, body = text.partition("\n\n")
        entries = body.split("\n## ")
        while entries and len((header + "\n\n## ".join(entries)).encode()) > MAX_BYTES:
            entries.pop(0)
        text = header + "\n\n" + "\n## ".join(entries)
        if not text.split("\n\n", 1)[1].startswith("## "):
            text = header + "\n\n## " + text.split("\n\n", 1)[1]
    tmp = target + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, target)
    return target


def projects(state_dir):
    """Every project that has a memory file, newest first."""
    found = []
    for path in glob.glob(os.path.join(state_dir, "projects", "*", "memory.md")):
        try:
            with open(path) as fh:
                first = fh.readline().strip()
        except OSError:
            continue
        m = first.split(" for ", 1)
        found.append({"id": os.path.basename(os.path.dirname(path)),
                      "project": m[1] if len(m) == 2 else os.path.basename(os.path.dirname(path)),
                      "updated": os.path.getmtime(path), "bytes": os.path.getsize(path)})
    return sorted(found, key=lambda p: -p["updated"])


def read_memory(state_dir, project_id):
    path = os.path.join(state_dir, "projects", os.path.basename(project_id), "memory.md")
    with open(path) as fh:
        return fh.read()


def save_memory(state_dir, project_id, text):
    folder = os.path.join(state_dir, "projects", os.path.basename(project_id))
    if not os.path.isdir(folder):
        raise FileNotFoundError(project_id)
    path = os.path.join(folder, "memory.md")
    with open(path + ".tmp", "w") as fh:
        fh.write(text[:MAX_BYTES * 2])
    os.replace(path + ".tmp", path)
