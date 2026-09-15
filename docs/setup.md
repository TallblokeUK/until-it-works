# Setting up

mp-agent doesn't come with models of its own. It uses the AI coding tools you
already have (Claude Code, Codex, Cline and others), so setting up means making
sure at least one of them is installed and signed in, then choosing a team.

The **SETUP** screen in the workshop does the looking for you. It opens by
itself the first time, and any time the models you chose stop being available.

## 1. Open the workshop

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

## Checking everything at once

```bash
mp-agent selftest
```

It checks the tools your chosen team uses, then runs one tiny real job with all
the checks. It takes about two minutes.

## Safety of the workshop page

The workshop server only listens on your own computer (127.0.0.1). It refuses
requests that arrive under any other host name, and changes that come from
other websites. That stops a web page you visit from reaching your keys or
starting jobs.
