import { createLatestRequest } from './latestRequest.ts';

/** Serial frame sampling. Stopping invalidates in-flight reads, including across restarts. */
export function createAsyncFrameSampler<T>(options: {
  active: () => boolean;
  read: () => Promise<T>;
  accept: (value: T) => void;
  onError: (error: unknown) => void;
}) {
  const requests = createLatestRequest();
  let pending: number | null = null;
  let running = false;

  const stop = () => {
    requests.invalidate();
    running = false;
    if (pending !== null) window.cancelAnimationFrame(pending);
    pending = null;
  };

  const schedule = (isCurrent: () => boolean) => {
    pending = window.requestAnimationFrame(async () => {
      pending = null;
      if (!isCurrent() || !running) return;
      if (!options.active()) { stop(); return; }
      try {
        const value = await options.read();
        if (!isCurrent() || !running) return;
        if (!options.active()) { stop(); return; }
        options.accept(value);
        if (isCurrent() && running) schedule(isCurrent);
      } catch (error) {
        if (!isCurrent() || !running) return;
        stop();
        options.onError(error);
      }
    });
  };

  return {
    start() {
      if (running) return;
      running = true;
      schedule(requests.begin());
    },
    stop,
  };
}
