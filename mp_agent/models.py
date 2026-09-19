"""Which models can do which role, and which ones were chosen.

Five roles:
    planner   plans the work, rules on disagreements, re-plans, writes questions for you
    worker    implements, many times over
    reviewer  the quick review after every passing check    (default: the workers' model)
    panel     the pre-audit panel of lenses                  (default: the workers' model)
    judge     the final audit, the last word

Any installed, logged-in model can take any role, with one rule: the planner and
the judge must not be the workers' model, because a model grading its own work is
not an independent check. The reviewer and panel may share it: each call is a
fresh session with no memory of writing the code, and the judge still follows.

Specs name a model the same way everywhere (CLI flags, config, the workshop):
    <tool>:<model>             claude:opus   codex:gpt-5.5   antigravity:gemini-3.1-pro-high   qwen:deepseek-v4-pro
    cline:<provider>:<model>   cline:inception:mercury-2.5
    opencode:<provider>/<model>   opencode:openrouter/qwen/qwen3-coder   opencode:ollama/qwen3

Presets pick all five from what is available, plus loop settings that suit them
(a fast, cheap worker can afford many passes and a three-lens panel; an expensive
one cannot). Choices live in ~/.mp-agent/config.json; flags and environment override them.
"""
import fnmatch
import json
import os
import shutil
import subprocess
import time

HOME = os.path.expanduser("~")
ROLES = ("planner", "worker", "reviewer", "panel", "judge", "designer")
REQUIRED = ("planner", "worker", "judge")
SAME = ""                           # reviewer / panel: use the workers' model; designer: chosen for you
BUILTIN = {"worker": "cline:inception:mercury-2.5", "planner": "claude:sonnet", "judge": "claude:sonnet",
           "reviewer": SAME, "panel": SAME, "designer": SAME}
TUNING = {"panel_size": 3, "patience": 3, "churn": 8}

# What each line-up actually did, measured the same way: docs/benchmarks.md. Only line-ups that
# were run exactly as the preset builds them carry a "measured" note; "mixed" was not.
MEASURED_ON = "16 Sep 2026"

PRESETS = [
    {"id": "fast", "name": "Fast and cheap",
     "about": "A fast, cheap model does the work many times over and reviews itself with fresh eyes; Claude plans "
              "and has the last word. The quickest line-up measured (6.9 minutes a task), and the only one that "
              "bills an API key.",
     "roles": {"worker": ["cline:*:mercury*", "opencode:*mercury*"], "planner": ["claude:sonnet", "codex:*"],
               "judge": ["claude:sonnet", "claude:opus", "codex:*"]},
     "measured": {"runs": 6, "works": 6, "minutes": 6.9, "passes": 2.5, "billed_per_run": 0.16},
     "tuning": {"panel_size": 3, "patience": 3, "churn": 8}},
    {"id": "claude", "name": "All Claude",
     "about": "Everything on a Claude subscription: Opus plans and judges, Sonnet builds and reviews itself with "
              "fresh eyes. The steadiest measured (usually one pass), and no API bills.",
     # Haiku used to do the quick reviews here. Measured as a worker it was the slowest of all and the only
     # model to spend a whole budget producing nothing, so this preset keeps the reviewing at Sonnet's level.
     "roles": {"planner": ["claude:opus"], "worker": ["claude:sonnet"], "judge": ["claude:opus"],
               "reviewer": ["claude:sonnet"], "panel": ["claude:sonnet"]},
     "measured": {"runs": 6, "works": 6, "minutes": 8.6, "passes": 1.0, "billed_per_run": 0.0},
     "tuning": {"panel_size": 1, "patience": 2, "churn": 5}},
    {"id": "openai", "name": "All OpenAI",
     "about": "Everything on a ChatGPT plan through Codex: a fast GPT builds, a stronger one plans and judges. "
              "Measured at 10.2 minutes a task, with more passes than Claude needs.",
     "roles": {"worker": ["codex:*luna*", "codex:*mini*", "codex:*"], "planner": ["codex:*sol*", "codex:*"],
               "judge": ["codex:*sol*", "codex:*terra*", "codex:*"]},
     "measured": {"runs": 6, "works": 6, "minutes": 10.2, "passes": 3.0, "billed_per_run": 0.0},
     "tuning": {"panel_size": 1, "patience": 2, "churn": 5}},
    {"id": "mixed", "name": "Claude plans, GPT builds",
     "about": "Opus plans and judges, a fast GPT does the work, Sonnet reviews: two companies' models checking each "
              "other.",
     "roles": {"planner": ["claude:opus"], "worker": ["codex:*luna*", "codex:*mini*", "codex:*"], "judge": ["claude:opus"],
               "reviewer": ["claude:sonnet"], "panel": ["claude:sonnet"]},
     "tuning": {"panel_size": 1, "patience": 2, "churn": 5}},
]


def config_path(state_dir):
    return os.path.join(state_dir, "config.json")


def _load(state_dir):
    try:
        with open(config_path(state_dir)) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state_dir, data):
    os.makedirs(state_dir, exist_ok=True)
    tmp = config_path(state_dir) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, config_path(state_dir))


def load_config(state_dir):
    """The chosen spec for every role; reviewer and panel are "" when they use the workers' model."""
    data = _load(state_dir)
    return {role: str(data[role] if role in data and data[role] is not None else BUILTIN[role]) for role in ROLES}


def project_config(state_dir, project):
    """The roles this project always uses, if it has its own. A project's choice beats the
    general one and is beaten by what a job is started with."""
    if not project:
        return {}
    saved = (_load(state_dir).get("project_roles") or {}).get(os.path.realpath(os.path.expanduser(project)))
    return {role: str(spec) for role, spec in (saved or {}).items() if role in ROLES}


def save_project_config(state_dir, project, choices):
    """Keep (or, with choices None or empty, forget) one project's own roles."""
    data = _load(state_dir)
    projects = dict(data.get("project_roles") or {})
    key = os.path.realpath(os.path.expanduser(project))
    kept = {role: spec for role, spec in (choices or {}).items() if role in ROLES and spec}
    if kept:
        projects[key] = kept
    else:
        projects.pop(key, None)
    data["project_roles"] = projects
    _save(state_dir, data)
    return kept


def projects_with_roles(state_dir):
    return dict(_load(state_dir).get("project_roles") or {})


def effective(choices):
    """Every role with a real spec: an empty reviewer or panel means the workers' model."""
    return {role: (choices.get(role) or choices.get("worker")) for role in ROLES}


# Claude Code carries the frontend-design skill, so a designer on Claude is worth more than a
# stronger model somewhere else; strongest Claude first, then anything decent that is not the workers'.
DESIGNERS = ["claude:fable", "claude:opus", "claude:sonnet", "codex:*astra*", "codex:*sol*",
             "antigravity:*pro*", "antigravity:*opus*", "opencode:anthropic/*"]


def pick_designer(options, avoid=()):
    """Who designs when nobody chose: the best available model that is not the workers'."""
    specs = [o["spec"] for o in options if not o.get("problem")]
    for pattern in DESIGNERS:
        for spec in specs:
            if fnmatch.fnmatch(spec, pattern) and spec not in avoid:
                return spec
    return next((spec for spec in specs if spec not in avoid), "")


def designs_with_a_skill(spec):
    """True when the designer runs on a tool that has a design skill of its own."""
    return str(spec or "").startswith("claude:")


def save_config(state_dir, choices):
    data = _load(state_dir)
    for role in ROLES:
        if role in choices and (choices[role] or role not in REQUIRED):
            data[role] = choices[role] or SAME
    _save(state_dir, data)
    return load_config(state_dir)


def load_tuning(state_dir):
    saved = _load(state_dir).get("tuning") or {}
    return {k: int(saved.get(k, v)) for k, v in TUNING.items()}


def load_extra(state_dir):
    """Settings other than the roles (the spending cap, protected repos, loop tuning)."""
    return {k: v for k, v in _load(state_dir).items() if k not in ROLES}


def save_extra(state_dir, values):
    data = _load(state_dir)
    data.update(values)
    _save(state_dir, data)


# Roughly strongest first, for offering an upgrade when the workers get stuck. Within a company
# the order is theirs: Anthropic Haiku < Sonnet < Opus < Fable; OpenAI GPT-5.6 Luna < Terra < Sol < Astra;
# DeepSeek Flash < Pro.
# Between companies it is a judgement. A model that matches none of these counts as weaker than all
# of them (a fast, cheap worker, usually).
STRENGTH = ["claude:fable", "codex:*astra*", "claude:opus", "codex:*sol*", "claude:sonnet", "codex:*terra*",
            "codex:gpt-5.5", "antigravity:*opus*", "antigravity:*pro*", "opencode:anthropic/*fable*",
            "opencode:anthropic/*opus*", "opencode:openai/*", "codex:*luna*", "antigravity:*sonnet*", "qwen:*pro*",
            "opencode:*deepseek*pro*", "claude:haiku", "qwen:*flash*", "opencode:*deepseek*flash*", "antigravity:*flash*"]


def strength(spec):
    """Lower is stronger; a spec that matches nothing gets the weakest rank."""
    return next((i for i, pattern in enumerate(STRENGTH) if fnmatch.fnmatch(spec, pattern)), len(STRENGTH))


def upgrade_options(current, options, exclude=(), limit=3):
    """Available models stronger than the current workers, strongest first. The planner and
    judge are excluded: a worker may never be the model that judges its work."""
    rank = strength(current)
    found = [o for o in options
             if o["spec"] != current and o["spec"] not in exclude and strength(o["spec"]) < rank and not o.get("problem")]
    return sorted(found, key=lambda o: strength(o["spec"]))[:limit]


def account(spec):
    """Which account a model is paid from: the tool, plus the provider for tools that reach many
    (cline:inception:..., opencode:openrouter/...). When one model runs out, so do its neighbours."""
    parts = str(spec or "").split(":")
    if parts[0] in ("cline", "opencode") and len(parts) > 1:
        return parts[0] + ":" + parts[1].split("/")[0]
    return parts[0]


def switch_options(current, options, exclude=(), limit=3):
    """Models to switch to when one cannot be used (out of credit or usage): from another account,
    as close as possible to its strength, the stronger side first."""
    rank = strength(current)
    found = [o for o in options if o["spec"] != current and o["spec"] not in exclude and not o.get("problem")
             and account(o["spec"]) != account(current)]
    return sorted(found, key=lambda o: (strength(o["spec"]) > rank, abs(strength(o["spec"]) - rank)))[:limit]


def load_upgrade(state_dir):
    """What to do when the workers get stuck: {"mode": "ask"|"auto"|"never", "to": spec or "", "max": n}."""
    saved = _load(state_dir).get("upgrade") or {}
    mode = saved.get("mode") if saved.get("mode") in ("ask", "auto", "never") else "ask"
    return {"mode": mode, "to": str(saved.get("to") or ""), "max": max(0, int(saved.get("max", 1) or 0))}


def load_unattended(state_dir):
    """What a job with nobody watching does when a model cannot be used at all:
    "switch" to another one and carry on, or "stop"."""
    mode = _load(state_dir).get("unattended")
    return mode if mode in ("switch", "stop") else "switch"


def resolve_preset(preset, options):
    """The specs a preset would use with the models available here, or (None, what is missing)."""
    specs = [o["spec"] for o in options]

    def first(patterns, avoid=()):
        for pattern in patterns:
            for spec in specs:
                if fnmatch.fnmatch(spec, pattern) and spec not in avoid:
                    return spec
        return None

    roles, missing = {}, []
    roles["worker"] = first(preset["roles"].get("worker") or [])
    for role in ("planner", "judge"):
        roles[role] = first(preset["roles"].get(role) or [], avoid=(roles["worker"],))
    for role in ("reviewer", "panel"):
        wanted = preset["roles"].get(role)
        roles[role] = (first(wanted) or SAME) if wanted else SAME
    for role in REQUIRED:
        if not roles[role]:
            missing.append(f"a {role} ({' or '.join(preset['roles'].get(role) or ['?'])})")
    return (None, missing) if missing else (roles, [])


def presets(options):
    """Every preset, with the specs it would use here, or what it still needs."""
    out = []
    for preset in PRESETS:
        roles, missing = resolve_preset(preset, options)
        out.append({"id": preset["id"], "name": preset["name"], "about": preset["about"],
                    "tuning": dict(preset["tuning"]), "roles": roles, "missing": missing,
                    "measured": dict(preset.get("measured") or {}) or None, "measured_on": MEASURED_ON})
    return out


def apply_preset(state_dir, preset_id, options):
    """Choose a preset's models and loop settings. Returns (roles, problem)."""
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if preset is None:
        return None, f"no preset called '{preset_id}' (there are: {', '.join(p['id'] for p in PRESETS)})"
    roles, missing = resolve_preset(preset, options)
    if roles is None:
        return None, f"the {preset['name']} preset needs " + " and ".join(missing) + ", which is not available here"
    save_config(state_dir, roles)
    save_extra(state_dir, {"tuning": dict(preset["tuning"]), "preset": preset["id"]})
    return load_config(state_dir), None


def _read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def available(home=HOME, check_claude_login=True, key_env=None, claude_billing="subscription"):
    """Every model that is installed and logged in, as dicts with spec and label.
    key_env: stored API keys as environment variables (keys.environment), which some tools read."""
    key_env = key_env or {}
    found = []
    if shutil.which("claude"):
        logged_in = True
        if check_claude_login:
            try:
                env = {k: v for k, v in os.environ.items()
                       if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") and not k.startswith("CLAUDE_CODE_")}
                status = json.loads(subprocess.run(["claude", "auth", "status"], capture_output=True, text=True,
                                                   timeout=20, env=env).stdout or "{}")
                logged_in = bool(status.get("loggedIn"))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                logged_in = False
        paid_by_key = claude_billing == "api" and bool(key_env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
        if logged_in or paid_by_key:
            how = "API key" if paid_by_key else "subscription"
            for model, name in (("fable", "Claude Fable"), ("opus", "Claude Opus"), ("sonnet", "Claude Sonnet"),
                                ("haiku", "Claude Haiku")):
                found.append({"spec": f"claude:{model}", "label": f"{name} (Claude Code, {how})"})
    if shutil.which("codex") and os.path.exists(os.path.join(home, ".codex", "auth.json")):
        cache = _read_json(os.path.join(home, ".codex", "models_cache.json")) or {}
        for m in cache.get("models") or []:
            if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list":
                found.append({"spec": f"codex:{m['slug']}", "label": f"{m.get('display_name') or m['slug']} (Codex)"})
    # The Gemini CLI's personal Google login (free individual tier) was withdrawn
    # for this client; only API-key or Vertex setups still work headless.
    gemini_settings = _read_json(os.path.join(home, ".gemini", "settings.json")) or {}
    gemini_auth = str(((gemini_settings.get("security") or {}).get("auth") or {}).get("selectedType")
                      or gemini_settings.get("selectedAuthType") or "")
    if shutil.which("gemini") and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                                   or key_env.get("GEMINI_API_KEY") or gemini_auth in ("gemini-api-key", "vertex-ai")):
        found.append({"spec": "gemini:default", "label": "Gemini, its default model (Gemini CLI)"})
    if shutil.which("agy"):
        for model_id, name in antigravity_models():
            found.append({"spec": f"antigravity:{model_id}", "label": f"{name} (Antigravity)"})
    if shutil.which("qwen"):
        settings = _read_json(os.path.join(home, ".qwen", "settings.json")) or {}
        for provider in (settings.get("modelProviders") or {}).values():
            for m in provider or []:
                if isinstance(m, dict) and m.get("id"):
                    found.append({"spec": f"qwen:{m['id']}", "label": f"{m.get('name') or m['id']} (Qwen Code)"})
    if shutil.which("opencode"):
        for model in opencode_models(key_env=key_env):
            found.append({"spec": f"opencode:{model}", "label": f"{model} (OpenCode)"})
    if shutil.which("cline"):
        providers = (_read_json(os.path.join(home, ".cline", "data", "settings", "providers.json")) or {})
        for name, entry in (providers.get("providers") or {}).items():
            model = ((entry or {}).get("settings") or {}).get("model")
            if model:
                pretty = "Mercury 2.5" if model == "mercury-2.5" else model
                found.append({"spec": f"cline:{name}:{model}", "label": f"{pretty} (Cline, {name})"})
    return found


def opencode_models(cache_hours=6, key_env=None):
    """`opencode models` lists provider/model for every provider OpenCode can use (with the
    stored keys in its environment, so providers added on the setup screen count)."""
    cache = os.path.join(HOME, ".mp-agent", "opencode-models.json")
    data = _read_json(cache)
    keys_now = sorted((key_env or {}).keys())
    if data and time.time() - float(data.get("at", 0)) < cache_hours * 3600 and data.get("keys") == keys_now:
        return list(data.get("models") or [])
    try:
        out = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=60,
                             env={**os.environ, **(key_env or {})}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return list((data or {}).get("models") or [])
    found = [line.strip() for line in out.splitlines() if "/" in line.strip() and " " not in line.strip()]
    if found:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".tmp", "w") as fh:
            json.dump({"at": time.time(), "models": found, "keys": keys_now}, fh)
        os.replace(cache + ".tmp", cache)
    return found


def antigravity_models(cache_hours=12):
    """`agy models` asks Google each time and takes a few seconds, so keep the list for a while."""
    cache = os.path.join(HOME, ".mp-agent", "antigravity-models.json")
    data = _read_json(cache)
    if data and time.time() - float(data.get("at", 0)) < cache_hours * 3600:
        return [tuple(m) for m in data.get("models") or []]
    try:
        out = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=45).stdout
    except (OSError, subprocess.TimeoutExpired):
        return [tuple(m) for m in (data or {}).get("models") or []]
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip() and " " not in parts[0].strip():
            found.append((parts[0].strip(), parts[1].strip()))
    if found:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".tmp", "w") as fh:
            json.dump({"at": time.time(), "models": found}, fh)
        os.replace(cache + ".tmp", cache)
    return found


PROBLEM_HOURS = 24


def note_problem(state_dir, spec, problem):
    """Remember that a model hit a hard stop (out of credit, login gone) so the
    choices can warn about it."""
    path = os.path.join(state_dir, "model-problems.json")
    data = _read_json(path) or {}
    data[spec] = {"problem": problem[:200], "at": time.time()}
    os.makedirs(state_dir, exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)


def clear_problem(state_dir, spec):
    """The model works again (usage reset, credit added, logged back in): stop warning about it."""
    path = os.path.join(state_dir, "model-problems.json")
    data = _read_json(path) or {}
    if data.pop(spec, None) is None:
        return False
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)
    return True


def with_problems(state_dir, options):
    data = _read_json(os.path.join(state_dir, "model-problems.json")) or {}
    out = []
    for o in options:
        seen = data.get(o["spec"])
        if seen and time.time() - float(seen.get("at", 0)) < PROBLEM_HOURS * 3600:
            o = {**o, "problem": seen["problem"], "label": o["label"] + " ⚠ " + seen["problem"][:60]}
        out.append(o)
    return out


class ChoiceError(ValueError):
    pass


def resolve(name, options):
    """Turn what a person typed ("opus", "gpt-5.5", "deepseek pro", a full spec) into one spec."""
    wanted = (name or "").strip().lower()
    if not wanted:
        raise ChoiceError("no model named")
    specs = [o["spec"] for o in options]
    if wanted in (s.lower() for s in specs):
        return next(s for s in specs if s.lower() == wanted)
    # the model part of a spec, exactly: "opus" is claude:opus even when another tool offers "Opus 4.6"
    exact = [s for s in specs if s.lower().split(":")[-1] == wanted or s.lower().split("/")[-1] == wanted]
    if len(exact) == 1:
        return exact[0]
    words = wanted.replace("-", " ").split()
    matches = [o for o in options
               if all(w in (o["spec"] + " " + o["label"]).lower().replace("-", " ") for w in words)]
    if len(matches) == 1:
        return matches[0]["spec"]
    if not matches:
        raise ChoiceError(f"no available model matches '{name}'. Available: "
                          + ", ".join(o["label"] for o in options))
    raise ChoiceError(f"'{name}' could mean " + " or ".join(o["label"] for o in matches) + "; be more specific")


def check_independent(choices):
    """The planner and judge must not be the workers' own model."""
    problems = []
    for role in ("planner", "judge"):
        if choices.get(role) and choices.get(role) == choices.get("worker"):
            problems.append(f"the {role} can't be the same model as the workers ({choices['worker']}): "
                            "a model checking its own work isn't an independent check")
    return problems


def label_for(spec, options, role=None):
    if not spec and role in ("reviewer", "panel"):
        return "the workers' model"
    if not spec and role == "designer":
        return "chosen for you, when a job needs one"
    return next((o["label"] for o in options if o["spec"] == spec), spec)
