# mp-agent in Claude Code

A [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) that watches a job
of agents from the prompt you are already at, and answers it there.

```
/plugin marketplace add TallblokeUK/until-it-works
/plugin install mp-agent@until-it-works
```

While a job runs, a line above the prompt says what it is doing. When it needs a decision the
line turns into **mp-agent needs you**: press `1` to answer "go on", or `2` to open a pane with
the question in full, the buttons the workshop has, and a field for a longer answer. The same
field says something to the builders when nothing is being asked, and a button holds the job at
the end of the current pass. `/mp-agent-job` opens the pane whenever you want it.

Nothing else changes. It draws only when there is a job, so a session that has never started
one looks exactly as it did before.

## How it talks to a job

A job runs in its own processes and can last an hour, so the mod owns none of it. It reads
`mp-agent status --json` on a timer — every two seconds while a job is live, every ten when
none is — and the three things it can do it does by running the commands you could type
yourself:

| In the mod | The command |
|---|---|
| `1`, a choice button, or a typed answer | `mp-agent answer "…"` |
| the field, when nothing is being asked | `mp-agent say "…"` |
| hold at next pass / carry on | `mp-agent pause` / `mp-agent pause --off` |

So it can see and do nothing you could not, and the layout of a run folder stays the CLI's
business.

## Working on it

Claude Code writes `mod/.claude-plugin/types/` and `mod/tsconfig.json` for your installed
version each time it loads the mod, which is why neither is in git.

```bash
claude --plugin-dir ./mod    # load it from this checkout; it reloads when you save
claude plugin validate ./mod # what Claude Code reads from it
cd mod && claude plugin test  # its tests: the band, the pane, the timer, a missing mp-agent
```
