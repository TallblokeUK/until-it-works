// daynight.js - Day/night window easter egg
// The window shows daytime (7:00-19:00) or night based on local clock.
// URL params egg=day or egg=night force the mode.

(function () {
  // Save original night sky function
  const originalNightSky = drawWindowSky;

  // Parse egg=day or egg=night from URL
  const params = new URLSearchParams(location.search);
  const forceMode = params.get('egg') === 'day' ? true :
                    params.get('egg') === 'night' ? false : null;

  // Cloud state - one pixel cloud that drifts
  let cloudX = 0;
  let cloudPrevTime = performance.now() / 1000;

  function isDaytime() {
    if (forceMode !== null) return forceMode;
    const hour = new Date().getHours();
    return hour >= 7 && hour < 19;
  }

  drawWindowSky = function(t, wx, wy) {
    if (isDaytime()) {
      // Daytime: light blue sky, sun, and drifting cloud
      // Fill window with light blue sky
      px(wx, wy, 48, 36, '#87ceeb');

      // Draw sun - yellow circle-ish shape in upper right
      px(wx + 36, wy + 4, 6, 6, '#ffd700');
      px(wx + 38, wy + 2, 2, 2, '#ffd700');
      px(wx + 38, wy + 10, 2, 2, '#ffd700');
      px(wx + 34, wy + 6, 2, 2, '#ffd700');
      px(wx + 42, wy + 6, 2, 2, '#ffd700');

      // Drifting cloud - position based on elapsed time (loops every ~30 seconds)
      const now = performance.now() / 1000;
      const dt = now - cloudPrevTime;
      cloudPrevTime = now;
      cloudX = (cloudX + dt * 5) % 60; // 5 pixels/sec, wraps at 60

      // Draw cloud at cloudX position (3-pixel wide blob)
      px(wx + cloudX, wy + 14, 3, 1, '#ffffff');
      px(wx + cloudX + 1, wy + 13, 3, 1, '#ffffff');
    } else {
      // Nighttime - use original function
      if (originalNightSky) originalNightSky(t, wx, wy);
    }
  }
})();
