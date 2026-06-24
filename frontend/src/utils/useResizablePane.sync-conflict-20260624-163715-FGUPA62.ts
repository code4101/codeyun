import { onMounted, onUnmounted, ref } from 'vue';

interface ResizeBounds {
  min: number;
  max?: number;
}

interface UseResizablePaneOptions {
  initialHeight?: number;
  getAdaptiveHeight?: () => number;
  getResizeBounds: () => ResizeBounds;
}

export const useResizablePane = (options: UseResizablePaneOptions) => {
  const paneHeight = ref(options.initialHeight ?? 600);
  const isResizing = ref(false);
  const isManualResized = ref(false);
  const startY = ref(0);
  const startHeight = ref(0);

  const updateAdaptiveHeight = () => {
    if (!options.getAdaptiveHeight || isManualResized.value) return;
    paneHeight.value = options.getAdaptiveHeight();
  };

  const stopResizing = () => {
    if (!isResizing.value) return;

    isResizing.value = false;
    window.removeEventListener('mousemove', handleResizing);
    window.removeEventListener('mouseup', stopResizing);
    document.body.style.userSelect = '';
  };

  const handleResizing = (event: MouseEvent) => {
    if (!isResizing.value) return;

    const delta = event.clientY - startY.value;
    const bounds = options.getResizeBounds();
    const nextHeight = startHeight.value + delta;
    const maxHeight = typeof bounds.max === 'number' ? bounds.max : Number.POSITIVE_INFINITY;

    paneHeight.value = Math.max(bounds.min, Math.min(maxHeight, nextHeight));
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
    updateAdaptiveHeight();
    window.addEventListener('resize', updateAdaptiveHeight);
  });

  onUnmounted(() => {
    stopResizing();
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
