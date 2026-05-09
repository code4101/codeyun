import api from '@/api';

export interface OrphanImage {
  filename: string;
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
  size: number;
  mtime: number;
  url: string;
}

export interface TopNode {
  id: string;
  title: string;
  size: number;
  updated_at: number;
}

export interface DeadLink {
  note_id: string;
  note_title: string;
  link: string;
}

export interface FixableLink {
  note_id: string;
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
}

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
}

export interface BackgroundTaskRunSummary {
  id?: string;
  name?: string;
  status?: string;
  stage?: string;
  stage_label?: string;
  trigger_reason?: string;
  repo_count?: number;
  changed_repo_count?: number;
  committed_repo_count?: number;
  skipped_repo_count?: number;
  failed_repo_count?: number;
  sample_count?: number;
  diary_date?: string;
  source_turn_count?: number;
  created_note_count?: number;
  duplicate_note_ids?: string[];
  error_message?: string | null;
  queued_at?: number;
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
  updated_at?: number;
  result?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface BackgroundTaskItem {
  key: string;
  title: string;
  category: string;
  description: string;
  cron_expression: string;
  schedule_label: string;
  enabled: boolean;
  scheduler_running: boolean;
  runner_running: boolean;
  next_run_at: string | null;
  retry_policy: string;
  can_trigger: boolean;
  trigger_warning: string;
  active: boolean;
  latest_run: BackgroundTaskRunSummary | null;
}

export interface BackgroundTaskQueueSnapshot {
  is_idle: boolean;
  running: BackgroundTaskRunSummary | null;
  pending: BackgroundTaskRunSummary[];
  recent: BackgroundTaskRunSummary[];
}

export interface BackgroundTaskStatusResponse {
  queue: BackgroundTaskQueueSnapshot;
  tasks: BackgroundTaskItem[];
  runner_running: boolean;
  next_wake_at: string | null;
  runner_error?: string | null;
}

export interface BackgroundTaskTriggerResponse {
  task_key: string;
  queued: boolean;
  queue_task_id: string | null;
  run: BackgroundTaskRunSummary | null;
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

export const fetchBackgroundTaskStatus = async (): Promise<BackgroundTaskStatusResponse> => {
  const response = await api.get('/admin/background-tasks/status');
  return response.data;
};

export const triggerBackgroundTask = async (taskKey: string): Promise<BackgroundTaskTriggerResponse> => {
  const response = await api.post(`/admin/background-tasks/${encodeURIComponent(taskKey)}/trigger`);
  return response.data;
};

export const toggleBackgroundTask = async (taskKey: string, enabled: boolean) => {
  const response = await api.post(`/admin/background-tasks/${encodeURIComponent(taskKey)}/toggle`, { enabled });
  return response.data;
};

export const resetBackgroundTaskSchedule = async (taskKey: string) => {
  const response = await api.post(`/admin/background-tasks/${encodeURIComponent(taskKey)}/reset-schedule`);
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
