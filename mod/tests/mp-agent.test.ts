import { expect, mock, test } from 'claude-code/testing'

// What Claude Code passes a ui.render hook for each of the two sites this mod draws in.
const BAND = {
  plugin: 'mp-agent',
  component: 'AbovePrompt',
  surface: 'terminal',
  viewport: { columns: 120, rows: 40 },
  props: { hasSurvey: false, isWorking: true, maxRows: 4, bodyColumns: 100, scroll: { offset: 0, bodyRows: 4 }, view: {} },
} as const

const PANE = {
  plugin: 'mp-agent',
  component: 'Pane',
  surface: 'terminal',
  requestId: 'mp-agent',
  viewport: { columns: 120, rows: 40 },
  props: { title: 'mp-agent', isFocused: true, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 20 }, view: {} },
} as const

const RUNNING = {
  run: '20261002T090000Z-a-job',
  live: true,
  task: 'Build a link shortener',
  phase: 'wave 1/2: api, store',
  question: null,
  paused: false,
  alone: false,
  units: [
    { name: 'tests', state: 'approved', phase: 'pass 1: fast review', passes: 1, wave: null },
    { name: 'api', state: 'working', phase: 'pass 2: implementing', passes: 2, wave: 1 },
  ],
  elapsed: 320,
  calls: 18,
  usd: 0.12,
  approved: null,
  outcome: '',
}

const ASKING = {
  ...RUNNING,
  phase: 'waiting for you',
  question: {
    unit: 'plan',
    question: 'The plan is three parts. Reply "go" to carry on, "stop" to end the run.',
    choices: [{ label: 'GO ON', answer: 'go' }, { label: 'STOP', answer: 'stop' }],
  },
}

/** Stub the mp-agent CLI: `status --json` answers with a snapshot, everything else succeeds. */
function cli(on, snapshot, ran: string[][]) {
  on('process.run', ($, e) => {
    const argv = e.argv as string[]
    ran.push(argv)
    if (argv[1] === 'status') return { value: { exitCode: 0, stdout: JSON.stringify(snapshot()), stderr: '' } }
    return { value: { exitCode: 0, stdout: 'done', stderr: '' } }
  })
  on('command.register', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', () => ({ cwd: '/work' }))
  // The band returns next(e) when there is no job, so Claude Code needs something to draw.
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
}

test('the band stays out of the way when no job has run', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => ({ run: null, live: false, units: [], question: null }), ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(BAND)
  // Claude Code's own drawing, which means this mod drew nothing of its own.
  expect(await ui.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})

test('the band says what the job is doing', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => RUNNING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /wave 1\/2: api, store/ })).toBeDefined()
  // The one part still working, and how long it has been going.
  expect(await ui.find({ type: 'Text', text: /api pass 2/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /5m/ })).toBeDefined()
})

test('a question reaches the band, and one key answers it', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => ASKING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'needs you' })).toBeDefined()

  await ui.press({ key: 'band-go' })
  expect(ran).toContainEqual(['mp-agent', 'answer', 'go'])
})

test('a question in the phase does not flood the pane', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  // What a waiting job really writes: the whole plan, in the phase.
  const wordy = {
    ...ASKING,
    phase: 'waiting for you: This is the plan, before anything is built.\n\nDone means:\n  - a lot\n  - of lines\n',
  }
  cli(on, () => wordy, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(PANE)
  // The status line is one line, and the plan is not in it.
  expect(await ui.find({ type: 'Text', text: /Done means/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^waiting for you: This is the plan, before anything is built\. · api pass 2 · 5m · \$0\.12$/ })).toBeDefined()
})

test('the pane lists the parts and the question, and a choice button answers', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => ASKING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /✓ tests/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /· api/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /The plan is three parts/ })).toBeDefined()

  await ui.press({ key: 'choice-1' })
  expect(ran).toContainEqual(['mp-agent', 'answer', 'stop'])
})

test('the field answers a question in full, not just yes or no', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => ASKING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(PANE)
  await ui.input({ key: 'say', text: 'go up to PB and EB as well' })
  expect(ran).toContainEqual(['mp-agent', 'answer', 'go up to PB and EB as well'])
})

test('the same field steers the job when nothing is being asked', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => RUNNING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(PANE)
  await ui.input({ key: 'say', text: 'keep the api surface small' })
  expect(ran).toContainEqual(['mp-agent', 'say', 'keep the api surface small'])
})

test('holding asks the job to wait, and letting go releases it', async ($, on) => {
  const ran: string[][] = []
  let snap = RUNNING
  const clock = mock.clock(on)
  cli(on, () => snap, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(PANE)
  await ui.press({ key: 'hold' })
  expect(ran).toContainEqual(['mp-agent', 'pause'])

  // Once the job is holding, the same button lets it go again. The job writes that state
  // itself, so the mod only knows once it has looked again.
  snap = { ...RUNNING, paused: true }
  await clock.advance(2000)
  await ui.unmount()
  const again = await $.ui.mount(PANE)
  await again.press({ key: 'hold' })
  expect(ran).toContainEqual(['mp-agent', 'pause', '--off'])
})

test('the workshop repaints itself while the pane is open and a job is live', async ($, on) => {
  const ran: string[][] = []
  const clock = mock.clock(on)
  const frames: string[] = []
  on('ui.blit', ($, e) => {
    frames.push(e.cells)
    return { value: undefined }
  })
  cli(on, () => RUNNING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  // Nothing is painted until somebody opens the pane.
  await clock.advance(1000)
  expect(frames).toEqual([])

  await $.ui.mount(PANE)
  await clock.advance(1000)
  expect(frames.length).toBeGreaterThan(1)
  // Two frames, alternating: the cast moves rather than standing still.
  expect(new Set(frames).size).toBe(2)
})

test('a finished job is not animated', async ($, on) => {
  const ran: string[][] = []
  const clock = mock.clock(on)
  const frames: string[] = []
  on('ui.blit', ($, e) => {
    frames.push(e.cells)
    return { value: undefined }
  })
  cli(on, () => ({ ...RUNNING, live: false, phase: 'finished', approved: true, question: null }), ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await $.ui.mount(PANE)
  await clock.advance(2000)
  expect(frames).toEqual([])
})

test('the desktop app gets the words rather than a worse picture', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  cli(on, () => RUNNING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Raster' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /· api/ })).toBeDefined()
})

test('it finds the CLI where the installer puts it, not only on the PATH', async ($, on) => {
  const tried: string[] = []
  mock.clock(on)
  on('env.get', () => ({ value: '/tmp/a-home' }))
  on('process.run', ($, e) => {
    const argv = e.argv as string[]
    tried.push(argv[0])
    // A bare PATH, as a process started without a login shell gets.
    if (argv[0] === 'mp-agent') return { value: { exitCode: 127, stdout: '', stderr: 'not found' } }
    if (argv[0] === '/tmp/a-home/.local/bin/mp-agent') {
      return { value: { exitCode: 0, stdout: JSON.stringify(RUNNING), stderr: '' } }
    }
    return { value: { exitCode: 127, stdout: '', stderr: 'not found' } }
  })
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  expect(tried).toContain('/tmp/a-home/.local/bin/mp-agent')
  // Having found it, the band shows the job rather than nothing.
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /wave 1\/2: api, store/ })).toBeDefined()
})

test('a session that cannot find mp-agent at all says so', async ($, on) => {
  mock.clock(on)
  on('env.get', () => ({ value: '/tmp/a-home' }))
  on('process.run', () => ({ value: { exitCode: 127, stdout: '', stderr: 'not found' } }))
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(BAND)
  // Not silence: silence is indistinguishable from "no job", which is the wrong thing to think.
  expect(await ui.find({ type: 'Text', text: /is not on this session/ })).toBeDefined()
})

test('a pane too short for both drops the picture, not the question', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  on('ui.blit', () => ({ value: undefined }))
  on('ui.scroll', () => ({ value: undefined }))
  cli(on, () => ASKING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const tall = await $.ui.mount(PANE)
  expect(await tall.find({ type: 'Raster' })).toBeDefined()
  await tall.unmount()

  const short = await $.ui.mount({ ...PANE, props: { ...PANE.props, scroll: { offset: 0, bodyRows: 10 } } })
  expect(await short.find({ type: 'Raster' })).toBeUndefined()
  expect(await short.find({ type: 'Text', text: /The plan is three parts/ })).toBeDefined()
})

test('an idle machine is not asked every two seconds', async ($, on) => {
  const ran: string[][] = []
  const clock = mock.clock(on)
  cli(on, () => ({ run: null, live: false, units: [], question: null }), ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const polls = () => ran.filter((argv) => argv[1] === 'status').length
  const atStart = polls()

  // Six seconds with nothing running: the slow pace has not come round yet.
  await clock.advance(6000)
  expect(polls()).toBe(atStart)
  // Past ten seconds it looks again.
  await clock.advance(6000)
  expect(polls()).toBe(atStart + 1)
})

test('a job that is running is watched closely', async ($, on) => {
  const ran: string[][] = []
  const clock = mock.clock(on)
  cli(on, () => RUNNING, ran)

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const polls = () => ran.filter((argv) => argv[1] === 'status').length
  const atStart = polls()
  await clock.advance(6000)
  expect(polls()).toBe(atStart + 3)
})

test('a broken or missing mp-agent leaves the session alone', async ($, on) => {
  const ran: string[][] = []
  mock.clock(on)
  on('process.run', ($, e) => {
    ran.push(e.argv as string[])
    return { value: { exitCode: 127, stdout: '', stderr: 'mp-agent: command not found' } }
  })
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const ui = await $.ui.mount(BAND)
  // It asked, got nothing, and drew nothing: the band is Claude Code's own.
  expect(ran.length).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})
