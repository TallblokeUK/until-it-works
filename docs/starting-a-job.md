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

Open **Until It Works(hop)** from your app launcher, Launchpad or Spotlight (or
run `mp-viz`), and press **NEW JOB**. The form asks the same three things: what, where (a local
project, a GitHub repository, a new project or a one-off) and which models.
**START** runs it now; **ADD TO QUEUE** runs it after whatever is already
running.

## Solo or swarm

A job runs **solo** (one worker on the whole task) or as a **swarm** (the task
split into parts that are built at the same time, then merged and judged as a
whole). By default the planner decides, and the log says why. It chooses a swarm
when there are two or more independent parts of real size, especially with fast
workers; small or tightly connected changes stay solo.

To decide yourself, set **Shape** in NEW JOB, or pass `--solo` or `--swarm`.

In a swarm:

- **Parts own files.** No two parts may change the same file. When every part
  would edit one big file, the planner can add a small first "scaffold" part
  that gives each part its own new file to fill.
- **A stuck part doesn't stop the others.** They finish, and their approved work
  is merged and kept. Resuming the job only redoes the part that stopped.
- **Upgrades happen per part.** If one part's workers are upgraded, that part
  and any parts not yet started get the stronger model. Parts already going
  well keep theirs. If they get stuck later, they take the same upgrade, and it
  isn't counted as a second one.

### Working on one project

The **PROJECT** picker at the top of the workshop narrows everything to one
project:

- **Run tabs:** only that project's runs, and "follow this project's newest"
  follows its jobs, not everyone's.
- **HISTORY and QUEUE:** only that project's runs and waiting jobs.
- **NEW JOB:** starts in that project.
- **The project bar** under the buttons shows how many results are waiting for
  KEEP IT or DISCARD (press it to go to the first), and has MEMORY,
  OPEN FOLDER and NEW JOB HERE.

The picker lists every project a job has run in that still exists, with
"running", "needs you" or "2 to keep or discard" beside the name, plus any
folder you opened. The choice is remembered in this browser. Choose
**all projects** to see everything again.

**Open a folder.** Choose **open a folder…** in the picker (or next to Project
in NEW JOB) to browse your home folder and open any project, including one no
job has run in yet. Git projects are marked.

**REPO** in the project bar shows what the folder is connected to:

- **Where it stands:** the branch, whether it's ahead of or behind the remote
  (as of the last fetch), and any uncommitted changes.
- **Every remote** (origin, upstream…), the GitHub repository it points at,
  and whether that's **YOURS** (your account or one of your organisations) or
  **SOMEONE ELSE'S**.
- **From GitHub:** private or public, a fork of what, the default branch, and
  your permission on it.
- **A warning in plain words** when a push from this folder would change
  someone else's repository, with a **PROTECT** button beside it.

REPO only looks. It never fetches, pushes or changes anything.

**TEAM** in the project bar sets the models this project uses. A job started here uses
them unless the job says otherwise, and a project without its own team uses your usual
one. From a terminal: `mp-agent config --project . --preset claude`, and
`--preset none` to forget it.

**AGAIN** beside a run in HISTORY does that run's task again, in the same project
(`mp-agent again --run DIR`, with `--same-models` to use the models that run used rather
than your current ones).

**RULES** in the project bar shows the project's own instructions for AI agents
(CLAUDE.md, AGENTS.md, rules.md, Cursor and Cline rules). Every role in a job
gets the same copy, whatever tool it runs on, and reviewers can hold the work
to them. Untick the box to stop a project's rules files steering the agents,
for example in a repository you didn't write.

## 3. From a terminal

```bash
mp-agent start "add CSV export to the report page"          # in the project you are in
mp-agent start --repo ~/code/shop "add CSV export"          # a project somewhere else
mp-agent start --github you/shop "add CSV export"           # pulls the latest (or clones it) first
mp-agent start --new --name csv-tool "a CSV cleaning tool"  # a brand-new project in ~/mp-projects
mp-agent start --oneoff "convert data.xlsx to JSON"         # a throwaway folder; the result is kept there
mp-agent start --judge opus --worker luna "add CSV export"  # different models for this job only
mp-agent start --swarm "add the four easter eggs"           # split into parts built in parallel
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
