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
| Luna builds, Sol plans and judges (`openai`) | 5/6 | 10.2 | 3.0 | $0.00 |
| Gemini 3.8 Flash builds, Opus plans and judges | 0/6 | — | — | $0.00 |

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
  capacity."
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
