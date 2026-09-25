/** 凡修笔记转换的单一延迟加载入口；各领域 API 复用同一缓存。 */
let fanxiuNoteHelpersPromise: Promise<typeof import('../fanxiuNoteHelpers')> | null = null;

export const loadFanxiuNoteHelpers = () => {
  fanxiuNoteHelpersPromise ??= import('../fanxiuNoteHelpers');
  return fanxiuNoteHelpersPromise;
};
