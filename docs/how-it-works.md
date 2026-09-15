# How it works

The detail behind the promise: what happens between typing a task and getting a branch
that a planner, deterministic checks, reviewers and a final judge all agree on.

## How a task flows

```
setup     in a git repo: needs a clean tree
          --new or an empty folder: git set up silently; loose files without git: needs --init
plan      the planner reads the project and writes the plan: single or swarm, a contract
          (what "done" means, what is out of scope), the check command, and
          acceptance tests to write first if nothing checks this task yet
wave 0    a worker writes those tests; the reviewer checks them against the
          contract; then they are frozen (no worker may change them)
single    one unit: implement → check → quick reviewer → panel → judge
swarm     subtasks own disjoint files; independent ones run in parallel waves,
          each: implement → own check → reviewer; merged after the wave; the
          merged checks run; failures get an integration unit;
          then the final gates (check → reviewer → panel → audit) on the whole
```

Everything happens on a branch in a throwaway worktree. The user's checkout is
never touched; the finished work is a branch to review and merge.

## Until it works, without looping forever

There is no pass ceiling. A unit keeps going while it makes progress and
escalates when it stops:

- **stalled** means: passes that change nothing (`--patience`, 3), the code
  returning to a version it already tried, or many rounds in a row without
  approval (`--churn`, 8)
- **ruling**: the planner settles what is going round in circles and amends the
  contract, has a wrong frozen test repaired, or gives advice
- **re-plan**: the planner rewrites the unit's goal, contract and approach
- **ask**: the run pauses, sends a desktop notification, shows the question in
  `mp-status` and the workshop, and waits for `mp-agent answer "…"`

A safety net remains for runaway spend: `--budget` (180 working minutes; time
waiting for you does not count) and `--max-calls` (800). Reaching it is
reported NOT approved, with everything kept.

## Project rules

Each AI tool loads its own kind of instructions file by itself (Claude Code reads
CLAUDE.md, Codex AGENTS.md, Cline .clinerules), so on its own a judge could
enforce a rule the workers never saw. Before planning, every job collects the
project's CLAUDE.md, AGENTS.md, GEMINI.md, QWEN.md, rules.md, CONVENTIONS.md,
.cursorrules, .cursor/rules, .clinerules, .windsurfrules and
.github/copilot-instructions.md. Identical copies are included once, links
pointing outside the project are ignored, and the size is capped. The same
"Project rules" section then goes to the planner, the workers, the reviewer,
the panel, the judge and any ruling. A reviewer may block a change that breaks
one, citing the file. The rules never override the loop's own rules: file
ownership, frozen tests and the verdict format. Switch them off per project
with RULES in the project bar, or `mp-agent rules PATH off`.

## MCP servers

Each job builds one list of MCP servers: the built-in Context7 and Playwright
(unless switched off), then yours for every project, then this project's. Each
tool gets that list its own way:

- **Claude Code:** `--mcp-config` with `--strict-mcp-config`, so your other
  connectors (mail, drive) never load.
- **Codex:** a `-c mcp_servers.*` setting for each server, with the servers
  from your own Codex config switched off for the job.
- **OpenCode:** its per-call config.
- **Gemini CLI:** its system settings.
- **Qwen Code:** `--mcp-config`, plus `--allowed-mcp-server-names` for both
  Gemini and Qwen.
- **Cline:** it has no per-call list, so missing servers are added to its own
  settings.
- **Antigravity:** keeps its own.

A server's key is referenced by name (`${CONTEXT7_API_KEY}`) and passed to the
tool as an environment variable for that call only. A reference is only included
when the key is actually set.

## Contracts and the decisions log

Reviewers judge against the plan's contract, not their own taste. A blocking
objection must cite a contract line or a real in-scope bug with a concrete
input; out-of-scope items are never reasons to block. When the implementer
resolves a finding it records `DECISION: …`, or refuses with `DECLINE: … — C2`;
every later reviewer sees the log and may not reopen a settled decision. A
reviewer that finds a genuine gap writes `AMBIGUITY: …` and the planner rules.

Why: without a contract, the first version's panel objected that whitespace handling
was too loose, later that it was too strict, then demanded Unicode digits and
`None` handling nobody asked for: 12 passes, 10 panel rounds, 29 minutes, and
code full of out-of-scope handling.

## Safety rails

- Reviewers run as agents with auto-approval, so the implementer's version is
  snapshotted before every review and restored if a reviewer changed anything.
- A worker that edits files it does not own, or a frozen test, has those
  changes reverted and is told so.
- Provider trouble is never counted against the work. Each call is classified:
  rate limited (backs off 1, 2, 4, 5, 5 minutes and widens the pacing), server
  errors and a missing `cline` (it updates itself in place; 30s up to 5 min),
  or fatal (out of credit, out of plan usage, not logged in; no pointless
  retries). If trouble outlasts the backoff, or is fatal, the run pauses and
  asks you, e.g. "claude:sonnet cannot continue (out of credit or usage…) —
  reply 'retry' to carry on", instead of ending. A reviewer that merely
  crashes is re-run once on the same code before anyone is bothered.
- Claude Code calls run with no API key and no parent Claude Code session
  variables in their environment, so they can only use your Claude login,
  unless you chose to pay for Claude Code with your Anthropic API key in SETUP.
- Providers meter tokens per minute, so calls to the same provider wait their
  turn in a shared queue (`--min-interval`, 3s to start, widening by itself when
  a provider refuses). This, not the model, limits how wide a swarm can go.

## The workshop

`mp-viz` serves http://127.0.0.1:7788 and every run starts it; it opens a
window only if none is watching and exits after 20 idle minutes (`MP_VIZ=0` to skip).
The Judge drafts the plan at the blueprint board and bangs the gavel on
audits and rulings; the Builder codes at the desk; Checkbot runs the test rig;
Red Pen reviews; three interns form the pre-audit panel; each swarm subtask
gets its own mini builder at a labelled desk, and they carry their work to the
rig when a wave merges. When a run needs you, the phone rings and the question
appears under the screen. The PROJECT picker narrows the run tabs, HISTORY,
QUEUE and NEW JOB to one project. `?demo=1` plays a scripted swarm run for free.

## Tests

    cd tests && python3 -m unittest

Unit tests for git handling, plan validation, waves, progress, parsing, retries, keys, launchers and the
desktop pieces, plus end-to-end flows through the real orchestrator with scripted agents: single mode,
objections and decisions, reviewer vandalism, a stall settled by a ruling, stuck → question → answer, a
two-wave swarm with an ownership violation, a failed subtask, frozen wave-0 tests and their repair, and
the workshop server's guards. They run on Linux and macOS, Python 3.9 and 3.12, on every push.
