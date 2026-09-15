// Order in the workshop easter egg
// Click judge 5 times within 3 seconds

const JUDGE_CLICK_TIMES = [];

JUDGE_CLICK_HOOKS.push(function(now) {
  // Add the click time
  JUDGE_CLICK_TIMES.push(now);

  // Keep only clicks within the last 3 seconds
  const threeSecondsAgo = now - 3;
  while (JUDGE_CLICK_TIMES.length > 0 && JUDGE_CLICK_TIMES[0] < threeSecondsAgo) {
    JUDGE_CLICK_TIMES.shift();
  }

  // Check if we have 5+ clicks in the window
  if (JUDGE_CLICK_TIMES.length >= 5) {
    // Trigger the effect once
    judge.gavelUp = now + 0.6;

    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      shakeUntil = now + 0.4;
    }

    say(judge, "ORDER IN THE WORKSHOP!", "ok", 4);

    // Reset the click count
    JUDGE_CLICK_TIMES.length = 0;
  }
});
