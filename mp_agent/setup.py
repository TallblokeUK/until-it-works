"""What the setup screen shows: the tools, whether each is ready, the API keys, the
presets that would work, and a one-call test for any model.

Nothing here installs anything or logs in to anything. It looks, and tells you
the command to run.
"""
import os
import shutil
import sys
import tempfile
import time

from . import keys, models
from .providers import classify, error_line, make_agent

TOOLS = [
    {"id": "claude", "name": "Claude Code", "command": "claude", "install": "npm install -g @anthropic-ai/claude-code",
     "login": "run `claude` once and sign in with your Claude account, or add an Anthropic API key and choose API billing",
     "pays": "your Claude subscription, or an Anthropic API key"},
    {"id": "codex", "name": "Codex", "command": "codex", "install": "npm install -g @openai/codex",
     "login": "codex login", "pays": "your ChatGPT plan, or an OpenAI API key (codex login --with-api-key)"},
    {"id": "cline", "name": "Cline", "command": "cline", "install": "npm install -g cline",
     "login": "cline auth   (choose a provider such as inception for Mercury, and paste its API key)",
     "pays": "the API key of the provider you choose in Cline"},
    {"id": "opencode", "name": "OpenCode", "command": "opencode", "install": "npm install -g opencode-ai",
     "login": "opencode auth login, or add an API key (OpenRouter reaches the most models)",
     "pays": "API keys, or nothing for local models (Ollama, LM Studio)"},
    {"id": "antigravity", "name": "Antigravity", "command": "agy",
     "install": "install Google Antigravity, which provides the agy command",
     "login": "run `agy` once and sign in with your Google account", "pays": "your Google plan"},
    {"id": "qwen", "name": "Qwen Code", "command": "qwen", "install": "npm install -g @qwen-code/qwen-code",
     "login": "add a model provider to ~/.qwen/settings.json", "pays": "the provider's API key"},
    {"id": "gemini", "name": "Gemini CLI", "command": "gemini", "install": "npm install -g @google/gemini-cli",
     "login": "add a Google (Gemini) API key", "pays": "a Gemini API key"},
]

TEST_SYSTEM = "You are being checked by a setup screen. Do not read or change any files."
TEST_PROMPT = "Reply with the single word READY and nothing else."


def scan(state_dir):
    """Everything the setup screen needs, in one go. Takes a few seconds (it asks
    Claude Code whether it is logged in, and some tools for their model lists)."""
    extra = models.load_extra(state_dir)
    key_env = keys.environment(state_dir)
    options = models.with_problems(state_dir, models.available(
        key_env=key_env, claude_billing=extra.get("claude_billing") or "subscription"))
    tools = []
    for tool in TOOLS:
        mine = [o for o in options if o["spec"].split(":")[0] == tool["id"]]
        problems = sorted({o["problem"] for o in mine if o.get("problem")})
        tools.append({**tool, "installed": bool(shutil.which(tool["command"])), "ready": bool(mine),
                      "models": len(mine), "problem": problems[0] if problems else None})
    config = models.load_config(state_dir)
    chosen, problem = _check_choices(config, options)
    return {
        "platform": sys.platform,
        "tools": tools,
        "keys": keys.status(state_dir),
        "available": options,
        "chosen": config,
        "presets": models.presets(options),
        "preset": extra.get("preset"),
        "claude_billing": extra.get("claude_billing") or "subscription",
        "ready": chosen,
        "ready_detail": problem or "the chosen models are all available",
        "first_time": not os.path.exists(models.config_path(state_dir)),
    }


def _check_choices(config, options):
    specs = {o["spec"] for o in options}
    effective = models.effective(config)
    missing = [f"the {role} ({spec})" for role, spec in effective.items() if spec and spec not in specs]
    if not options:
        return False, "no models found yet: set up at least one tool (see SETUP in the workshop, or mp-agent setup)"
    if missing:
        return False, "not available here: " + ", ".join(dict.fromkeys(missing)) + ". Pick a preset or choose again in MODELS"
    problems = models.check_independent(config)
    return (False, "; ".join(problems)) if problems else (True, "")


def test_model(state_dir, spec, timeout=150):
    """One tiny, read-only call. Returns {"spec", "ok", "seconds", "answer", "problem", "kind"}."""
    extra = models.load_extra(state_dir)
    key_env = keys.environment(state_dir)
    claude_key = None
    if extra.get("claude_billing") == "api":
        claude_key = key_env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    started = time.time()
    try:
        agent = make_agent(spec, timeout, worker=False, key_env=key_env, claude_api_key=claude_key)
    except ValueError as exc:
        return {"spec": spec, "ok": False, "seconds": 0, "answer": "", "problem": str(exc), "kind": "failed"}
    with tempfile.TemporaryDirectory(prefix="mp-agent-test-") as folder:
        reply = agent.ask(TEST_SYSTEM, TEST_PROMPT, folder)
    ok = reply.ok and "READY" in (reply.text or "").upper()
    kind = classify(reply.status, reply.text) if not reply.ok else ("ok" if ok else "failed")
    problem = None
    if not ok:
        problem = (error_line(reply.text) if not reply.ok else
                   f"it answered, but not as asked: {(reply.text or '').strip()[:120] or '(nothing)'}")
        if kind == "fatal":
            models.note_problem(state_dir, spec, problem)
    return {"spec": spec, "ok": ok, "seconds": round(time.time() - started, 1), "answer": (reply.text or "")[:200],
            "problem": problem, "kind": kind}
