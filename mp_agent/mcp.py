"""One list of MCP servers, handed to every AI tool the same way.

The list is two built-in servers (Context7 for current library docs, and a headless
Playwright browser whose screenshots are kept with the run) plus any you add, for all
projects or for one. Every role gets the same tools, whatever tool it runs on:

    Claude Code  --mcp-config (a file written for the run) with --strict-mcp-config: exactly this list
    Codex        -c mcp_servers.* for each server, and the servers in its own config switched off
    OpenCode     the "mcp" block of its per-call config
    Gemini CLI   a settings file passed as its system settings, with --allowed-mcp-server-names
    Qwen Code    --mcp-config with the same file, and --allowed-mcp-server-names
    Cline        added to Cline's own MCP settings (servers already there stay: Cline has no per-call list)
    Antigravity  its own settings only

API keys are never written into these files: a server's env holds a reference such as
${CONTEXT7_API_KEY}, and the key reaches the tool only as an environment variable for
that call (see keys.py).
"""
import json
import os
import re
import shlex
import subprocess

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

BUILTIN_ABOUT = {
    "context7": "current documentation for libraries and frameworks",
    "playwright": "a headless browser, so agents can check web pages (screenshots are kept with the run)",
}


def builtin(shots_dir):
    return {
        "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"], "env_keys": ["CONTEXT7_API_KEY"]},
        "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp@latest", "--headless", "--isolated",
                                                  "--output-dir", shots_dir]},
    }


def _load(state_dir):
    try:
        with open(os.path.join(state_dir, "config.json")) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state_dir, data):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "config.json.tmp"), "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(os.path.join(state_dir, "config.json.tmp"), os.path.join(state_dir, "config.json"))


def settings(state_dir):
    mcp = _load(state_dir).get("mcp") or {}
    return {"servers": dict(mcp.get("servers") or {}), "disabled": list(mcp.get("disabled") or []),
            "projects": dict(mcp.get("projects") or {})}


def listing(state_dir, shots_dir, project=None):
    """Every server with where it comes from, for the workshop and `mp-agent mcp list`."""
    s = settings(state_dir)
    rows = []
    for name, spec in builtin(shots_dir).items():
        rows.append({"name": name, "scope": "built-in", "on": name not in s["disabled"], "about": BUILTIN_ABOUT[name],
                     **_describe(spec)})
    for name, spec in s["servers"].items():
        rows.append({"name": name, "scope": "all projects", "on": True, **_describe(spec)})
    for path, servers in s["projects"].items():
        if project and os.path.realpath(path) != os.path.realpath(project):
            continue
        for name, spec in servers.items():
            rows.append({"name": name, "scope": path, "on": True, **_describe(spec)})
    return rows


def _describe(spec):
    if spec.get("url"):
        return {"what": spec["url"], "kind": "remote"}
    return {"what": " ".join([spec.get("command", "")] + list(spec.get("args") or [])), "kind": "local"}


def servers_for(state_dir, shots_dir, project=None):
    """{name: spec} for a job: built-ins that are on, then yours for all projects, then this project's."""
    s = settings(state_dir)
    out = {name: spec for name, spec in builtin(shots_dir).items() if name not in s["disabled"]}
    out.update(s["servers"])
    if project:
        for path, servers in s["projects"].items():
            if os.path.realpath(path) == os.path.realpath(project):
                out.update(servers)
    return out


def parse_spec(command=None, url=None, env_keys=None):
    """A server from what a person typed: a command line, or a URL."""
    if url:
        if not re.match(r"^https?://", url):
            raise ValueError("a remote server's address must start with http:// or https://")
        spec = {"url": url}
    else:
        parts = shlex.split(command or "")
        if not parts:
            raise ValueError("give the command that starts the server, or its URL")
        spec = {"command": parts[0], "args": parts[1:]}
    keys = [k for k in (env_keys or []) if re.match(r"^[A-Z][A-Z0-9_]{1,60}$", k)]
    if keys:
        spec["env_keys"] = keys
    return spec


def add(state_dir, name, spec, project=None):
    if not NAME_RE.match(name):
        raise ValueError("a server name is lowercase letters, digits, - and _ (for example: sentry)")
    data = _load(state_dir)
    mcp = data.setdefault("mcp", {})
    if project:
        mcp.setdefault("projects", {}).setdefault(os.path.realpath(project), {})[name] = spec
    else:
        mcp.setdefault("servers", {})[name] = spec
    _save(state_dir, data)


def remove(state_dir, name, project=None):
    data = _load(state_dir)
    mcp = data.setdefault("mcp", {})
    if project:
        servers = mcp.get("projects", {}).get(os.path.realpath(project), {})
    else:
        servers = mcp.get("servers", {})
    found = servers.pop(name, None) is not None
    if project and not servers:
        mcp.get("projects", {}).pop(os.path.realpath(project), None)
    _save(state_dir, data)
    return found


def switch_builtin(state_dir, name, on):
    if name not in BUILTIN_ABOUT:
        raise ValueError(f"{name} is not a built-in server (those are: {', '.join(BUILTIN_ABOUT)})")
    data = _load(state_dir)
    mcp = data.setdefault("mcp", {})
    disabled = [n for n in mcp.get("disabled") or [] if n != name]
    if not on:
        disabled.append(name)
    mcp["disabled"] = disabled
    _save(state_dir, data)


# ---- how each tool is given the list ------------------------------------------------------

def claude_config(servers):
    out = {}
    for name, spec in servers.items():
        if spec.get("url"):
            out[name] = {"type": "http", "url": spec["url"]}
        else:
            out[name] = {"type": "stdio", "command": spec["command"], "args": list(spec.get("args") or []),
                         "env": {k: "${" + k + "}" for k in spec.get("env_keys") or []}}
    return {"mcpServers": out}


def claude_allowed_tools(servers):
    return ",".join(f"mcp__{name}" for name in servers)


def codex_args(servers, own=()):
    args = []
    for name in own:
        if name not in servers:
            args += ["-c", f"mcp_servers.{name}.enabled=false"]
    for name, spec in servers.items():
        if spec.get("url"):
            args += ["-c", f"mcp_servers.{name}.url={json.dumps(spec['url'])}"]
        else:
            args += ["-c", f"mcp_servers.{name}.command={json.dumps(spec['command'])}",
                     "-c", f"mcp_servers.{name}.args={json.dumps(list(spec.get('args') or []))}"]
            if spec.get("env_keys"):
                args += ["-c", f"mcp_servers.{name}.env_vars={json.dumps(list(spec['env_keys']))}"]
    return args


def codex_own_servers(run=None):
    """The servers in the person's own Codex config, so a job can switch them off."""
    try:
        out = subprocess.run(["codex", "mcp", "list", "--json"], capture_output=True, text=True, timeout=20).stdout
        return [s.get("name") for s in json.loads(out) if s.get("name")]
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return []


def opencode_config(servers):
    out = {}
    for name, spec in servers.items():
        if spec.get("url"):
            out[name] = {"type": "remote", "url": spec["url"], "enabled": True}
        else:
            out[name] = {"type": "local", "command": [spec["command"], *list(spec.get("args") or [])], "enabled": True,
                         "environment": {k: "{env:" + k + "}" for k in spec.get("env_keys") or []}}
    return {"mcp": out}


def gemini_settings(servers):
    """For Gemini CLI and Qwen Code (a Gemini CLI fork)."""
    out = {}
    for name, spec in servers.items():
        if spec.get("url"):
            out[name] = {"httpUrl": spec["url"]}
        else:
            out[name] = {"command": spec["command"], "args": list(spec.get("args") or []),
                         "env": {k: "$" + k for k in spec.get("env_keys") or []}}
    return {"mcpServers": out}


def sync_cline(servers, run=subprocess.run):
    """Add any missing local servers to Cline's own MCP settings. Returns the names added."""
    path = os.path.join(os.path.expanduser("~"), ".cline", "data", "settings", "cline_mcp_settings.json")
    try:
        with open(path) as fh:
            have = set((json.load(fh).get("mcpServers") or {}).keys())
    except (OSError, ValueError):
        have = set()
    added = []
    for name, spec in servers.items():
        if name in have or spec.get("url"):
            continue
        proc = run(["cline", "mcp", "install", name, "--yes", "--", spec["command"], *list(spec.get("args") or [])],
                   capture_output=True, text=True, timeout=120)
        if getattr(proc, "returncode", 1) == 0:
            added.append(name)
    return added


class Tools:
    """What one job hands to its agents: the servers, and the files some tools read them from."""

    def __init__(self, servers, folder, codex_own=(), available_env=None):
        # A key reference whose variable is not set would reach the server as literal text
        # (and Context7 would reject it), so only keys that are actually available are referenced.
        available = set(available_env or ()) | {k for k, v in os.environ.items() if v}
        servers = {name: {**spec, "env_keys": [k for k in spec.get("env_keys") or [] if k in available]}
                   for name, spec in servers.items()}
        self.servers = dict(servers)
        self.env_keys = sorted({k for spec in servers.values() for k in spec.get("env_keys") or []})
        os.makedirs(folder, exist_ok=True)
        self.claude_file = os.path.join(folder, "mcp-claude.json")
        self.gemini_file = os.path.join(folder, "mcp-gemini.json")
        with open(self.claude_file, "w") as fh:
            json.dump(claude_config(self.servers), fh, indent=2)
        with open(self.gemini_file, "w") as fh:
            json.dump(gemini_settings(self.servers), fh, indent=2)
        self.codex = codex_args(self.servers, codex_own)
        self.opencode = opencode_config(self.servers)["mcp"]
