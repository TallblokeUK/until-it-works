import { expect, test } from 'claude-code/testing'
import { CAST, FIGURE, LENS_HELMET, SWARM_HELMETS, at, buffer, figure, pack, reading, rect, render } from '../hooks/scene.js'

/** Unpack what a Raster is given, so a test can read the cells back as numbers. */
function cellsOf(packed: { columns: number; rows: number; cells: string }) {
  const bytes = Uint8Array.fromBase64(packed.cells)
  const words = new Uint32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4)
  return (x: number, row: number) => {
    const i = (row * packed.columns + x) * 3
    return { glyph: words[i], fg: words[i + 1], bg: words[i + 2] }
  }
}

const SPACE = 0x20
const TOP = 0x2580
const BOTTOM = 0x2584
const DEFAULT = 0x01000000

/** Every colour drawn anywhere in a packed scene. */
function colours(packed: { columns: number; rows: number; cells: string }) {
  const cell = cellsOf(packed)
  const seen = new Set<number>()
  for (let row = 0; row < packed.rows; row++) {
    for (let x = 0; x < packed.columns; x++) {
      const c = cell(x, row)
      if (c.fg !== DEFAULT) seen.add(c.fg)
      if (c.bg !== DEFAULT) seen.add(c.bg)
    }
  }
  return seen
}

test('two pixel rows become one cell, top in the foreground', async () => {
  const buf = buffer(2, 2)
  rect(buf, 0, 0, 1, 1, 0xff0000) // top left
  rect(buf, 0, 1, 1, 1, 0x00ff00) // under it
  const packed = pack(buf)
  expect(packed.columns).toBe(2)
  expect(packed.rows).toBe(1)

  const cell = cellsOf(packed)
  // One cell holding both pixels: the upper half-block, top colour in front.
  expect(cell(0, 0)).toEqual({ glyph: TOP, fg: 0xff0000, bg: 0x00ff00 })
  // Nothing was drawn in the second column, so it keeps the terminal's own colours.
  expect(cell(1, 0)).toEqual({ glyph: SPACE, fg: DEFAULT, bg: DEFAULT })
})

test('a pixel with nothing above it uses the lower half-block', async () => {
  const buf = buffer(1, 2)
  rect(buf, 0, 1, 1, 1, 0x0000ff)
  const cell = cellsOf(pack(buf))
  // Drawn as ▄ on the terminal's background, not as a block over a guessed colour.
  expect(cell(0, 0)).toEqual({ glyph: BOTTOM, fg: 0x0000ff, bg: DEFAULT })
})

test('an odd number of pixel rows still packs, and drawing off the edge is clipped', async () => {
  const buf = buffer(3, 5)
  rect(buf, -4, -4, 20, 20, 0x123456) // far bigger than the buffer
  const packed = pack(buf)
  expect(packed.rows).toBe(3)
  expect(Uint8Array.fromBase64(packed.cells).byteLength).toBe(3 * 3 * 3 * 4)
  expect(at(buf, 2, 4)).toBe(0x123456)
  expect(at(buf, 3, 0)).toBe(0x7f000000) // outside is nothing, not a crash
})

test('a builder wears the hard hat, and a helmet overrides it', async () => {
  const floor = FIGURE.height - 1
  const plain = buffer(FIGURE.width, FIGURE.height)
  figure(plain, 0, floor, 'builder', { doing: 'work' })
  // The hat sits on the row above the head, in the workshop's gold.
  expect(at(plain, 3, 0)).toBe(CAST.builder.helmet)

  const lens = buffer(FIGURE.width, FIGURE.height)
  figure(lens, 0, floor, 'intern', { doing: 'work', helmet: LENS_HELMET.truth })
  expect(at(lens, 3, 0)).toBe(LENS_HELMET.truth)
})

test('checkbot\'s visor lights up only while the check runs', async () => {
  const floor = FIGURE.height - 1
  const idle = buffer(FIGURE.width, FIGURE.height)
  figure(idle, 0, floor, 'checker', { doing: 'wait', frame: 1 })
  const busy = buffer(FIGURE.width, FIGURE.height)
  figure(busy, 0, floor, 'checker', { doing: 'check', frame: 1 })
  // Same character, same place: the difference is the eye.
  expect(at(idle, 2, 2)).toBe(CAST.checker.eye)
  expect(at(busy, 2, 2)).not.toBe(CAST.checker.eye)
})

test('the phase decides who is on stage', async () => {
  const base = { run: 'r', live: true, units: [], question: null }
  expect(reading({ ...base, phase: 'pass 2: implementing', units: [{ name: 'api', state: 'working', phase: 'pass 2: implementing', passes: 2 }] }).building).toBe(true)
  expect(reading({ ...base, phase: 'pass 1: fast review', units: [{ name: 'api', state: 'working', phase: 'pass 1: fast review', passes: 1 }] }).reviewing).toBe(true)
  expect(reading({ ...base, phase: 'pass 1: panel (edges, truth)', units: [{ name: 'api', state: 'working', phase: 'pass 1: panel', passes: 1 }] }).panel).toBe(true)
  expect(reading({ ...base, phase: 'pass 1: final audit (claude:sonnet)', units: [{ name: 'main', state: 'working', phase: 'pass 1: final audit', passes: 1 }] }).judging).toBe(true)
  // The real phase string a job writes while the panel sits. It has "audit" in it, so the
  // Judge's gavel went up a stage early until this was pinned down.
  expect(reading({ ...base, phase: 'pass 1: pre-audit panel', units: [{ name: 'final', state: 'working', phase: 'pass 1: pre-audit panel', passes: 1 }] }).judging).toBe(false)
  expect(reading({ ...base, phase: 'pass 1: pre-audit panel', units: [{ name: 'final', state: 'working', phase: 'pass 1: pre-audit panel', passes: 1 }] }).panel).toBe(true)
  // A panel is not a plain review, or Red Pen and the interns would both be marking.
  expect(reading({ ...base, phase: 'pass 1: panel (edges, truth)', units: [] }).reviewing).toBe(false)
})

test('the panel brings one intern per lens, each in its own helmet', async () => {
  const quiet = render({ run: 'r', live: true, phase: 'pass 2: implementing', units: [{ name: 'api', state: 'working', phase: 'pass 2: implementing', passes: 2 }], question: null }, 80)
  const convened = render({ run: 'r', live: true, phase: 'pass 1: panel (edges, truth, rehearsal)', units: [{ name: 'api', state: 'working', phase: 'pass 1: panel', passes: 1 }], question: null }, 80)

  const before = colours(quiet)
  const after = colours(convened)
  // The edges helmet is the workshop's cyan, which is also Checkbot's eye, so it cannot be
  // told apart by colour; truth and rehearsal are the panel's alone.
  for (const lens of ['truth', 'rehearsal'] as const) {
    expect(before.has(LENS_HELMET[lens])).toBe(false)
    expect(after.has(LENS_HELMET[lens])).toBe(true)
  }
  // The builder stays, standing for the work being judged.
  expect(after.has(CAST.builder.helmet)).toBe(true)
  // Red Pen does not: the quick review happened before the panel, and in a narrow pane the
  // lenses are what there is to see.
  expect(before.has(CAST.reviewer.body)).toBe(false) // not out during the building either
  const reviewing = colours(render({ run: 'r', live: true, phase: 'pass 1: fast review', question: null,
    units: [{ name: 'api', state: 'working', phase: 'pass 1: fast review', passes: 1 }] }, 80))
  expect(reviewing.has(CAST.reviewer.body)).toBe(true)
  expect(after.has(CAST.reviewer.body)).toBe(false)
})

test('a swarm puts one builder on stage per part, in the part\'s colour', async () => {
  const swarm = render({
    run: 'r', live: true, phase: 'wave 1/2: api, store, pages', question: null,
    units: [
      { name: 'api', state: 'working', phase: 'pass 1: implementing', passes: 1, wave: 1 },
      { name: 'store', state: 'working', phase: 'pass 1: implementing', passes: 1, wave: 1 },
      { name: 'pages', state: 'working', phase: 'pass 1: implementing', passes: 1, wave: 1 },
    ],
  }, 80)
  const seen = colours(swarm)
  expect(seen.has(SWARM_HELMETS[0])).toBe(true)
  expect(seen.has(SWARM_HELMETS[1])).toBe(true)
  expect(seen.has(SWARM_HELMETS[2])).toBe(true)
})

test('the two frames differ, which is the whole animation', async () => {
  const job = { run: 'r', live: true, phase: 'pass 2: implementing', question: null,
                units: [{ name: 'api', state: 'working', phase: 'pass 2: implementing', passes: 2 }] }
  expect(render(job, 60, 0).cells).not.toBe(render(job, 60, 1).cells)
  // And the frames repeat, so the picture settles rather than drifting.
  expect(render(job, 60, 0).cells).toBe(render(job, 60, 2).cells)
})

test('an approved job celebrates and a stopped one does not', async () => {
  const done = { run: 'r', live: false, phase: 'finished', question: null, units: [{ name: 'main', state: 'approved', phase: 'approved', passes: 1 }] }
  const win = render({ ...done, approved: true }, 60, 0)
  const lose = render({ ...done, approved: false }, 60, 0)
  expect(win.cells).not.toBe(lose.cells)
})

test('it fits a narrow pane without drawing outside it', async () => {
  for (const columns of [16, 24, 40, 60, 120]) {
    const scene = render({ run: 'r', live: true, phase: 'pass 1: panel', question: null,
                           units: [{ name: 'api', state: 'working', phase: 'pass 1: panel', passes: 1 }] }, columns)
    expect(scene.columns).toBe(Math.max(FIGURE.width * 2, columns))
    expect(Uint8Array.fromBase64(scene.cells).byteLength).toBe(scene.columns * scene.rows * 3 * 4)
  }
})

test('the scene stays inside the palette the terminal can paint at once', async () => {
  // A Raster paints 1024 distinct colour pairs and approximates the rest, so a scene that
  // uses more than that would come out wrong on a real terminal.
  const scene = render({
    run: 'r', live: true, phase: 'wave 1/2: a, b, c, d, e, f', question: null,
    units: ['a', 'b', 'c', 'd', 'e', 'f'].map((name) => ({ name, state: 'working', phase: 'pass 1: implementing', passes: 1, wave: 1 })),
  }, 200)
  const cell = cellsOf(scene)
  const pairs = new Set<string>()
  for (let row = 0; row < scene.rows; row++) {
    for (let x = 0; x < scene.columns; x++) {
      const c = cell(x, row)
      pairs.add(c.fg + ':' + c.bg)
    }
  }
  expect(pairs.size).toBeLessThan(1024)
})

test('with no job at all it draws somebody asleep rather than nothing', async () => {
  const idle = render(null, 40, 0)
  expect(colours(idle).size).toBeGreaterThan(1)
})
