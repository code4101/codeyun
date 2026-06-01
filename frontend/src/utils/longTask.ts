export interface LongTaskSnapshot<T = unknown> {
  task_id: string;
  kind: string;
  status: string;
  running: boolean;
  stage: string;
  message: string;
  created_at: number;
  started_at?: number | null;
  updated_at: number;
  finished_at?: number | null;
  progress_current?: number | null;
  progress_total?: number | null;
  metadata?: Record<string, unknown>;
  error?: string | null;
  error_status_code?: number | null;
  elapsed_ms?: number;
  result?: T;
}

export interface RunLongTaskOptions<T> {
  start: () => Promise<LongTaskSnapshot<T>>;
  poll: (taskId: string) => Promise<LongTaskSnapshot<T>>;
  pollIntervalMs?: number;
  idleTimeoutMs?: number;
  onUpdate?: (snapshot: LongTaskSnapshot<T>) => void;
}

export interface MonitorPolledTaskOptions<T> {
  initial: T;
  poll: (task: T) => Promise<T>;
  isRunning: (task: T) => boolean;
  getUpdatedAt?: (task: T) => number | null | undefined;
  getError?: (task: T) => string | null | undefined;
  pollIntervalMs?: number;
  idleTimeoutMs?: number;
  onUpdate?: (task: T) => void;
}

const DEFAULT_POLL_INTERVAL_MS = 1000;
const DEFAULT_IDLE_TIMEOUT_MS = 30_000;

const sleep = (delayMs: number) => new Promise((resolve) => window.setTimeout(resolve, delayMs));

const taskUpdatedAtMs = (snapshot: LongTaskSnapshot<unknown>) => {
  const updatedAt = Number(snapshot.updated_at || 0);
  return updatedAt > 0 ? updatedAt * 1000 : Date.now();
};

export async function runLongTask<T>(options: RunLongTaskOptions<T>): Promise<T> {
  const pollIntervalMs = Math.max(250, options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
  const idleTimeoutMs = Math.max(1000, options.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS);
  let snapshot = await options.start();
  let lastHeartbeatAt = taskUpdatedAtMs(snapshot);
  options.onUpdate?.(snapshot);

  while (snapshot.running) {
    await sleep(pollIntervalMs);
    snapshot = await options.poll(snapshot.task_id);
    options.onUpdate?.(snapshot);

    const heartbeatAt = taskUpdatedAtMs(snapshot);
    if (heartbeatAt > lastHeartbeatAt) {
      lastHeartbeatAt = heartbeatAt;
    }

    if (Date.now() - lastHeartbeatAt > idleTimeoutMs) {
      throw new Error(`${snapshot.message || '任务运行中'}，但超过 ${Math.round(idleTimeoutMs / 1000)} 秒没有心跳`);
    }
  }

  if (snapshot.status === 'completed') {
    return snapshot.result as T;
  }

  throw new Error(snapshot.error || snapshot.message || '任务执行失败');
}

export async function monitorPolledTask<T>(options: MonitorPolledTaskOptions<T>): Promise<T> {
  const pollIntervalMs = Math.max(250, options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
  const idleTimeoutMs = Math.max(1000, options.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS);
  let snapshot = options.initial;
  let lastHeartbeatAt = Date.now();
  const initialUpdatedAt = Number(options.getUpdatedAt?.(snapshot) || 0);
  if (initialUpdatedAt > 0) {
    lastHeartbeatAt = initialUpdatedAt * 1000;
  }
  options.onUpdate?.(snapshot);

  while (options.isRunning(snapshot)) {
    await sleep(pollIntervalMs);
    snapshot = await options.poll(snapshot);
    options.onUpdate?.(snapshot);

    const updatedAt = Number(options.getUpdatedAt?.(snapshot) || 0);
    if (updatedAt > 0) {
      const heartbeatAt = updatedAt * 1000;
      if (heartbeatAt > lastHeartbeatAt) {
        lastHeartbeatAt = heartbeatAt;
      }
    } else if (options.isRunning(snapshot)) {
      lastHeartbeatAt = Date.now();
    }

    if (Date.now() - lastHeartbeatAt > idleTimeoutMs) {
      throw new Error(`任务仍显示运行中，但超过 ${Math.round(idleTimeoutMs / 1000)} 秒没有心跳`);
    }
  }

  const error = options.getError?.(snapshot);
  if (error) {
    throw new Error(error);
  }
  return snapshot;
}
