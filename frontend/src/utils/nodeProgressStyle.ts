/** Explicit progress is independent of lifecycle stage; done alone defaults to full. */
export const resolveNodeProgressStyle = (
  stage: string,
  progress: number | null | undefined,
  baseColor: string,
  foregroundColor: string,
) => {
  const ratio = typeof progress === 'number' && Number.isFinite(progress)
    ? Math.min(1, Math.max(0, progress))
    : stage === 'done' ? 1 : null;
  if (ratio === null) return null;
  if (ratio === 1) return {
    backgroundColor: baseColor,
    backgroundImage: 'none',
    color: foregroundColor,
    fillTextColor: foregroundColor,
    emptyTextColor: foregroundColor,
    partialFillRatio: null,
  };
  const pct = `${(ratio * 100).toFixed(2)}%`;
  return {
    backgroundColor: '#FFFFFF',
    backgroundImage: `linear-gradient(to right, ${baseColor} 0%, ${baseColor} ${pct}, #FFFFFF ${pct}, #FFFFFF 100%)`,
    color: '#111827',
    fillTextColor: foregroundColor,
    emptyTextColor: '#111827',
    partialFillRatio: ratio,
  };
};
