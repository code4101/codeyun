/** Frame pacing uses elapsed intervals, never reinterprets past time at a new FPS. */
export function createFramePacer(tick: () => void, getFps: () => number) {
  let previous: number | undefined;
  let accumulated = 0;
  let previousFps = 0;
  return (time: number) => {
    const fps = Math.max(1, getFps());
    const step = 1000 / fps;
    if (previous === undefined) { previous = time; previousFps = fps; return; }
    accumulated += Math.max(0, time - previous);
    previous = time;
    // Focus changes start a fresh interval, without a catch-up burst or a long stall.
    if (fps !== previousFps) { accumulated = Math.min(accumulated, step); previousFps = fps; }
    const count = Math.floor((accumulated + 1e-7) / step);
    accumulated = Math.max(0, accumulated - count * step);
    for (let index = 0; index < Math.min(count, 10); index++) tick();
  };
}
