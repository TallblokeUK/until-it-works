"""/mp-agent in whichever AI coding tool you use.

One set of instructions, written out in each tool's own format:

    Claude Code  ~/.claude/commands/mp-agent.md          /mp-agent [task]      ($ARGUMENTS)
    OpenCode     ~/.config/opencode/commands/mp-agent.md /mp-agent [task]      ($ARGUMENTS)
    Gemini CLI   ~/.gemini/commands/mp-agent.toml        /mp-agent [task]      ({{args}})
    Qwen Code    ~/.qwen/commands/mp-agent.toml          /mp-agent [task]      ({{args}})
    Codex        ~/.codex/skills/mp-agent/SKILL.md       $mp-agent [task]      (Codex has no custom slash commands)
    Cline        ~/.cline/workflows/mp-agent.md          /mp-agent [task]      (a copy: Cline ignores symlinked workflows)

Each launcher only guides you through what, where and which models, runs
`mp-agent start`, and hands back; the work happens in the background and in the
workshop. A file that is already there and was not written by mp-agent is left
alone.
"""
import os
import shutil

MARK = "mp-agent launcher"

INSTRUCTIONS = """You are only the launcher for the mp-agent team. Never do the task yourself, never read or edit project files, and run no commands other than the mp-agent ones below. Keep every message short.

## 1. What
{what}

## 2. Where
If the user already said where (a project name, a GitHub repo, "new project", "one-off", "here"), use that and skip the question. Otherwise run `mp-agent where`, show its list to the user as it is, and ask:
"Where should the agents work? Reply with a code from the list (L1, G3…), 'here', 'new', or 'one-off'."

## 3. Confirm
Run `mp-agent models` and take the first five lines (planner, worker, reviewer, panel, judge). Ask:
"Ready to start: <task, shortened> — in <place> — planner <planner>, workers <worker>, reviewer <reviewer>, panel <panel>, judge <judge>. Go? (yes, or tell me what to change)"
If they change a model, remember it as --planner NAME, --worker NAME, --reviewer NAME, --panel-model NAME or --judge NAME using their words ("same" for the reviewer or panel means the workers' model). If they change the place or task, use the new one and confirm again. If `mp-agent models` fails or shows no models, tell them to open the workshop (run `mp-viz`) and follow SETUP, then stop.

## 4. Start
Run exactly one command. Put the task between the TASK lines exactly as the user wrote it, and add any model flags before `<<'TASK'`:
- a local project (L-code): `mp-agent start --repo "<the ~/path shown for that code>" <<'TASK'`
- a GitHub repo (G-code): `mp-agent start --github <owner/repo> <<'TASK'` (this pulls the latest, or clones it)
- here: `mp-agent start <<'TASK'`
- new: `mp-agent start --new <<'TASK'` (add `--name NAME` if they gave one)
- one-off: `mp-agent start --oneoff <<'TASK'`

```
mp-agent start [flags] <<'TASK'
<the task>
TASK
```

## 5. After
- If the output starts with `NEEDS:`, show that message word for word and wait. If they want a new, separate project, run the same command with `--new` instead of the place flag; if they want to use that folder anyway, add `--init`. Otherwise stop.
- When the output starts with `Started:`, tell the user in one or two sentences that the agents are working and the workshop window has opened at http://127.0.0.1:7788, where any question and the result will appear. Then stop. Do not wait for the run or check on it."""

DESCRIPTION = "Hand a coding task to the mp-agent team: it asks what, where and which models, starts it, and opens the live workshop."


def _what(placeholder):
    if placeholder is None:
        return ("The task is the text that follows these instructions. If there is none, reply only with "
                "\"What would you like the agents to do?\" and wait; the user's reply is the task.")
    return (f"The task is: {placeholder}\n\nIf that is empty, reply only with \"What would you like the agents to "
            "do?\" and wait; the user's reply is the task.")


def body(placeholder):
    return INSTRUCTIONS.format(what=_what(placeholder))


def render(tool):
    """The launcher file's text for one tool."""
    if tool == "claude":
        return (f"---\ndescription: {DESCRIPTION}\nargument-hint: [task]\nallowed-tools: Bash(mp-agent:*)\n---\n"
                f"<!-- {MARK} -->\n\n{body('$ARGUMENTS')}\n")
    if tool == "opencode":
        return f"---\ndescription: {DESCRIPTION}\n---\n<!-- {MARK} -->\n\n{body('$ARGUMENTS')}\n"
    if tool in ("gemini", "qwen"):
        text = body("{{args}}")
        if "'''" in text:
            raise ValueError("the instructions cannot contain ''' in a TOML literal string")
        return f"# {MARK}\ndescription = \"{DESCRIPTION}\"\nprompt = '''\n{text}\n'''\n"
    if tool == "codex":
        return (f"---\nname: mp-agent\ndescription: {DESCRIPTION} Use when the user types $mp-agent or asks to hand a "
                f"task to mp-agent.\n---\n<!-- {MARK} -->\n\n"
                + body("whatever the user wrote after $mp-agent") + "\n")
    if tool == "cline":
        return f"---\nname: mp-agent\ndescription: {DESCRIPTION}\n---\n<!-- {MARK} -->\n\n{body(None)}\n"
    raise ValueError(f"no launcher for {tool}")


def targets(home):
    """tool -> (path of its launcher, whether the tool looks installed)."""
    exists = lambda *parts: os.path.isdir(os.path.join(home, *parts))
    return {
        "claude": (os.path.join(home, ".claude", "commands", "mp-agent.md"), bool(shutil.which("claude")) or exists(".claude")),
        "opencode": (os.path.join(home, ".config", "opencode", "commands", "mp-agent.md"),
                     bool(shutil.which("opencode")) or exists(".config", "opencode")),
        "gemini": (os.path.join(home, ".gemini", "commands", "mp-agent.toml"), bool(shutil.which("gemini")) or exists(".gemini")),
        "qwen": (os.path.join(home, ".qwen", "commands", "mp-agent.toml"), bool(shutil.which("qwen")) or exists(".qwen")),
        "codex": (os.path.join(home, ".codex", "skills", "mp-agent", "SKILL.md"), bool(shutil.which("codex")) or exists(".codex")),
        "cline": (os.path.join(home, ".cline", "workflows", "mp-agent.md"), bool(shutil.which("cline")) or exists(".cline")),
    }


def install(home, only=None):
    """Write the launcher for every installed tool. Returns [(tool, path, what happened)]."""
    done = []
    for tool, (path, present) in targets(home).items():
        if (only and tool not in only) or not present:
            continue
        text = render(tool)
        if os.path.exists(path):
            with open(path, errors="replace") as fh:
                current = fh.read()
            if MARK not in current:
                done.append((tool, path, "left alone: a file that mp-agent did not write is already there"))
                continue
            if current == text:
                done.append((tool, path, "up to date"))
                continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
        done.append((tool, path, "installed"))
    # the name Cline users had before /mp-agent existed: kept up to date where it is still there
    legacy = os.path.join(home, ".cline", "workflows", "agents.md")
    if os.path.exists(legacy) and (not only or "cline" in only):
        with open(legacy, errors="replace") as fh:
            old = fh.read()
        if MARK in old or "You are only the launcher for the agent team" in old:
            with open(legacy, "w") as fh:
                fh.write(render("cline").replace("name: mp-agent", "name: agents", 1))
            done.append(("cline", legacy, "updated (the older /agents name)"))
    return done
