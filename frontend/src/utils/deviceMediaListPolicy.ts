export interface DeviceMediaListPolicyRequest {
  recursive?: boolean;
  scan_limit?: number;
  limit?: number;
  snapshot_id?: string;
  sort_program?: {
    rules?: Array<{ field?: string | null }>;
  } | null;
}

const NON_RECURSIVE_FAST_PATH_SCAN_LIMIT = 2_000;
const FAST_PATH_PAGE_LIMIT = 100;
const DATABASE_MEDIA_SORT_FIELDS = new Set(['weight', 'modified_at', 'size', 'relative_path', 'kind']);

export const usesDuplicateClusterSort = (payload: DeviceMediaListPolicyRequest) =>
  Array.isArray(payload.sort_program?.rules)
  && payload.sort_program.rules.some((rule) => rule?.field === 'duplicate_cluster');

export const canUseRecursiveDatabaseMediaPage = (payload: DeviceMediaListPolicyRequest) => {
  if (payload.recursive !== true) {
    return false;
  }

  const rules = Array.isArray(payload.sort_program?.rules) ? payload.sort_program.rules : [];
  return rules.length > 0 && rules.every((rule, index) => {
    if (rule?.field === 'name' && index > 0) {
      return true;
    }
    return DATABASE_MEDIA_SORT_FIELDS.has(String(rule?.field || ''));
  });
};

export const shouldAttemptDeviceMediaSync = (payload: DeviceMediaListPolicyRequest) => {
  if (
    usesDuplicateClusterSort(payload)
    || Boolean(payload.snapshot_id)
    || Number(payload.limit ?? 0) > FAST_PATH_PAGE_LIMIT
  ) {
    return false;
  }

  if (payload.recursive === true) {
    // The indexed recursive page reads only the requested page and aggregates from
    // SQLite; scan_limit does not control its cost. Attempt it first and let the
    // existing short request timeout fall back to the durable worker when the
    // backend cannot serve the indexed path.
    return canUseRecursiveDatabaseMediaPage(payload);
  }

  return Number(payload.scan_limit ?? 0) <= NON_RECURSIVE_FAST_PATH_SCAN_LIMIT;
};
