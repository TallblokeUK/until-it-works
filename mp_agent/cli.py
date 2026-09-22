"""mp-agent command line.

    mp-agent start "task"                launch in the background (what /agents in Cline runs)
    mp-agent [options] "task"            do a task in the foreground
    mp-agent answer "text" [--run DIR]   answer the question a waiting run asked
    mp-agent status [-f] [-n N]          same as mp-status
    mp-agent clean [--yes]               remove worktrees left by finished runs (branches are kept)
    mp-agent stop [--run DIR]            stop the running run (its work so far is kept)
    mp-agent keep [--run DIR]            merge a finished run's branch into the project
    mp-agent discard [--run DIR]         delete a finished run's branch
    mp-agent selftest                    check everything works end to end (~2 minutes)
    mp-agent resume [--run DIR]          carry on a stopped or crashed run where it left off
    mp-agent again [--run DIR] [--same-models]   do a finished run's task again
    mp-agent setup [--json]              what is installed and set up, API keys, presets you can use
    mp-agent keys list | set P | remove P   API keys, kept in the system keychain
    mp-agent test MODEL [--json]         one tiny call, to check a model works
    mp-agent launchers [--install]       /mp-agent in Claude Code, Codex, Gemini CLI, OpenCode, Qwen Code and Cline
    mp-agent app [--install]             an app icon that opens the workshop without a terminal
    mp-agent protect [add|remove OWNER[/REPO]]   repositories that are never pushed to
    mp-agent rules [PATH] [on|off]       the project's CLAUDE.md, AGENTS.md and the like, given to every role
    mp-agent mcp [list|add|remove|on|off]   the MCP servers every role is given
    mp-agent pagecheck FILE|URL [--query Q]  load a web page headless; fail if its JavaScript throws
    mp-agent models [--json]             the models available for each role, the presets, the current choices
    mp-agent where [--json] [--refresh]  where a job can run: recent local projects, GitHub repos, new, one-off
    mp-agent pr [--run DIR] [--base BRANCH]   push a finished run's branch and open a GitHub pull request
    mp-agent github [--repo DIR] [--name N] [--owner O] [--public]   put a finished project on GitHub
    mp-agent queue add [start flags] "task" | list | remove ID | run-next   jobs that run one after another
    mp-agent history [--json]            where the time and money went, and which judges object
    mp-agent bench [--list] [--task T] [--combo "worker=X,judge=Y"] [--repeat N]   compare model line-ups
    mp-agent bench table | suggest       the last comparison's table, or what it recommends
    mp-agent pregate [DIR]               what the pre-gate thought, against what the panel said
    mp-agent tidy [--yes] [--all-runs]   clear mp-agent's own leftovers (never your projects or branches)
    mp-agent config [--project [PATH]] --preset P | --planner|--worker|--judge NAME | --max-usd N
                    | --claude-billing subscription|api   change the defaults
"""
import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import desktop, gitops, models, where as places
from .context import Context, Options, notify
from .contract import Decisions
from .orchestrator import Orchestrator, start_visualizer
from . import providers
from .providers import Pacer, Retrying, make_agent
from .runlog import Run

HOME = os.path.expanduser("~")
STATE = os.environ.get("MP_HOME", os.path.join(HOME, ".mp-agent"))
RUNS = os.environ.get("MP_RUNS", os.path.join(STATE, "runs"))
TREES = os.environ.get("MP_TREES", os.path.join(STATE, "worktrees"))
PACE = os.environ.get("MP_PACE", os.path.join(STATE, "pace"))


def env(name, default, cast=str):
    value = os.environ.get(name)
    return default if value in (None, "") else cast(value)


ROLE_HELP = {
    "planner": "who plans the work and rules on disagreements, e.g. opus",
    "worker": "the model that does the work, e.g. mercury, sonnet, gpt-5.5",
    "reviewer": "the quick review after each passing check ('same' = the workers' model)",
    "panel": "the pre-audit panel ('same' = the workers' model)",
    "designer": "how the work looks, when a job needs a designer ('same' = chosen for you, Claude first)",
    "judge": "the final judge, e.g. opus",
}


def role_dest(role):
    """--panel is how many lenses the panel has, so the panel's model is --panel-model."""
    return "panel_model" if role == "panel" else role


def add_role_flags(p, from_env=False):
    for role in models.ROLES:
        flag = "--panel-model" if role == "panel" else f"--{role}"
        names = [flag, "--auditor"] if role == "judge" else [flag]
        default = (os.environ.get(f"MP_{role_dest(role).upper()}") or None) if from_env else None
        p.add_argument(*names, dest=role_dest(role), default=default,
                       help=f"{ROLE_HELP[role]} (default: from mp-agent config)")


def given_roles(args):
    return {role: getattr(args, role_dest(role)) for role in models.ROLES if getattr(args, role_dest(role), None)}


def role_args(choices):
    """Flags that pass every chosen role on to a run."""
    out = []
    for role in models.ROLES:
        flag = "--panel-model" if role == "panel" else f"--{role}"
        out += [flag, choices.get(role) or "same"]
    return out


def parser():
    p = argparse.ArgumentParser(
        prog="mp-agent", allow_abbrev=False,
        description="Keeps working on a coding task until the checks, a quick reviewer, a pre-audit panel and a "
                    "final judge all agree. A planner model plans the work and decides whether to split it into "
                    "parallel subtasks for the workers. It keeps going while it makes progress; when it stops "
                    "making progress it asks the planner for a ruling, then a re-plan, then asks you.",
        epilog='Also: mp-agent answer "text"  |  mp-agent status [-f]  |  mp-agent clean [--yes]   '
               "Watch: http://127.0.0.1:7788 (opens by itself; MP_VIZ=0 to skip)")
    p.add_argument("task", nargs="+", help="what to do")
    p.add_argument("--repo", "--folder", dest="folder", help="project folder (default: current directory; "
                   "with --new, a fresh folder under ~/mp-projects)")
    p.add_argument("--new", action="store_true", help="start a new project in ~/mp-projects/<task> instead of "
                   "the current directory")
    p.add_argument("--init", action="store_true", help="allow setting up git in a folder that has files but no git")
    p.add_argument("--check", help="validation command (default: the planner chooses)")
    p.add_argument("--workers", type=int, default=env("MP_WORKERS", 3, int), help="parallel subtasks (default 3)")
    p.add_argument("--patience", type=int, default=env("MP_PATIENCE", None, int),
                   help="unchanged passes before escalating (default: from the preset, else 3)")
    p.add_argument("--churn", type=int, default=env("MP_CHURN", None, int),
                   help="unapproved rounds in a row before escalating (default: from the preset, else 8)")
    p.add_argument("--budget", type=float, default=env("MP_BUDGET", 180.0, float),
                   help="safety net in working minutes, time waiting for you excluded (default 180)")
    p.add_argument("--max-calls", type=int, default=env("MP_MAX_CALLS", 800, int),
                   help="safety net on total model calls (default 800)")
    p.add_argument("--max-usd", type=float, default=None,
                   help="spending cap on real money per job (API-billed models; subscriptions are not counted); "
                        "when reached it asks before carrying on (default: from mp-agent config, none)")
    p.add_argument("--panel", type=int, default=env("MP_PANEL", None, int),
                   help="pre-audit panel lenses 0-3 (default: from the preset, else 3)")
    p.add_argument("--no-critic", action="store_true", help="skip the fast reviewer")
    p.add_argument("--no-audit", action="store_true", help="skip the final auditor")
    add_role_flags(p, from_env=True)
    design = p.add_mutually_exclusive_group()
    design.add_argument("--design", dest="design", action="store_const", const="on",
                        help="settle how it looks first, whatever the planner thinks")
    design.add_argument("--no-design", dest="design", action="store_const", const="off",
                        help="no designer on this job")
    p.add_argument("--no-plan", action="store_true", help="skip planning: one unit, the task as its contract")
    shape = p.add_mutually_exclusive_group()
    shape.add_argument("--solo", dest="shape", action="store_const", const="solo",
                       help="one worker on the whole task (default: the planner decides)")
    shape.add_argument("--swarm", dest="shape", action="store_const", const="swarm",
                       help="split the task into parts built in parallel (default: the planner decides)")
    p.add_argument("--no-ask", action="store_true", help="never wait for you; stop NOT approved instead")
    p.add_argument("--skills", choices=("off", "workers"),
                   help="let the builders load the skills installed on this machine (Claude Code only)")
    p.add_argument("--alone", action="store_true",
                   help="settle what the job can by itself (a model out of credit, workers that need upgrading); "
                        "a real question still waits for you")
    p.add_argument("--unattended", choices=("switch", "stop"), default=None,
                   help="with --no-ask, when a model cannot be used at all: switch to another and carry on "
                        "(default), or stop")

    p.add_argument("--timeout", type=int, default=env("MP_TIMEOUT", 900, int), help="per call, seconds")
    p.add_argument("--min-interval", type=float, default=env("MP_MIN_INTERVAL", 3.0, float),
                   help="shortest gap between call starts per provider; widens by itself when a provider "
                        "refuses (default 3)")
    p.add_argument("--keep", action="store_true", help="keep the worktree after success")
    p.add_argument("--resume-run", help=argparse.SUPPRESS)
    p.add_argument("--oneoff", action="store_true", help=argparse.SUPPRESS)
    return p


def answer(argv):
    p = argparse.ArgumentParser(prog="mp-agent answer")
    p.add_argument("text", nargs="+")
    p.add_argument("--run", help="run folder (default: the run that is waiting)")
    args = p.parse_args(argv)
    target = args.run
    if not target:
        waiting = []
        for name in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
            if os.path.exists(os.path.join(RUNS, name, "question.json")):
                waiting.append(os.path.join(RUNS, name))
        if not waiting:
            print("no run is waiting for an answer", file=sys.stderr)
            return 1
        if len(waiting) > 1:
            print("more than one run is waiting; pass --run:\n  " + "\n  ".join(waiting), file=sys.stderr)
            return 1
        target = waiting[0]
    with open(os.path.join(target, "question.json")) as fh:
        q = json.load(fh)
    tmp = os.path.join(target, "answer.json.tmp")
    with open(tmp, "w") as fh:
        json.dump({"answer": " ".join(args.text)}, fh)
    os.replace(tmp, os.path.join(target, "answer.json"))
    print(f"answered: {q.get('question')}")
    return 0


def installed_models():
    """Every model usable here, with the stored API keys counted."""
    from . import keys
    return models.available(key_env=keys.environment(STATE),
                            claude_billing=models.load_extra(STATE).get("claude_billing") or "subscription")


def choose_models(given=None, options=None, project=None):
    """Chosen names (flags or config) → specs, checked. Returns (choices, options, problem).
    given: {role: name} from flags; then the project's own roles, if it has any; then the
    general config. For the reviewer and panel, "same" (or nothing) means the workers' model."""
    given = given or {}
    config = {**models.load_config(STATE), **models.project_config(STATE, project)}
    options = options if options is not None else installed_models()
    choices = {}
    try:
        for role in models.ROLES:
            name = given.get(role) or config[role]
            if role in models.REQUIRED:
                choices[role] = models.resolve(name, options)
            else:
                same = not name or str(name).strip().lower() in ("same", "workers", "worker")
                choices[role] = models.SAME if same else models.resolve(name, options)
    except models.ChoiceError as exc:
        return None, options, str(exc)
    problems = models.check_independent(choices)
    return (None, options, "; ".join(problems)) if problems else (choices, options, None)


def roles_line(choices, options):
    return "; ".join(f"{role} {models.label_for(choices.get(role), options, role)}" for role in models.ROLES)


def list_models(argv):
    p = argparse.ArgumentParser(prog="mp-agent models")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    options = models.with_problems(STATE, installed_models())
    config = models.load_config(STATE)
    extra = models.load_extra(STATE)
    found = models.presets(options)
    if args.json:
        print(json.dumps({"available": options, "chosen": config, "builtin": models.BUILTIN, "roles": models.ROLES,
                          "presets": found, "preset": extra.get("preset"), "tuning": models.load_tuning(STATE),
                          "upgrade": models.load_upgrade(STATE),
                          "unattended": models.load_unattended(STATE),
                          "pregate": models.load_pregate(STATE),
                          "skills": models.load_skills(STATE),
                          "max_usd": float(extra.get("max_usd") or 0)}))
        return 0
    for role in models.ROLES:
        print(f"{role:9} {models.label_for(config[role], options, role)}" + (f"  [{config[role]}]" if config[role] else ""))
    tuning = models.load_tuning(STATE)
    print(f"\nloop      panel of {tuning['panel_size']}, escalates after {tuning['patience']} unchanged passes or "
          f"{tuning['churn']} unapproved rounds")
    print("\npresets:")
    for preset in found:
        mark = "*" if extra.get("preset") == preset["id"] else " "
        print(f" {mark} {preset['id']:8} {preset['name']}: {preset['about']}")
        if preset.get("measured"):
            m = preset["measured"]
            money = f", ${m['billed_per_run']:.2f} a task billed" if m["billed_per_run"] else ", no API bills"
            print(f"            measured {models.MEASURED_ON}: worked {m['works']}/{m['runs']}, "
                  f"{m['minutes']:g} min a task, {m['passes']:g} passes{money}")
        print("            " + (roles_line(preset["roles"], options) if preset["roles"]
                                 else "needs " + " and ".join(preset["missing"])))
    print("\navailable (any can take any role; the planner and judge must differ from the workers):")
    for o in options:
        print(f"  {o['spec']:34} {o['label']}")
    print("\nchange with: mp-agent config --preset claude   or   mp-agent config --judge opus --reviewer same")
    return 0


def set_config(argv):
    p = argparse.ArgumentParser(prog="mp-agent config", allow_abbrev=False)
    add_role_flags(p)
    p.add_argument("--preset", help="choose every role and the loop settings from a preset (see mp-agent models)")
    p.add_argument("--max-usd", type=float, help="default spending cap per job in dollars (0 = none)")
    p.add_argument("--unattended", choices=("switch", "stop"),
                   help="jobs with nobody watching: switch to another model when one cannot be used, or stop")
    p.add_argument("--skills", choices=("off", "workers"),
                   help="whether the builders may load the skills installed on this machine")
    p.add_argument("--pre-gate", choices=("off", "watch", "on"),
                   help="a cheap calibrated look before the panel: off, watch (ask and record, convene everyone), "
                        "or on (skip a member it is confident about)")
    p.add_argument("--pre-gate-threshold", type=float,
                   help="how sure the pre-gate must be to skip a member (0.5-1.0, default 0.9)")
    p.add_argument("--project", nargs="?", const=".",
                   help="set these models for one project only (default: this folder); "
                        "--project PATH --preset none forgets them")
    p.add_argument("--when-stuck", choices=("ask", "auto", "never"),
                   help="when the workers get stuck: offer a stronger model (ask), switch automatically (auto), or never")
    p.add_argument("--upgrade-to", help="with --when-stuck auto: the model to switch to (default: the strongest available)")
    p.add_argument("--max-upgrades", type=int, help="how many times one job may switch to a stronger worker (default 1)")
    p.add_argument("--claude-billing", choices=("subscription", "api"),
                   help="how Claude Code is paid for: your Claude login (default) or your stored Anthropic API key")
    args = p.parse_args(argv)
    if args.skills:
        models.save_extra(STATE, {"skills": args.skills})
        print("skills: " + ("the builders may load them" if args.skills == "workers" else "off"))
    if args.pre_gate or args.pre_gate_threshold is not None:
        current = models.load_pregate(STATE)
        if args.pre_gate:
            current["mode"] = args.pre_gate
        if args.pre_gate_threshold is not None:
            current["threshold"] = min(1.0, max(0.5, args.pre_gate_threshold))
        models.save_extra(STATE, {"pregate": current})
        print(f"pre-gate: {current['mode']}, threshold {current['threshold']:g}")
    if args.unattended:
        models.save_extra(STATE, {"unattended": args.unattended})
        print("jobs with nobody watching: " + ("switch to another model when one cannot be used"
                                               if args.unattended == "switch" else "stop"))
    if args.when_stuck or args.upgrade_to is not None or args.max_upgrades is not None:
        current = models.load_upgrade(STATE)
        if args.when_stuck:
            current["mode"] = args.when_stuck
        if args.upgrade_to is not None:
            current["to"] = models.resolve(args.upgrade_to, installed_models()) if args.upgrade_to else ""
        if args.max_upgrades is not None:
            current["max"] = max(0, args.max_upgrades)
        models.save_extra(STATE, {"upgrade": current})
        print(f"when stuck: {current['mode']}" + (f", to {current['to']}" if current["to"] else "") + f", at most {current['max']} per job")
        if not (args.claude_billing or args.max_usd is not None or args.preset or given_roles(args)):
            return 0
    if args.claude_billing:
        models.save_extra(STATE, {"claude_billing": args.claude_billing})
        print(f"claude_billing  {args.claude_billing}")
        if not (args.max_usd is not None or args.preset or given_roles(args)):
            return 0
    if args.max_usd is not None:
        models.save_extra(STATE, {"max_usd": max(0.0, args.max_usd)})
        print(f"max_usd   {max(0.0, args.max_usd):g}" + (" (no cap)" if not args.max_usd else ""))
    options = installed_models()
    if args.project:
        return set_project_config(args, options)
    if args.preset:
        chosen, problem = models.apply_preset(STATE, args.preset, options)
        if problem:
            print(f"not changed: {problem}", file=sys.stderr)
            return 1
        print(f"preset    {args.preset}")
    given = given_roles(args)
    if not given:
        if args.preset:
            for role, spec in models.load_config(STATE).items():
                print(f"{role:9} {models.label_for(spec, options, role)}")
        return 0
    choices, _, problem = choose_models(given, options)
    if problem:
        print(f"not changed: {problem}", file=sys.stderr)
        return 1
    saved = models.save_config(STATE, choices)
    models.save_extra(STATE, {"preset": None})      # hand-picked now
    for role in models.ROLES:
        print(f"{role:9} {saved[role] or 'same as the workers'}")
    return 0


def set_project_config(args, options):
    """One project's own models: what a job started there uses unless it is told otherwise."""
    project = gitops.toplevel(os.path.abspath(os.path.expanduser(args.project))) or \
        os.path.abspath(os.path.expanduser(args.project))
    if not os.path.isdir(project):
        print(f"not changed: there is no folder {project}", file=sys.stderr)
        return 1
    if (args.preset or "").lower() in ("none", "off", "forget"):
        models.save_project_config(STATE, project, {})
        print(f"{project}: back to your usual models")
        return 0
    given = given_roles(args)
    if args.preset:
        preset = next((p for p in models.presets(options) if p["id"] == args.preset), None)
        if preset is None or not preset["roles"]:
            problem = f"no preset called '{args.preset}'" if preset is None else \
                f"the {preset['name']} preset needs " + " and ".join(preset["missing"])
            print(f"not changed: {problem}", file=sys.stderr)
            return 1
        given = {role: spec for role, spec in preset["roles"].items() if spec}
    if not given:
        kept = models.project_config(STATE, project)
        print(f"{project}: " + (", ".join(f"{r} {s}" for r, s in sorted(kept.items())) if kept
                                else "no models of its own; it uses your usual ones"))
        return 0
    choices, _, problem = choose_models(given, options)
    if problem:
        print(f"not changed: {problem}", file=sys.stderr)
        return 1
    kept = models.save_project_config(STATE, project, {r: choices[r] for r in given})
    print(f"{project}: " + ", ".join(f"{r} {s}" for r, s in sorted(kept.items())))
    print("jobs started here use these unless the job says otherwise; forget them with: "
          f"mp-agent config --project {project} --preset none")
    return 0


def list_places(argv):
    p = argparse.ArgumentParser(prog="mp-agent where")
    p.add_argument("--json", action="store_true")
    p.add_argument("--refresh", action="store_true", help="re-read the GitHub repo list now")
    p.add_argument("--cwd", default=os.getcwd())
    args = p.parse_args(argv)
    here = gitops.toplevel(args.cwd)
    here = here if here and os.path.realpath(here) != os.path.realpath(HOME) else None
    local = places.local_repos(HOME)
    remote = places.github_repos(STATE, refresh=args.refresh)
    clones = {r["github"].lower(): r["path"] for r in places.local_repos(HOME, limit=500) if r["github"]}
    for r in remote:
        r["local"] = clones.get(r["github"].lower())
    if args.json:
        print(json.dumps({"here": here, "local": local, "github": remote, "home": HOME}))
        return 0
    if here:
        print(f"This folder:  {here}")
    print("\nRecent local projects:")
    for i, r in enumerate(local, 1):
        flags = (" (uncommitted changes)" if r["dirty"] else "") + (" (protected: never pushed)" if r["protected"] else "")
        print(f"  L{i:<2} ~/{r['name']}  [{r['branch']}]{flags}")
    print("\nYour GitHub repos (most recently pushed):")
    for i, r in enumerate(remote[:15], 1):
        where_now = f"local copy: {r['local']}" if r["local"] else "not cloned yet"
        print(f"  G{i:<2} {r['github']}  ({where_now})")
    print("\nOr: 'new' for a new project, or 'one-off' for a throwaway job whose result is kept in its own folder.")
    return 0


def read_task(words):
    if words:
        return " ".join(words).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def start(argv):
    """Work out where the task should run, launch it detached, open the workshop,
    and return within seconds. Cline kills commands after 30 seconds, so nothing
    here may wait for the work itself."""
    p = argparse.ArgumentParser(prog="mp-agent start", allow_abbrev=False)
    p.add_argument("task", nargs="*", help="the task (or pass it on stdin)")
    p.add_argument("--new", action="store_true", help="build it in a new folder under ~/mp-projects")
    p.add_argument("--init", action="store_true", help="set up git in this folder even though it has files")
    add_role_flags(p)
    p.add_argument("--repo", help="work in this local project instead of the current folder")
    p.add_argument("--github", help="owner/repo: pull the latest (or clone it) and work there")
    p.add_argument("--oneoff", action="store_true", help="a throwaway project; the approved result is kept in its folder")
    p.add_argument("--name", help="folder name for --new or --oneoff")
    p.add_argument("--max-usd", help="spending cap for this job in dollars")
    # the run's own options that take a value, passed on as they are (unknown flags
    # without a value, such as --no-critic, pass through by themselves)
    forwarded = ("--panel", "--patience", "--churn", "--budget", "--max-calls", "--timeout", "--min-interval",
                 "--workers", "--check")
    for flag in forwarded:
        p.add_argument(flag, dest="fwd_" + flag[2:].replace("-", "_"))
    args, passthrough = p.parse_known_args(argv)
    for flag in forwarded:
        value = getattr(args, "fwd_" + flag[2:].replace("-", "_"))
        if value is not None:
            passthrough = [*passthrough, flag, value]
    if args.max_usd not in (None, ""):
        try:
            passthrough = [*passthrough, "--max-usd", str(float(args.max_usd))]
        except ValueError:
            print(f"NEEDS: the spending cap must be a number, not '{args.max_usd}'")
            return 3
    task = read_task(args.task)
    if not task:
        print("NEEDS: a task to do. Say what you want built or fixed.")
        return 3
    # a project with its own models is honoured here too, so what is printed is what runs
    here = args.repo or (None if (args.github or args.new or args.oneoff) else os.getcwd())
    choices, options, problem = choose_models(given_roles(args), project=here)
    if problem:
        print(f"NEEDS: {problem}")
        return 3
    passthrough = [*passthrough, *role_args(choices)]

    cwd = os.path.abspath(os.path.expanduser(args.repo)) if args.repo else os.getcwd()
    if args.github:
        try:
            cwd = places.prepare_github(args.github, HOME, say=lambda m: print(m))
        except gitops.SetupError as exc:
            print(f"NEEDS: {exc}")
            return 3
    top = gitops.toplevel(cwd)
    home = os.path.realpath(HOME)
    folder, where = cwd, "your project, on a separate branch"
    if args.oneoff:
        from datetime import datetime
        name = args.name or f"oneoff-{datetime.now().strftime('%Y%m%d-%H%M')}-{gitops.slugify(task, 24)}"
        folder, where = os.path.join(HOME, "mp-projects", gitops.slugify(name, 60)), "a one-off; the result is kept in its folder"
        passthrough = [*passthrough, "--oneoff"]
    elif args.new:
        folder = os.path.join(HOME, "mp-projects", gitops.slugify(args.name, 60)) if args.name else None
        where = "a new project"
    elif top and os.path.realpath(top) != home:
        folder = top
        if gitops.is_dirty(top):
            print(f"NEEDS: {top} has uncommitted changes, and the agents only work from a clean state. Commit "
                  f"them first and ask again, or reply 'new project' to build this in a separate new folder.")
            return 3
    elif args.repo and not top:
        print(f"NEEDS: {cwd} is not a git project. Reply 'use this folder' to let the agents set up git there, "
              f"or 'new project' to build it in a separate new folder.")
        return 3
    elif os.path.realpath(cwd) == home or os.path.realpath(cwd) in ("/tmp", "/") or not os.listdir(cwd):
        folder, where = None, "a new project"
    elif not args.init:
        print(f"NEEDS: {cwd} is not a git project. Reply 'use this folder' to let the agents set up git here "
              f"(your files are not changed), or 'new project' to build it in a separate new folder.")
        return 3
    try:
        repo = gitops.setup_project(folder, task, init=args.init, home=HOME, say=lambda *_: None)
    except gitops.SetupError as exc:
        print(f"NEEDS: {exc}")
        return 3

    run_dir = launch([sys.executable, ENTRY, "--repo", repo, *passthrough, task], repo)
    if run_dir is None:
        return 3
    print(f"Started: {task}")
    print(f"Working in: {repo} ({where})")
    print(f"Models: {roles_line(choices, options)}")
    print(f"{models.label_for(choices['planner'], options)} is planning it now. "
          "Watch it live at http://127.0.0.1:7788 (opened in your browser);")
    print("any question and the final result will appear there, with a desktop notification.")
    print(f"Run: {run_dir}")
    return 0


ENTRY = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin", "mp-agent")


def launch(command, cwd):
    """Start a run detached from this process, open the workshop, and return the
    new run's folder once it exists (or None, having printed why)."""
    os.makedirs(STATE, exist_ok=True)
    log_path = os.path.join(STATE, "last-start.log")
    offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    with open(log_path, "a") as log:
        child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    start_visualizer(None)
    run_dir = None
    deadline = time.time() + 20
    while time.time() < deadline and run_dir is None:
        if child.poll() is not None:
            break
        for name in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
            try:
                with open(os.path.join(RUNS, name, "pid")) as fh:
                    if int(fh.read().strip()) == child.pid:
                        run_dir = os.path.join(RUNS, name)
                        break
            except (OSError, ValueError):
                continue
        time.sleep(0.2)
    if run_dir is None:
        with open(log_path, errors="replace") as fh:
            fh.seek(offset)            # only what this launch wrote
            said = fh.read().strip()
        print(f"NEEDS: the agents could not start. {said[-800:] or 'See ' + log_path}")
    return run_dir


def resumable(run_dir):
    """Why a run cannot be resumed, or None if it can."""
    if not os.path.exists(os.path.join(run_dir, "plan.json")):
        return "it stopped before its plan was made; start the task again instead"
    try:
        with open(os.path.join(run_dir, "pid")) as fh:
            os.kill(int(fh.read().strip()), 0)
        return "it is still running"
    except (OSError, ValueError):
        pass
    meta_path = os.path.join(run_dir, "metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path) as fh:
            meta = json.load(fh)
        if meta.get("approved"):
            return "it already finished and was approved"
        if meta.get("resolution"):
            return f"it was already {meta['resolution']['action']}"
        if meta.get("resumed_as"):
            return f"it was already resumed as {os.path.basename(meta['resumed_as'])}"
    return None


def again(argv):
    """Start a finished run's task again, in the same project, with the same models unless told otherwise."""
    p = argparse.ArgumentParser(prog="mp-agent again", description=again.__doc__)
    p.add_argument("--run", help="run folder (default: the most recent finished run)")
    add_role_flags(p)
    p.add_argument("--same-models", action="store_true", help="use the models that run used, not your current ones")
    args, passthrough = p.parse_known_args(argv)
    target = args.run
    if not target:
        for name in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
            if os.path.exists(os.path.join(RUNS, name, "metadata.json")):
                target = os.path.join(RUNS, name)
                break
    if not target or not os.path.isdir(target):
        print("NEEDS: no finished run to repeat", file=sys.stderr)
        return 3
    try:
        with open(os.path.join(target, "metadata.json")) as fh:
            meta = json.load(fh)
        task = open(os.path.join(target, "task.md")).read().strip()
    except (OSError, ValueError):
        print(f"NEEDS: {os.path.basename(target)} cannot be read", file=sys.stderr)
        return 3
    project = meta.get("project") or ""
    if not os.path.isdir(project):
        print(f"NEEDS: {project or 'that project'} is not here any more", file=sys.stderr)
        return 3
    flags = ["--repo", project]
    if args.same_models and not given_roles(args):
        try:
            with open(os.path.join(target, "roles.json")) as fh:
                for role, spec in json.load(fh).items():
                    if role in models.ROLES and spec:
                        flags += ["--panel-model" if role == "panel" else f"--{role}", str(spec)]
        except (OSError, ValueError):
            pass
    chosen = given_roles(args)
    if chosen:
        flags += role_args(chosen)
    return start([*flags, *passthrough, task])


def resume(argv):
    p = argparse.ArgumentParser(prog="mp-agent resume")
    p.add_argument("--run", help="run folder (default: the most recent run that can be resumed)")
    args, passthrough = p.parse_known_args(argv)
    target = args.run
    if not target:
        for name in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
            if resumable(os.path.join(RUNS, name)) is None:
                target = os.path.join(RUNS, name)
                break
    if not target:
        print("NEEDS: there is no stopped run to resume")
        return 3
    why = resumable(target)
    if why:
        print(f"NEEDS: cannot resume {os.path.basename(target)}: {why}")
        return 3
    repo = open(os.path.join(target, "repo")).read().strip()
    task = open(os.path.join(target, "task.md")).read().strip()
    run_dir = launch([sys.executable, ENTRY, "--resume-run", target, *passthrough, task], repo)
    if run_dir is None:
        return 3
    print(f"Resumed: {task}")
    print(f"Carrying on from {os.path.basename(target)}; settled work is kept. Watch it at http://127.0.0.1:7788")
    print(f"Run: {run_dir}")
    return 0


def live_runs():
    found = []
    for name in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
        try:
            with open(os.path.join(RUNS, name, "pid")) as fh:
                pid = int(fh.read().strip())
            os.kill(pid, 0)
            if "mp-agent" not in desktop.command_of(pid):
                continue
            found.append((os.path.join(RUNS, name), pid))
        except (OSError, ValueError):
            continue
    return found


def stop(argv):
    p = argparse.ArgumentParser(prog="mp-agent stop")
    p.add_argument("--run", help="run folder (default: the one running)")
    args = p.parse_args(argv)
    live = live_runs()
    if args.run:
        live = [(d, pid) for d, pid in live if os.path.realpath(d) == os.path.realpath(args.run)]
    if not live:
        print("nothing is running")
        return 1
    if len(live) > 1:
        print("more than one run is going; pass --run:\n  " + "\n  ".join(d for d, _ in live), file=sys.stderr)
        return 1
    run_dir, pid = live[0]
    os.kill(pid, signal.SIGTERM)
    print(f"stopping {os.path.basename(run_dir)}; its work so far is kept")
    return 0


def finished_run(arg):
    if arg:
        return arg
    for name in sorted(os.listdir(RUNS), reverse=True) if os.path.isdir(RUNS) else []:
        path = os.path.join(RUNS, name)
        if os.path.exists(os.path.join(path, "metadata.json")):
            return path
    return None


def resolve(argv, which):
    from . import actions
    p = argparse.ArgumentParser(prog=f"mp-agent {which}")
    p.add_argument("--run", help="run folder (default: the most recent finished run)")
    args = p.parse_args(argv)
    run_dir = finished_run(args.run)
    if not run_dir:
        print("no finished run found", file=sys.stderr)
        return 1
    try:
        print((actions.keep if which == "keep" else actions.discard)(run_dir))
        return 0
    except actions.ActionError as exc:
        print(f"not done: {exc}", file=sys.stderr)
        return 1


SELFTEST_TASK = "Fix add() in calc.py so python3 test_calc.py prints ok"


def github_command(argv):
    """Put a project on GitHub: create the repository and push, or push to the remote it has.
    Nothing is created until you say so, and it refuses rather than guesses."""
    from . import ghrepo
    p = argparse.ArgumentParser(prog="mp-agent github", description=github_command.__doc__)
    p.add_argument("--repo", default=os.getcwd(), help="the project (default: this folder)")
    p.add_argument("--name", help="what to call it (default: the folder's name)")
    p.add_argument("--owner", help="your account or one of your organisations (default: your account)")
    p.add_argument("--public", action="store_true", help="anyone can see it (default: private)")
    p.add_argument("--yes", action="store_true", help="do it without asking")
    args = p.parse_args(argv)
    plan = ghrepo.plan(args.repo, STATE)
    print(f"project   {plan['project']}")
    print(f"branch    {plan['branch']}")
    for warning in plan["warnings"]:
        print(f"note      {warning}")
    if plan["stoppers"]:
        for stopper in plan["stoppers"]:
            print(f"NEEDS: {stopper}", file=sys.stderr)
        return 3
    if plan["action"] == "push":
        print(f"remote    {plan['remote']} (nothing new is created)")
    else:
        print(f"new       {(args.owner + '/') if args.owner else ''}{args.name or plan['name']}"
              f" ({'public' if args.public else 'private'})")
    if not args.yes:
        answer = input("go ahead? [y/N] ").strip().lower() if sys.stdin.isatty() else "n"
        if answer not in ("y", "yes"):
            print("nothing was done")
            return 0
    if plan["action"] == "push":
        detail, problem = ghrepo.push(args.repo)
    else:
        url, problem = ghrepo.create(args.repo, args.name or plan["name"], owner=args.owner,
                                     private=not args.public)
        detail = f"created {url}" if url else None
    if problem:
        print(f"not done: {problem}", file=sys.stderr)
        return 1
    print(detail)
    return 0


def pregate_report(argv):
    """mp-agent pregate [RUNS_DIR...] — what the pre-gate thought, against what the panel said.
    mp-agent pregate backtest [RUNS_DIR] — ask it again about passes that already happened."""
    from . import fastjudge
    if argv and argv[0] == "backtest":
        return pregate_backtest(argv[1:])
    folders = argv or [RUNS]
    rows = []
    for folder in folders:
        if glob.glob(os.path.join(folder, "*", "pass-*")):
            rows += fastjudge.readings(folder)
        else:
            for run in sorted(glob.glob(os.path.join(folder, "*"))):
                rows += fastjudge.readings(run)
    if not rows:
        print("no pre-gate readings yet: run some jobs with `mp-agent config --pre-gate watch`")
        return 0
    return show_pregate(rows)


def pregate_backtest(argv):
    """Replay the pre-gate against panel verdicts already on disk: a population of real
    objections, without running a single job. Used to check a change to the wording."""
    from concurrent.futures import ThreadPoolExecutor
    from . import fastjudge, keys
    folder = argv[0] if argv else RUNS
    key = keys.environment(STATE).get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        print("NEEDS: no TypeSafe key (mp-agent keys set typesafe)", file=sys.stderr)
        return 3
    work = fastjudge.past_passes(folder)
    if not work:
        print(f"no past panel passes with stored prompts under {folder}")
        return 0
    print(f"asking again about {len(work)} past pass(es)…")

    def judge(case):
        _, prompt, verdicts = case
        probabilities = fastjudge.ask(fastjudge.state_from_prompt(prompt),
                                      fastjudge.questions(list(verdicts)), key)
        return [{"lens": lens, "probability": probabilities[lens], "objected": objected}
                for lens, objected in verdicts.items() if lens in probabilities]

    rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for got in pool.map(judge, work):
            rows += got
    return show_pregate(rows)


def show_pregate(rows):
    from . import fastjudge
    if not rows:
        print("nothing was judged")
        return 0
    apart = fastjudge.separation(rows)
    objected = len([r for r in rows if r["objected"]])
    print(f"\n{len(rows)} member(s) judged, {objected} of them objected\n")
    print(f"mean probability when the member approved: {apart['approved']}")
    print(f"mean probability when the member objected: {apart['objected']}")
    print(f"gap (higher is better; at or below zero the pre-gate is worthless): {apart['gap']}\n")
    print(f"{'threshold':>10} {'skipped':>9} {'of members':>11} {'would have objected':>21}")
    for row in fastjudge.curve(rows):
        print(f"{row['threshold']:>10} {row['skipped']:>9} {row['saved'] * 100:>10.0f}% {row['missed']:>21}")
    print("\n'would have objected' is the only number that decides it: a skipped member that "
          "would have asked for changes\nis work that would have shipped unreviewed.")
    for lens in sorted({r["lens"] for r in rows}):
        mine = [r for r in rows if r["lens"] == lens]
        bad = sorted(r["probability"] for r in mine if r["objected"])
        print(f"\n{lens}: {len(mine)} member(s); objected at {bad if bad else 'never'}")
    return 0


def bench(argv):
    """Compare model line-ups on the same bench tasks, graded by tests the agents never see."""
    from . import bench as benchmark
    if argv and argv[0] == "suggest":
        folder = argv[1] if len(argv) > 1 else newest_bench()
        if not folder:
            print("NEEDS: no bench results yet; run: mp-agent bench --preset fast", file=sys.stderr)
            return 3
        picks = benchmark.suggest(benchmark.load_results(folder))
        if not picks:
            print("no line-up finished a run in these results")
            return 0
        print(f"from {os.path.basename(folder)}:\n")
        for pick in picks:
            roles = " ".join(f"--{r if r != 'panel' else 'panel-model'} {spec}" for r, spec in sorted(pick["roles"].items()))
            if pick["for"] == "avoid":
                print(f"  avoid {pick['combo']}: worked {pick['works']} ({pick['why']})")
                continue
            money = f", ${pick['billed_per_run']:.2f} a task billed" if pick["billed_per_run"] else ", no API bills"
            print(f"  for {pick['for']}: {pick['combo']} — {pick['why']}{money}")
            print(f"      mp-agent config {roles}")
        return 0
    if argv and argv[0] == "table":
        folder = argv[1] if len(argv) > 1 else newest_bench()
        if not folder:
            print("NEEDS: no bench results yet", file=sys.stderr)
            return 3
        print(benchmark.table(benchmark.summarize(benchmark.load_results(folder))))
        return 0
    p = argparse.ArgumentParser(prog="mp-agent bench", description=bench.__doc__)
    p.add_argument("--list", action="store_true", help="the bench tasks and what each one is for")
    p.add_argument("--task", action="append", default=[], help="a task to run (default: all of them)")
    p.add_argument("--combo", action="append", default=[],
                   help='a line-up: "name=fast,worker=cline:inception:mercury-2.5,judge=claude:sonnet"')
    p.add_argument("--preset", action="append", default=[], help="a line-up taken from a preset, by its id")
    p.add_argument("--repeat", type=int, default=3, help="runs per task and line-up (default 3)")
    p.add_argument("--budget", type=float, default=45, help="working minutes per run (default 45)")
    p.add_argument("--max-calls", type=int, default=250, help="model calls per run (default 250)")
    p.add_argument("--out", help="where the results go (default: ~/.mp-agent/bench/<date>)")
    args = p.parse_args(argv)

    if args.list:
        for task in benchmark.listing():
            print(f"{task['name']:10} {task['about']}")
        return 0
    try:
        tasks = [benchmark.load_task(name) for name in (args.task or [t["name"] for t in benchmark.listing()])]
        combos = [benchmark.parse_combo(text) for text in args.combo]
    except ValueError as exc:
        print(f"NEEDS: {exc}", file=sys.stderr)
        return 3
    for preset in args.preset:
        roles, missing = models.resolve_preset(preset, installed_models())
        if not roles:
            print(f"NEEDS: preset {preset}: {missing}", file=sys.stderr)
            return 3
        combos.append({"name": preset, "roles": {r: spec for r, spec in roles.items() if spec}})
    if not tasks or not combos:
        print("NEEDS: give at least one --combo or --preset (and see --list for the tasks)", file=sys.stderr)
        return 3

    out = args.out or os.path.join(STATE, "bench", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    runs_dir = os.path.join(out, "runs")
    projects = os.path.join(out, "projects")
    os.makedirs(runs_dir, exist_ok=True)
    os.makedirs(projects, exist_ok=True)
    total = len(tasks) * len(combos) * max(1, args.repeat)
    print(f"{total} run(s): {len(tasks)} task(s) × {len(combos)} line-up(s) × {args.repeat}")
    print(f"results: {out}")
    done = 0
    for task in tasks:
        for combo in combos:
            for attempt in range(1, max(1, args.repeat) + 1):
                done += 1
                slug = re.sub(r"[^a-z0-9]+", "-", f"{task['name']}-{combo['name']}-{attempt}".lower()).strip("-")
                print(f"[{done}/{total}] {slug}")
                row = benchmark.run_one(task, combo, os.path.join(projects, slug), runs_dir, ENTRY,
                                        budget=args.budget, max_calls=args.max_calls)
                row["attempt"] = attempt
                with open(os.path.join(out, slug + ".json"), "w") as fh:
                    json.dump(row, fh, indent=2)
    rows = benchmark.load_results(out)
    summary = benchmark.summarize(rows)
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    text = benchmark.table(summary)
    with open(os.path.join(out, "table.txt"), "w") as fh:
        fh.write(text + "\n")
    print("\n" + text)
    return 0


def newest_bench():
    folder = os.path.join(STATE, "bench")
    names = sorted(os.listdir(folder), reverse=True) if os.path.isdir(folder) else []
    return next((os.path.join(folder, n) for n in names if os.path.isdir(os.path.join(folder, n))), None)


def selftest(argv):
    """Everything this depends on can change under it (Cline updates itself;
    logins expire), so check the pieces, then do one tiny real task."""
    ok = True

    def check(label, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        print(f"  {'✓' if passed else '✗'} {label}{(': ' + detail) if detail else ''}")

    print("mp-agent selftest\n")
    print("the pieces")
    check("git", bool(shutil.which("git")))
    chosen, options, problem = choose_models()
    check("models chosen and available", chosen is not None, problem or roles_line(chosen, options))
    tools = sorted({spec.split(":")[0] for spec in models.effective(chosen or {}).values() if spec})
    commands = {"claude": "claude", "codex": "codex", "cline": "cline", "qwen": "qwen", "gemini": "gemini",
                "antigravity": "agy", "opencode": "opencode"}
    for tool in tools:
        check(f"{tool} is installed", bool(shutil.which(commands.get(tool, tool))))
    if "claude" in tools and shutil.which("claude"):
        status = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, env=providers.claude_env())
        try:
            auth = json.loads(status.stdout)
        except ValueError:
            auth = {}
        check("claude logged in on a subscription", bool(auth.get("loggedIn")) and auth.get("authMethod") == "claude.ai",
              f"{auth.get('authMethod')} / {auth.get('subscriptionType')}" if auth else "no answer from claude")
    if chosen and chosen["worker"].startswith("cline:") and shutil.which("cline"):
        if providers.restart_stale_cline_hubs(print):
            time.sleep(2)
        # Cline's background process reads MCP settings when it starts, so a server
        # added later is invisible until that process restarts.
        probe = subprocess.run(["cline", "--cwd", tempfile.gettempdir(), "--provider", chosen["worker"].split(":")[1],
                                "--model", chosen["worker"].split(":", 2)[2], "--timeout", "90",
                                "List the names of the MCP tools you have whose names contain context7 or playwright, "
                                "comma separated, nothing else. If you have none, reply NONE."],
                               capture_output=True, text=True, timeout=150)
        seen = probe.stdout.lower()
        check("workers can use Context7 and Playwright (MCP)", "context7" in seen and "playwright" in seen,
              "" if "context7" in seen and "playwright" in seen else
              "not visible to Cline; run `cline mcp install context7 --yes -- npx -y @upstash/context7-mcp` (and "
              "playwright), then restart Cline's background process: pkill -f cline-hub-daemon")
    if not desktop.can_notify():
        print("  · no desktop notifications (notify-send is not installed); questions still show in the workshop")
    check("workshop (mp-viz)", bool(shutil.which("mp-viz")))
    if not ok:
        print("\nfix the ✗ items above, then run the selftest again")
        return 1

    print(f"\none tiny real task with your chosen models ({roles_line(chosen, options)}), all gates\n")
    folder = tempfile.mkdtemp(prefix="mp-selftest-")
    with open(os.path.join(folder, "calc.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a - b\n")
    with open(os.path.join(folder, "test_calc.py"), "w") as fh:
        fh.write('from calc import add\nassert add(2, 3) == 5\nprint("ok")\n')
    gitops.git(folder, "init", "-q", "-b", "main")
    gitops.git(folder, "add", "-A")
    gitops.git(folder, "commit", "-q", "-m", "selftest start")
    started = time.time()
    code = main(["--repo", folder, *argv, SELFTEST_TASK])
    latest = finished_run(None)
    meta = {}
    if latest:
        with open(os.path.join(latest, "metadata.json")) as fh:
            meta = json.load(fh)
    counters = meta.get("counters") or {}
    costs = meta.get("costs") or {}
    print("\nresult")
    check("approved", code == 0, meta.get("outcome", ""))
    print(f"    {int(time.time() - started)}s in all, {int(counters.get('queue_seconds', 0))}s queueing; "
          f"{providers.cost_line(costs) if 'by_model' in costs else ''}")
    shutil.rmtree(folder, ignore_errors=True)
    print("\nall good" if ok else f"\nsomething is wrong; the logs are in {latest}")
    return 0 if ok else 1


def pagecheck_command(argv):
    from . import pagecheck
    p = argparse.ArgumentParser(prog="mp-agent pagecheck", allow_abbrev=False,
                                description="Load a web page in a headless browser; fail if its JavaScript throws.")
    p.add_argument("target", help="an HTML file (served from its folder) or a URL")
    p.add_argument("--query", action="append", default=[], help='added to the address, e.g. "?demo=1" (repeatable)')
    p.add_argument("--wait", type=int, default=5000, help="how long the page runs, in milliseconds (default 5000)")
    args = p.parse_args(argv)
    return pagecheck.check(args.target, args.query or [""], args.wait)


def mcp_command(argv):
    """mp-agent mcp list | add NAME (--command "..." | --url URL) [--env KEY] [--project PATH]
    | remove NAME [--project PATH] | on NAME | off NAME"""
    from . import mcp
    p = argparse.ArgumentParser(prog="mp-agent mcp", allow_abbrev=False)
    p.add_argument("action", nargs="?", default="list", choices=("list", "add", "remove", "on", "off"))
    p.add_argument("name", nargs="?")
    p.add_argument("--command", dest="server_command", help='how to start a local server, e.g. "npx -y @sentry/mcp-server"')
    p.add_argument("--url", help="the address of a remote server")
    p.add_argument("--env", action="append", default=[], help="an environment variable the server needs, e.g. SENTRY_TOKEN")
    p.add_argument("--project", help="only for this project (default: every project)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    project = os.path.realpath(os.path.expanduser(args.project)) if args.project else None
    try:
        if args.action == "add":
            if not args.name:
                raise ValueError("give the server a name")
            mcp.add(STATE, args.name, mcp.parse_spec(args.server_command, args.url, args.env), project)
            print(f"added {args.name}" + (f" for {project}" if project else " for every project"))
        elif args.action == "remove":
            print("removed" if mcp.remove(STATE, args.name or "", project) else "there was no such server")
        elif args.action in ("on", "off"):
            mcp.switch_builtin(STATE, args.name or "", args.action == "on")
            print(f"{args.name} {args.action}")
    except ValueError as exc:
        print(f"not changed: {exc}", file=sys.stderr)
        return 1
    rows = mcp.listing(STATE, SHOTS, project)
    if args.json:
        print(json.dumps({"servers": rows}))
        return 0
    if args.action == "list":
        for r in rows:
            print(f"  {'on ' if r['on'] else 'off'}  {r['name']:12} {r['scope']:14} {r['what']}")
        print("\nadd one: mp-agent mcp add sentry --command \"npx -y @sentry/mcp-server\" --env SENTRY_TOKEN")
    return 0


def rules_command(argv):
    """mp-agent rules [PATH] [on|off] [--json]: the project's own instructions every role is given."""
    from . import rules
    words = [a for a in argv if a != "--json"]
    path = os.path.abspath(os.path.expanduser(words[0])) if words and words[0] not in ("on", "off") else os.getcwd()
    project = gitops.toplevel(path) or path
    switch = next((w for w in words if w in ("on", "off")), None)
    if switch:
        rules.set_enabled(STATE, project, switch == "on")
    found = rules.find(project)
    on = rules.enabled(STATE, project)
    if "--json" in argv:
        print(json.dumps({"project": project, "enabled": on,
                          "files": [{"path": rel, "text": text} for rel, text in found]}))
        return 0
    print(f"{project}: project rules are {'ON' if on else 'OFF'}")
    for rel, text in found:
        print(f"  {rel}  ({len(text)} characters)")
    if not found:
        print("  no rules files (CLAUDE.md, AGENTS.md, rules.md, .cursorrules, .clinerules and the like)")
    print(f"\nswitch with: mp-agent rules {project} {'off' if on else 'on'}")
    return 0


def protect_command(argv):
    """mp-agent protect [list | add OWNER[/REPO] | remove OWNER[/REPO]]: GitHub accounts or
    repositories that are never pushed to and never get a pull request."""
    action, rest = (argv[0], argv[1:]) if argv else ("list", [])
    current = places.protected(STATE)
    if action == "list":
        if "--json" in rest:
            print(json.dumps(current))
            return 0
        print("\n".join(f"  {p}" for p in current) or "  (nothing protected)")
        print("\nadd one: mp-agent protect add someone   or   mp-agent protect add someone/their-repo")
        return 0
    if action in ("add", "remove") and rest:
        value = rest[0].strip().strip("/")
        if action == "add":
            if not places.PROTECTED_RE.match(value):
                print("not changed: give a GitHub owner (someone) or a repository (someone/their-repo)", file=sys.stderr)
                return 1
            saved = places.set_protected(STATE, current + [value])
        else:
            if value.lower() not in current:
                print(f"not changed: {value} is not protected", file=sys.stderr)
                return 1
            saved = places.set_protected(STATE, [p for p in current if p != value.lower()])
        print("protected: " + (", ".join(saved) or "nothing"))
        return 0
    print("mp-agent protect [list | add OWNER[/REPO] | remove OWNER[/REPO]]", file=sys.stderr)
    return 2


def app_command(argv):
    """mp-agent app [--install]: an app icon that opens the workshop without a terminal."""
    from . import appicon
    viz = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin", "mp-viz")
    viz = os.path.realpath(viz)
    if "--install" not in argv:
        for name, path in appicon.paths(HOME).items():
            print(f"  {name:9} {path}")
        print("\nrun `mp-agent app --install` to add the icon")
        return 0
    for path in appicon.install(HOME, viz):
        print(f"  wrote {path}")
    where = "your applications (Launchpad and Spotlight)" if sys.platform == "darwin" else "your app launcher"
    print(f"\n{appicon.APP_NAME} is in {where}; it opens the workshop.")
    return 0


def launchers_command(argv):
    """mp-agent launchers [--install] [--print TOOL]: /mp-agent in each AI coding tool you have."""
    from . import launchers
    p = argparse.ArgumentParser(prog="mp-agent launchers", allow_abbrev=False)
    p.add_argument("--install", action="store_true", help="write them (default: show where they would go)")
    p.add_argument("--print", dest="show", metavar="TOOL", help="print one tool's launcher")
    args = p.parse_args(argv)
    if args.show:
        print(launchers.render(args.show))
        return 0
    if args.install:
        for tool, path, what in launchers.install(HOME):
            print(f"  {tool:9} {what}: {path}")
        return 0
    for tool, (path, present) in launchers.targets(HOME).items():
        print(f"  {tool:9} {'would install' if present else 'not installed, skipped'}: {path}")
    print("\nrun `mp-agent launchers --install` to write them")
    return 0


def keys_command(argv):
    """mp-agent keys list | set PROVIDER | remove PROVIDER. set reads the key from stdin
    (or asks without echoing), so it never appears in your shell history or process list."""
    import getpass
    from . import keys
    action, rest = (argv[0], argv[1:]) if argv else ("list", [])
    if action == "list":
        info = keys.status(STATE)
        if "--json" in rest:
            print(json.dumps(info))
            return 0
        print(f"keys are kept in {info['store']}\n")
        for p in info["providers"]:
            state = (f"stored (…{p['last4']})" if p["stored"] else
                     "set in your environment" if p["in_environment"] else "not set")
            print(f"  {p['id']:11} {state:24} {p['env']:20} {p['for']}")
        print("\nadd one: mp-agent keys set openrouter   (it asks for the key)")
        return 0
    if action in ("set", "remove") and rest:
        provider = rest[0]
        if action == "remove":
            print("removed" if keys.remove_key(STATE, provider) else "there was no key stored for it")
            return 0
        secret = sys.stdin.readline() if not sys.stdin.isatty() else getpass.getpass(f"{provider} API key: ")
        try:
            saved = keys.set_key(STATE, provider, secret)
        except keys.KeyError_ as exc:
            print(f"not saved: {exc}", file=sys.stderr)
            return 1
        print(f"saved (…{saved['last4']}) in {saved['where']}")
        return 0
    print("mp-agent keys list | set PROVIDER | remove PROVIDER   (providers: " + ", ".join(keys.PROVIDERS) + ")",
          file=sys.stderr)
    return 2


def test_command(argv):
    from . import setup
    p = argparse.ArgumentParser(prog="mp-agent test", allow_abbrev=False)
    p.add_argument("model", help="a model name or spec, e.g. opus or opencode:ollama/qwen3")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    options = installed_models()
    try:
        spec = models.resolve(args.model, options)
    except models.ChoiceError as exc:
        result = {"spec": args.model, "ok": False, "problem": str(exc), "seconds": 0}
    else:
        result = setup.test_model(STATE, spec)
    if args.json:
        print(json.dumps(result))
    else:
        print(f"{result['spec']}: " + (f"works ({result['seconds']}s)" if result["ok"] else f"not working: {result['problem']}"))
    return 0 if result["ok"] else 1


def setup_command(argv):
    from . import setup
    info = setup.scan(STATE)
    if "--json" in argv:
        print(json.dumps(info))
        return 0
    print("tools")
    for t in info["tools"]:
        state = ("ready" if t["ready"] else "installed, not set up" if t["installed"] else "not installed")
        print(f"  {t['name']:12} {state:22} {t['models']} model(s)" + (f"  ⚠ {t['problem']}" if t.get("problem") else ""))
        if not t["installed"]:
            print(f"               install: {t['install']}")
        elif not t["ready"]:
            print(f"               set up:  {t['login']}")
    print(f"\nAPI keys (kept in {info['keys']['store']}): " + ", ".join(
        f"{p['id']} {'stored' if p['stored'] else 'from environment' if p['in_environment'] else '-'}"
        for p in info["keys"]["providers"]))
    usable = [p for p in info["presets"] if p["roles"]]
    print("\npresets you can use: " + (", ".join(p["id"] for p in usable) or "none yet"))
    print("\n" + ("ready: " + info["ready_detail"] if info["ready"] else "not ready yet: " + info["ready_detail"]))
    return 0


def pull_request(argv):
    from . import actions
    p = argparse.ArgumentParser(prog="mp-agent pr")
    p.add_argument("--run", help="run folder (default: the most recent finished run)")
    p.add_argument("--base", help="branch to merge into (default: the branch the run started from)")
    args = p.parse_args(argv)
    run_dir = finished_run(args.run)
    if not run_dir:
        print("no finished run found", file=sys.stderr)
        return 1
    try:
        print(actions.pull_request(run_dir, args.base))
        return 0
    except actions.ActionError as exc:
        print(f"not done: {exc}", file=sys.stderr)
        return 1


def queue_command(argv):
    from . import jobqueue
    if not argv or argv[0] in ("-h", "--help"):
        print('mp-agent queue add [--repo PATH|--github owner/repo|--new|--oneoff] [--judge ..] "task"\n'
              "mp-agent queue list [--json]\nmp-agent queue remove ID\nmp-agent queue run-next")
        return 0
    action, rest = argv[0], argv[1:]
    if action == "add":
        flags = rest[:]
        # the task is the last positional argument (or stdin); everything else is passed to start
        task = read_task([rest[-1]] if rest and not rest[-1].startswith("-") else [])
        if rest and not rest[-1].startswith("-"):
            flags = rest[:-1]
        if not task:
            print("NEEDS: a task to queue", file=sys.stderr)
            return 3
        job = jobqueue.add(STATE, task, flags)
        start_visualizer(None, open_window=False)
        print(f"queued {job['id']}: {task[:80]} ({len(jobqueue.listing(STATE)['jobs'])} waiting)")
        return 0
    if action == "list":
        data = jobqueue.listing(STATE)
        if "--json" in rest:
            print(json.dumps(data))
            return 0
        for j in data["jobs"]:
            print(f"  {j['id']}  {j['label']}  {' '.join(j['args'])}")
        if not data["jobs"]:
            print("  (nothing waiting)")
        return 0
    if action == "remove" and rest:
        print("removed" if jobqueue.remove(STATE, rest[0]) else "no such job")
        return 0
    if action == "run-next":
        if live_runs():
            print("a run is going; the next job waits")
            return 0
        job = jobqueue.take_next(STATE)
        if not job:
            print("nothing queued")
            return 0
        proc = subprocess.run([sys.executable, ENTRY, "start", *job["args"]], input=job["task"], capture_output=True,
                              text=True, cwd=HOME, timeout=900)
        out = (proc.stdout + proc.stderr).strip()
        run_dir = next((l.split("Run: ", 1)[1] for l in out.splitlines() if l.startswith("Run: ")), None)
        jobqueue.record(STATE, job, out.splitlines()[0] if out else "no output", run_dir)
        print(out)
        return 0 if run_dir else 1
    print(f"unknown queue command {action}", file=sys.stderr)
    return 2


def show_history(argv):
    from . import history
    summary = history.summarize(RUNS)
    if "--json" in argv:
        print(json.dumps(summary))
        return 0
    t = summary["totals"]
    print(f"{t['runs']} runs, {t['approved']} approved, {t['hours']} hours, ${t['billed_usd']:.2f} billed\n")
    print("by project:")
    for p in summary["projects"]:
        print(f"  {p['project'][:30]:30} {p['runs']:3} runs  {p['approved']:3} approved  ${p['billed_usd']:.3f}  "
              f"{p['seconds'] // 60} min")
    g = summary["gates"]
    print(f"\ngates: checks failed {g['check_failures']}, reviewer objected {g['review_objections']}, panel objected "
          f"{g['panel_objections']}, judge objected {g['judge_objections']} ({g['judge_after_panel']} after the panel "
          f"had approved), rulings {g['rulings']}, questions for you {g['questions']}")
    if summary["lenses"]:
        print("panel objections by lens: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["lenses"].items())))
    return 0


def tidy_command(argv):
    from . import tidy
    p = argparse.ArgumentParser(prog="mp-agent tidy")
    p.add_argument("--yes", action="store_true", help="do it (default: just show what would be tidied)")
    p.add_argument("--all-runs", action="store_true", help="also archive every finished run, for a fresh history")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    planned = tidy.plan(STATE)
    chosen = [i["id"] for i in planned["items"] if i["count"] and (not i.get("optional") or args.all_runs)]
    if args.json and not args.yes:
        print(json.dumps(planned))
        return 0
    if not args.yes:
        for i in planned["items"]:
            mark = "·" if not i["count"] else ("?" if i.get("optional") and not args.all_runs else "•")
            print(f"  {mark} {i['count']:3}  {i['label']}")
        print("\nrun `mp-agent tidy --yes` to do the • items" + ("" if args.all_runs else " (add --all-runs for the ? one)"))
        return 0
    done = tidy.apply(STATE, chosen)
    print(json.dumps(done) if args.json else "\n".join(f"  {k}: {v}" for k, v in done.items()) or "  nothing to tidy")
    return 0


def clean(argv):
    """Worktrees are kept when a run stops so its state can be inspected. Once
    looked at they are clutter. Branches, and so the work itself, are never removed."""
    p = argparse.ArgumentParser(prog="mp-agent clean")
    p.add_argument("--yes", action="store_true", help="actually remove them (default: just list)")
    args = p.parse_args(argv)
    live = set()
    for name in os.listdir(RUNS) if os.path.isdir(RUNS) else []:
        pid_file = os.path.join(RUNS, name, "pid")
        try:
            with open(pid_file) as fh:
                os.kill(int(fh.read().strip()), 0)
            live.add(name)
        except (OSError, ValueError):
            pass
    found = 0
    for name in sorted(os.listdir(TREES)) if os.path.isdir(TREES) else []:
        path = os.path.join(TREES, name)
        if any(name == run or name.startswith(run + "-") for run in live):
            print(f"live, left alone: {path}")
            continue
        repo = None
        try:
            with open(os.path.join(path, ".git")) as fh:
                gitdir = fh.read().split("gitdir:", 1)[1].strip()
            repo = gitdir.split("/.git/worktrees/")[0]
        except (OSError, IndexError):
            pass
        found += 1
        where = f"(repo {repo})" if repo and os.path.isdir(repo) else "(its repository no longer exists)"
        if not args.yes:
            print(f"would remove {path} {where}")
            continue
        if repo and os.path.isdir(repo):
            gitops.worktree_remove(repo, path)
            gitops.git(repo, "worktree", "prune", check=False)
        if os.path.isdir(path):
            shutil.rmtree(path)
        print(f"removed {path} {where}")
    if not found:
        print("nothing to clean")
    elif not args.yes:
        print(f"\n{found} worktree(s); run `mp-agent clean --yes` to remove them (branches are kept)")
    return 0


def main(argv):
    if argv and argv[0] == "answer":
        return answer(argv[1:])
    if argv and argv[0] == "clean":
        return clean(argv[1:])
    if argv and argv[0] == "start":
        return start(argv[1:])
    if argv and argv[0] == "stop":
        return stop(argv[1:])
    if argv and argv[0] in ("keep", "discard"):
        return resolve(argv[1:], argv[0])
    if argv and argv[0] == "github":
        return github_command(argv[1:])
    if argv and argv[0] == "pregate":
        return pregate_report(argv[1:])
    if argv and argv[0] == "bench":
        return bench(argv[1:])
    if argv and argv[0] == "selftest":
        return selftest(argv[1:])
    if argv and argv[0] == "again":
        return again(argv[1:])
    if argv and argv[0] == "resume":
        return resume(argv[1:])
    if argv and argv[0] == "models":
        return list_models(argv[1:])
    if argv and argv[0] == "where":
        return list_places(argv[1:])
    if argv and argv[0] == "pr":
        return pull_request(argv[1:])
    if argv and argv[0] == "queue":
        return queue_command(argv[1:])
    if argv and argv[0] == "history":
        return show_history(argv[1:])
    if argv and argv[0] == "tidy":
        return tidy_command(argv[1:])
    if argv and argv[0] == "config":
        return set_config(argv[1:])
    if argv and argv[0] == "setup":
        return setup_command(argv[1:])
    if argv and argv[0] == "pagecheck":
        return pagecheck_command(argv[1:])
    if argv and argv[0] == "mcp":
        return mcp_command(argv[1:])
    if argv and argv[0] == "rules":
        return rules_command(argv[1:])
    if argv and argv[0] == "protect":
        return protect_command(argv[1:])
    if argv and argv[0] == "app":
        return app_command(argv[1:])
    if argv and argv[0] == "launchers":
        return launchers_command(argv[1:])
    if argv and argv[0] == "keys":
        return keys_command(argv[1:])
    if argv and argv[0] == "test":
        return test_command(argv[1:])
    if argv and argv[0] == "status":
        os.execvp("mp-status", ["mp-status", *argv[1:]])
    args = parser().parse_args(argv)
    task = " ".join(args.task)
    if args.shape == "swarm" and args.no_plan:
        print("a swarm needs the planner to split the work; drop --no-plan or --swarm", file=sys.stderr)
        return 2

    resume_info = None
    if args.resume_run:
        resume_info = load_resume(args.resume_run, TREES)
        repo = resume_info["repo"]
    else:
        folder = None if args.new else (args.folder or os.getcwd())
        try:
            repo = gitops.setup_project(folder, task, init=args.init, home=HOME)
        except gitops.SetupError as exc:
            print(exc, file=sys.stderr)
            return 2
    choices, _, problem = choose_models(given_roles(args), project=repo)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    tuning = models.load_tuning(STATE)
    for name, key in (("panel", "panel_size"), ("patience", "patience"), ("churn", "churn")):
        if getattr(args, name) is None:
            setattr(args, name, tuning[key])

    os.makedirs(RUNS, exist_ok=True)
    run = Run(RUNS, task)
    pacer = Pacer(PACE, args.min_interval, record=lambda seconds: run.count("queue_seconds", seconds))
    tree_prefix = (os.path.join(TREES, os.path.basename(run.dir)) if not args.resume_run
                   else os.path.join(TREES, os.path.basename(args.resume_run.rstrip("/"))))
    usage = providers.Usage(lambda totals: run.write_json("usage.json", totals), tree_prefix, run.started - 60)
    from . import mcp as mcp_servers
    os.makedirs(SHOTS, exist_ok=True)
    job_servers = mcp_servers.servers_for(STATE, SHOTS, repo)
    used_tools = {spec.split(":")[0] for spec in models.effective(choices).values() if spec}
    from . import keys as stored_keys
    job_tools = mcp_servers.Tools(job_servers, run.dir,
                                  mcp_servers.codex_own_servers() if "codex" in used_tools else (),
                                  available_env=stored_keys.environment(STATE).keys())
    if "cline" in used_tools:
        added = mcp_servers.sync_cline(job_servers)
        if added:
            run.say(f"   added to Cline's MCP settings: {', '.join(added)}")
    run.say(f"   MCP servers: {', '.join(job_servers) or 'none'}")

    if any(spec.startswith("cline:") for spec in models.effective(choices).values() if spec):
        providers.restart_stale_cline_hubs(run.say)
    from . import keys
    key_env = keys.environment(STATE)
    claude_key = None
    if models.load_extra(STATE).get("claude_billing") == "api":
        claude_key = key_env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

    def wrap(spec, worker=False, skills=False):
        agent = make_agent(spec, args.timeout, worker=worker, mcp=job_tools, key_env=key_env,
                           claude_api_key=claude_key, skills=skills)
        agent.on_activity = run.activity
        return Retrying(agent, pacer, run.say, usage=usage,
                        on_fatal=lambda line: models.note_problem(STATE, spec, line))
    # the skills installed on this machine reach the builders only when asked for
    worker_skills = (args.skills or models.load_skills(STATE)) == "workers"
    worker_agent = wrap(choices["worker"], worker=True, skills=worker_skills)
    # the designer wants Claude Code's frontend-design skill, so it gets the Skill tool
    designer_spec = choices.get("designer") or models.pick_designer(installed_models(), avoid=(choices["worker"],))
    designer = wrap(designer_spec, skills=models.designs_with_a_skill(designer_spec)) if designer_spec else None
    judge = None if args.no_audit else wrap(choices["judge"])
    planner_agent = None if args.no_plan else wrap(choices["planner"])
    # the same model as the workers still reviews read-only, as a separate agent
    reviewer = wrap(choices["reviewer"] or choices["worker"])
    panel = wrap(choices["panel"] or choices["worker"])
    run.write_json("roles.json", models.effective(choices))
    options = Options(workers=args.workers, patience=args.patience, churn=args.churn, panel_size=args.panel,
                      review=not args.no_critic, audit=not args.no_audit, ask=not args.no_ask, keep=args.keep,
                      budget_minutes=args.budget, max_calls=args.max_calls, shape=args.shape or "auto",
                      design=args.design or "auto",
                      unattended=args.unattended or models.load_unattended(STATE),
                      max_usd=args.max_usd if args.max_usd is not None else float(models.load_extra(STATE).get("max_usd") or 0))
    if args.alone:
        run.write_text("alone", "started with --alone\n")
    decisions = Decisions(save=lambda items: run.write_json("decisions.json", items))
    if resume_info:
        decisions.items = list(resume_info["decisions"])
        mark_resumed(resume_info["dir"], run.dir)
    ctx = Context(run, worker_agent, judge, planner_agent, decisions, options, reviewer=reviewer, panel=panel)
    pregate = models.load_pregate(STATE)
    ctx.fastjudge = {"key": key_env.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_API_KEY", ""),
                     "threshold": pregate["threshold"], "skipping": pregate["mode"] == "on"}
    if pregate["mode"] != "off" and not ctx.fastjudge["key"]:
        print("the pre-gate is switched on but there is no TypeSafe key; the panel runs in full",
              file=sys.stderr)
    if worker_skills:
        from . import skills as skills_on_disk
        found = skills_on_disk.installed(HOME)
        ctx.skills_shelf = skills_on_disk.section(found)
        if found:
            run.say(f"skills     {len(found)} available to the builders: "
                    + ", ".join(s["name"] for s in found[:6]) + ("…" if len(found) > 6 else ""))
    ctx.designer = designer
    ctx.upgrade = models.load_upgrade(STATE)
    ctx.make_worker = lambda spec: wrap(spec, worker=True, skills=worker_skills)
    ctx.make_agent = lambda spec: wrap(spec)
    ctx.model_options = models.with_problems(STATE, installed_models())
    ctx.roles = dict(choices)
    ctx.usage = usage
    start_visualizer(run)

    def stopped_by_you(*_):
        ctx.halt("stopped by you")
        providers.kill_active()

    signal.signal(signal.SIGTERM, stopped_by_you)
    try:
        result = Orchestrator(ctx, task, repo, TREES, check_override=args.check,
                              use_planner=not args.no_plan, resume=resume_info).run()
    except KeyboardInterrupt:
        ctx.halt("interrupted")
        run.say("\nNOT approved: interrupted")
        run.finish({"approved": False, "outcome": "NOT approved: interrupted", "task": task, "project": repo})
        return 130
    if args.oneoff:
        from . import actions
        try:
            if result.get("approved"):
                run.say(f"one-off: {actions.keep(run.dir)}; your files are in {repo}")
        except actions.ActionError as exc:
            run.say(f"one-off: the result was not merged automatically ({exc})")
        meta_path = os.path.join(run.dir, "metadata.json")
        with open(meta_path) as fh:
            meta = json.load(fh)
        meta["oneoff"] = True
        with open(meta_path, "w") as fh:
            json.dump(meta, fh, indent=2)
    notify("mp-agent: approved" if result.get("approved") else "mp-agent: stopped",
           f"{task[:80]}\n{result.get('outcome', '')[:160]}")
    return 0 if result.get("approved") else 1


def load_resume(old, trees_root):
    """Everything a stopped run left behind that its successor needs."""
    def read_json(name, default):
        try:
            with open(os.path.join(old, name)) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default
    with open(os.path.join(old, "repo")) as fh:
        repo = fh.read().strip()
    branch = read_json("metadata.json", {}).get("branch") or f"mp/{os.path.basename(old)}"
    return {"dir": old, "repo": repo, "plan": read_json("plan.json", None), "units": read_json("units.json", {}),
            "meta": read_json("metadata.json", {}),
            "decisions": read_json("decisions.json", []), "branch": branch,
            "tree": os.path.join(trees_root, branch[len("mp/"):])}


SHOTS = os.path.join(STATE, "shots")
def mark_resumed(old_dir, new_dir):
    path = os.path.join(old_dir, "metadata.json")
    try:
        with open(path) as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {"approved": False, "outcome": "NOT approved: the run ended without a summary (crashed or rebooted)"}
    meta["resumed_as"] = new_dir
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(meta, fh, indent=2)
    os.replace(tmp, path)


def _has(command):
    return any(os.access(os.path.join(d, command), os.X_OK) for d in os.environ.get("PATH", "").split(os.pathsep))
