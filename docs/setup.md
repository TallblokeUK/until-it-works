# Setting up

mp-agent doesn't come with models of its own. It uses the AI coding tools you
already have (Claude Code, Codex, Cline and others), so setting up means making
sure at least one of them is installed and signed in, then choosing a team.

The **SETUP** screen in the workshop does the looking for you. It opens by
itself the first time, and any time the models you chose stop being available.

## 1. Open the workshop

Open **Until It Works(hop)** from your app launcher (Linux) or Launchpad and
Spotlight (Mac). `./install.sh` adds it. Or, in a terminal:

```bash
mp-viz
```

A window opens at http://127.0.0.1:7788. Press **SETUP** if it isn't already
showing. (In a terminal, `mp-agent setup` prints the same information.)

## 2. Tools

Each tool gets one line:

| It says | What it means | What to do |
|---|---|---|
| **READY** | Installed, signed in, and mp-agent found its models | Nothing. Press **TEST** to make one tiny call and be sure |
| **NOT SET UP** | Installed, but not signed in, or no model configured | Run the command shown under it, in a terminal |
| **NOT INSTALLED** | Not on this computer | Run the install command shown, if you want that tool. You don't need them all |

**TEST** asks the model to reply with one word. It takes a few seconds and tells
you either "works" or, in plain words, what is wrong (for example "You've hit
your usage limit").

After installing or signing in to a tool, press **SCAN AGAIN**.

**Which tool should I start with?**

- If you have a **Claude** subscription, install Claude Code and sign in. That
  alone is enough for the **All Claude** team.
- If you have a **ChatGPT** plan, install Codex and run `codex login`. That
  is enough for the **All OpenAI** team.
- For the fastest and cheapest team, add **Cline** with an
  [Inception](https://inceptionlabs.ai) API key for Mercury, alongside Claude
  Code.
- To use models from many providers, or models running on your own computer,
  install **OpenCode** and add an OpenRouter key, or run Ollama.

## 3. API keys

Some tools read an API key from the environment. Paste a key into its box and
press **SAVE**.

- Keys are kept in your system's keychain: the macOS keychain, or GNOME
  Keyring or KWallet on Linux. If neither is available, they go in
  `~/.mp-agent/keys.json`, which only your user can read, and the screen says so.
- After saving, the screen only ever shows the key's last four characters.
  The page doesn't keep the key, and mp-agent never writes it to a log or a run
  folder.
- A key is passed to a tool only for the call that needs it. If you already
  have the same variable set in your own environment (for example
  `OPENROUTER_API_KEY`), yours is used instead.
- **REMOVE** deletes it from the keychain.

| Key | Used by |
|---|---|
| Anthropic | Claude models through OpenCode; Claude Code too, if you tick "Pay for Claude Code with my Anthropic API key" |
| OpenAI | GPT models through OpenCode |
| OpenRouter | hundreds of models through OpenCode |
| Google (Gemini) | the Gemini CLI, and Gemini models through OpenCode |

Tools that sign in their own way keep their own credentials, and mp-agent
never sees them: Claude Code, Codex (`codex login`), Cline (`cline auth`) and
Antigravity.

From a terminal:

```bash
mp-agent keys list
mp-agent keys set openrouter      # asks for the key without showing it
mp-agent keys remove openrouter
```

## 4. Choose a team

The last section lists the presets. The ones you can use have a
**USE THIS TEAM** button, and the ones you can't say what they still need. You
can change any single role later in **MODELS**. [Choosing models](models.md)
explains the roles and presets.

When the box at the top says **Ready**, press **NEW JOB**.

## MCP servers

MCP servers give the agents extra tools. Two are built in: **context7**, for
current documentation for libraries and frameworks, and **playwright**, a
headless browser so agents can check web pages. The screenshots it takes are
kept with the run. Switch either off with **SWITCH OFF**.

To add your own, give it a name and either the command that starts it
(`npx -y @sentry/mcp-server`) or its address (`https://…`). Then choose
**every project**, or only the project chosen in the picker. If it needs a key,
put the key's name in the third box (for example `SENTRY_TOKEN`) and the key
itself in your environment. The server's settings only ever hold the name.
Context7's optional key has its own line under API keys.

Every role in a job gets the same servers, whatever AI tool it runs on:

| Tool | Gets |
|---|---|
| Claude Code, Codex, OpenCode, Gemini CLI, Qwen Code | exactly this list; their own extra servers are left out for the job |
| Cline | this list added to its own MCP settings (servers you added to Cline yourself stay) |
| Antigravity | its own settings only |

From a terminal: `mp-agent mcp list`,
`mp-agent mcp add sentry --command "npx -y @sentry/mcp-server" --env SENTRY_TOKEN`,
`mp-agent mcp add db --url https://… --project ~/code/shop`,
`mp-agent mcp remove sentry`, `mp-agent mcp off playwright`.

## GitHub

SETUP shows which GitHub account the `gh` command is signed in with, and your
organisations. That decides which repositories NEW JOB can clone or pull, and
what counts as "yours" in a project's REPO view. To sign in or switch account:
`gh auth login`.

## 5. Protected repositories

Some repositories must never be changed from here: a client's, or someone
else's that you only work on copies of. Add the GitHub owner (`someone`), or a
single repository (`someone/their-repo`), and press **PROTECT**. Protected
repositories are marked in NEW JOB, and nothing is ever pushed to them or opened
as a pull request. Jobs can still run on your local copy.

From a terminal: `mp-agent protect add someone/their-repo`,
`mp-agent protect remove …`, `mp-agent protect list`.

## 6. Checking everything at once

Press **RUN SELFTEST**. It checks the tools your chosen team uses, then runs one
tiny real job through every step while you watch its log (and the job itself in
the workshop). It takes about two minutes and uses a little of your models'
allowance. At the end it says **all good**, or which item needs fixing and how.

From a terminal: `mp-agent selftest`.

## Safety of the workshop page

The workshop server only listens on your own computer (127.0.0.1). It refuses
requests that arrive under any other host name, and changes that come from
other websites. That stops a web page you visit from reaching your keys or
starting jobs.
