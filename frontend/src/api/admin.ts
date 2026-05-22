import api from '@/api';

export interface OrphanImage {
  filename: string;
  device_file_id: number | null;
  size: number;
  mtime: number;
  url: string;
}

export interface StorageStats {
  total_count: number;
  total_size: number;
  orphan_count: number;
  orphan_size: number;
}

export interface OrphanImageResponse {
  stats: StorageStats;
  orphans: OrphanImage[];
}

export const fetchOrphanImages = async (): Promise<OrphanImageResponse> => {
  const response = await api.get('/admin/images/orphans');
  return response.data;
};

export const deleteOrphanImages = async (filenames: string[]): Promise<{ deleted_count: number; errors: string[] }> => {
  const response = await api.post('/admin/images/delete', { filenames });
  return response.data;
};

export interface TopFile {
  filename: string;
  device_file_id: number | null;
  size: number;
  mtime: number;
  url: string;
}

export interface TopNode {
  id: number | null;
  title: string;
  size: number;
  updated_at: number;
}

export interface DeadLink {
  note_id: number | null;
  note_title: string;
  link: string;
}

export interface FixableLink {
  note_id: number | null;
  note_title: string;
  original_url: string;
  suggested_url: string;
}

export interface StorageDashboardStats {
  total_size_bytes: number;
  total_file_count: number;
  total_note_count: number;
  orphan_size_bytes: number;
  orphan_count: number;
  dead_link_count: number;
  health_score: number;
  attachments_path: string;
  data_workspace_path: string;
}

export interface WorkspaceUsageEntry {
  name: string;
  path: string;
  is_dir: boolean;
  logical_size_bytes: number;
  allocated_size_bytes: number;
  file_count: number;
  directory_count: number;
  symlink_count: number;
  inaccessible_count: number;
  modified_at: number | null;
}

export interface StorageHealthIssue {
  id: string;
  severity: 'critical' | 'warning' | 'info' | string;
  title: string;
  detail: string;
  path: string;
  size_bytes: number;
  action_label: string;
  action_kind: string;
}

export interface StorageSlimmingCandidate {
  id: string;
  category: string;
  title: string;
  path: string;
  logical_size_bytes: number;
  allocated_size_bytes: number;
  file_count: number;
  directory_count: number;
  risk: 'low' | 'review' | string;
  cleanup_kind: string;
  action_label: string;
  detail: string;
}

export interface WorkspaceUsageResponse {
  scope: string;
  label: string;
  expected_role: string;
  health_score: number;
  health_status: 'healthy' | 'attention' | 'problem' | string;
  health_issues: StorageHealthIssue[];
  slimming_candidates: StorageSlimmingCandidate[];
  root_path: string;
  logical_size_bytes: number;
  allocated_size_bytes: number;
  file_count: number;
  directory_count: number;
  symlink_count: number;
  inaccessible_count: number;
  top_entries: WorkspaceUsageEntry[];
  scan_started_at: number;
  elapsed_ms: number;
  source: string;
}

export type StorageUsageScope = 'data_workspace' | 'data_dir' | 'source_dir';

export interface StorageAnalysisResponse {
  top_files: TopFile[];
  top_nodes: TopNode[];
  file_type_distribution: Record<string, number>;
}

export interface MaintenanceStatusResponse {
  orphan_count: number;
  orphan_size: number;
  dead_links: DeadLink[];
  fixable_links: FixableLink[];
}

export interface ScheduleConfig {
  enabled: boolean;
  cron_expression: string;
  cleanup_enabled: boolean;
  max_storage_bytes: number;
  trash_grace_days: number;
  vacuum_sqlite: boolean;
}

export interface AdminAccountSummary {
  id: number;
  username: string;
  nickname: string;
  email: string | null;
  phone: string | null;
  password_plain: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: number;
}

export const fetchAdminAccounts = async (): Promise<AdminAccountSummary[]> => {
  const response = await api.get('/admin/accounts');
  return response.data;
};

export const createAdminAccount = async (payload: {
  username: string;
  password: string;
  nickname: string;
  isSuperuser: boolean;
  isActive: boolean;
  email: string;
  phone: string;
}): Promise<AdminAccountSummary> => {
  const response = await api.post('/admin/accounts', {
    username: payload.username,
    password: payload.password,
    nickname: payload.nickname,
    is_superuser: payload.isSuperuser,
    is_active: payload.isActive,
    email: payload.email,
    phone: payload.phone,
  });
  return response.data;
};

export const resetAdminAccountPassword = async (
  userId: number,
  password: string,
): Promise<AdminAccountSummary> => {
  const response = await api.post(`/admin/accounts/${userId}/password`, { password });
  return response.data;
};

export const updateAdminAccountProfile = async (
  userId: number,
  nickname: string,
  isSuperuser: boolean,
  isActive: boolean,
  password: string,
  email: string,
  phone: string,
): Promise<AdminAccountSummary> => {
  const response = await api.post(`/admin/accounts/${userId}/profile`, {
    nickname,
    is_superuser: isSuperuser,
    is_active: isActive,
    password,
    email,
    phone,
  });
  return response.data;
};

export const deleteAdminAccount = async (userId: number): Promise<{ success: boolean }> => {
  const response = await api.delete(`/admin/accounts/${userId}`);
  return response.data;
};

export const fetchStorageDashboard = async (): Promise<StorageDashboardStats> => {
  const response = await api.get('/admin/storage/dashboard');
  return response.data;
};

export const fetchWorkspaceUsage = async (
  scope: StorageUsageScope = 'data_workspace',
  refresh = false,
  topLimit = 20,
): Promise<WorkspaceUsageResponse> => {
  const response = await api.get('/admin/storage/workspace-usage', {
    params: {
      scope,
      refresh,
      top_limit: topLimit,
    },
    timeout: 120000,
  });
  return response.data;
};

export const fetchStorageAnalysis = async (): Promise<StorageAnalysisResponse> => {
  const response = await api.get('/admin/storage/analysis');
  return response.data;
};

export const fetchMaintenanceStatus = async (): Promise<MaintenanceStatusResponse> => {
  const response = await api.get('/admin/storage/maintenance');
  return response.data;
};

export const fetchScheduleConfig = async (): Promise<ScheduleConfig> => {
  const response = await api.get('/admin/storage/schedule');
  return response.data;
};

export const updateScheduleConfig = async (config: ScheduleConfig): Promise<ScheduleConfig> => {
  const response = await api.post('/admin/storage/schedule', config);
  return response.data;
};

export interface StorageCleanupRunResult {
  cleanup_enabled: boolean;
  dry_run: boolean;
  force: boolean;
  max_storage_bytes: number;
  trash_grace_seconds: number;
  usage_before_bytes: number;
  usage_after_bytes: number;
  bytes_over_limit_before: number;
  candidate_count: number;
  estimated_candidate_bytes: number;
  purged_count: number;
  purged_estimated_bytes: number;
  purged_by_kind: Record<string, number>;
  vacuum_ran: boolean;
  skipped_reason: string;
}

export const runStorageCleanup = async (
  payload: { dry_run?: boolean; force?: boolean } = {},
): Promise<StorageCleanupRunResult> => {
  const response = await api.post('/admin/storage/cleanup/run', payload);
  return response.data;
};

export const fixBrokenLinks = async (): Promise<{ fixed_links_count: number; fixed_notes_count: number; message: string }> => {
  const response = await api.post('/admin/storage/fix-links');
  return response.data;
};

export interface OptimizeImageRequest {
  filename: string;
  target_format: 'jpeg' | 'webp';
  quality: number;
}

export interface OptimizedPreview {
  original_size: number;
  optimized_size: number;
  saved_bytes: number;
  preview_url: string;
}

export const previewOptimizedImage = async (request: OptimizeImageRequest): Promise<OptimizedPreview> => {
  const response = await api.post('/admin/images/optimize-preview', request);
  return response.data;
};

export const confirmImageOptimization = async (request: OptimizeImageRequest): Promise<{ success: boolean; new_filename: string; db_updates?: number }> => {
  const response = await api.post('/admin/images/optimize-confirm-with-db', request);
  return response.data;
};
