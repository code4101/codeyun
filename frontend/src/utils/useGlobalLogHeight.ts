import { useResizablePane } from '@/utils/useResizablePane';

export const GLOBAL_LOG_HEIGHT_STORAGE_KEY = 'layout.globalLogHeightPx';
export const DEFAULT_GLOBAL_LOG_HEIGHT = 600;
export const MIN_GLOBAL_LOG_HEIGHT = 220;
export const MAX_GLOBAL_LOG_HEIGHT = 1200;

export const useGlobalLogHeight = () => {
  return useResizablePane({
    initialHeight: DEFAULT_GLOBAL_LOG_HEIGHT,
    storageKey: GLOBAL_LOG_HEIGHT_STORAGE_KEY,
    getResizeBounds: () => ({
      min: MIN_GLOBAL_LOG_HEIGHT,
      max: MAX_GLOBAL_LOG_HEIGHT,
    }),
  });
};
