export const INVENTORY_SNAPSHOT_AUTO_COLLECT_STALE_MS = 24 * 60 * 60 * 1000

export function shouldAutoCollectInventorySnapshot(
  runtimeUpdatedAt: number | null | undefined,
  now = new Date(),
  staleAfterMs = INVENTORY_SNAPSHOT_AUTO_COLLECT_STALE_MS,
): boolean {
  const timestamp = Number(runtimeUpdatedAt)
  if (!Number.isFinite(timestamp) || timestamp <= 0) return true
  return now.getTime() - timestamp * 1000 >= staleAfterMs
}
