# Starting a job

There are three ways in. They all do the same thing: work out **what** to do,
**where** (which project) and **which models**, then start the job in the
background. The workshop window opens so you can watch it, answer any question
the agents ask, and look at the result.

## 1. From your AI coding tool

`./install.sh` adds an `mp-agent` command to each AI coding tool it finds on
your computer. Type it, optionally followed by the task:

| Tool | Type |
|---|---|
| Claude Code | `/mp-agent add CSV export to the report page` |
| Gemini CLI | `/mp-agent add CSV export to the report page` |
| Qwen Code | `/mp-agent add CSV export to the report page` |
| OpenCode | `/mp-agent add CSV export to the report page` |
| Cline | `/mp-agent` (or `/agents`), then the task |
| Codex | `$mp-agent add CSV export to the report page` (Codex has no custom slash commands, so this is a skill) |

Your tool then:

1. asks what you want done, if you didn't say;
2. shows your recent projects and GitHub repositories and asks where the agents
   should work (or you can say "new project" or "one-off");
3. shows the five models it will use and asks you to confirm or change them
   ("use opus as the judge");
4. starts the job and tells you it's running.

That's all it does. It doesn't write any code itself, and it doesn't wait for
the job: the agents work in the background, and your tool is free again
straight away.

If you installed a tool after running `./install.sh`, run
`mp-agent launchers --install` to add the command to it. If you already had
your own command called `mp-agent` in a tool, it's left alone.

## 2. From the workshop

Press **NEW JOB**. The form asks the same three things: what, where (a local
project, a GitHub repository, a new project or a one-off) and which models.
**START** runs it now; **ADD TO QUEUE** runs it after whatever is already
running.

## 3. From a terminal

```bash
mp-agent start "add CSV export to the report page"          # in the project you are in
mp-agent start --repo ~/code/shop "add CSV export"          # a project somewhere else
mp-agent start --github you/shop "add CSV export"           # pulls the latest (or clones it) first
mp-agent start --new --name csv-tool "a CSV cleaning tool"  # a brand-new project in ~/mp-projects
mp-agent start --oneoff "convert data.xlsx to JSON"         # a throwaway folder; the result is kept there
mp-agent start --judge opus --worker luna "add CSV export"  # different models for this job only
```

`mp-status` shows what's running. `mp-agent answer "…"` answers a question the
agents are waiting on.

## Where the work goes

The agents never touch the files you are working on. Every job happens on a
separate git branch in a separate folder. When it's approved, the result panel
offers:

- **VIEW CHANGES:** the changed files, the diff, and the final judge's notes
- **KEEP IT:** merge the branch into your project
- **KEEP AS PR:** push the branch and open a GitHub pull request (it asks first)
- **DISCARD:** delete the branch

A project with uncommitted changes is refused, so nothing of yours can be mixed
into the agents' work. Commit your changes first, or start the job as a new
project.
