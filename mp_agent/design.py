"""The designer: who decides how the work looks, before anyone builds it.

Nothing else in the loop owns the look. A planner writes what must be true, a worker
makes it true, and reviewers check it — so a page that meets every line of the contract
can still arrive in the same grey-card, Inter-and-a-purple-gradient outfit as everything
else. The designer settles the look first, in writing, and then everyone is judged
against it: the workers build to it, and a panel lens holds the finished thing to it.

It runs once a job, only when the job changes something a person looks at (the planner
says so in the plan), and it never writes code.

On Claude Code it uses that tool's own frontend-design skill when it has one, which is
why the designer wants a Claude model by default. On any other tool the brief below
carries the same intent in the prompt itself.
"""
import os
import re

BRIEF_FILE = "design/brief.md"

DESIGN_SYSTEM = """You are the designer for a piece of work about to be built by other agents. You decide how it \
looks and feels. You never write the code: you write the brief they build to, and the reviewers judge against.

If you have a frontend-design skill, load and follow it before writing anything.

Look at what is already there (you have read-only tools). If the project has a look of its own — its own colours, \
type, spacing, components — your job is to name it and extend it, not to replace it. Say so in the brief.

Reply with ONLY the brief, as markdown, in these sections and nothing else:

# Design brief

## The direction
One or two sentences naming the aesthetic and committing to it, plainly enough that someone could tell whether the \
finished thing obeyed it. Pick something with a point of view.

## Colour
The palette as a short list: a name, a hex value and what it is for (ground, text, accent, edges, danger). Say which \
one dominates. Include what the dark version becomes, when the thing can be seen in both.

## Type
The display face and the body face, each with a real fallback stack that works without anything being downloaded. \
Sizes as a small scale, and the weights that are actually used.

## Space and layout
The rhythm (a spacing scale), how wide things get, where things line up, and what happens at a phone's width.

## Motion
What moves, how far, how fast, and what stays still. What must not move when someone has asked for reduced motion.

## Not this
Five to eight specific things to avoid for this piece of work: the clichés, the default fonts, the stock gradient, \
the generic component shapes. Be concrete enough to be checkable.

Rules:
- Everything you name must be buildable with what the project already has. No downloads, no new dependencies, no \
fonts fetched from the internet unless the project already fetches them.
- Keep it short: a page. It is a brief, not an essay.
- Do not invent product requirements, features or copy. The contract says what is built; you say how it looks.
"""

ASK = """# The work

{task}

# The plan

{summary}

# The project

{notes}

Write the design brief for this work."""

HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def wanted(plan, choice="auto"):
    """Whether this job gets a designer: what the person said, else what the planner decided."""
    if choice == "on":
        return True
    if choice == "off":
        return False
    return bool(plan.get("design"))


def write(ctx, task, plan, repo):
    """Ask the designer for the brief. Returns the text, or "" if it could not."""
    from .planner import project_notes
    from .providers import acting

    prompt = ASK.format(task=task, summary=plan.get("summary", ""), notes=project_notes(repo))
    if getattr(ctx, "project_rules", ""):
        prompt += ("\n\n" + ctx.project_rules + "\n\nThe look you choose must keep to these rules.")
    ctx.run.write_text("design/prompt.md", prompt)
    with acting("designer", "design"):
        reply = ctx.designer.ask(DESIGN_SYSTEM, prompt, repo)
    ctx.run.count("designer_calls")
    ctx.run.write_text("design/reply.md", reply.text)
    if not reply.ok or "## The direction" not in (reply.text or ""):
        return ""
    brief = reply.text[reply.text.index("# Design brief"):] if "# Design brief" in reply.text else reply.text
    ctx.run.write_text(BRIEF_FILE, brief.strip() + "\n")
    return brief.strip()


def direction(brief):
    """The one line worth saying out loud: the direction the designer committed to."""
    lines = (brief or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## the direction"):
            for text in lines[i + 1:]:
                if text.strip():
                    return re.sub(r"[*_`]", "", text.strip())[:160]
    return ""


def palette(brief, limit=6):
    """The hex colours in the brief, in the order they appear: the workshop paints with these."""
    seen = []
    for match in HEX.finditer(brief or ""):
        value = match.group(0).lower()
        if len(value) == 4:                       # #abc -> #aabbcc
            value = "#" + "".join(c * 2 for c in value[1:])
        if value not in seen:
            seen.append(value)
    return seen[:limit]


def section(brief):
    """The brief as every other role sees it: their own heading level, and what it means."""
    if not brief:
        return ""
    body = brief.replace("# Design brief", "", 1).strip()
    body = re.sub(r"^## ", "### ", body, flags=re.M)
    return ("# Design brief\n\nThis is how this work is meant to look, decided before it was built. Build to it, and "
            "judge against it: a change that ignores it is wrong in the same way as one that breaks a contract line. "
            "It never overrides the contract, the frozen tests or file ownership, and it is not a reason to add work "
            "the contract does not ask for.\n\n" + body)


def saved(run_dir):
    path = os.path.join(run_dir, BRIEF_FILE)
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""
