/** Format-independent collaboration values. A provider chooses stable object IDs. */
export type Objects = Record<string, Record<string, any>>;
export type Change = { id: string; before: Record<string, any> | null; after: Record<string, any> | null };
export const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
export function differences(before: Objects, after: Objects): Change[] {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(id => !equal(before[id], after[id]))
    .map(id => ({ id, before: before[id] ?? null, after: after[id] ?? null }));
}
export function changed(objects: Objects, changes: Change[]): Objects {
  const next = { ...objects };
  for (const change of changes) { if (change.after === null) delete next[change.id]; else next[change.id] = change.after; }
  return next;
}
/** Preserve disjoint edits; conflicting content requires an explicit provider policy. */
export function rebaseObjects(_base: Objects, local: Change[], remote: Objects): Change[] {
  return local.flatMap(change => {
    const current = remote[change.id] ?? null;
    if (equal(current, change.after)) return [];
    if (equal(current, change.before)) return [{ ...change, before: current }];
    throw new Error(`对象 ${change.id} 已被其他协作者修改`);
  });
}
