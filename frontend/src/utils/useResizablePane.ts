import { onMounted, onUnmounted, ref } from 'vue';

interface ResizeBounds {
  min: number;
  max?: number;
}

interface UseResizablePaneOptions {
  initialHeight?: number;
  getAdaptiveHeight?: () => number;
  getResizeBounds: () => ResizeBounds;
  storageKey?: string;
}

export const useResizablePane = (options: UseResizablePaneOptions) => {
  const canUseLocalStorage = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';

  const clampHeight = (value: number) => {
    const bounds = options.getResizeBounds();
    const maxHeight = typeof bounds.max === 'number' ? bounds.max : Number.POSITIVE_INFINITY;
    return Math.max(bounds.min, Math.min(maxHeight, Math.round(value)));
  };

  const resolveInitialPaneState = () => {
    if (options.storageKey && canUseLocalStorage()) {
      const rawValue = window.localStorage.getItem(options.storageKey);
      if (rawValue) {
        const parsedHeight = Number(rawValue);
        if (Number.isFinite(parsedHeight)) {
          return {
            height: clampHeight(parsedHeight),
            manual: true,
          };
        }
        window.localStorage.removeItem(options.storageKey);
      }
    }

    if (options.getAdaptiveHeight) {
      return {
        height: clampHeight(options.getAdaptiveHeight()),
        manual: false,
      };
    }

    return {
      height: clampHeight(options.initialHeight ?? 600),
      manual: false,
    };
  };

  const initialPaneState = resolveInitialPaneState();
  const paneHeight = ref(initialPaneState.height);
  const isResizing = ref(false);
  const isManualResized = ref(initialPaneState.manual);
  const startY = ref(0);
  const startHeight = ref(0);
  let pendingHeight: number | null = null;
  let resizeFrameId: number | null = null;

  const persistHeight = (value: number | null) => {
    if (!options.storageKey || !canUseLocalStorage()) return;

    if (value === null) {
      window.localStorage.removeItem(options.storageKey);
      return;
    }

    window.localStorage.setItem(options.storageKey, String(Math.round(value)));
  };

  const cancelScheduledResize = () => {
    if (resizeFrameId === null || typeof window === 'undefined') return;
    window.cancelAnimationFrame(resizeFrameId);
    resizeFrameId = null;
  };

  const flushPendingHeight = () => {
    if (pendingHeight === null) return;
    paneHeight.value = clampHeight(pendingHeight);
    pendingHeight = null;
  };

  const schedulePaneHeight = (value: number) => {
    if (typeof window === 'undefined') {
      paneHeight.value = clampHeight(value);
      return;
    }

    pendingHeight = value;
    if (resizeFrameId !== null) return;
    resizeFrameId = window.requestAnimationFrame(() => {
      resizeFrameId = null;
      flushPendingHeight();
    });
  };

  const restoreManualHeight = () => {
    if (!options.storageKey || !canUseLocalStorage()) return false;

    const rawValue = window.localStorage.getItem(options.storageKey);
    if (!rawValue) return false;

    const parsedHeight = Number(rawValue);
    if (!Number.isFinite(parsedHeight)) {
      window.localStorage.removeItem(options.storageKey);
      return false;
    }

    paneHeight.value = clampHeight(parsedHeight);
    isManualResized.value = true;
    return true;
  };

  const updateAdaptiveHeight = () => {
    cancelScheduledResize();
    pendingHeight = null;

    if (isManualResized.value) {
      paneHeight.value = clampHeight(paneHeight.value);
      persistHeight(paneHeight.value);
      return;
    }

    if (options.getAdaptiveHeight) {
      paneHeight.value = clampHeight(options.getAdaptiveHeight());
      return;
    }

    paneHeight.value = clampHeight(paneHeight.value);
  };

  const stopResizing = () => {
    if (!isResizing.value) return;

    cancelScheduledResize();
    flushPendingHeight();
    isResizing.value = false;
    window.removeEventListener('mousemove', handleResizing);
    window.removeEventListener('mouseup', stopResizing);
    document.body.style.userSelect = '';
    if (isManualResized.value) {
      persistHeight(paneHeight.value);
    }
  };

  const handleResizing = (event: MouseEvent) => {
    if (!isResizing.value) return;

    const delta = event.clientY - startY.value;
    schedulePaneHeight(startHeight.value + delta);
  };

  const startResizing = (event: MouseEvent) => {
    isResizing.value = true;
    isManualResized.value = true;
    startY.value = event.clientY;
    startHeight.value = paneHeight.value;

    window.addEventListener('mousemove', handleResizing);
    window.addEventListener('mouseup', stopResizing);
    document.body.style.userSelect = 'none';
  };

  onMounted(() => {
    if (!isManualResized.value && !restoreManualHeight()) {
      updateAdaptiveHeight();
    }
    window.addEventListener('resize', updateAdaptiveHeight);
  });

  onUnmounted(() => {
    stopResizing();
    cancelScheduledResize();
    window.removeEventListener('resize', updateAdaptiveHeight);
  });

  return {
    paneHeight,
    isManualResized,
    isResizing,
    startResizing,
    stopResizing,
    updateAdaptiveHeight,
  };
};
