"""The skills installed on this machine, offered to the builders by name.

Claude Code can load a skill, but only when it has the Skill tool — and even then it does
not go looking. Given the tool and nothing else, a worker asked to classify support
messages with a confidence wrote keyword lists, with a skill for exactly that job sitting
unopened on the shelf. The tool is necessary and not sufficient: the shelf has to be
described.

So a job that allows skills puts a short list in front of the builders — each skill's name
and its own one-line description, no more — and leaves the choosing to them. Nothing is
loaded on their behalf, and a skill never overrides the contract.

Only Claude Code has this. Other tools have their own arrangements and are left alone.
"""
import glob
import os
import re

FRONT = re.compile(r"^---\s*\n(.*?)\n---", re.S)
FIELD = re.compile(r"^(name|description):\s*(>-?|\|)?\s*(.*)$", re.M)
MAX = 40


def _read(path):
    """(name, description) from a SKILL.md's frontmatter, or None."""
    try:
        with open(path, errors="replace") as fh:
            text = fh.read(8000)
    except OSError:
        return None
    front = FRONT.search(text)
    if not front:
        return None
    block, found = front.group(1), {}
    for match in FIELD.finditer(block):
        key, folded, first = match.group(1), match.group(2), match.group(3).strip()
        if folded and not first:                       # a folded block: take its indented lines
            after = block[match.end():]
            lines = []
            for line in after.splitlines():
                if line.strip() and not line.startswith((" ", "\t")):
                    break
                lines.append(line.strip())
            first = " ".join(l for l in lines if l)
        found[key] = first
    name, description = found.get("name"), found.get("description")
    return (name, description) if name and description else None


def installed(home=None):
    """[{name, description}] for every skill on this machine, by name, without duplicates."""
    home = home or os.path.expanduser("~")
    roots = [os.path.join(home, ".claude", "skills", "*", "SKILL.md"),
             os.path.join(home, ".claude", "plugins", "cache", "*", "*", "*", "skills", "*", "SKILL.md")]
    out = {}
    for pattern in roots:
        for path in sorted(glob.glob(pattern)):
            got = _read(path)
            if got and got[0] not in out:
                out[got[0]] = {"name": got[0], "description": got[1]}
    return list(out.values())[:MAX]


def section(skills, limit=220):
    """The list as the builders see it: names and their own words, and nothing else."""
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description'][:limit].strip()}" for s in skills]
    return ("# Skills available here\n\nThese are installed on this machine and you can load one with the Skill "
            "tool. Load a skill when it covers what you are about to do — it carries current, specific guidance "
            "you would otherwise guess at. Do not load one that does not apply, and never let a skill override "
            "the contract, the frozen tests or which files you own.\n\n" + "\n".join(lines))
