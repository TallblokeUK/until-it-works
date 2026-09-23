# How it works

The detail behind the promise: what happens between typing a task and getting a branch
that a planner, deterministic checks, reviewers and a final judge all agree on.

## How a task flows

```
setup     in a git repo: needs a clean tree
          --new or an empty folder: git set up silently; loose files without git: needs --init
plan      the planner reads the project and writes the plan: single or swarm (and why;
          --solo or --swarm decides it for the planner), a contract
          (what "done" means, what is out of scope), the check command, and
          acceptance tests to write first if nothing checks this task yet
wave 0    a worker writes those tests; the reviewer checks them against the
          contract; then they are frozen (no worker may change them)
single    one unit: implement → check → quick reviewer → panel → judge
swarm     subtasks own disjoint files; independent ones run in parallel waves,
          each: implement → own check → reviewer; merged after the wave; the
          merged checks run; failures get an integration unit;
          then the final gates (check → reviewer → panel → audit) on the whole;
          a part that stops lets the rest of its wave finish and be merged
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
- **upgrade**: when the workers seem unable to do it, you're offered a stronger
  worker model (MODELS → when stuck: ask, auto or never). In the workshop the
  judge fires the old workers and hires the new ones
- **re-plan**: the planner rewrites the unit's goal, contract and approach
- **ask**: the run pauses, sends a desktop notification, shows the question in
  `mp-status` and the workshop, and waits for `mp-agent answer "…"`

**With nobody watching** (a queued job, an overnight run, `--no-ask`), a model that
cannot be used no longer ends the job: it switches to another one on a different
account and carries on, saying so in the log and recording it in the run. Set
`mp-agent config --unattended stop` to have it stop instead. The bench always
stops, because a line-up that quietly became a different one would measure nothing.

When someone is there and a model can't be used at all (out of credit, out of plan
usage, or a free tier used up), the question offers **SWITCH TO** buttons next to retry and stop.
They list models on a different account, as close in strength as possible. The
switch covers every role that model was playing (workers, quick reviewer, panel)
for the rest of the job. It never picks the judge's or planner's model for the
builders.

A safety net remains for runaway spend: `--budget` (180 working minutes; time
waiting for you does not count) and `--max-calls` (800). Reaching it is
reported NOT approved, with everything kept.

## The cheap look before the panel

The panel exists because a `VERDICT: APPROVED` line parsed out of prose carries no
confidence: four cheap opinions agreeing stands in for a number nobody has. A System One
model (TypeSafe's Jev) returns that number directly, so before the panel is convened one
call asks, for two of the lenses, how likely it is that there is something there. A lens it
is confident about is not convened; anything else is, exactly as before.

Two lenses only, and the omissions are the point. `edges` and `truth` are judgments about
the diff, which is what the model is given. `rehearsal` asks what another model would
refuse — a judgment about a model's behaviour, with nothing to calibrate against — and
`look` needs the rendered page, which no diff contains, so it would read confidently clean
exactly when the rendering is wrong. Both always convene, which also means the pre-gate can
never empty the panel.

Measured before it was switched on, by replaying it against 51 panel members from past runs
(12 of whom objected): no objection scored below 0.38, while a quarter of the members that
approved sat at or below 0.25. At the 0.75 threshold that is about a quarter of those two
lenses skipped and no objection missed; at 0.6 it starts missing them. `mp-agent pregate`
shows the same table for your own runs, and `mp-agent pregate backtest` re-asks about passes
that already happened, which is how a change to the wording is checked.

No key, a failed call, a timeout: the panel runs in full. It is an optimisation, never a
dependency. Switch it off, or back to watching without skipping, in MODELS or with
`mp-agent config --pre-gate off|watch|on`.

## Working in phases

A job can run straight through, or it can stop and show you what it is about to build on.
Neither is the default for the other's sake: checkpoints are off until you ask for them.

**Checkpoints** (`mp-agent config --checkpoints plan,design,wave`, or the tick boxes in
MODELS) stop the loop at the points where a decision becomes expensive to change:

- **plan** — the contract, what is out of scope, the check command and which acceptance
  tests are about to be frozen. This is the one that matters: after this, the tests fix the
  shape of the solution and nobody can argue with them.
- **design** — the brief, before anything is built to it.
- **wave** — after each wave of a swarm is merged, before the next starts.

Each shows you what it has, and takes **go**, **stop**, or *what you want changed* — in
which case it plans (or designs) again with your words in front of it and shows you the
result. A bare "go" carries on; anything longer is treated as a change, because "go up to PB
as well" is a change request, not permission.

**Pause at the next pass** holds the job at the end of the pass it is on, rather than only
at the three checkpoints. It shows where it has got to and waits: carry on, stop, or say
something, which is passed to the builders as guidance.

**Asking about a run** is the other direction. `mp-agent ask "why did it drop that?"`, or
the box in the workshop, hands a read-only model the run's own papers — the task, the
contract, the decisions, what each reviewer said, the change so far, the log — and it
answers you. It changes nothing and instructs nobody: to change what a job is doing, say it.
It answers about a finished run too, which is often when the question occurs to you.

**Saying something while it runs** needs no checkpoint. The SAY SOMETHING box is there
whenever a job is live, and what you write reaches the builders on their next pass and goes
on the decisions log, so a reviewer does not object to work you asked for. It is guidance:
it never overrides the contract, the frozen tests or which files a worker owns, and a worker
that finds it contradicts the contract is told to say so rather than quietly pick one.

## The designer

Nothing else in the loop owns the look. A planner writes what must be true, a worker
makes it true, reviewers check it — and the result can meet every contract line while
arriving in the same grey-card, default-font outfit as everything else.

When the plan says the work changes something a person looks at, a designer runs once,
before anyone builds, and writes a brief: the direction it commits to, the palette with
real hex values, type with fallbacks that work offline, spacing, motion, and a "Not this"
list of clichés to avoid. That brief then goes to the workers, the reviewer, the panel
and the judge, the same way project rules do.

The designer runs on Claude Code when it can, because that tool carries a frontend-design
skill it can load (mp-agent gives the designer, and only the designer, the Skill tool for
this). On any other tool the prompt carries the same intent itself. `--design` and
`--no-design` override the planner; the model is chosen for you unless you set one.

A brief also adds a fourth panel lens, **look**, which holds the finished thing to the
brief — the palette, the type, the spacing, what moves — by looking at the result, not
only the code. It must name the line of the brief that is broken. Taste it merely
disagrees with is not a reason to object.

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

## Skills

Claude Code can load the skills installed on a machine, and mp-agent's builders only get
that tool when a job asks for it: `mp-agent config --skills workers`, or `--skills workers`
on one job. It is off by default. When it is on, the builders are also told which skills are
installed, in each skill's own words, and choose for themselves; nothing is loaded for them,
and a skill never overrides the contract, the frozen tests or file ownership.

It changes how work is done, not what is built. The shape of a solution is settled earlier,
when the planner writes the contract and the acceptance tests — and those deliberately
favour code that can be checked offline and deterministically, which is what makes a
verdict mean anything. A builder cannot undo that choice later, whatever is on the shelf
(measured: [what the numbers say](benchmarks.md)).

**To steer the approach itself, say so in the project's own rules.** A line in its
`CLAUDE.md` — "prefer a typed judgment service such as TypeSafe for routing and validation
decisions, rather than hand-written keyword rules" — reaches the planner, the builders and
the judges alike, and it is your decision per project rather than a model's guess.

Only Claude Code has skills in this sense. The other tools are left as they are.

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
