"""What past runs say about where the time and money go.

Per project: runs, how many were approved, time and money billed to API keys. Per gate:
how often each judge objected, and the most telling pattern, how often the
final judge still objected after the cheaper panel had approved (the panel
missed something) and how often a panel objection led to a code change (the
objection was acted on).
"""
import glob
import json
import os
import re


def _read(path, default=None):
    try:
        with open(path, errors="replace") as fh:
            return json.load(fh) if path.endswith(".json") else fh.read()
    except (OSError, ValueError):
        return default


def summarize(runs_dir, limit=300):
    runs = []
    for run in sorted(glob.glob(os.path.join(runs_dir, "*")), reverse=True)[:limit]:
        meta = _read(os.path.join(run, "metadata.json"))
        if not meta or not os.path.exists(os.path.join(run, "plan.json")) and "units" not in meta:
            continue          # not finished, or a version-1 run without the details we count
        log = _read(os.path.join(run, "run.log"), "") or ""
        lines = [re.sub(r"^\[[^\]]+\] ", "", l) for l in log.splitlines()]
        count = lambda pattern: sum(1 for l in lines if re.search(pattern, l))
        panel_lenses = {}
        for l in lines:
            m = re.search(r"panel (\w+): objected", l)
            if m:
                panel_lenses[m.group(1)] = panel_lenses.get(m.group(1), 0) + 1
        # a final judge objection that followed a panel approval: the panel missed it
        missed, panel_ok = 0, False
        for l in lines:
            if re.search(r"^\s*panel approved$", l):
                panel_ok = True
            elif re.search(r"final reviewer asked for changes", l):
                missed += 1 if panel_ok else 0
                panel_ok = False
            elif re.search(r"── pass", l):
                panel_ok = False
        counters, costs = meta.get("counters") or {}, meta.get("costs") or {}
        units = meta.get("units") or {}
        runs.append({
            "run": os.path.basename(run), "task": (meta.get("task") or "")[:100],
            "project": os.path.basename(meta.get("project") or ""), "approved": bool(meta.get("approved")),
            "outcome": meta.get("outcome", ""), "seconds": int(meta.get("seconds") or 0),
            "passes": sum(int((u or {}).get("passes") or 0) for u in units.values()),
            # older runs recorded Mercury and Claude separately
            "billed_usd": float(costs.get("billed_usd", costs.get("mercury_usd")) or 0),
            "subscription_usd": float(costs.get("subscription_usd", costs.get("claude_equiv_usd")) or 0),
            "mode": (_read(os.path.join(run, "plan.json"), {}) or {}).get("mode"),
            "check_failures": count(r"check exit [1-9]"), "review_objections": count(r"^\s*reviewer asked for changes"),
            "panel_objections": count(r"panel asked for changes"), "panel_lenses": panel_lenses,
            "judge_objections": count(r"final reviewer asked for changes"), "judge_after_panel": missed,
            "rulings": count(r"ruling A\d+:"), "questions": count(r"waiting for you:"),
            "resolution": (meta.get("resolution") or {}).get("action"),
        })
    projects = {}
    for r in runs:
        p = projects.setdefault(r["project"] or "?", {"project": r["project"] or "?", "runs": 0, "approved": 0,
                                                       "seconds": 0, "billed_usd": 0.0, "passes": 0})
        p["runs"] += 1
        p["approved"] += r["approved"]
        p["seconds"] += r["seconds"]
        p["billed_usd"] += r["billed_usd"]
        p["passes"] += r["passes"]
    gates = {k: sum(r[k] for r in runs) for k in ("check_failures", "review_objections", "panel_objections",
                                                    "judge_objections", "judge_after_panel", "rulings", "questions")}
    lenses = {}
    for r in runs:
        for lens, n in r["panel_lenses"].items():
            lenses[lens] = lenses.get(lens, 0) + n
    return {"runs": runs, "projects": sorted(projects.values(), key=lambda p: -p["runs"]), "gates": gates,
            "lenses": lenses, "totals": {"runs": len(runs), "approved": sum(r["approved"] for r in runs),
                                         "billed_usd": round(sum(r["billed_usd"] for r in runs), 4),
                                         "hours": round(sum(r["seconds"] for r in runs) / 3600, 2)}}
