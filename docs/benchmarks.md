# What the numbers say

Which models to put in which role is the question everyone asks first. These are
measurements, not opinions, and you can repeat them: `mp-agent bench`.

## How it is measured

Each bench task is a folder under `bench/tasks`: the starting project, the task in exact
words, and **tests the agents never see**. Every run gets a brand-new project folder, so
nothing is remembered between runs. Nobody is waiting, so questions are off. The budget and
the call limit are the same for everyone. The work is judged by the hidden tests after the
job ends.

That last part is the point. A judge from the same family as the workers approves more
easily, so "approved" measures agreement, not quality. Only the hidden tests decide whether
the work does what was asked. The gap between the two is worth knowing in itself:

- **a false approval**: the judge said yes, the hidden tests say the work is wrong
- **a false rejection**: the judge said no, but the work was right all along

Models vary from run to run, so each line-up runs each task twice and the table reports the
median.

## The tasks

| task | what it asks for | hidden tests |
|---|---|---|
| `tally` | count words from a written spec: apostrophes, hyphens, ties broken alphabetically | 14 |
| `ledger` | fix a money bug in code that already exists, from the symptom, without breaking its tests | 10 |
| `fields` | read quoted comma-separated rows by hand: escapes, commas and line breaks inside quotes | 19 |

`fields` is the hard one: a naive `line.split(",")` passes the easy cases and fails 10 of
the 19.

## Results: 16 September 2026

Seven line-ups, three tasks, twice each: 42 runs, 9.7 hours, $1.95 of real money. Everything
except Mercury ran on a subscription, so the only bills were Mercury's.

| line-up | works | median minutes | median passes | billed |
|---|---|---|---|---|
| Mercury builds, Sonnet plans and judges (`fast`) | 6/6 | 6.9 | 2.5 | $0.94 |
| Sonnet builds, Opus plans and judges | 6/6 | 8.6 | 1.0 | $0.00 |
| Mercury builds, Opus plans and judges | 6/6 | 9.1 | 3.0 | $1.01 |
| Luna builds, Opus plans and judges | 6/6 | 13.9 | 5.0 | $0.00 |
| Haiku builds, Opus plans and judges | 5/6 | 18.7 | 2.0 | $0.00 |
| Luna builds, Sol plans and judges (`openai`) | 6/6* | 10.2 | 3.0 | $0.00 |
| Gemini 3.8 Flash builds, Opus plans and judges | 0/6 | — | — | $0.00 |

\* one run stopped because OpenAI refused the model ("Selected model is at capacity"); re-run
the next day on the same task and line-up, it worked.

**In 42 runs: no false approvals and no false rejections.** Every piece of work the judge
approved passes the hidden tests, and nothing correct was turned away.

### What separates the line-ups

**Not whether the work is right.** Every line-up that could run finished with work that
passes the hidden tests, on all three tasks, including the hard one. The checks, the
reviewer, the panel and the judge send work back until it is right, so the model doing the
building changes how long that takes, not the result.

There were three exceptions, none of them a wrong answer:

- Haiku once used the whole 30-minute budget and produced nothing at all. Stopped, not
  approved.
- One all-OpenAI run stopped because OpenAI refused the model: "Selected model is at
  capacity." Re-run later, the same line-up did the same task without trouble.
- Every Gemini run was lost to its provider (see below).

**The strong model belongs where the thinking is, not where the agreeing is.** Same Mercury
worker, two judges: 6.9 minutes with Sonnet, 9.1 with Opus, both right every time. A
heavyweight judge bought nothing but a longer wait.

**A cheap worker is not a fast one.** Haiku took 18.7 minutes against Sonnet's 8.6 on the
same tasks, and was the only worker to run out of budget entirely.

**Sonnet is the steadiest.** One pass, most of the time, on every task. Mercury and Luna are
quicker at their best and much slower at their worst: Mercury 3 to 27 minutes, Luna 6 to 26.

**Gemini through Antigravity could not be measured at all.** Six runs, six losses, none of
them about the work: "Individual quota reached" (twice), a quota window that resets in 20 to
30 minutes, and "No capacity available for model gemini-3.8-flash-high on the server". A
job needs a sustained half hour; that plan would not give it. This says nothing about how
good the model is.

### What this means for choosing

- **Quickest:** Mercury building with a Sonnet-class planner and judge. It costs real money
  (about $0.16 a run here) and its worst runs are its slowest.
- **Steadiest, and no bills:** Sonnet building with Opus planning and judging, on a Claude
  subscription.
- **On a ChatGPT plan:** Luna building with Sol planning and judging works, though it needs
  more passes.
- **Avoid:** the cheapest model as the worker. It does not fail so much as grind, and it is
  the only one that produced nothing at all.

### The caveats, plainly

- Three small, self-contained tasks. Nothing here has a build step, a framework, a browser
  or a large codebase to find its way around.
- Two runs per task. Enough to show that Mercury and Luna swing widely; not enough to rank
  neighbours confidently.
- One day, one machine. Provider capacity and quotas were part of the results, as they are
  part of real use.
- The reviewer and panel were the workers' own model in every line-up here, so nothing in
  this table says what a different reviewer would do.
- Money is only what an API key is billed. Subscription usage is counted at
  API-equivalent prices, which is a guide, not a bill.
- GPT-6-Astra was not measured: it is the strongest model on the ChatGPT plan, and worth
  keeping for the planner and judge rather than spending on the building.

## The pre-gate: six shapes, measured

The cheap look before the panel (`docs/how-it-works.md`) rests on one number: the lowest
probability an objection ever scores. A threshold can only skip below that. Each shape below
was measured the same way — `mp-agent pregate backtest`, 51 panel members from past runs, 12
of which objected — changing one thing at a time.

| shape | gap | lowest objection | safely skippable |
|---|---|---|---|
| **one broad question per lens, whole prompt as state** | 0.15 | **0.39** | **37%** |
| the same, asked twice, keeping the worse answer | 0.15 | 0.39 | 37% |
| five narrow questions per lens, worst kept | 0.15 | 0.22 | 20% |
| a score over three levels instead of yes/no | 0.17 | 0.23 | 29% |
| state cut to the diff and the contract | 0.11 | 0.30 | 33% |
| state cut to the diff alone | 0.04 | 0.12 | 6% |

Then it was switched on and measured again, live, across 12 bench runs (2.7 hours, $1.45):

| | backtest (51 members) | live (34 members) |
|---|---|---|
| skipped | 26% | **29%** |
| objections missed | 0 | **0** |
| gap | 0.15 | 0.18 |
| where it breaks | 0.6 | 0.6 |

Every run containing a skip ended approved with no objection from the final judge. The
backtest predicted the live behaviour closely enough to tune on, which matters: tuning by
replay costs pennies and minutes, tuning by running the bench costs hours.

**What it saves is calls, not time.** Panel members run in parallel, so a skipped member
does not shorten the pass — the slowest remaining member still sets the pace. Across those
12 runs it was 41 panel calls instead of about 51.

Four things worth keeping:

**The average gap is the wrong metric.** Twice a shape separated the two populations better
on average and was worse as a gate, because one objection landed low. What a gate rests on
is the floor of the population it must not skip.

**Narrower was not better.** Splitting each lens into small questions and taking the worst
dragged the objected floor from 0.39 to 0.22. Several individually-unlikely questions do not
add up to one reliable one.

**Less state was much worse.** TypeSafe's own notes warn that accuracy falls as state grows
with content unrelated to the decision — but in a code review nothing in that prompt is
unrelated. The contract is what makes something a defect rather than a preference. Cutting
it does not remove noise, it removes the criteria.

**Asking twice changed nothing.** The floor was 0.39 both times, so it is a property of the
model on this task rather than a lucky draw — which is why one call is enough.

Two other uses were tried and are not built. Asking per contract line ("does this change
satisfy C3?") could not be measured honestly: the only labels available are which lines a
reviewer *cited*, and citing a line is not the same as failing it. Routing an escalation
between amendment, test fix, advice and upgrade has six examples in total on this machine,
five of them the same class — too few to measure, and reading them suggests the difficulty
is in writing the ruling, not in choosing its category.

## Skills: why the builders did not use one

Claude Code can load the skills installed on a machine, and mp-agent's builders could not:
they had no Skill tool. Giving them one (`mp-agent config --skills workers`) was expected to
let a job reach for, say, a skill about typed judgments when a task called for one.

The same task was run twice, with the same models: *classify a support message into one of
four teams and report how confident the decision is* — the shape a judgment service exists
for. The task text named no service or skill.

| | what the builder wrote |
|---|---|
| Skill tool, nothing else | keyword lists |
| Skill tool, plus every installed skill named in its prompt | keyword lists |

The skill was listed by name in the second prompt and went unopened. The cause turned out
not to be the builder at all: **wave 0 had already frozen the answer.** The acceptance tests
call the function directly and assert exact outputs, and the check is `python3 -m unittest
discover` — offline, deterministic, no key. An implementation that called a service would be
slow, non-deterministic, and would fail outright without a key. Keyword matching was not the
lazy choice; it was the only choice that could pass the contract the planner wrote.

So the decision that matters is made before any builder sees a skill, and it is made by the
planner when it writes the contract and the tests. Two things follow. A shelf in front of
the builders is harmless and occasionally useful, but it cannot change the shape of a
solution. And the loop's bias towards deterministic, offline-testable code — which is what
ruled the service out — is a property worth keeping, not a bug to prompt around.

## Turning results into a choice

`mp-agent bench suggest` reads the last results and says which line-up to use for the thing
you actually want, with the command to set it:

```
for the quickest: fast-preset — 5.8 minutes a task, and it worked every time, $0.04 a task billed
    mp-agent config --judge claude:sonnet --planner claude:sonnet --worker cline:inception:mercury-2.5
for no API bills: sonnet-builds — nothing billed to an API key, 8.7 minutes a task
    mp-agent config --judge claude:opus --planner claude:opus --worker claude:sonnet
avoid haiku-builds: worked 3/4 (a run did not finish)
```

A line-up that never finished a run recommends nothing, however fast its failures were.

The ready-made presets carry their own measured numbers, in `mp-agent models` and in the
workshop's MODELS screen, so you can see what a team did before you pick it. `mixed` has no
numbers: it was never run exactly as that preset builds it.

## Running it yourself

```bash
mp-agent bench --list                                  # the tasks
mp-agent bench --task tally --preset fast --repeat 3   # a preset, three times
mp-agent bench --combo "name=mine,worker=claude:sonnet,judge=claude:opus" --repeat 2
mp-agent bench table                                   # the last run's table again
```

Results go to `~/.mp-agent/bench/<date>`: one file per run, a summary and the table. The
projects are kept, so you can look at what a line-up actually wrote.
