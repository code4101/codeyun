/** Finger-accessible selection commands. Capture targets before asynchronous
 * collaboration work; repeated taps must never delete the next selection. */
export function installTouchSelectionActions(toolbar: HTMLElement, options: {
  selectedIds: () => string[];
  readOnly: () => boolean;
  enabled: () => boolean;
  remove: (ids: string[]) => Promise<void>;
  undo: () => Promise<void>;
  busy: (value: boolean) => void;
  error: (error: unknown) => void;
}) {
  const document = toolbar.ownerDocument;
  const group = document.createElement('div'); group.className = 'codeyun-touch-selection';
  const remove = document.createElement('button'), undo = document.createElement('button');
  remove.type = undo.type = 'button';
  remove.dataset.touchAction = 'delete'; undo.dataset.touchAction = 'undo';
  undo.textContent = '撤销'; undo.title = '撤销上一步操作';
  group.append(remove, undo); toolbar.append(group);
  let pending = false, disposed = false;
  const refresh = () => {
    const count = options.selectedIds().length;
    group.hidden = options.readOnly();
    remove.textContent = count ? `删除 (${count})` : '删除';
    remove.title = count ? `删除选中的 ${count} 个对象，可撤销` : '先选择要删除的节点或连线';
    remove.disabled = pending || !options.enabled() || options.readOnly() || !count;
    undo.disabled = pending || !options.enabled() || options.readOnly();
  };
  const run = async (action: () => Promise<void>) => {
    if (pending || options.readOnly() || !options.enabled()) return;
    pending = true; options.busy(true); refresh();
    try { await action(); } catch (error) { options.error(error); }
    finally { pending = false; options.busy(false); if (!disposed) refresh(); }
  };
  remove.onclick = () => {
    const ids = options.selectedIds();
    if (ids.length) void run(() => options.remove(ids));
  };
  undo.onclick = () => { void run(options.undo); };
  refresh();
  return { refresh, dispose() { disposed = true; group.remove(); } };
}
