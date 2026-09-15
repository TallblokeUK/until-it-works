# Adding a tool

Every model call in mp-agent goes through one small interface in
`mp_agent/providers.py`. Supporting another command-line coding tool means
writing one class and adding one line to `make_agent`.

## The interface

```python
class Agent:
    name = "tool:model"      # shown in logs and the workshop
    pace_key = "tool"        # calls with the same key share one pacing queue
    billing = "api"          # "api", "subscription" or "local"
    on_activity = None       # set by mp-agent; report tool use through self._watcher

    def ask(self, system, prompt, cwd) -> Reply:
        ...
```

`ask` runs **one fresh session** of the tool in the folder `cwd` and returns a
`Reply`:

| Field | Meaning |
|---|---|
| `text` | The model's final answer (not the whole transcript, if the tool can separate them) |
| `status` | The exit status: 0 for success. Use 127 if the tool is missing and 124 for a timeout |
| `seconds` | How long it took (`run_cli` fills this in) |
| `usage` | Optional: `{model: {"input", "output", "cache_read", "cache_write", "usd"}}` |

## What an adapter must get right

1. **Two permission levels.** mp-agent constructs the agent with
   `worker=True` for workers, which may edit files and run commands inside
   `cwd` without stopping to ask. Everything else (planner, reviewer, panel,
   judge) gets `worker=False` and must not change files. mp-agent also
   snapshots the worktree before every review and restores it if a reviewer
   changed anything, but the adapter should make that unnecessary.
2. **No questions.** The session must never wait for a person. Use the tool's
   non-interactive mode, and make sure a permission prompt is refused rather
   than left hanging.
3. **A fresh session each time.** No `--continue` and no shared history: a
   reviewer must not remember writing the code.
4. **Run it with `run_cli`.** It writes output to a file rather than a pipe
   (some Node-based tools truncate long output on exit), enforces the timeout,
   and lets the person's STOP button kill the call.
5. **Honest errors.** Leave out-of-credit, rate-limit and login messages in
   `text` with a non-zero `status`. `classify()` recognises the common phrasings
   and decides whether to back off, retry or ask the person. If the tool reports
   an error inside a successful exit, turn it into a non-zero status (see
   `AntigravityAgent`).
6. **Billing.** Say how calls are paid for, so the spending cap counts real
   money only.

## Showing activity in the workshop

Pass `on_line=self._watcher(parse)` to `run_cli`, where `parse(line)` returns
`(tool_name, details)` when a line of output shows the model using a tool, and
`None` otherwise. Documentation lookups and browser use then appear as the
character visiting the DOCS cabinet or the WEB screen.

## Registering it

In `make_agent`:

```python
if kind == "mytool":
    return MyToolAgent(rest, timeout, worker=worker)
```

And in `mp_agent/models.py`, add the tool's models to `available()` (only when
the tool is installed and logged in), so they appear in `mp-agent models` and
the workshop.

## Testing it

`tests/test_units.py` has `test_opencode_adapter_reads_its_json_events`. It puts
a fake `opencode` script first on `PATH` that records its arguments and prints
sample output, then checks the command line, the parsed text, the usage and the
permissions. Do the same for a new tool, then run one real job with it:

```bash
mp-agent start --worker mytool:some-model "Create hello.txt containing the word hello."
```

## The adapters so far

| Class | Tool | Read-only mode | Worker mode | Notes |
|---|---|---|---|---|
| `ClaudeAgent` | Claude Code | read tools only | edit and bash, bypass permissions | no API key in its environment; only mp-agent's MCP servers |
| `CodexAgent` | Codex | `-s read-only` | `-s workspace-write` | final answer read from `-o FILE` |
| `ClineAgent` | Cline | (reviews are guarded by snapshot and restore) | `--auto-approve true` | usage read from Cline's session files |
| `OpenCodeAgent` | OpenCode | edits denied; other requests refused | `--auto` | JSON events; built from the documentation and tested against a fake, not yet a real install |
| `AntigravityAgent` | Antigravity | `--mode plan` | `--mode accept-edits` | errors arrive inside a successful exit |
| `QwenAgent` | Qwen Code | `--approval-mode plan` | `--approval-mode yolo` | |
| `GeminiAgent` | Gemini CLI | `--approval-mode plan` | `--approval-mode yolo` | needs `--skip-trust` to honour plan mode headless |
