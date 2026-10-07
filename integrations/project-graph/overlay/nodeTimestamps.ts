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

/** Unix milliseconds travel inside each stage object, not a side database.
 * Opening legacy nodes keeps unknown dates unknown. Only serialized node
 * content changes count; camera, selection, save and reference expansion do not.
 * Creation survives identity-preserving undo/gallery transfers. A paste with a
 * new UUID clears source times before reaching this boundary.
 */
export class NodeTimestamps {
  private current: Objects = {};
  private known = new Map<string, NodeTimes>();
  adopt(objects: Objects) {
    this.current = objects;
    for (const [id, value] of Object.entries(objects)) if (!id.startsWith('@')) this.known.set(id, nodeTimes(value));
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
      const prior = previous ? nodeTimes(previous) : this.known.get(id);
      const imported = nodeTimes(value);
      const createdAt = prior ? prior.createdAt ?? null : imported.createdAt ?? now;
      const updatedAt = Math.max(now, (prior?.updatedAt ?? imported.updatedAt ?? 0) + 1, createdAt ?? 0);
      result[id] = { ...value, createdAt, updatedAt };
    }
    return result;
  }
  capture(objects: Objects, now = Date.now()): Objects {
    const next = this.project(this.current, objects, now);
    this.adopt(next);
    return next;
  }
}
