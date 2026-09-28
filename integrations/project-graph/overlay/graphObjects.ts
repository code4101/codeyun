/** Protocol 1 values; identical archive boundary to backend/core/project_graph/codec.py. */
import { equal, type Objects, type Change } from '../../../frontend/src/collaboration/objectState.ts';
export { equal, differences, changed, type Objects, type Change } from '../../../frontend/src/collaboration/objectState.ts';
/** Rebase independent insert/delete operations on the shared drawing order.
 * Existing-object reorder wins over an unchanged order; two incompatible
 * reorders remain a visible conflict. No object content is silently merged. */
export function rebaseLocal(base: Objects, local: Change[], remote: Objects): Change[] {
  return local.flatMap(change => {
    const current = remote[change.id] ?? null;
    if (equal(current, change.after)) return [];
    if (equal(current, change.before)) return [{ ...change, before: current }];
    if (change.id !== '@order' || !change.after || !current) throw new Error(`对象 ${change.id} 已被其他协作者修改`);
    const original = base['@order'].value as string[], wanted = change.after.value as string[], received = current.value as string[];
    const common = new Set(original.filter(id => wanted.includes(id) && received.includes(id)));
    const oldOrder = original.filter(id => common.has(id)), localOrder = wanted.filter(id => common.has(id)), remoteOrder = received.filter(id => common.has(id));
    if (!equal(localOrder, oldOrder) && !equal(remoteOrder, oldOrder) && !equal(localOrder, remoteOrder)) throw new Error('双方同时调整了对象层级，请保留草稿后合并');
    const deleted = new Set(original.filter(id => !wanted.includes(id) || !received.includes(id)));
    const preferred = equal(localOrder, oldOrder) ? received : wanted;
    const secondary = preferred === received ? wanted : received;
    const order = [...preferred.filter(id => !deleted.has(id)), ...secondary.filter(id => !deleted.has(id) && !preferred.includes(id))];
    return [{ id: '@order', before: current, after: { value: order } }];
  });
}
export function references(value: any): string[] {
  if (!value || typeof value !== 'object') return [];
  if (Object.keys(value).length === 1 && typeof value.$graphRef === 'string') return [value.$graphRef];
  return Object.values(value).flatMap(references);
}
export function stageToObjects(stage: any[]): Objects {
  const resolve = (item: any): any => {
    const visited = new Set<string>();
    while (item && typeof item === 'object' && Object.keys(item).length === 1 && typeof item.$ === 'string') {
      if (visited.has(item.$)) throw new Error('循环图对象引用');
      visited.add(item.$);
      item = item.$.split('/').filter(Boolean).reduce((value: any, key: string) => value[key], stage);
    }
    return item;
  };
  const top = stage.map(resolve), ids = new Set(top.map(item => item.uuid));
  if (ids.size !== top.length || [...ids].some(id => typeof id !== 'string')) throw new Error('无效图对象身份');
  const rewrite = (value: any, root = false): any => {
    value = resolve(value);
    if (Array.isArray(value)) return value.map(item => rewrite(item));
    if (value && typeof value === 'object') {
      if (!root && ids.has(value.uuid)) return { $graphRef: value.uuid };
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, rewrite(item)]));
    }
    return value;
  };
  return { ...Object.fromEntries(top.map(item => [item.uuid, rewrite(item, true)])), '@order': { value: top.map(item => item.uuid) } };
}
export function objectsToStage(objects: Objects): any[] {
  const order = objects['@order']?.value as string[], paths = new Map<string, string>();
  if (!Array.isArray(order)) throw new Error('缺少图对象顺序');
  const expand = (value: any, path: string): any => {
    if (Array.isArray(value)) return value.map((item, index) => expand(item, `${path}/${index}`));
    if (value && typeof value === 'object') {
      if (Object.keys(value).length === 1 && typeof value.$graphRef === 'string') return objectAt(value.$graphRef, path);
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, expand(item, `${path}/${key}`)]));
    }
    return value;
  };
  const objectAt = (id: string, path: string): any => {
    if (paths.has(id)) return { $: paths.get(id) };
    if (!objects[id]) throw new Error(`对象引用不存在：${id}`);
    paths.set(id, path);
    return expand(objects[id], path);
  };
  return order.map((id, index) => objectAt(id, `/${index}`));
}
