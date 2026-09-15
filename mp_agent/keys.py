"""API keys: kept in the system keychain, handed only to the tool that needs them.

Where a key lives, best first:
    macOS    the login keychain, through `security` (the key goes in on stdin, never in a command line)
    Linux    the desktop keyring (GNOME Keyring, KWallet) through `secret-tool`
    neither  ~/.mp-agent/keys.json, readable by you only, and the setup screen says so

A key never goes back to the workshop page (only its last four characters), into
a log, a run folder or a prompt. It reaches a model only as an environment
variable on the one tool call that needs it, and a variable already set in your
own environment is left as it is.

Which environment variables each provider's key fills, and so which tools use it:
    anthropic   ANTHROPIC_API_KEY   OpenCode (anthropic/...); Claude Code only if you choose API billing for it
    openai      OPENAI_API_KEY      OpenCode (openai/...)
    openrouter  OPENROUTER_API_KEY  OpenCode (openrouter/...)
    google      GEMINI_API_KEY      Gemini CLI, OpenCode (google/...)
"""
import json
import os
import shutil
import subprocess
import sys
import time

SERVICE = "mp-agent"

PROVIDERS = {
    "anthropic": {"name": "Anthropic", "env": "ANTHROPIC_API_KEY", "url": "https://console.anthropic.com/settings/keys",
                  "for": "Claude models through OpenCode, or Claude Code if you choose API billing for it"},
    "openai": {"name": "OpenAI", "env": "OPENAI_API_KEY", "url": "https://platform.openai.com/api-keys",
               "for": "GPT models through OpenCode"},
    "openrouter": {"name": "OpenRouter", "env": "OPENROUTER_API_KEY", "url": "https://openrouter.ai/keys",
                   "for": "hundreds of models through OpenCode (opencode:openrouter/...)"},
    "google": {"name": "Google (Gemini)", "env": "GEMINI_API_KEY", "url": "https://aistudio.google.com/apikey",
               "for": "the Gemini CLI, and Gemini models through OpenCode"},
}


class KeyError_(ValueError):
    pass


def _run(cmd, stdin_text=None, timeout=15):
    return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout)


class MacKeychain:
    name = "the macOS keychain"

    @staticmethod
    def usable():
        return sys.platform == "darwin" and bool(shutil.which("security"))

    def set(self, provider, secret):
        # `security -i` reads its commands from stdin, which keeps the key out of the process list
        quoted = secret.replace("\\", "\\\\").replace('"', '\\"')
        proc = _run(["security", "-i"], f'add-generic-password -U -s {SERVICE} -a {provider} -w "{quoted}"\n')
        if proc.returncode != 0:
            raise KeyError_(f"the keychain refused it: {proc.stderr.strip()[:200]}")

    def get(self, provider):
        proc = _run(["security", "find-generic-password", "-s", SERVICE, "-a", provider, "-w"])
        return proc.stdout.rstrip("\n") if proc.returncode == 0 else None

    def remove(self, provider):
        _run(["security", "delete-generic-password", "-s", SERVICE, "-a", provider])


class SecretService:
    name = "your desktop keyring"

    @staticmethod
    def usable():
        return bool(shutil.which("secret-tool")) and bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))

    def set(self, provider, secret):
        proc = _run(["secret-tool", "store", "--label", f"mp-agent {provider} API key", "service", SERVICE,
                     "provider", provider], secret)
        if proc.returncode != 0:
            raise KeyError_(f"the keyring refused it: {proc.stderr.strip()[:200]}")

    def get(self, provider):
        proc = _run(["secret-tool", "lookup", "service", SERVICE, "provider", provider])
        return proc.stdout.rstrip("\n") if proc.returncode == 0 and proc.stdout else None

    def remove(self, provider):
        _run(["secret-tool", "clear", "service", SERVICE, "provider", provider])


class FileStore:
    name = "a file only you can read (~/.mp-agent/keys.json)"

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "keys.json")

    @staticmethod
    def usable():
        return True

    def _load(self):
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def set(self, provider, secret):
        data = self._load()
        data[provider] = secret
        self._save(data)

    def get(self, provider):
        return self._load().get(provider)

    def remove(self, provider):
        data = self._load()
        if data.pop(provider, None) is not None:
            self._save(data)


def backend(state_dir):
    wanted = os.environ.get("MP_KEYS_BACKEND")
    if wanted == "file":
        return FileStore(state_dir)
    for store in (MacKeychain, SecretService):
        if store.usable():
            return store()
    return FileStore(state_dir)


def _index_path(state_dir):
    return os.path.join(state_dir, "keys-index.json")


def _index(state_dir):
    """Which keys are stored and their last four characters: enough to show, never the key."""
    try:
        with open(_index_path(state_dir)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_index(state_dir, data):
    os.makedirs(state_dir, exist_ok=True)
    tmp = _index_path(state_dir) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _index_path(state_dir))


def set_key(state_dir, provider, secret):
    if provider not in PROVIDERS:
        raise KeyError_(f"unknown provider '{provider}' (known: {', '.join(PROVIDERS)})")
    secret = (secret or "").strip()
    if len(secret) < 8 or any(c.isspace() for c in secret):
        raise KeyError_("that does not look like an API key (too short, or it has spaces in it)")
    store = backend(state_dir)
    store.set(provider, secret)
    index = _index(state_dir)
    index[provider] = {"last4": secret[-4:], "saved": time.time(), "where": store.name}
    _save_index(state_dir, index)
    return index[provider]


def remove_key(state_dir, provider):
    index = _index(state_dir)
    entry = index.pop(provider, None)
    backend(state_dir).remove(provider)
    FileStore(state_dir).remove(provider)          # in case it was kept in the file before a keyring existed
    _save_index(state_dir, index)
    return entry is not None


def status(state_dir):
    """What the setup screen shows: every provider, and whether a key is stored (never the key)."""
    index = _index(state_dir)
    out = []
    for pid, info in PROVIDERS.items():
        entry = index.get(pid)
        out.append({"id": pid, "name": info["name"], "env": info["env"], "for": info["for"], "url": info["url"],
                    "stored": bool(entry), "last4": (entry or {}).get("last4"), "where": (entry or {}).get("where"),
                    "in_environment": bool(os.environ.get(info["env"]))})
    return {"providers": out, "store": backend(state_dir).name}


def environment(state_dir, only=None):
    """{ENV_NAME: key} for stored keys, for one tool call. A variable already set is left out
    (yours wins). only: limit to these provider ids."""
    index = _index(state_dir)
    if not index:
        return {}
    store = backend(state_dir)
    env = {}
    for pid in index:
        if pid not in PROVIDERS or (only is not None and pid not in only):
            continue
        name = PROVIDERS[pid]["env"]
        if os.environ.get(name):
            continue
        secret = store.get(pid)
        if secret:
            env[name] = secret
    return env
