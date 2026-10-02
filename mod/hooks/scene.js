/**
 * The workshop, in terminal cells.
 *
 * The browser workshop draws its cast with `px(x, y, w, h, colour)` calls onto a canvas
 * (`viz/index.html`). A terminal has no canvas, but a `Raster` is close enough: a grid of
 * cells, each with a glyph, a foreground and a background. Paint the upper half-block `▀`
 * and one cell holds two pixels — the top in the foreground, the bottom in the background —
 * so the same kind of drawing fits, at half the vertical cost.
 *
 * Everything here is arithmetic on a pixel buffer, with no `$` and no engine, so the scene
 * can be tested by reading pixels back out of it rather than by looking at a screen.
 *
 * The cast is smaller than the workshop's, because a docked pane is about forty columns
 * wide: eleven pixels tall rather than twenty. The palettes are the workshop's own, so the
 * same characters are recognisably at work in both.
 */

const CLEAR = 0x7f000000 // nothing drawn here; the terminal's own background shows through
const DEFAULT = 0x01000000 // what a cell means by "the terminal's colour"
const TOP = 0x2580 // ▀
const BOTTOM = 0x2584 // ▄
const SPACE = 0x20

// The workshop's palette, from viz/index.html.
export const NIGHT = 0x0d0b1e
export const INK = 0x1b1838
export const BEZEL = 0x241f4a
export const EDGE = 0x3b3470
export const GOLD = 0xf4c542
export const CYAN = 0x5ef2e6
export const GREEN = 0x6dff8e
export const RED = 0xff5a5a

export const LENS_HELMET = { edges: 0x5ef2e6, truth: 0xff4fa3, rehearsal: 0xb07bf2, look: 0xf4c542 }
export const SWARM_HELMETS = [0xf4c542, 0x6dff8e, 0xff9a5e, 0x5ec3f2, 0xff6fd0, 0xc0f25e]

// The cast's own colours, from makeChar in the workshop.
export const CAST = {
  builder: { skin: 0xf2c9a0, body: 0xc9d6e3, shade: 0x8aa0b5, legs: 0x8aa0b5, shoes: 0xf4c542, eye: 0x141024, helmet: 0xf4c542 },
  checker: { skin: 0xa9bcc6, body: 0x4f7280, shade: 0x35505b, legs: 0x35505b, shoes: 0x1e2a33, eye: 0x5ef2e6, visor: 0x1e2a33 },
  reviewer: { skin: 0xe6b48a, body: 0xa33b4f, shade: 0x7a2638, legs: 0x2e2a4a, shoes: 0x141024, eye: 0x141024, hair: 0x5a3a22 },
  judge: { skin: 0xf0c8a4, body: 0x1a1830, shade: 0x0e0d1c, legs: 0x1a1830, shoes: 0x1a1830, eye: 0x141024, robe: true },
  intern: { skin: 0xf2c9a0, body: 0xdfe7ee, shade: 0x9fb0c0, legs: 0x9fb0c0, shoes: 0xf4c542, eye: 0x141024 },
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

/**
 * Standard padded base64 of some bytes. `Uint8Array.prototype.toBase64` does this and the
 * engine has it, but it arrived in ES2025 and Node 22 does not, which would leave the scene
 * untestable and unpreviewable outside a session. The cells are decoded and checked in the
 * tests, so this is held to the same answer the built-in gives.
 */
export function base64(bytes) {
  if (typeof bytes.toBase64 === 'function') return bytes.toBase64()
  let out = ''
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i], b = bytes[i + 1], c = bytes[i + 2]
    const has2 = i + 1 < bytes.length, has3 = i + 2 < bytes.length
    out += B64[a >> 2]
    out += B64[((a & 3) << 4) | (has2 ? b >> 4 : 0)]
    out += has2 ? B64[((b & 15) << 2) | (has3 ? c >> 6 : 0)] : '='
    out += has3 ? B64[c & 63] : '='
  }
  return out
}

/** A pixel buffer the drawing functions paint into. */
export function buffer(width, height) {
  return { width, height, px: new Uint32Array(width * height).fill(CLEAR) }
}

/** One filled rectangle, clipped to the buffer: the workshop's px(), for cells. */
export function rect(buf, x, y, w, h, colour) {
  const x0 = Math.max(0, Math.round(x)), y0 = Math.max(0, Math.round(y))
  const x1 = Math.min(buf.width, Math.round(x + w)), y1 = Math.min(buf.height, Math.round(y + h))
  for (let yy = y0; yy < y1; yy++) for (let xx = x0; xx < x1; xx++) buf.px[yy * buf.width + xx] = colour
}

export function at(buf, x, y) {
  if (x < 0 || y < 0 || x >= buf.width || y >= buf.height) return CLEAR
  return buf.px[y * buf.width + x]
}

export const FIGURE = { width: 7, height: 11 }

/**
 * One member of the cast, seven pixels wide and eleven tall, standing with their feet at
 * (x, floor). `doing` is what they are up to: work, check, mark, rule, dance, slump, wait.
 * `frame` alternates 0 and 1, which is the whole of the animation.
 *
 * The head is three pixels wide against a body of five, which is the whole reason the
 * figures read as people at this size: a head as wide as the shoulders is a totem pole.
 */
export function figure(buf, x, floor, who, { doing = 'wait', frame = 0, helmet, hat = true } = {}) {
  const p = CAST[who] || CAST.intern
  const top = floor - FIGURE.height + 1
  const slump = doing === 'slump' ? 1 : 0
  const bob = doing === 'dance' ? frame : 0
  const y = top + slump - bob
  const hatted = hat && (helmet !== undefined || who === 'builder')

  // head: three wide, with an eye at each edge, from row 1
  rect(buf, x + 2, y + 1, 3, 3, p.skin)
  if (p.visor) {
    rect(buf, x + 2, y + 2, 3, 1, p.visor)
    const lit = doing === 'check' ? (frame ? GREEN : p.eye) : p.eye
    rect(buf, x + 2, y + 2, 1, 1, lit)
    rect(buf, x + 4, y + 2, 1, 1, lit)
    rect(buf, x + 3, y - 1, 1, 2, 0x777777) // the aerial
    rect(buf, x + 3, y - 2, 1, 1, frame ? lit : 0x333333)
  } else if (doing === 'slump') {
    rect(buf, x + 2, y + 3, 3, 1, p.eye) // eyes shut
  } else {
    rect(buf, x + 2, y + 2, 1, 1, p.eye)
    rect(buf, x + 4, y + 2, 1, 1, p.eye)
  }
  if (hatted) {
    rect(buf, x + 1, y, 5, 1, helmet !== undefined ? helmet : p.helmet)
  } else if (who === 'reviewer') {
    rect(buf, x + 2, y, 3, 1, p.hair)
    rect(buf, x + 1, y + 1, 1, 2, p.hair)
  }

  // shoulders and body, five wide from row 4
  rect(buf, x + 1, y + 4, 5, 4, p.body)
  rect(buf, x + 1, y + 7, 5, 1, p.shade)
  if (who === 'judge') rect(buf, x + 3, y + 4, 1, 2, 0xffffff) // the collar

  // arms: one reaches out and taps while working, both go up to celebrate
  if (doing === 'dance') {
    rect(buf, x, y + 1 + frame, 1, 4, p.body)
    rect(buf, x, y + 1 + frame, 1, 1, p.skin)
    rect(buf, x + 6, y + 2 - frame, 1, 4, p.body)
    rect(buf, x + 6, y + 2 - frame, 1, 1, p.skin)
  } else if (doing === 'work' || doing === 'mark' || doing === 'check') {
    rect(buf, x, y + 4, 1, 2, p.body)
    rect(buf, x + 6, y + 4 + frame, 1, 1, p.body)
    rect(buf, x + 6, y + 5 + frame, 1, 1, p.skin)
    if (who === 'reviewer' && doing === 'mark') rect(buf, x + 6, y + 6 + frame, 1, 1, RED) // the red pen
  } else {
    rect(buf, x, y + 4, 1, 2, p.body)
    rect(buf, x + 6, y + 4, 1, 2, p.body)
  }
  if (who === 'judge' && doing === 'rule') rect(buf, x + 6, y + 2 + frame * 2, 2, 1, 0x8a6a3a) // the gavel

  // legs, or a robe to the floor for the judge
  if (p.robe) {
    rect(buf, x + 1, y + 8, 5, 2, p.body)
    rect(buf, x, y + 10, 7, 1, p.shade)
  } else {
    rect(buf, x + 1, y + 8, 2, 2, p.legs)
    rect(buf, x + 4, y + 8, 2, 2, p.legs)
    rect(buf, x + 1, y + 10, 2, 1, p.shoes)
    rect(buf, x + 4, y + 10, 2, 1, p.shoes)
  }
}

/**
 * Pack a pixel buffer into a Raster's cells: two pixel rows to a cell, as `▀` with the top
 * pixel in the foreground and the bottom in the background. Where a pixel is CLEAR the cell
 * falls back to the terminal's own colour, so the scene sits on the pane rather than on a
 * painted rectangle of its own.
 */
export function pack(buf) {
  const rows = Math.ceil(buf.height / 2)
  const words = new Uint32Array(buf.width * rows * 3)
  let i = 0
  for (let row = 0; row < rows; row++) {
    for (let x = 0; x < buf.width; x++) {
      const upper = at(buf, x, row * 2)
      const lower = at(buf, x, row * 2 + 1)
      const hasUpper = upper !== CLEAR, hasLower = lower !== CLEAR
      if (!hasUpper && !hasLower) {
        words[i++] = SPACE, words[i++] = DEFAULT, words[i++] = DEFAULT
      } else if (hasUpper && !hasLower) {
        words[i++] = TOP, words[i++] = upper, words[i++] = DEFAULT
      } else if (!hasUpper && hasLower) {
        words[i++] = BOTTOM, words[i++] = lower, words[i++] = DEFAULT
      } else {
        words[i++] = TOP, words[i++] = upper, words[i++] = lower
      }
    }
  }
  return { columns: buf.width, rows, cells: base64(new Uint8Array(words.buffer)) }
}

/** Who the phase says is at work, and what each of them is doing. */
export function reading(snap) {
  const phase = ((snap && snap.phase) || '').toLowerCase()
  const units = (snap && snap.units) || []
  const working = units.filter((u) => u.state && u.state !== 'approved' && u.state !== 'stopped')
  const unitPhase = working.map((u) => (u.phase || '').toLowerCase()).join(' ')
  const where = phase + ' ' + unitPhase
  const asking = !!(snap && snap.question)
  const done = snap && !snap.live
  return {
    asking,
    done,
    approved: done && snap && snap.approved === true,
    stopped: done && snap && snap.approved === false,
    planning: /planning|plan\b/.test(phase) && !working.length,
    designing: /design/.test(where),
    building: /implement|writing|pass \d+: (implementing|work)/.test(where) || (!!working.length && !/review|panel|audit|check/.test(where)),
    checking: /check/.test(where),
    reviewing: /review/.test(where) && !/panel/.test(where),
    panel: /panel|lens/.test(where),
    // "pre-audit panel" has the word audit in it, and so does the panel's own unit. The
    // Judge's gavel only goes up for the final audit, which happens after the panel.
    judging: /final audit|judging/.test(where) && !/panel|lens/.test(where),
    waves: working,
  }
}

/**
 * The whole scene: the workshop floor, and the cast doing what the job is doing.
 * `columns` is how wide the pane's body is; the height follows from the figures.
 */
export function render(snap, columns, tick = 0) {
  const width = Math.max(FIGURE.width * 2, Math.min(columns, 512))
  const height = 16
  const buf = buffer(width, height)
  const frame = tick % 2
  const floor = height - 3

  rect(buf, 0, floor + 1, width, 1, EDGE)
  rect(buf, 0, floor + 2, width, 1, BEZEL)

  const r = reading(snap)
  if (!snap || !snap.run) {
    figure(buf, 2, floor, 'builder', { doing: 'slump' })
    return { buf, ...pack(buf) }
  }

  // The Judge keeps the right-hand end, behind a bench, so nobody else is drawn into it.
  const spacing = FIGURE.width + 2
  const bench = FIGURE.width + 4
  const seat = width - FIGURE.width - 2
  const room = width >= spacing * 2 ? seat - 2 : width

  // Who is on stage, in the order they stand: the builders, then whoever the pass has
  // reached. More of them than the pane is wide means the ones at the end are left off,
  // which is why the builders come first.
  const cast = []
  // While the panel sits, nobody is building, so one builder stands for the work and the
  // lenses get the room: in a narrow pane they are what there is to see.
  const parts = r.panel ? [{ name: 'main' }] : r.waves.length ? r.waves : [{ name: 'main' }]
  const doing = r.approved ? 'dance' : r.stopped ? 'slump' : r.asking ? 'wait' : r.building ? 'work' : 'wait'
  parts.forEach((_, i) => {
    cast.push({ who: 'builder', doing, frame: (frame + i) % 2,
                helmet: parts.length > 1 ? SWARM_HELMETS[i % SWARM_HELMETS.length] : undefined })
  })
  if (r.panel) {
    // The panel is the lenses, and Red Pen has already had their say by then.
    for (const lens of ['edges', 'truth', 'rehearsal']) {
      cast.push({ who: 'intern', doing: 'work', frame, helmet: LENS_HELMET[lens] })
    }
  } else {
    cast.push({ who: 'checker', doing: r.checking ? 'check' : 'wait', frame })
    if (r.reviewing || r.judging || r.asking) {
      cast.push({ who: 'reviewer', doing: r.reviewing ? 'mark' : 'wait', frame })
    }
  }

  // Spread the cast across the room there is rather than huddling them at the left: a pane
  // docked beside the transcript is wide, and a gap between the workers and the bench reads
  // as a mistake. More of them than will fit means the ones at the end are left off.
  const fit = Math.max(1, Math.min(cast.length, Math.floor(room / spacing)))
  const shown = cast.slice(0, fit)
  const spread = Math.max(spacing, Math.min(FIGURE.width + 11, Math.floor((room - 2) / shown.length)))
  let x = 1
  for (const member of shown) {
    if (x + FIGURE.width > room) break
    figure(buf, x, floor, member.who, member)
    x += spread
  }

  if (width >= spacing * 2) {
    rect(buf, seat - 2, floor - 1, bench, 1, EDGE)  // the bench's top
    rect(buf, seat - 2, floor, bench, 2, INK)       // and its front, down to the floor
    figure(buf, seat, floor - 2, 'judge', { doing: r.judging ? 'rule' : r.approved ? 'dance' : 'wait', frame })
  }

  return { buf, ...pack(buf) }
}
