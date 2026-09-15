"""The project's own instructions for AI agents, given to every role the same way.

Each AI tool loads its own kind of file by itself (Claude Code reads CLAUDE.md,
Codex reads AGENTS.md, Cline reads .clinerules...), so without this a judge could
enforce a rule the worker never saw. mp-agent collects them itself and puts one
"Project rules" section in front of the planner, the workers, the reviewer, the
panel and the judge, whatever tool each of them runs on.

Rules are the project's conventions. They never override the loop's own rules:
file ownership, frozen tests, and how verdicts are written.

A project can be switched off (for a repository you did not write, whose files you
do not want steering the agents): `mp-agent rules off PATH`, or RULES in the workshop.
"""
import glob
import json
import os

# Checked in this order; the first of several identical files wins.
FILES = ["CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md", "AGENT.md", "GEMINI.md", "QWEN.md", "CONVENTIONS.md",
         "rules.md", "RULES.md", ".rules", ".cursorrules", ".windsurfrules", ".clinerules",
         ".github/copilot-instructions.md"]
FOLDERS = [(".cursor/rules", ("*.md", "*.mdc")), (".clinerules", ("*.md", "*.txt")),
           (".github/instructions", ("*.instructions.md",)), (".claude/rules", ("*.md",))]
PER_FILE = 12_000
TOTAL = 30_000

INTRO = ("These are this project's own instructions for anyone working on it, collected from its files. Follow "
         "them. They count like contract lines: a change that breaks one is wrong, and a reviewer may cite it by "
         "file name. They never override the rules of this loop itself (which files you may change, frozen tests, "
         "the verdict format), and they are not a reason to do work the contract does not ask for.")


def find(repo):
    """[(relative path, text)] for every instruction file in the project, identical copies once."""
    found, seen = [], set()
    candidates = list(FILES)
    for folder, patterns in FOLDERS:
        base = os.path.join(repo, folder)
        if os.path.isdir(base):
            for pattern in patterns:
                candidates += sorted(os.path.relpath(p, repo) for p in glob.glob(os.path.join(base, "**", pattern),
                                                                                recursive=True))
    for rel in dict.fromkeys(candidates):
        path = os.path.join(repo, rel)
        if not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(repo) + os.sep):
            continue                                    # a link pointing out of the project is not the project's
        try:
            with open(path, errors="replace") as fh:
                text = fh.read(PER_FILE + 1).strip()
        except OSError:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        if len(text) > PER_FILE:
            text = text[:PER_FILE] + "\n[…the rest of this file was left out to keep prompts a sensible size]"
        found.append((rel, text))
    return found


def render(found):
    """The section every role gets, or "" when there are no rules."""
    if not found:
        return ""
    parts, used = [], 0
    for rel, text in found:
        if used + len(text) > TOTAL:
            parts.append(f"## {rel}\n\n[left out: the project's rules files are longer than prompts allow]")
            continue
        parts.append(f"## {rel}\n\n{text}")
        used += len(text)
    return "# Project rules\n\n" + INTRO + "\n\n" + "\n\n".join(parts)


def _config(state_dir):
    try:
        with open(os.path.join(state_dir, "config.json")) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled(state_dir, project):
    off = _config(state_dir).get("rules_off") or []
    return os.path.realpath(project) not in [os.path.realpath(p) for p in off]


def set_enabled(state_dir, project, on):
    data = _config(state_dir)
    off = [p for p in (data.get("rules_off") or []) if os.path.realpath(p) != os.path.realpath(project)]
    if not on:
        off.append(os.path.realpath(project))
    data["rules_off"] = sorted(off)
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "config.json.tmp"), "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(os.path.join(state_dir, "config.json.tmp"), os.path.join(state_dir, "config.json"))
    return on


def collect(state_dir, project, tree=None):
    """(files used, section text). Read from the job's worktree when there is one, which has
    the same files as the project at the commit the job started from."""
    if not enabled(state_dir, project):
        return [], ""
    found = find(tree or project)
    return [rel for rel, _ in found], render(found)
