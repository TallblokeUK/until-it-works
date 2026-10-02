declare module 'claude-code' {
  interface PluginState {
    'mp-agent': {
      // The last snapshot `mp-agent status --json` printed, or null before the first poll.
      job: {
        run: string | null
        live: boolean
        task?: string
        phase?: string
        question?: { unit?: string; question?: string; choices?: { label: string; answer: string }[] } | null
        paused?: boolean
        alone?: boolean
        units?: { name: string; state: string; phase: string; passes: number; wave: number | null }[]
        elapsed?: number
        calls?: number
        usd?: number
        approved?: boolean | null
        outcome?: string
      } | null
      // What the mod last did, shown in the pane so a press has a visible result.
      said: string
    }
  }
}
