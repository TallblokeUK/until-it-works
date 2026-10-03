/**
 * mp-agent in Claude Code: a band above the prompt while a job runs, and a pane to answer it.
 *
 * A job runs in its own processes and can last an hour, so this mod owns none of it. It polls
 * `mp-agent status --json` and draws what comes back, and the three things it can do to a job
 * — answer a question, say something, hold at the next pass — it does by calling the same
 * commands you would type. Nothing here reaches into the run folder, so the layout of a run
 * stays the CLI's business and this stays a view.
 *
 * The point of it is the question. A job that needs a decision used to mean the workshop in a
 * browser or `mp-agent answer` in another terminal; here it is a line above the prompt you are
 * already looking at, with the buttons the workshop has.
 */
import { atom, read, update } from 'claude-code'
import { fresh, render as renderScene, SWARM_HELMETS } from './scene.js'

const PANE = 'mp-agent'
const FRAME_MS = 120 // the cast's two-frame animation, while the pane is open and a job is live
const LIVE_MS = 2000 // while a job is running, and the band is showing something that moves
const IDLE_MS = 10000 // while nothing is, so an idle session is not spawning a process a second

// The snapshot, and the last thing this mod did. Both in $.state: a drawing depends on them,
// they should survive a reload while you are editing this file, and writing them redraws.
const job = atom({ plugin: 'mp-agent', key: 'job' }, null)
const said = atom({ plugin: 'mp-agent', key: 'said' }, '')

// The frame counter lives in a module variable, not in $.state: a write to state redraws
// every reader, and the whole point of blitting is to repaint the picture without that.
let tick = 0
// What the pane last drew, so a frame can be repainted at the same size, and whether the
// pane is open at all: blitting into a pane nobody has open is work for nothing.
let painting = null
let paneOpen = false
// How to reach the CLI. `mp-agent` installs into ~/.local/bin, which is on a login shell's
// PATH but not on a bare one, and a mod that cannot find it would simply draw nothing for
// ever. So it is looked for once, and if it is not there the band says so rather than
// leaving somebody staring at an empty prompt wondering what they did wrong.
let cli = ['mp-agent']
let missing = ''
// The question the pane last scrolled to. A new one is worth moving the window for; a redraw
// of the same one is not, or the pane would yank itself back while you were reading upwards.
let scrolledTo = ''
// The last snapshot, kept beside the one in $.state. The timers decide their own pace and
// paint their own frames from this; $.state is for the drawings, which redraw when it is
// written. A timer that had to read state to decide what to do would be doing engine work
// every tick for an answer it already has.
let latest = null


const minutes = (seconds) => (seconds < 60 ? `${seconds}s` : `${Math.round(seconds / 60)}m`)

/** One line of what is happening, short enough for the band. */
function line(snap) {
  // While a job waits for an answer its phase holds the whole question - the contract, what
  // is out of scope, every line of it. That belongs in the pane's body, not in a status line.
  const bits = [(snap.phase || 'working').split('\n')[0].slice(0, 80)]
  const busy = (snap.units || []).filter((u) => u.state && u.state !== 'approved')
  if (busy.length === 1) bits.push(`${busy[0].name} pass ${busy[0].passes}`)
  else if (busy.length > 1) bits.push(`${busy.length} parts`)
  bits.push(minutes(snap.elapsed || 0))
  if (snap.usd) bits.push(`$${snap.usd.toFixed(2)}`)
  return bits.join(' · ')
}

/** Run an mp-agent command and keep what it said, so a press has a visible result. */
async function told($, argv, note) {
  const out = await $.process.run([...cli, ...argv])
  const text = out.exitCode === 0 ? note : (out.stderr || out.stdout || 'that did not work').trim()
  await update($, said, () => text.split('\n')[0].slice(0, 120))
  return out.exitCode === 0
}

/** Find the CLI once: on the PATH, else where the installer puts it. */
async function findCli($) {
  const home = (await $.env.get('HOME')) || ''
  for (const candidate of [['mp-agent'], [home + '/.local/bin/mp-agent'], ['/usr/local/bin/mp-agent']]) {
    if (candidate[0].startsWith('/') && !home) continue
    try {
      const out = await $.process.run([...candidate, 'status', '--json'], { timeoutMs: 10000 })
      if (out.exitCode === 0) {
        cli = candidate
        missing = ''
        return true
      }
    } catch {
      /* try the next one */
    }
  }
  missing = 'mp-agent is not on this session\u2019s PATH'
  return false
}

/** Bring the part you can act on into view: the question, and the field under it. */
async function reveal($) {
  if (!paneOpen) return
  try {
    await $.ui.scroll({ to: { key: 'say' }, in: PANE, block: 'end' })
  } catch {
    /* the pane went away */
  }
}

async function poll($) {
  // A job that has gone is not an error: there is simply nothing to draw. A CLI that cannot
  // be run at all is an error, and one worth showing.
  try {
    const out = await $.process.run([...cli, 'status', '--json'], { timeoutMs: 10000 })
    if (out.exitCode !== 0) {
      if (await findCli($)) return poll($)
      return
    }
    missing = ''
    const snap = JSON.parse(out.stdout)
    // A question that has just arrived is the one thing worth moving the window for. This
    // happens before the state is written, so a redraw cannot get between the two.
    const asking = fresh(snap, scrolledTo)
    if (asking) {
      scrolledTo = asking
      await reveal($)
    } else if (!(snap && snap.question)) {
      scrolledTo = ''
    }
    latest = snap && snap.run ? snap : null
    await update($, job, () => latest)
  } catch {
    await findCli($)
  }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'mp-agent-job', description: 'Watch the mp-agent job in a pane' })
    await poll($)
    // One timer, which decides its own pace: a live job moves, an idle machine does not.
    // The cast's two frames. A blit repaints the one element without running the render
    // hook, so the animation costs no redraw of the pane's words and no model's attention.
    $.clock.every(FRAME_MS, async () => {
      if (!paneOpen || !painting || !latest || !latest.live) return
      tick += 1
      const scene = renderScene(latest, painting.columns, tick)
      await $.ui.blit({ requestId: PANE, key: 'workshop', columns: scene.columns, rows: scene.rows, cells: scene.cells })
    })

    let since = 0
    $.clock.every(LIVE_MS, async () => {
      const wanted = latest && latest.live ? LIVE_MS : IDLE_MS
      since += LIVE_MS
      if (since < wanted) return
      since = 0
      await poll($)
    })
    return next(e)
  })

  on('command.run', { command: 'mp-agent-job' }, async ($) => {
    await poll($)
    await $.ui.open({ id: PANE, title: 'mp-agent', focus: true, closeOnEscape: true })
    paneOpen = true
    await reveal($)
    return {}
  })

  on('ui.close', async ($, e, next) => {
    if (e.id === PANE) paneOpen = false
    return next(e)
  })

  // The band: one line while a job runs, and the question when there is one. Nothing otherwise,
  // so a session that has never run a job looks exactly as it did before this mod was installed.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const snap = await read($, job)
    if (!snap || !snap.live) {
      if (!missing) return next(e)
      const { Text } = $.ui.resolve(e)
      return Text({ children: ['mp-agent: ' + missing], dimColor: true })
    }
    const { Box, Text, Button } = $.ui.resolve(e)
    const question = snap.question
    const row = [
      Text({ children: ['mp-agent '], dimColor: true }),
      Text({ children: [question ? 'needs you' : line(snap)], bold: !!question, color: question ? 'yellow' : undefined }),
    ]
    if (question) {
      row.push(
        Text({ children: ['  '] }),
        Button({ key: 'band-go', label: 'go on', hotkey: '1', plain: true,
                 onPress: () => told($, ['answer', 'go'], 'carrying on') }),
        Text({ children: ['  '] }),
        Button({ key: 'band-open', label: 'read it', hotkey: '2', plain: true,
                 onPress: async () => {
                   await $.ui.open({ id: PANE, title: 'mp-agent', focus: true, closeOnEscape: true })
                   paneOpen = true
                   await reveal($)
                 } }),
      )
    } else if (snap.paused) {
      row.push(Text({ children: ['  holding at the next pass'], dimColor: true }))
    }
    return Box({ flexDirection: 'row', children: row })
  })

  // The pane: what it is doing, the question in full with its buttons, and a field to say
  // something. The field answers the question when one is waiting, and otherwise steers.
  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button, Input, Raster } = $.ui.resolve(e)
    const snap = await read($, job)
    const note = await read($, said)
    if (!snap) {
      return Text({ children: ['No mp-agent job to show. Start one with /mp-agent.'] })
    }

    const children = []

    // The workshop, where the surface can draw cells. Desktop has no Raster, so it gets the
    // words alone rather than a worse picture.
    const bodyRows = (e.props.scroll && e.props.scroll.bodyRows) || 20
    if (Raster && e.surface === 'terminal' && bodyRows >= 15) {
      const columns = Math.max(16, (e.props.bodyColumns || 40) - 1)
      painting = { columns }
      paneOpen = true
      const scene = renderScene(snap, columns, tick)
      children.push(Raster({ key: 'workshop', columns: scene.columns, rows: scene.rows, cells: scene.cells }))
    }

    children.push(
      Text({ children: [(snap.task || snap.run || '').split('\n')[0].slice(0, 200)], bold: true }),
      Text({ children: [snap.live ? line(snap) : `finished · ${snap.outcome || ''}`.slice(0, 200)], dimColor: true }),
      Text({ children: [' '] }),
    )

    const busy = (snap.units || []).filter((u) => u.state && u.state !== 'approved' && u.state !== 'stopped')
    for (const unit of snap.units || []) {
      const mark = unit.state === 'approved' ? '✓' : unit.state === 'stopped' ? '✗' : '·'
      const helmet = busy.length > 1 ? SWARM_HELMETS[busy.indexOf(unit) % SWARM_HELMETS.length] : -1
      const colour = helmet >= 0 && busy.includes(unit) ? '#' + helmet.toString(16).padStart(6, '0') : undefined
      children.push(Text({
        children: [`${mark} ${unit.name}  ${unit.phase || unit.state}`.slice(0, 200)],
        dimColor: unit.state === 'approved',
        color: colour,
      }))
    }

    const question = snap.live ? snap.question : null
    if (question) {
      children.push(Text({ children: [' '] }))
      children.push(Text({ children: [(question.question || '').slice(0, 2000)], color: 'yellow' }))
      const choices = (question.choices || []).slice(0, 4)
      if (choices.length) {
        children.push(Box({
          flexDirection: 'row',
          columnGap: 2,
          children: choices.map((choice, i) =>
            Button({
              key: `choice-${i}`,
              label: choice.label || choice.answer,
              onPress: () => told($, ['answer', choice.answer], `answered: ${choice.answer}`),
            })),
        }))
      }
    }

    children.push(Text({ children: [' '] }))
    children.push(Input({
      key: 'say',
      label: question ? 'Answer' : 'Say',
      placeholder: question ? 'Type your answer and press Enter' : 'Tell the builders something',
      value: '',
      submitLabel: question ? 'answer' : 'say',
      onSubmit: async (value) => {
        const text = (value || '').trim()
        if (!text) return
        if (question) await told($, ['answer', text], `answered: ${text}`)
        else await told($, ['say', text], `said: ${text}`)
        await poll($)
      },
    }))

    if (snap.live && !question) {
      children.push(Box({
        flexDirection: 'row',
        columnGap: 2,
        children: [
          Button({
            key: 'hold',
            label: snap.paused ? 'carry on' : 'hold at next pass',
            onPress: async () => {
              await told($, snap.paused ? ['pause', '--off'] : ['pause'],
                        snap.paused ? 'carrying on' : 'it will stop after this pass')
              await poll($)
            },
          }),
        ],
      }))
    }

    if (note) children.push(Text({ children: [note], dimColor: true }))
    return Box({ flexDirection: 'column', children })
  })
}
