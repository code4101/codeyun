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
