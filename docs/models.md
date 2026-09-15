# Choosing models

mp-agent works with the models you already have. It finds the command-line
tools installed on your computer, asks each one which models it can use, and
lets you give every job to whichever model suits it.

## The five roles

| Role | What it does | How often it runs |
|---|---|---|
| **Planner** | Reads your project, writes the plan and the contract ("what done means"), settles disagreements, and writes the question if it ever needs to ask you | A few times per job |
| **Workers** | Write the code | Many times: every pass |
| **Quick reviewer** | Reads every change that passes its checks | Once per passing change |
| **Panel** | Three quick lenses (edge cases, things that are untrue, "what would the judge say") before the final judge | Once per change the reviewer approves |
| **Judge** | The final audit. Nothing is approved until it agrees | Once per change the panel approves |

One rule: **the planner and the judge can't be the same model as the workers.**
A model checking its own work isn't an independent check. The quick reviewer
and the panel may use the workers' model: every call is a fresh session with
no memory of writing the code, and the judge still comes after them.

## Presets

A preset picks all five roles from what you have installed, plus loop settings
that suit those models.

| Preset | Planner | Workers | Reviewer and panel | Judge | Good for |
|---|---|---|---|---|---|
| **Fast and cheap** | Claude Sonnet | Mercury (Inception) | the workers' model | Claude Sonnet | Speed and value. Many cheap passes and a three-lens panel |
| **All Claude** | Claude Opus | Claude Sonnet | Claude Haiku | Claude Opus | Only a Claude subscription, no API bills |
| **All OpenAI** | GPT (Sol) | GPT (Luna) | the workers' model | GPT (Sol) | Only a ChatGPT plan, through Codex |
| **Claude plans, GPT builds** | Claude Opus | GPT (Luna) | Claude Sonnet | Claude Opus | Two companies' models checking each other |

A preset only appears as available when you have what it needs. Otherwise the
list says what is missing, for example "needs a worker (cline:\*:mercury\* or opencode:\*mercury\*)".

**Why the loop settings change.** A fast, cheap worker can afford many passes
and a panel of three. With a slower or more expensive worker, the strong presets
use a panel of one and escalate sooner (after 2 unchanged passes rather than 3),
so a stuck job asks the planner for help before it burns time and money.

## Choosing

**In the workshop:** press **MODELS**. Pick a preset, or choose each role
yourself, then **SAVE AS DEFAULTS**. To use different models for one job only,
choose them in **NEW JOB**.

**In a terminal:**

```bash
mp-agent models                      # what you have, what is chosen, and the presets
mp-agent config --preset claude      # use a preset
mp-agent config --judge opus         # change one role (short names work)
mp-agent config --reviewer same      # the reviewer goes back to the workers' model
mp-agent start --worker luna "task"  # different models for one job
```

Names can be short when they are unambiguous: `opus`, `sonnet`, `luna`,
`mercury`, `gpt-5.5`. The full form is the spec shown by `mp-agent models`, for
example `claude:opus` or `cline:inception:mercury-2.5`.

## Which tools are supported

| Tool | Command | Spec | Paid through |
|---|---|---|---|
| Claude Code | `claude` | `claude:opus` | your Claude subscription (mp-agent never gives it an API key) |
| Codex | `codex` | `codex:gpt-5.6-luna` | your ChatGPT plan |
| Cline | `cline` | `cline:inception:mercury-2.5` | the provider's API key, set up in Cline |
| OpenCode | `opencode` | `opencode:openrouter/qwen/qwen3-coder`, `opencode:ollama/qwen3` | the provider's API key, or free for local models |
| Antigravity | `agy` | `antigravity:gemini-3.8-flash-high` | your Google plan |
| Qwen Code | `qwen` | `qwen:deepseek-v4-pro` | the provider's API key |
| Gemini CLI | `gemini` | `gemini:default` | a Gemini API key |

Each tool has to be installed and logged in on its own first. mp-agent then
finds it, and `mp-agent selftest` checks the tools your chosen roles use.

## What counts as spending

Every tool reports how its calls are paid for:

- **Billed:** tokens charged to an API key. The spending cap counts these, and
  they are what HISTORY and the result panel show as "billed".
- **Subscription:** covered by a plan such as Claude or ChatGPT. The price shown
  is what the same tokens would cost on the API; you are not charged it.
- **Local:** a model running on your own computer. Free.

Set a cap per job with `mp-agent config --max-usd 5` or in MODELS. When a job
reaches it, the job pauses and asks you whether to raise the cap or stop.

Using a subscription through automation is governed by that provider's terms.
Check them for your plan; an API key is the alternative.
