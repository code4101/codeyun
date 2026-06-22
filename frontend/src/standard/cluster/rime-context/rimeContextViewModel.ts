import type { RimeContextPredictionRow } from '@/api/rimeContextPrediction';

export interface PrefixGroup {
  key: string;
  rows: RimeContextPredictionRow[];
  totalWeight: number;
}

export interface ContextGroup {
  key: string;
  prefixes: PrefixGroup[];
  rowCount: number;
  totalWeight: number;
}

export interface ContextTailGroup {
  key: string;
  contexts: ContextGroup[];
  contextCount: number;
  rowCount: number;
  totalWeight: number;
}

function compareAlphabetical(left: string, right: string) {
  return left.localeCompare(right, 'en-US');
}

export function comparePrefixGroups(left: PrefixGroup, right: PrefixGroup) {
  return right.totalWeight - left.totalWeight || compareAlphabetical(left.key, right.key);
}

function contextTailKey(value: string) {
  if (!value || value === '__global') return '__global';
  const tokens = (value || '').trim().split(/\s+/).filter(Boolean);
  return tokens[tokens.length - 1] || value || '__empty';
}

export function buildGroupedContexts(rows: RimeContextPredictionRow[]): ContextGroup[] {
  const contextMap = new Map<string, Map<string, RimeContextPredictionRow[]>>();
  for (const row of rows) {
    const context = row.context || '__global';
    const prefix = row.prefix || '';
    if (!contextMap.has(context)) {
      contextMap.set(context, new Map());
    }
    const prefixMap = contextMap.get(context)!;
    if (!prefixMap.has(prefix)) {
      prefixMap.set(prefix, []);
    }
    prefixMap.get(prefix)!.push(row);
  }

  return Array.from(contextMap.entries()).map(([context, prefixMap]) => {
    const prefixes = Array.from(prefixMap.entries()).map(([prefix, prefixRows]) => {
      const sortedRows = prefixRows.slice().sort((left, right) => right.weight - left.weight || left.candidate.localeCompare(right.candidate, 'zh-CN'));
      return {
        key: prefix,
        rows: sortedRows,
        totalWeight: sortedRows.reduce((sum, row) => sum + Number(row.weight || 0), 0),
      };
    }).sort(comparePrefixGroups);

    return {
      key: context,
      prefixes,
      rowCount: prefixes.reduce((sum, item) => sum + item.rows.length, 0),
      totalWeight: prefixes.reduce((sum, item) => sum + item.totalWeight, 0),
    };
  }).sort((left, right) => right.rowCount - left.rowCount || left.key.localeCompare(right.key, 'zh-CN'));
}

export function filterContexts(
  contexts: ContextGroup[],
  keyword: string,
  displayContext: (context: string) => string,
): ContextGroup[] {
  if (!keyword) return contexts;
  return contexts
    .map((context) => {
      const contextMatched = `${context.key} ${displayContext(context.key)}`.toLowerCase().includes(keyword);
      const prefixes = context.prefixes
        .map((prefix) => {
          const prefixMatched = prefix.key.toLowerCase().includes(keyword);
          const rows = prefix.rows.filter((row) => (
            contextMatched
            || prefixMatched
            || row.candidate.toLowerCase().includes(keyword)
          ));
          return rows.length || prefixMatched || contextMatched
            ? { ...prefix, rows, totalWeight: rows.reduce((sum, row) => sum + Number(row.weight || 0), 0) }
            : null;
        })
        .filter((item): item is PrefixGroup => Boolean(item))
        .sort(comparePrefixGroups);
      return prefixes.length
        ? {
          ...context,
          prefixes,
          rowCount: prefixes.reduce((sum, item) => sum + item.rows.length, 0),
          totalWeight: prefixes.reduce((sum, item) => sum + item.totalWeight, 0),
        }
        : null;
    })
    .filter((item): item is ContextGroup => Boolean(item));
}

export function buildContextTailGroups(contexts: ContextGroup[]): ContextTailGroup[] {
  const groupMap = new Map<string, ContextGroup[]>();
  for (const context of contexts) {
    const key = contextTailKey(context.key);
    if (!groupMap.has(key)) {
      groupMap.set(key, []);
    }
    groupMap.get(key)!.push(context);
  }

  return Array.from(groupMap.entries()).map(([key, groupContexts]) => {
    const sortedContexts = groupContexts.slice().sort((left, right) => (
      right.rowCount - left.rowCount || left.key.localeCompare(right.key, 'zh-CN')
    ));
    return {
      key,
      contexts: sortedContexts,
      contextCount: sortedContexts.length,
      rowCount: sortedContexts.reduce((sum, item) => sum + item.rowCount, 0),
      totalWeight: sortedContexts.reduce((sum, item) => sum + item.totalWeight, 0),
    };
  }).sort((left, right) => right.rowCount - left.rowCount || left.key.localeCompare(right.key, 'zh-CN'));
}

export function aggregatePrefixGroups(contexts: ContextGroup[], contextKey: string): PrefixGroup[] {
  const prefixMap = new Map<string, Map<string, RimeContextPredictionRow>>();
  for (const context of contexts) {
    for (const prefix of context.prefixes) {
      if (!prefixMap.has(prefix.key)) {
        prefixMap.set(prefix.key, new Map());
      }
      const candidateMap = prefixMap.get(prefix.key)!;
      for (const row of prefix.rows) {
        const existing = candidateMap.get(row.candidate);
        if (existing) {
          existing.weight += Number(row.weight || 0);
        } else {
          candidateMap.set(row.candidate, {
            context: contextKey,
            prefix: prefix.key,
            candidate: row.candidate,
            weight: Number(row.weight || 0),
            comment: '',
          });
        }
      }
    }
  }

  return Array.from(prefixMap.entries()).map(([prefix, rowsByCandidate]) => {
    const rows = Array.from(rowsByCandidate.values()).sort((left, right) => (
      Number(right.weight || 0) - Number(left.weight || 0)
      || left.candidate.localeCompare(right.candidate, 'zh-CN')
    ));
    return {
      key: prefix,
      rows,
      totalWeight: rows.reduce((sum, row) => sum + Number(row.weight || 0), 0),
    };
  }).sort(comparePrefixGroups);
}

export function buildPrefixSummaryRows(prefixes: PrefixGroup[]): RimeContextPredictionRow[] {
  const candidateMap = new Map<string, RimeContextPredictionRow>();
  for (const prefix of prefixes) {
    for (const row of prefix.rows) {
      const existing = candidateMap.get(row.candidate);
      if (existing) {
        existing.weight += Number(row.weight || 0);
      } else {
        candidateMap.set(row.candidate, {
          ...row,
          prefix: '',
          weight: Number(row.weight || 0),
          comment: '',
        });
      }
    }
  }
  return Array.from(candidateMap.values()).sort((left, right) => (
    Number(right.weight || 0) - Number(left.weight || 0)
    || left.candidate.localeCompare(right.candidate, 'zh-CN')
  ));
}
