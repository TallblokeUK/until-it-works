#!/usr/bin/env node
/**
 * See the workshop without starting a session: prints the scene the pane would draw.
 *
 *   node mod/preview.mjs                     the states it goes through, at 60 columns
 *   node mod/preview.mjs 40 panel            one state, at a width
 *   node mod/preview.mjs 60 --animate        two frames alternating, until you stop it
 *
 * The pane paints these cells through a Raster; this prints the same cells as ANSI, so what
 * you see here is what the terminal draws there, give or take your colour scheme.
 */
import { render } from './hooks/scene.js'

const STATES = {
  idle: null,
  planning: { run: 'r', live: true, phase: 'planning', units: [], question: null },
  building: { run: 'r', live: true, phase: 'pass 2: implementing', question: null,
              units: [{ name: 'main', state: 'working', phase: 'pass 2: implementing', passes: 2 }] },
  checking: { run: 'r', live: true, phase: 'pass 2: check', question: null,
              units: [{ name: 'main', state: 'working', phase: 'pass 2: check', passes: 2 }] },
  reviewing: { run: 'r', live: true, phase: 'pass 1: fast review', question: null,
               units: [{ name: 'main', state: 'working', phase: 'pass 1: fast review', passes: 1 }] },
  panel: { run: 'r', live: true, phase: 'pass 1: panel (edges, truth, rehearsal)', question: null,
           units: [{ name: 'main', state: 'working', phase: 'pass 1: panel', passes: 1 }] },
  judging: { run: 'r', live: true, phase: 'pass 1: final audit (claude:sonnet)', question: null,
             units: [{ name: 'main', state: 'working', phase: 'pass 1: final audit', passes: 1 }] },
  swarm: { run: 'r', live: true, phase: 'wave 1/2: api, store, pages', question: null,
           units: ['api', 'store', 'pages'].map((name) => ({ name, state: 'working', phase: 'pass 1: implementing', passes: 1, wave: 1 })) },
  asking: { run: 'r', live: true, phase: 'waiting for you: is this plan right?', question: { question: 'is this plan right?' },
            units: [{ name: 'main', state: 'working', phase: 'pass 1: waiting', passes: 1 }] },
  approved: { run: 'r', live: false, approved: true, phase: 'finished', question: null,
              units: [{ name: 'main', state: 'approved', phase: 'approved', passes: 1 }] },
  stopped: { run: 'r', live: false, approved: false, phase: 'finished', question: null,
             units: [{ name: 'main', state: 'stopped', phase: 'stopped', passes: 3 }] },
}

const rgb = (v, bg) => (v === 0x01000000 ? `\x1b[${bg ? 49 : 39}m`
  : `\x1b[${bg ? 48 : 38};2;${(v >> 16) & 255};${(v >> 8) & 255};${v & 255}m`)

function paint(scene) {
  const bytes = Uint8Array.from(Buffer.from(scene.cells, 'base64'))  // Node 22 has no fromBase64
  const words = new Uint32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4)
  const lines = []
  for (let row = 0; row < scene.rows; row++) {
    let out = ''
    for (let x = 0; x < scene.columns; x++) {
      const i = (row * scene.columns + x) * 3
      out += rgb(words[i + 1], false) + rgb(words[i + 2], true) + String.fromCodePoint(words[i])
    }
    lines.push(out + '\x1b[0m')
  }
  return lines
}

const columns = Number(process.argv[2]) || 60
const which = process.argv[3]

if (which === '--animate') {
  const job = STATES.swarm
  let tick = 0
  setInterval(() => {
    const lines = paint(render(job, columns, tick++))
    process.stdout.write(`\x1b[H\x1b[2J${lines.join('\n')}\n`)
  }, 150)
} else if (which && which in STATES) {
  console.log(paint(render(STATES[which], columns, 0)).join('\n'))
} else {
  for (const [name, job] of Object.entries(STATES)) {
    console.log(`\n\x1b[1m${name}\x1b[0m`)
    console.log(paint(render(job, columns, 0)).join('\n'))
  }
}
