"""Compare model line-ups on the same work, the same way, every time.

A bench task is a folder under bench/tasks: the starting project (seed/), the task in
exact words (task.md), and tests the agents never see (hidden/). The agents are judged
by those hidden tests after the job ends, not by their own verdict — a judge from the
same family as the workers approves more easily, so "approved" measures agreement, not
quality. The gap between the two is itself worth knowing: a false approval is a judge
saying yes to work that does not do what was asked.

    mp-agent bench --list
    mp-agent bench --task tally --combo "name=fast,worker=cline:inception:mercury-2.5" --repeat 3
    mp-agent bench table ~/.mp-agent/bench/20260916T090000Z

Every run gets a brand-new project folder (so nothing is remembered between runs), no
questions (nobody is waiting), and the same budget. Models vary from run to run, so a
combination is run several times and reported as a median with its spread.
"""
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

from . import gitops

HIDDEN_NAME = "_bench_hidden"
ROLES = ("planner", "worker", "reviewer", "panel", "judge")


def tasks_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench", "tasks")


def listing():
    """Every bench task: its name and its first line of description."""
    out = []
    for name in sorted(os.listdir(tasks_dir())) if os.path.isdir(tasks_dir()) else []:
        folder = os.path.join(tasks_dir(), name)
        if not os.path.isfile(os.path.join(folder, "task.md")):
            continue
        about = ""
        path = os.path.join(folder, "about.txt")
        if os.path.isfile(path):
            with open(path) as fh:
                about = fh.read().strip().splitlines()[0]
        out.append({"name": name, "about": about, "folder": folder})
    return out


def load_task(name):
    folder = os.path.join(tasks_dir(), name)
    if not os.path.isfile(os.path.join(folder, "task.md")):
        raise ValueError(f"no bench task called '{name}' (try: mp-agent bench --list)")
    with open(os.path.join(folder, "task.md")) as fh:
        text = fh.read().strip()
    return {"name": name, "folder": folder, "text": text,
            "seed": os.path.join(folder, "seed"), "hidden": os.path.join(folder, "hidden")}


def parse_combo(text):
    """"name=fast,worker=claude:sonnet,judge=claude:opus" -> {"name": ..., "roles": {...}}."""
    combo = {"name": "", "roles": {}}
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        if "=" not in part:
            raise ValueError(f"'{part}' should be key=value (keys: name, {', '.join(ROLES)})")
        key, value = (s.strip() for s in part.split("=", 1))
        if key == "name":
            combo["name"] = value
        elif key in ROLES:
            combo["roles"][key] = value
        else:
            raise ValueError(f"'{key}' is not a role (use: name, {', '.join(ROLES)})")
    if not combo["roles"]:
        raise ValueError("a combination needs at least one role, for example worker=claude:sonnet")
    combo["name"] = combo["name"] or "+".join(f"{r[0]}:{s.split(':')[-1]}" for r, s in sorted(combo["roles"].items()))
    return combo


def prepare(task, folder):
    """A fresh project with the task's starting files, committed."""
    os.makedirs(folder, exist_ok=True)
    if os.path.isdir(task["seed"]):
        for name in os.listdir(task["seed"]):
            source, target = os.path.join(task["seed"], name), os.path.join(folder, name)
            shutil.copytree(source, target) if os.path.isdir(source) else shutil.copy2(source, target)
    if not os.path.exists(os.path.join(folder, "README.md")):
        with open(os.path.join(folder, "README.md"), "w") as fh:
            fh.write("A project for one bench run.\n")
    return gitops.setup_project(folder, task["name"], init=True, say=lambda *a: None)


def grade(project, branch, hidden, timeout=300):
    """Run the hidden tests against the finished work, in a copy that is thrown away."""
    result = {"ran": 0, "failed": 0, "passed": False, "detail": ""}
    if not os.path.isdir(hidden):
        result["detail"] = "this task has no hidden tests"
        return result
    tree = project + "-graded"
    shutil.rmtree(tree, ignore_errors=True)
    try:
        gitops.git(project, "worktree", "add", "--detach", tree, branch)
    except gitops.GitError:
        result["detail"] = f"the finished work could not be checked out ({branch})"
        return result
    try:
        # in a folder of its own, so nothing the agents wrote can be mistaken for it or shadow it
        shutil.copytree(hidden, os.path.join(tree, HIDDEN_NAME))
        try:
            proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", HIDDEN_NAME, "-t", ".", "-v"],
                                  cwd=tree, capture_output=True, text=True, timeout=timeout)
            text = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            result["detail"] = "the hidden tests did not finish in time"
            return result
        # a hidden test may run tests of its own, so the run's own summary is the LAST one printed
        ran = re.findall(r"^Ran (\d+) tests?", text, re.M)
        result["ran"] = int(ran[-1]) if ran else 0
        tail = text[text.rfind("Ran "):] if ran else ""
        result["failed"] = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", tail))
        result["passed"] = proc.returncode == 0 and result["ran"] > 0
        first = next((l.strip() for l in text.splitlines() if l.startswith(("FAIL:", "ERROR:"))), "")
        result["detail"] = "all hidden tests pass" if result["passed"] else (first or text.strip()[-200:])
    finally:
        gitops.git(project, "worktree", "remove", "--force", tree, check=False)   # already gone is fine
        shutil.rmtree(tree, ignore_errors=True)
    return result


def run_one(task, combo, folder, runs_dir, entry, budget=45, max_calls=250, timeout=5400, say=print):
    """One job, start to graded. Returns the row for the results file."""
    started = time.time()
    prepare(task, folder)
    flags = []
    for role, spec in combo["roles"].items():
        flags += ["--panel-model" if role == "panel" else f"--{role}", spec]
    args = [sys.executable, entry, task["text"], "--repo", folder, "--no-ask", "--budget", str(budget),
            "--max-calls", str(max_calls), *flags]
    env = {**os.environ, "MP_RUNS": runs_dir, "MP_VIZ": "0"}
    row = {"task": task["name"], "combo": combo["name"], "roles": dict(combo["roles"]), "folder": folder}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        row["exit"] = proc.returncode
    except subprocess.TimeoutExpired:
        row.update(exit=124, error=f"the job did not finish in {timeout}s")
        say(f"   {task['name']} / {combo['name']}: the job ran out of time")
        return row
    meta = newest_metadata(runs_dir, folder)
    if meta is None:
        row.update(error="the run left no metadata", tail=(proc.stdout + proc.stderr).strip()[-300:])
        say(f"   {task['name']} / {combo['name']}: the job did not run ({row['tail'][:120]})")
        return row
    row.update(approved=bool(meta.get("approved")), outcome=meta.get("outcome", ""), mode=meta.get("mode"),
               seconds=round(float(meta.get("seconds") or 0)), counters=meta.get("counters") or {},
               costs=meta.get("costs") or {}, run=meta.get("run"), branch=meta.get("branch"),
               upgrades=meta.get("upgrades") or [], switches=meta.get("switches") or [],
               passes=max([(u or {}).get("passes") or 0 for u in (meta.get("units") or {}).values()] or [0]))
    row["hidden"] = grade(folder, meta.get("branch") or "", task["hidden"])
    mark_graded(meta, row["hidden"])
    row["works"] = bool(row["hidden"]["passed"])
    row["elapsed"] = round(time.time() - started)
    say(f"   {task['name']} / {combo['name']}: {'works' if row['works'] else 'does not work'}"
        f" ({'approved' if row['approved'] else 'not approved'}), {row['passes']} pass(es), {row['seconds']}s")
    return row


def mark_graded(meta, hidden):
    """A bench run is never kept or thrown away by hand, so it must not sit there asking."""
    path = os.path.join(meta.get("run") or "", "metadata.json")
    if not os.path.isfile(path):
        return
    meta = dict(meta)
    meta["resolution"] = {"action": "graded", "detail": "a bench run: " + (hidden.get("detail") or "graded")}
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(meta, fh, indent=2)
    os.replace(tmp, path)


def newest_metadata(runs_dir, project):
    """The metadata of the newest run in runs_dir that worked in project."""
    best = None
    for name in sorted(os.listdir(runs_dir), reverse=True) if os.path.isdir(runs_dir) else []:
        path = os.path.join(runs_dir, name, "metadata.json")
        try:
            with open(path) as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if os.path.realpath(meta.get("project") or "") == os.path.realpath(project):
            best = meta
            break
    return best


def summarize(rows):
    """Per combination: how often the work really works, what the judge said, and what it cost."""
    combos = {}
    for row in rows:
        if "works" not in row:
            combos.setdefault(row["combo"], []).append(None)
            continue
        combos.setdefault(row["combo"], []).append(row)
    out = []
    for name, entries in combos.items():
        done = [r for r in entries if r]
        runs = len(entries)
        works = [r for r in done if r["works"]]
        false_yes = [r for r in done if r["approved"] and not r["works"]]
        false_no = [r for r in done if r["works"] and not r["approved"]]
        out.append({
            "combo": name, "runs": runs, "failed_to_run": runs - len(done),
            "works": len(works), "works_rate": round(len(works) / runs, 2) if runs else 0,
            "approved": len([r for r in done if r["approved"]]),
            "false_approvals": len(false_yes), "false_rejections": len(false_no),
            "median_minutes": round(statistics.median([r["seconds"] for r in done]) / 60, 1) if done else None,
            "median_passes": statistics.median([r["passes"] for r in done]) if done else None,
            "median_calls": statistics.median([sum(v for k, v in (r["counters"] or {}).items()
                                                   if k.endswith("_calls")) for r in done]) if done else None,
            "billed_usd": round(sum((r["costs"] or {}).get("billed_usd") or 0 for r in done), 2),
            "subscription_usd": round(sum((r["costs"] or {}).get("subscription_usd") or 0 for r in done), 2),
            "upgrades": sum(len(r["upgrades"]) for r in done),
        })
    return sorted(out, key=lambda o: (-o["works_rate"], o["median_minutes"] if o["median_minutes"] else 1e9))


def table(summary):
    """The summary as text anyone can read."""
    head = f"{'line-up':28} {'works':>7} {'judge wrong':>12} {'minutes':>8} {'passes':>7} {'calls':>6} {'$ billed':>9}"
    lines = [head, "-" * len(head)]
    for s in summary:
        wrong = f"{s['false_approvals']}✓ {s['false_rejections']}✗"
        lines.append(f"{s['combo'][:28]:28} {s['works']}/{s['runs']:<5} {wrong:>12} "
                     f"{(s['median_minutes'] if s['median_minutes'] is not None else '-'):>8} "
                     f"{(s['median_passes'] if s['median_passes'] is not None else '-'):>7} "
                     f"{(s['median_calls'] if s['median_calls'] is not None else '-'):>6} "
                     f"{s['billed_usd']:>9.2f}")
    lines.append("")
    lines.append("works = the hidden tests pass. judge wrong: ✓ approved work that does not work, "
                 "✗ rejected work that does.")
    return "\n".join(lines)


def suggest(rows):
    """What the results recommend, per thing a person actually wants. A line-up that never
    finished a run recommends nothing, however fast its failures were."""
    summary = {s["combo"]: s for s in summarize(rows)}
    roles = {}
    for row in rows:
        roles.setdefault(row["combo"], row.get("roles") or {})
    usable = [s for s in summary.values() if s["works"] > 0 and s["median_minutes"] is not None]
    if not usable:
        return []
    reliable = [s for s in usable if s["works"] == s["runs"]] or usable
    picks = []

    def add(what, chosen, why):
        if chosen and not any(p["combo"] == chosen["combo"] and p["for"] == what for p in picks):
            picks.append({"for": what, "combo": chosen["combo"], "why": why, "roles": roles.get(chosen["combo"], {}),
                          "works": f"{chosen['works']}/{chosen['runs']}", "minutes": chosen["median_minutes"],
                          "billed_per_run": round(chosen["billed_usd"] / max(chosen["runs"], 1), 2)})

    quickest = min(reliable, key=lambda s: s["median_minutes"])
    add("the quickest", quickest, f"{quickest['median_minutes']} minutes a task, and it worked every time")
    free = [s for s in reliable if not s["billed_usd"]]
    if free:
        cheapest = min(free, key=lambda s: s["median_minutes"])
        add("no API bills", cheapest, f"nothing billed to an API key, {cheapest['median_minutes']} minutes a task")
    steadiest = min(reliable, key=lambda s: (s["median_passes"] if s["median_passes"] is not None else 99,
                                             s["median_minutes"]))
    add("the steadiest", steadiest, f"usually {steadiest['median_passes']:g} pass(es), so it rarely goes round again")
    avoid = [s for s in summary.values() if s["works"] < s["runs"]]
    return picks + [{"for": "avoid", "combo": s["combo"], "roles": roles.get(s["combo"], {}),
                     "works": f"{s['works']}/{s['runs']}",
                     "why": ("no run finished" if not s["works"] else "a run did not finish"),
                     "minutes": s["median_minutes"], "billed_per_run": 0} for s in avoid]


def load_results(folder):
    rows = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith(".json") and name != "summary.json":
            try:
                with open(os.path.join(folder, name)) as fh:
                    rows.append(json.load(fh))
            except (OSError, ValueError):
                continue
    return rows
