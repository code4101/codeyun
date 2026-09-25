/** 攻略视频的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuGuideVideoItem {
  item_id: string;
  platform: 'bilibili' | 'douyin';
  source_id: string;
  source_role: 'official' | 'original' | 'guide' | 'clip';
  identity_key: string;
  video_id: string;
  bvid: string;
  url: string;
  title: string;
  description: string;
  cover_url: string;
  duration_text: string;
  play_text: string;
  published_at: number;
  dynamic_id: string;
  uploader_mid: number;
  uploader_id: string;
  uploader_name: string;
  is_pinned?: boolean;
  research?: FanxiuGuideVideoResearch | null;
  download?: FanxiuGuideVideoDownload | null;
}

export interface FanxiuGuideVideoDownload {
  item_id: string;
  status: 'running' | 'done' | 'error';
  attempts: number;
  local_video_path?: string;
  error?: string;
}

export interface FanxiuGuideVideoResearchTimelineItem {
  time: number;
  label: string;
  description: string;
}

export interface FanxiuGuideVideoResearch {
  item_id: string;
  status: 'queued' | 'downloading' | 'transcribing' | 'analyzing' | 'done' | 'error';
  analyzed_at: number;
  duration: number;
  summary: string;
  conclusions: string[];
  topics: string[];
  timeline: FanxiuGuideVideoResearchTimelineItem[];
  version_note: string;
  local_video_path: string;
  document_path: string;
  transcript_path: string;
  media_url: string;
  document_url: string;
  transcript_url: string;
}

export interface FanxiuGuideVideoCollection {
  collection_id: string;
  title: string;
  url: string;
  episode_count: number;
  play_text: string;
}

export interface FanxiuGuideVideoSource {
  source_id: string;
  platform: 'bilibili' | 'douyin';
  role: 'official' | 'original' | 'guide' | 'clip';
  identity_key: string;
  uploader_id: string;
  uploader_name: string;
  profile_url: string;
  target_count: number;
  done_count: number;
  status: 'idle' | 'running' | 'done' | 'error';
  error: string;
  collections: FanxiuGuideVideoCollection[];
}

export interface FanxiuGuideVideoCatalogResponse {
  schema_version: number;
  status: 'idle' | 'running' | 'done' | 'error';
  sources: FanxiuGuideVideoSource[];
  target_count: number;
  done_count: number;
  updated_at: number;
  error: string;
  query: string;
  source_id: string;
  platform: string;
  role: string;
  page: number;
  page_size: number;
  total: number;
  research_count: number;
  download_status: 'idle' | 'running' | 'waiting' | 'done' | 'stopped' | 'stopped_after_limit' | 'paused_low_disk';
  download_target_count: number;
  download_done_count: number;
  download_failed_count: number;
  download_current_item_id: string;
  items: FanxiuGuideVideoItem[];
}

export const getFanxiuGuideVideos = (params: {
  query?: string;
  source_id?: string;
  platform?: string;
  role?: string;
  page?: number;
  page_size?: number;
} = {}) => (
  api.get<FanxiuGuideVideoCatalogResponse>('/fanxiu/wiki/guide-videos', { params }).then(res => res.data)
);

export const syncFanxiuGuideVideos = () => (
  api.post<FanxiuGuideVideoCatalogResponse>('/fanxiu/wiki/guide-videos/sync').then(res => res.data)
);
