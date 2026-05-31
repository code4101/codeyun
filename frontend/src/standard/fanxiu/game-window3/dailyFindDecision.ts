export type DailyFindProgress = {
  current: number;
  total: number;
};

export type DailyFindStatusCode = 0 | 1 | 2;

export type DailyFindDecision = 'ready' | 'ongoing' | 'completed' | 'retry' | 'not_found';

export type DailyFindDecisionTask = {
  query: string;
  notFoundStatus: number;
  requireProgress: boolean;
};

export type DailyFindDecisionSummary = {
  statusCode: DailyFindStatusCode;
  progress: DailyFindProgress | null;
};

export type DailyFindResult<TSummary> = {
  query: string;
  matchedText: string;
  statusCode: number;
  decision: DailyFindDecision;
  reason: string;
  summary: TSummary | null;
};

export const DAILY_COMPLETED_STATUS_PATTERN = /已结束|已完成|可购买/;
export const DAILY_ONGOING_STATUS_PATTERN = /闻道中/;
export const DAILY_STATUS_CANDIDATE_PATTERN = /已结束|已完成|可购买|闻道中|未占领|未领取|已领取|领取|前往|挑战|参与|进行|扫荡|报名|已报名|未报名|可挑战|可参与/;

export const normalizeDailyOcrText = (text: string) => (
  text
    .replace(/[：:]/g, '')
    .replace(/(?<=\d)[.。](?=\d)/g, '/')
    .replace(/[｜|]/g, '/')
    .replace(/[Oo零]/g, '0')
    .replace(/\s+/g, '')
);

export const extractDailyProgress = (text: string): DailyFindProgress | null => {
  const normalized = normalizeDailyOcrText(text);
  const pairs = Array.from(normalized.matchAll(/(?<!\d)(\d{1,3})\s*\/\s*(\d{1,3})(?!\d)/g))
    .map((match) => {
      const rawCurrent = Number(match[1]);
      const total = Number(match[2]);
      const foldedCurrent = rawCurrent > total && rawCurrent >= 10 ? rawCurrent % 10 : rawCurrent;
      return { current: foldedCurrent, total };
    })
    .filter((item) => item.total > 0 && item.current <= item.total);
  return pairs.at(-1) ?? null;
};

export const extractDailyStatusText = (text: string): string => (
  normalizeDailyOcrText(text).match(DAILY_STATUS_CANDIDATE_PATTERN)?.[0] ?? ''
);

export const getDailyStatusCode = (
  status: string,
  progress: DailyFindProgress | null,
): DailyFindStatusCode => {
  if (DAILY_ONGOING_STATUS_PATTERN.test(status)) return 1;
  if (DAILY_COMPLETED_STATUS_PATTERN.test(status)) return 2;
  if (progress && progress.current >= progress.total) return 2;
  return 0;
};

export const decideDailyFindResult = <TSummary extends DailyFindDecisionSummary>(
  task: DailyFindDecisionTask,
  matchedText: string,
  summary: TSummary | null,
): DailyFindResult<TSummary> => {
  if (!summary) {
    const decision: DailyFindDecision = task.notFoundStatus === 2 ? 'completed' : 'not_found';
    return {
      query: task.query,
      matchedText,
      statusCode: task.notFoundStatus,
      decision,
      reason: task.notFoundStatus === 2 ? '未找到任务，按旧版默认已完成语义处理' : '未找到任务块',
      summary: null,
    };
  }

  const progress = summary.progress;
  if (progress && progress.current < progress.total) {
    return {
      query: task.query,
      matchedText,
      statusCode: 0,
      decision: 'ready',
      reason: summary.statusCode > 0 ? '状态疑似完成但进度未满，按待执行处理' : '进度未满',
      summary,
    };
  }

  if (summary.statusCode === 1) {
    return {
      query: task.query,
      matchedText,
      statusCode: 1,
      decision: 'ongoing',
      reason: '命中特殊进行中状态',
      summary,
    };
  }

  if (summary.statusCode === 2) {
    if (task.requireProgress && !progress) {
      return {
        query: task.query,
        matchedText,
        statusCode: 2,
        decision: 'retry',
        reason: '状态显示完成但未读到进度，需要短重试复核',
        summary,
      };
    }
    return {
      query: task.query,
      matchedText,
      statusCode: 2,
      decision: 'completed',
      reason: progress ? '进度已满或状态已完成' : '状态已完成',
      summary,
    };
  }

  if (task.requireProgress && !progress) {
    return {
      query: task.query,
      matchedText,
      statusCode: 0,
      decision: 'retry',
      reason: '任务可见但未读到进度，需要短重试复核',
      summary,
    };
  }

  return {
    query: task.query,
    matchedText,
    statusCode: 0,
    decision: 'ready',
    reason: '任务可见且未判定完成',
    summary,
  };
};
