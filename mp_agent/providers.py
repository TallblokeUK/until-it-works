"""Every model call goes through here.

An Agent answers ask(system, prompt, cwd) with a Reply. Each real agent drives
one command-line tool (Claude Code, Codex, Cline, OpenCode, Antigravity, Qwen
Code, Gemini CLI); tests substitute scripted fakes. Retrying wraps any agent with
the provider pacing and the retry rules. Adding a tool means one Agent subclass
and one line in make_agent; see docs/adapters.md.
"""
import fcntl
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

ANSI = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")
RATE_LIMITED = re.compile(r"rate limit|token limit|quota|too many requests", re.I)
TRANSIENT = re.compile(r"server had an error|internal server error|bad gateway|service unavailable|"
                       r"overloaded|ECONNRESET|ETIMEDOUT|socket hang up|fetch failed", re.I)
# Out of credit, out of plan usage, or not logged in: waiting will not fix these,
# a person has to. Only specific phrases, never bare status numbers, because an
# agent's transcript can contain "401" in perfectly ordinary code.
FATAL = re.compile(r"invalid api key|incorrect api key|api key (is )?(missing|invalid)|unauthori[sz]ed|"
                   r"authentication (failed|error)|not logged in|please (log|sign) in|"
                   r"insufficient (funds|credits?|balance|quota)|credit balance (is )?too low|"
                   r"payment required|usage limit reached|hit your (usage )?limit|out of credits|"
                   r"purchase more credits|exceeded your (current )?quota|individual quota reached|"
                   r"upgrade your subscription", re.I)
VERDICT = re.compile(r"^[\s*_#>]*VERDICT:\s*(APPROVED|CHANGES REQUIRED)[\s*_]*$", re.M)

# Linux caps a single argv string at 128 KiB; cline takes the prompt as one.
MAX_ARG_BYTES = 120_000


@dataclass
class Reply:
    text: str
    status: int = 0
    seconds: float = 0.0
    cost: float = 0.0          # USD as reported by the provider's CLI (Claude on Max: API-equivalent, not billed)
    usage: dict = None         # {model: {"input", "output", "cache_read", "cache_write", "usd"}} when the CLI reports it

    @property
    def ok(self):
        return self.status == 0


# Who is making the current call (implementer, reviewer, panel:edges, judge,
# planner) and for which unit, so tool activity can be shown on the right
# character. Set by the caller's thread; captured when a call starts.
ACTOR = threading.local()


@contextmanager
def acting(role, unit):
    previous = (getattr(ACTOR, "role", None), getattr(ACTOR, "unit", None))
    ACTOR.role, ACTOR.unit = role, unit
    try:
        yield
    finally:
        ACTOR.role, ACTOR.unit = previous


def categorize(tool):
    """What kind of thing an agent is doing, for the workshop."""
    t = (tool or "").lower()
    if "context7" in t or "docs" in t:
        return "docs"
    if "playwright" in t or "browser" in t:
        return "browser"
    if "fetch" in t or "web" in t or "http" in t:
        return "web"
    if any(k in t for k in ("read", "grep", "glob", "search", "list", "find")):
        return "files"
    if any(k in t for k in ("run", "bash", "command", "terminal", "exec")):
        return "terminal"
    if any(k in t for k in ("edit", "write", "replace", "create")):
        return "edit"
    return "other"


def activity_detail(text):
    """The most telling bit of a tool call's arguments: a library, a URL or a path."""
    for key in ("libraryName", "libraryId", "url", "query", "path", "files", "commands"):
        m = re.search(rf'"{key}"\s*:\s*"?([^",}}\]]+)', text or "")
        if m:
            return m.group(1).strip()[:60]
    return (text or "").strip()[:60]


PROVIDER_TROUBLE = ("rate", "transient", "missing", "fatal")

# How long to back off, per kind of trouble, before handing the problem up.
# About 17 minutes of patience in total: long enough to ride out a bad patch.
BACKOFF = {
    "rate": [60, 120, 240, 300, 300],       # the window is per minute; shorter waits waste a call
    "transient": [30, 60, 120, 240, 300],
    "missing": [30, 60, 120, 240, 300],     # cline updates itself in place
}


def classify(status, text):
    """What went wrong with a call: ok | rate | transient | missing | fatal | timeout | failed."""
    if status == 0:
        return "ok"
    if status in (126, 127):
        return "missing"
    if status == 124:
        return "timeout"
    tail = (text or "")[-3000:]
    if FATAL.search(tail):
        return "fatal"
    if RATE_LIMITED.search(tail):
        return "rate"
    if TRANSIENT.search(tail):
        return "transient"
    return "failed"


def error_line(text, limit=160):
    """The most telling line of a failed call's output, for a person to read."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for line in reversed(lines):
        if re.search(r"error|limit|fail|denied|invalid|credit|unauthori|timed out|not found", line, re.I):
            return line[:limit]
    return (lines[-1] if lines else "no output")[:limit]


def strip_ansi(text):
    return ANSI.sub("", text)


def verdict(text):
    """The last VERDICT line in a reply, or None if it never gave one."""
    found = VERDICT.findall(text or "")
    return found[-1] if found else None


def tagged(text, tag):
    """Content of every line of the form `TAG: content`, markdown decoration tolerated."""
    pattern = re.compile(rf"^[\s*_\-#>]*{re.escape(tag)}:\s*(.+?)[\s*_]*$", re.M)
    return [m.strip() for m in pattern.findall(text or "") if m.strip()]


def fit_arg(prompt):
    data = prompt.encode()
    if len(data) <= MAX_ARG_BYTES:
        return prompt
    return data[:MAX_ARG_BYTES].decode(errors="ignore") + "\n\n[prompt truncated to fit]"


_ACTIVE = set()
_ACTIVE_LOCK = threading.Lock()


def kill_active():
    """Stop every model call in flight (used when the person stops the run)."""
    with _ACTIVE_LOCK:
        groups = list(_ACTIVE)
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


def run_cli(cmd, cwd, timeout, stdin_text=None, env=None, on_line=None):
    """Run a CLI to completion. Output goes to a file, not a pipe: the Node-based
    CLIs drop a pending write on exit, which silently truncates long answers.
    on_line, if given, sees each output line as it appears (for live activity)."""
    started = time.time()
    with tempfile.NamedTemporaryFile("w+", errors="replace", prefix="mp-agent-out-") as out:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, text=True, env=env,
                                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                    start_new_session=True)
        except FileNotFoundError as exc:
            return Reply(str(exc), 127, 0.0)
        except PermissionError as exc:
            return Reply(str(exc), 126, 0.0)
        with _ACTIVE_LOCK:
            _ACTIVE.add(proc.pid)
        watching = threading.Event()
        watcher = None
        if on_line is not None:
            def watch():
                position, partial = 0, ""
                with open(out.name, errors="replace") as reader:
                    while True:
                        reader.seek(position)
                        chunk = reader.read()
                        position = reader.tell()
                        if chunk:
                            lines = (partial + chunk).split("\n")
                            partial = lines.pop()
                            for line in lines:
                                try:
                                    on_line(strip_ansi(line))
                                except Exception:
                                    pass
                        if watching.is_set():
                            return
                        time.sleep(0.5)
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
        try:
            proc.communicate(input=stdin_text, timeout=timeout)
            status = proc.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            status = 124
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(proc.pid)
            if watcher is not None:
                watching.set()
                watcher.join(timeout=2)
        out.seek(0)
        text = out.read()
    return Reply(strip_ansi(text), status, time.time() - started)


# How a tool's calls are paid for, which decides what the spending cap counts:
#   api           billed per token to an API key (counted)
#   subscription  covered by a plan such as Claude or ChatGPT (shown as API-equivalent, not counted)
#   local         a model running on this machine (free)
BILLING = ("api", "subscription", "local")


def billing_of(model, row):
    """A usage row's billing; older records have none, and only Claude ran on a subscription then."""
    return row.get("billing") or ("subscription" if str(model).startswith("claude") else "api")


def summarize_costs(totals):
    """usage.json totals -> {"billed_usd", "subscription_usd", "by_model": {model: {"usd", "billing"}}}."""
    out = {"billed_usd": 0.0, "subscription_usd": 0.0, "by_model": {}}
    for model, row in (totals or {}).items():
        usd, billing = float(row.get("usd") or 0), billing_of(model, row)
        out["by_model"][model] = {"usd": round(usd, 4), "billing": billing}
        if billing == "api":
            out["billed_usd"] += usd
        elif billing == "subscription":
            out["subscription_usd"] += usd
    out["billed_usd"], out["subscription_usd"] = round(out["billed_usd"], 4), round(out["subscription_usd"], 4)
    return out


def cost_line(costs):
    """One sentence a person can read: what was billed, and what a subscription covered."""
    billed = [f"{m} ${v['usd']:.3f}" for m, v in costs["by_model"].items() if v["billing"] == "api" and v["usd"]]
    covered = [m for m, v in costs["by_model"].items() if v["billing"] == "subscription" and v["usd"]]
    text = f"${costs['billed_usd']:.3f} billed to API keys" + (f" ({', '.join(billed)})" if billed else "")
    if costs["subscription_usd"]:
        text += (f"; ${costs['subscription_usd']:.2f} API-equivalent on subscriptions, not billed"
                 f" ({', '.join(covered)})")
    return text


class Agent:
    """One model behind one command-line tool.

    name       shown in logs and the workshop, e.g. "claude:opus"
    pace_key   calls with the same key share one provider pacing queue
    billing    one of BILLING
    worker     True when the agent may change files; judges only read
    ask(system, prompt, cwd) -> Reply   one fresh session, working in cwd
    """
    name = "agent"
    pace_key = "agent"
    billing = "api"
    on_activity = None      # callback(dict) for tool use, set by whoever runs the agent

    def _watcher(self, parse):
        """A line watcher that reports tool use for the actor making this call."""
        if self.on_activity is None:
            return None
        role, unit = getattr(ACTOR, "role", None), getattr(ACTOR, "unit", None)
        report = self.on_activity

        def on_line(line):
            found = parse(line)
            if found:
                tool, detail = found
                category = categorize(tool)
                if category != "other":
                    report({"role": role, "unit": unit, "agent": self.name, "tool": tool,
                            "category": category, "detail": activity_detail(detail)})
        return on_line

    def ask(self, system, prompt, cwd):
        raise NotImplementedError


class ClineAgent(Agent):
    """Any model a Cline provider offers (for example Inception's Mercury). Each
    call is a fresh session, so a reviewer genuinely has no memory of the implementer."""

    def __init__(self, provider="inception", model="mercury-2.5", timeout=900):
        self.provider, self.model, self.timeout = provider, model, timeout
        self.name = f"{provider}/{model}"
        self.pace_key = provider

    def ask(self, system, prompt, cwd):
        cmd = ["cline", "--cwd", cwd, "--provider", self.provider, "--model", self.model,
               "--auto-approve", "true", "--timeout", str(self.timeout), "--system", system, fit_arg(prompt)]

        def parse(line):   # Cline prints each tool call as "[tool_name] arguments"
            m = re.match(r"^\[([A-Za-z0-9_.\-]+)\]\s*(.*)$", line.strip())
            return (m.group(1), m.group(2)) if m else None
        return run_cli(cmd, cwd, self.timeout + 60, on_line=self._watcher(parse))


def cline_usage(worktree_prefix, since, sessions=None):
    """Tokens and cost per model for the Cline sessions that ran in this run's
    worktrees (Cline records both, with the folder each session ran in)."""
    sessions = sessions or os.path.join(os.path.expanduser("~"), ".cline", "data", "sessions")
    usage = {}
    try:
        names = os.listdir(sessions)
    except OSError:
        return usage
    for name in names:
        folder = os.path.join(sessions, name)
        try:
            if os.path.getmtime(folder) < since:
                continue
            with open(os.path.join(folder, f"{name}.json")) as fh:
                meta = json.load(fh)
            if not str(meta.get("cwd") or "").startswith(worktree_prefix):
                continue
            with open(os.path.join(folder, f"{name}.messages.json")) as fh:
                messages = json.load(fh).get("messages") or []
        except (OSError, ValueError, AttributeError):
            continue
        counted = False
        for m in messages:
            metrics = m.get("metrics") or {}
            if not metrics:
                continue
            info = m.get("modelInfo") or {}
            model = f"{info.get('provider') or meta.get('provider') or 'cline'}/{info.get('id') or meta.get('model')}"
            row = usage.setdefault(model, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                                           "usd": 0.0, "billing": "api"})
            if not counted:
                row["calls"] += 1
                counted = True
            row["input"] += int(metrics.get("inputTokens") or 0)
            row["output"] += int(metrics.get("outputTokens") or 0)
            row["cache_read"] += int(metrics.get("cacheReadTokens") or 0)
            row["cache_write"] += int(metrics.get("cacheWriteTokens") or 0)
            row["usd"] += float(metrics.get("cost") or 0)
    return usage


class Usage:
    """Live per-model token use for one run, written to usage.json as it changes.
    Most tools report usage with each call; Cline's is read back from its
    session records, at most every few seconds."""

    def __init__(self, write, worktree_prefix, since, sessions=None):
        self.write, self.prefix, self.since, self.sessions = write, worktree_prefix, since, sessions
        self.reported = {}
        self.cline = {}
        self._lock = threading.Lock()
        self._last_scan = 0.0

    def add(self, usage, billing="api"):
        with self._lock:
            for model, u in (usage or {}).items():
                row = self.reported.setdefault(model, {"calls": 0, "input": 0, "output": 0, "cache_read": 0,
                                                       "cache_write": 0, "usd": 0.0, "billing": billing})
                row["calls"] += 1
                for key in ("input", "output", "cache_read", "cache_write", "usd"):
                    row[key] += u.get(key, 0)
        self.flush()

    def refresh(self, force=False):
        if not force and time.time() - self._last_scan < 5:
            return
        self._last_scan = time.time()
        found = cline_usage(self.prefix, self.since, self.sessions)
        with self._lock:
            self.cline = found
        self.flush()

    def totals(self):
        with self._lock:
            return {**{k: dict(v) for k, v in self.cline.items()}, **{k: dict(v) for k, v in self.reported.items()}}

    def flush(self):
        try:
            self.write(self.totals())
        except OSError:
            pass


def claude_env():
    """No API key, so the claude CLI can only use the Claude login (the Max
    subscription); and none of a parent Claude Code session's variables, so a
    run started from inside one stays apart from it."""
    drop = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT"}
    return {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("CLAUDE_CODE_")}


class ClaudeAgent(Agent):
    """Judges and planners read; workers may edit and run commands, inside their
    worktree, without stopping to ask (the same trust Cline's auto-approve has)."""

    def __init__(self, model="sonnet", timeout=900, tools="Read,Grep,Glob", worker=False, mcp_config=None,
                 api_key=None):
        self.model, self.timeout, self.worker, self.mcp_config = model, timeout, worker, mcp_config
        self.tools = "Read,Grep,Glob,Edit,Write,Bash" if worker else tools
        self.name = f"claude:{model}"
        self.pace_key = "claude"
        # By default claude_env() leaves no API key, so only the Claude login (a subscription)
        # can pay. When the person chose API billing for Claude Code, their key is passed instead.
        self.api_key = api_key
        self.billing = "api" if api_key else "subscription"

    def ask(self, system, prompt, cwd):
        # Only the MCP servers in our own config (docs and a browser) are loaded, never
        # the person's other connectors (mail, drive, calendar): --strict-mcp-config.
        cmd = ["claude", "-p", "--model", self.model, "--tools", self.tools, "--add-dir", cwd, "--output-format",
               "stream-json", "--verbose", "--strict-mcp-config", "--no-session-persistence",
               "--append-system-prompt", system]
        if self.mcp_config and os.path.exists(self.mcp_config):
            cmd[2:2] = ["--mcp-config", self.mcp_config, "--allowedTools", "mcp__context7,mcp__playwright"]
        if self.worker:
            cmd[2:2] = ["--permission-mode", "bypassPermissions"]

        def parse(line):
            if '"tool_use"' not in line:
                return None
            try:
                for part in json.loads(line).get("message", {}).get("content", []):
                    if part.get("type") == "tool_use":
                        return part.get("name"), json.dumps(part.get("input") or {})
            except (ValueError, AttributeError):
                return None
            return None
        env = claude_env()
        if self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key
        reply = run_cli(cmd, cwd, self.timeout, stdin_text=prompt, env=env, on_line=self._watcher(parse))
        data = None
        for line in reversed(reply.text.splitlines()):
            if line.startswith("{") and '"type":"result"' in line.replace(" ", ""):
                try:
                    data = json.loads(line)
                    break
                except ValueError:
                    continue
        if data is None:
            try:
                data = json.loads(reply.text[reply.text.index("{"):])
            except ValueError:
                return reply
        status = reply.status or (1 if data.get("is_error") else 0)
        usage = {}
        for model, u in (data.get("modelUsage") or {}).items():
            usage[model] = {"input": int(u.get("inputTokens") or 0), "output": int(u.get("outputTokens") or 0),
                            "cache_read": int(u.get("cacheReadInputTokens") or 0),
                            "cache_write": int(u.get("cacheCreationInputTokens") or 0),
                            "usd": float(u.get("costUSD") or 0)}
        return Reply(str(data.get("result") or ""), status, reply.seconds, float(data.get("total_cost_usd") or 0),
                     usage)


class QwenAgent(Agent):
    def __init__(self, model="deepseek-flash", timeout=900, worker=False):
        self.model, self.timeout, self.worker = model, timeout, worker
        self.name = f"qwen:{model}"
        self.pace_key = "qwen"

    def ask(self, system, prompt, cwd):
        mode = ["--approval-mode", "yolo"] if self.worker else ["--safe-mode", "--approval-mode", "plan"]
        cmd = ["qwen", *mode, "--max-tool-calls", "60", "--add-dir", cwd, "-m", self.model,
               fit_arg(f"{system}\n\n{prompt}")]
        return run_cli(cmd, cwd, self.timeout)


class CodexAgent(Agent):
    """GPT models through the codex CLI (ChatGPT plan). Judges run in a read-only
    sandbox; workers may write inside the worktree."""

    def __init__(self, model, timeout=900, worker=False):
        self.model, self.timeout, self.worker = model, timeout, worker
        self.name = f"codex:{model}"
        self.pace_key = "codex"
        self.billing = "subscription"

    def ask(self, system, prompt, cwd):
        with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as last:
            last_path = last.name
        try:
            cmd = ["codex", "exec", "-m", self.model, "-s", "workspace-write" if self.worker else "read-only",
                   "-C", cwd, "--skip-git-repo-check", "--ephemeral", "--color", "never", "-o", last_path,
                   fit_arg(f"{system}\n\n{prompt}")]
            reply = run_cli(cmd, cwd, self.timeout)
            with open(last_path, errors="replace") as fh:
                final = fh.read().strip()
        finally:
            try:
                os.remove(last_path)
            except OSError:
                pass
        return Reply(final, reply.status, reply.seconds) if reply.ok and final else reply


class GeminiAgent(Agent):
    def __init__(self, model="default", timeout=900, worker=False, key_env=None):
        self.model, self.timeout, self.worker, self.key_env = model, timeout, worker, key_env or {}
        self.name = f"gemini:{model}"
        self.pace_key = "gemini"

    def ask(self, system, prompt, cwd):
        # --skip-trust: headless Gemini refuses untrusted folders, and silently drops
        # plan (read-only) mode without it. It trusts this throwaway worktree for one call.
        cmd = ["gemini", "--skip-trust", "--approval-mode", "yolo" if self.worker else "plan", "-o", "json",
               "-p", fit_arg(f"{system}\n\n{prompt}")]
        if self.model and self.model != "default":
            cmd[1:1] = ["-m", self.model]
        reply = run_cli(cmd, cwd, self.timeout, env={**os.environ, **self.key_env})
        try:
            data = json.loads(reply.text[reply.text.index("{"):])
            return Reply(str(data.get("response") or ""), reply.status, reply.seconds)
        except ValueError:
            return reply


class AntigravityAgent(Agent):
    """Google's Antigravity CLI (`agy`), which replaced the Gemini CLI's personal
    login. It works in a workspace of its own (files land in its scratch folder
    unless told otherwise), so the worktree is added to its workspace and named
    in the prompt. Judges run in plan mode; workers accept edits unprompted.
    It reports quota errors inside a successful exit, so the JSON status decides."""

    def __init__(self, model, timeout=900, worker=False):
        self.model, self.timeout, self.worker = model, timeout, worker
        self.name = f"antigravity:{model}"
        self.pace_key = "antigravity"
        self.billing = "subscription"

    def ask(self, system, prompt, cwd):
        where = (f"The project is at {cwd}. Read and change files only there, using that absolute path; "
                 "never create files anywhere else." if self.worker else
                 f"The project to review is at {cwd}. Read files there using that absolute path; do not change anything.")
        cmd = ["agy", "--model", self.model, "--output-format", "json", "--add-dir", cwd,
               "--print-timeout", f"{max(60, self.timeout - 30)}s"]
        cmd += ["--mode", "accept-edits", "--dangerously-skip-permissions"] if self.worker else ["--mode", "plan"]
        cmd += ["--print", fit_arg(f"{system}\n\n{where}\n\n{prompt}")]
        reply = run_cli(cmd, cwd, self.timeout)
        try:
            data = json.loads(reply.text[reply.text.index("{"):reply.text.rindex("}") + 1])
        except ValueError:
            return reply
        u = data.get("usage") or {}
        usage = {self.model: {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0)
                              + int(u.get("thinking_tokens") or 0),
                              "cache_read": int(u.get("cache_read_tokens") or 0), "cache_write": 0, "usd": 0.0}}
        if str(data.get("status")).upper() != "SUCCESS":
            return Reply(str(data.get("error") or data.get("response") or "antigravity reported an error"),
                         reply.status or 1, reply.seconds, 0.0, usage)
        return Reply(str(data.get("response") or ""), reply.status, reply.seconds, 0.0, usage)


class OpenCodeAgent(Agent):
    """Any model OpenCode can reach: its own providers, OpenRouter, OpenAI-compatible
    endpoints and local servers such as Ollama or LM Studio. The model is written the
    way OpenCode writes it, provider/model. Workers run with --auto (edits and commands
    approved inside the worktree); judges get edits denied, and anything else they ask
    for is refused, because a non-interactive run rejects permission requests.

    Built from OpenCode's documented `run --format json` events; see docs/adapters.md."""

    LOCAL = {"ollama", "lmstudio", "llamacpp", "llama.cpp", "local"}

    def __init__(self, model, timeout=900, worker=False, key_env=None):
        self.model, self.timeout, self.worker, self.key_env = model, timeout, worker, key_env or {}
        provider = model.split("/", 1)[0]
        self.name = f"opencode:{model}"
        self.pace_key = f"opencode-{provider}"
        self.billing = "local" if provider in self.LOCAL else "api"

    def ask(self, system, prompt, cwd):
        cmd = ["opencode", "run", "--format", "json", "--model", self.model, "--dir", cwd]
        env = {**os.environ, **self.key_env}
        if self.worker:
            cmd.append("--auto")
        else:
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission": {"edit": "deny"}})
        cmd.append(fit_arg(f"{system}\n\n{prompt}"))

        def parse(line):
            if '"tool_use"' not in line:
                return None
            try:
                part = json.loads(line).get("part") or {}
                return part.get("tool"), json.dumps((part.get("state") or {}).get("input") or {})
            except (ValueError, AttributeError):
                return None
        reply = run_cli(cmd, cwd, self.timeout, env=env, on_line=self._watcher(parse))
        texts, errors, usage = [], [], {}
        row = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "usd": 0.0}
        for line in reply.text.splitlines():
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            part = event.get("part") or {}
            if event.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
            elif event.get("type") == "step_finish":
                tokens = part.get("tokens") or {}
                row["input"] += int(tokens.get("input") or 0)
                row["output"] += int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
                row["cache_read"] += int((tokens.get("cache") or {}).get("read") or 0)
                row["cache_write"] += int((tokens.get("cache") or {}).get("write") or 0)
                row["usd"] += float(part.get("cost") or 0)
                usage = {self.model: row}
            elif event.get("type") == "error":
                error = event.get("error") or {}
                errors.append(str((error.get("data") or {}).get("message") or error.get("message") or error))
        if errors:
            return Reply("\n".join(errors), reply.status or 1, reply.seconds, row["usd"], usage)
        if not texts:
            return reply
        return Reply("\n".join(texts), reply.status, reply.seconds, row["usd"], usage)


def make_agent(spec, timeout=900, worker=False, mcp_config=None, key_env=None, claude_api_key=None):
    """claude:MODEL | codex:MODEL | antigravity:MODEL | gemini:MODEL | qwen:MODEL | cline:PROVIDER:MODEL
    | opencode:PROVIDER/MODEL

    key_env: {ENV_NAME: key} from keys.environment(), for the tools that read keys from
    their environment. claude_api_key: set only when the person chose API billing for Claude Code."""
    kind, _, rest = spec.partition(":")
    if kind == "claude":
        return ClaudeAgent(rest or "sonnet", timeout, worker=worker, mcp_config=mcp_config, api_key=claude_api_key)
    if kind == "codex":
        return CodexAgent(rest, timeout, worker=worker)
    if kind == "gemini":
        return GeminiAgent(rest or "default", timeout, worker=worker, key_env=key_env)
    if kind == "antigravity":
        return AntigravityAgent(rest, timeout, worker=worker)
    if kind == "qwen":
        return QwenAgent(rest or "deepseek-flash", timeout, worker=worker)
    if kind == "cline":
        provider, _, model = rest.partition(":")
        return ClineAgent(provider, model, timeout)
    if kind == "opencode":
        return OpenCodeAgent(rest, timeout, worker=worker, key_env=key_env)
    raise ValueError(f"unknown model {spec!r} (want claude:, codex:, antigravity:, gemini:, qwen:, "
                     "cline:PROVIDER:MODEL or opencode:PROVIDER/MODEL)")


class Pacer:
    """Per-provider queue that learns how fast a provider can be called.

    Starts close together (floor) and widens sharply the moment the provider
    refuses a call for rate reasons, then narrows again slowly while calls keep
    getting through. A fixed 20s gap was safe but made every single-agent run
    pay for the spacing a swarm needed.

    The lock is held across the wait, so concurrent callers take turns rather
    than all waking at once, and it works across threads and separate runs
    (flock locks belong to open file descriptions). The learned spacing lives
    beside the lock, so separate runs share what was learned too.
    """

    CEILING = 60.0
    NARROW_AFTER = 8          # successful calls in a row before narrowing
    NARROW_FACTOR = 0.75

    def __init__(self, directory, floor=3.0, record=None, sleep=time.sleep):
        self.directory, self.floor, self.sleep = directory, float(floor), sleep
        self.record = record or (lambda seconds: None)
        os.makedirs(directory, exist_ok=True)

    def _state(self, key):
        try:
            with open(os.path.join(self.directory, key + ".json")) as fh:
                data = json.load(fh)
            return {"last": float(data.get("last", 0)), "interval": float(data.get("interval", self.floor)),
                    "streak": int(data.get("streak", 0))}
        except (OSError, ValueError):
            return {"last": 0.0, "interval": self.floor, "streak": 0}

    def _save(self, key, state):
        tmp = os.path.join(self.directory, key + ".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, os.path.join(self.directory, key + ".json"))

    def _locked(self, key, fn):
        with open(os.path.join(self.directory, key + ".lock"), "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                return fn(self._state(key))
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def interval(self, key):
        return self._state(key)["interval"]

    def wait_turn(self, key):
        def turn(state):
            interval = max(self.floor, state["interval"])
            now = time.time()
            wait = state["last"] + interval - now
            if wait > 0:
                self.sleep(wait)
                self.record(wait)
                now = state["last"] + interval
            state["last"] = now
            self._save(key, state)
        self._locked(key, turn)

    def refused(self, key):
        """The provider said slow down: widen to at least 20s, doubling each time."""
        def widen(state):
            state["interval"] = min(self.CEILING, max(20.0, state["interval"] * 2))
            state["streak"] = 0
            self._save(key, state)
            return state["interval"]
        return self._locked(key, widen)

    def succeeded(self, key):
        def narrow(state):
            state["streak"] += 1
            if state["streak"] >= self.NARROW_AFTER and state["interval"] > self.floor:
                state["interval"] = max(self.floor, state["interval"] * self.NARROW_FACTOR)
                state["streak"] = 0
            self._save(key, state)
        self._locked(key, narrow)


class Retrying(Agent):
    """Pacing plus backing off. None of these failures is the model's fault, so
    none of them is reported as a failed implementation or review. Trouble that
    outlasts the backoff, and fatal trouble, is returned to the caller, which
    asks a person rather than silently giving up."""

    def __init__(self, inner, pacer, say, retries=5, sleep=time.sleep, usage=None, on_fatal=None):
        self.inner, self.pacer, self.say, self.retries, self.sleep = inner, pacer, say, retries, sleep
        self.on_fatal = on_fatal or (lambda line: None)
        self.usage = usage
        self.name, self.pace_key = inner.name, inner.pace_key
        self.billing = getattr(inner, "billing", "api")

    def ask(self, system, prompt, cwd):
        attempt = 0
        while True:
            self.pacer.wait_turn(self.pace_key)
            reply = self.inner.ask(system, prompt, cwd)
            if self.usage is not None:
                if reply.usage:
                    self.usage.add(reply.usage, self.billing)
                elif isinstance(self.inner, ClineAgent):
                    self.usage.refresh()
            kind = classify(reply.status, reply.text)
            if kind == "rate":
                spacing = self.pacer.refused(self.pace_key)
            elif kind != "missing":
                self.pacer.succeeded(self.pace_key)
            schedule = BACKOFF.get(kind)
            if not schedule or attempt >= min(self.retries, len(schedule)):
                if kind == "fatal":
                    self.say(f"   {self.name} cannot continue: {error_line(reply.text)}")
                    self.on_fatal(error_line(reply.text))
                return reply
            wait = schedule[attempt]
            attempt += 1
            if kind == "rate":
                self.say(f"   rate limited; waiting {wait}s, then the same call again ({attempt}/{self.retries}, "
                         f"calls now at least {spacing:g}s apart)")
            elif kind == "missing":
                self.say(f"   {self.name} could not be started (exit {reply.status}, probably updating itself); "
                         f"retrying in {wait}s")
            else:
                self.say(f"   provider error; retrying the same call in {wait}s ({attempt}/{self.retries})")
            self.sleep(wait)
