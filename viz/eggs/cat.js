// Office cat easter egg
// Cat walks in when run is live and elapsed > 10 min (600s), or egg=cat in URL
// Stays curled up near desk, shows Z occasionally, walks out when run ends

const cat = {
  x: 0,
  y: 0,
  dir: 1,
  state: 'offstage', // offstage, walking-in, curled-up, walking-out
  zTimer: 0,
  showZ: false,
};

// Check if egg=cat is in URL
const params = new URLSearchParams(location.search);
const eggCat = params.has('egg') && params.get('egg') === 'cat';

// Cat walk speed (pixels per second)
const WALK_SPEED = 20;
// Z appears every 1.5 seconds while curled
const Z_INTERVAL = 1.5;

EGG_UPDATE.push((t, dt) => {
  // Check conditions for cat behavior
  const runLive = S.live;
  const elapsed = S.run ? S.elapsed : 0;
  const shouldStay = eggCat || (runLive && elapsed > 600);

  // State transitions
  if (cat.state === 'offstage') {
    // Start walking in if conditions met
    if (shouldStay) {
      cat.state = 'walking-in';
      cat.x = -30; // Start off left edge
      cat.y = SPOT.desk.y + 1;
      cat.dir = 1;
      cat.zTimer = t;
    }
  } else if (cat.state === 'walking-in') {
    // Walk towards desk
    const destX = SPOT.desk.x - 26;          // under the builder's desk, out of the builder's way
    const dx = destX - cat.x;
    if (Math.abs(dx) < 2) {
      cat.state = 'curled-up';
      cat.x = destX;
    } else {
      cat.x += Math.sign(dx) * WALK_SPEED * dt;
      cat.dir = dx > 0 ? 1 : -1;
    }
  } else if (cat.state === 'curled-up') {
    // Show Z occasionally
    if (t - cat.zTimer >= Z_INTERVAL) {
      cat.showZ = !cat.showZ;
      cat.zTimer = t;
    }
    // Start walking out if run no longer live
    if (!shouldStay) {
      cat.state = 'walking-out';
    }
  } else if (cat.state === 'walking-out') {
    // Walk off right edge
    if (cat.x > W + 30) {
      cat.state = 'offstage';
      cat.x = 0;
      cat.y = 0;
      cat.showZ = false;
    } else {
      cat.x += WALK_SPEED * dt;
    }
  }
});

EGG_DRAW.push((t) => {
  if (cat.state === 'offstage') return;

  // A ginger tabby, drawn with the page's pixel helpers. (x, y) is where its paws touch the floor.
  const x = Math.round(cat.x), y = Math.round(cat.y), d = cat.dir;
  const fur = '#f2a14a', stripe = '#c7702a', light = '#ffd9a0', eye = '#1b1838';
  const at = (dx, w) => (d > 0 ? x + dx : x - dx - w + 1);   // mirror when walking left

  ctx.globalAlpha = 0.35; px(x - 7, y, 14, 2, '#000'); ctx.globalAlpha = 1;

  if (cat.state === 'curled-up') {
    const breathe = Math.floor(t * 1.2) % 2;             // a slow rise and fall
    px(x - 6, y - 5 - breathe, 11, 5 + breathe, fur);     // the curled body
    px(x - 7, y - 4, 1, 3, fur);
    px(x - 4, y - 5 - breathe, 1, 2, stripe); px(x - 1, y - 5 - breathe, 1, 2, stripe);
    px(x - 6, y - 1, 13, 1, light);                       // tail wrapped round the front
    px(x + 3, y - 8, 5, 5, fur);                          // head resting on the body
    px(x + 3, y - 9, 1, 1, fur); px(x + 7, y - 9, 1, 1, fur);
    px(x + 4, y - 6, 1, 1, eye); px(x + 6, y - 6, 1, 1, eye);   // eyes closed
    px(x + 5, y - 5, 1, 1, '#ff8fa3');
    if (cat.showZ) {
      const rise = ((t % Z_INTERVAL) / Z_INTERVAL) * 4;
      text('z', x + 8, Math.round(y - 14 - rise), '#dfe8ff');
    }
    return;
  }

  // walking: body, legs that step, a tail held up, and a head looking where it goes
  const step = Math.floor(t * 8) % 2;
  px(at(-5, 10), y - 7, 10, 4, fur);
  px(at(-3, 1), y - 7, 1, 2, stripe); px(at(0, 1), y - 7, 1, 2, stripe);
  px(at(-5, 1), y - 3, 1, 3 - step, fur); px(at(-3, 1), y - 3, 1, 2 + step, fur);
  px(at(2, 1), y - 3, 1, 2 + step, fur); px(at(4, 1), y - 3, 1, 3 - step, fur);
  px(at(-7, 2), y - 11, 1, 4, fur); px(at(-7, 2), y - 12, 2, 1, fur);   // tail
  px(at(4, 5), y - 11, 5, 5, fur);                                        // head
  px(at(4, 1), y - 12, 1, 1, fur); px(at(8, 1), y - 12, 1, 1, fur);       // ears
  px(at(7, 1), y - 9, 1, 1, eye);
  px(at(5, 3), y - 7, 3, 1, light);
});
