import { equal, type Objects } from './graphObjects.ts';

export interface NodeTimes { createdAt?: number | null; updatedAt?: number | null }
export function nodeTimes(value: NodeTimes): NodeTimes {
  const times: NodeTimes = {};
  for (const key of ['createdAt', 'updatedAt'] as const) {
    const time = value[key];
    if (time !== undefined) {
      if (time !== null && (!Number.isSafeInteger(time) || time <= 0)) throw new Error('无效节点时间');
      times[key] = time;
    }
  }
  if (times.createdAt && times.updatedAt && times.updatedAt < times.createdAt) throw new Error('编辑时间早于创建时间');
  return times;
}
const content = (value: any) => {
  const { createdAt: _created, updatedAt: _updated, ...rest } = value;
  return rest;
};
export function nodeHistoryMatches(current: any, expected: any): boolean {
  if (!current?.uuid || !expected?.uuid) return equal(current, expected);
  return (current.createdAt ?? null) === (expected.createdAt ?? null) && equal(content(current), content(expected));
}

/** Unix milliseconds travel inside each stage object, not a side database.
 * Opening legacy nodes keeps unknown dates unknown. Only serialized node
 * content changes count; camera, selection, save and reference expansion do not.
 * Creation survives identity-preserving undo/gallery transfers. A paste with a
 * new UUID clears source times before reaching this boundary.
 */
export class NodeTimestamps {
  // Store immutable signatures: native plain-object properties may be edited
  // in place. Holding their references would erase the previous comparison.
  private current = new Map<string, { signature: string; times: NodeTimes }>();
  private known = new Map<string, NodeTimes>();
  adopt(objects: Objects) {
    this.current.clear();
    for (const [id, value] of Object.entries(objects)) if (!id.startsWith('@')) {
      const times = nodeTimes(value);
      this.current.set(id, { signature: JSON.stringify(content(value)), times });
      this.known.set(id, times);
    }
  }
  private edit(value: any, prior: NodeTimes | undefined, now: number) {
    const imported = nodeTimes(value);
    const createdAt = prior ? prior.createdAt ?? null : imported.createdAt ?? now;
    const updatedAt = Math.max(now, (prior?.updatedAt ?? imported.updatedAt ?? 0) + 1, createdAt ?? 0);
    return { ...value, createdAt, updatedAt };
  }
  project(before: Objects, next: Objects, now = Date.now()): Objects {
    const result = { ...next };
    for (const [id, value] of Object.entries(next)) {
      if (id.startsWith('@')) continue;
      const previous = before[id];
      if (previous && equal(content(previous), content(value))) {
        result[id] = { ...content(value), ...nodeTimes(previous) };
        continue;
      }
      result[id] = this.edit(value, previous ? nodeTimes(previous) : this.known.get(id), now);
    }
    return result;
  }
  capture(objects: Objects, now = Date.now()): Objects {
    const result = { ...objects }, current = new Map<string, { signature: string; times: NodeTimes }>();
    for (const [id, value] of Object.entries(objects)) {
      if (id.startsWith('@')) continue;
      const previous = this.current.get(id), signature = JSON.stringify(content(value));
      result[id] = previous?.signature === signature
        ? { ...content(value), ...previous.times }
        : this.edit(value, previous?.times ?? this.known.get(id), now);
      const times = nodeTimes(result[id]);
      current.set(id, { signature, times }); this.known.set(id, times);
    }
    this.current = current;
    return result;
  }
}
