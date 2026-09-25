/** 百科文本、媒体与静态资产的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuWikiCatalog {
  export_root: string;
  exists: boolean;
  text_count: number;
  text_assets: Record<string, number>;
  text_categories: Record<string, number>;
  text_display_kinds?: Record<string, number>;
  galleries: Record<string, number>;
}

export interface FanxiuWikiTextVariant {
  id: string;
  source: string;
  asset: string;
  key: string;
  locator: string;
  title: string;
  category: string;
  display_kind?: string;
  terms: string[];
  plain_preview: string;
  rich_preview: string;
  line_no: number;
  variant_preview?: string;
}

export interface FanxiuWikiTextItem extends Omit<FanxiuWikiTextVariant, 'locator'> {
  score: number;
  duplicate_count?: number;
  duplicate_keys?: string[];
  same_title_count?: number;
  variants?: FanxiuWikiTextVariant[];
}

export interface FanxiuWikiTextSearchResponse {
  query: string;
  asset: string;
  category: string;
  display_kind?: string;
  limit: number;
  offset: number;
  total: number;
  raw_total?: number;
  items: FanxiuWikiTextItem[];
}

export interface FanxiuWikiTextDetail extends Omit<FanxiuWikiTextItem, 'plain_preview' | 'rich_preview' | 'score'> {
  plain_text: string;
  rich_text: string;
}

export interface FanxiuWikiGalleryItem {
  kind: string;
  group: string;
  source: string;
  name: string;
  width: number;
  height: number;
  path: string;
}

export interface FanxiuWikiGalleryResponse {
  query: string;
  kind: string;
  limit: number;
  offset: number;
  total: number;
  items: FanxiuWikiGalleryItem[];
}

export interface FanxiuStaticVisualManifestRow {
  source_kind: string;
  name: string;
  category: string;
  asset_group?: string;
  width: number;
  height: number;
  atlas_key?: string;
  source_path?: string;
  path_id?: string;
  bytes?: number;
  media_path: string;
  absolute_media_path?: string;
  media_url?: string;
  phash_distance?: number;
  dhash_distance?: number | string;
  aspect_similarity?: number;
  similarity?: number;
  similarity_percent?: number;
  similarity_rank?: number;
}

export interface FanxiuStaticVisualManifestResponse {
  manifest_root: string;
  query: string;
  category: string;
  asset_group?: string;
  source_kind: string;
  total: number;
  filtered: number;
  offset: number;
  limit: number;
  stats: {
    total?: number;
    categories?: Record<string, number>;
    asset_groups?: Record<string, number>;
    query_asset_groups?: Record<string, number>;
    query_total?: number;
    source_kinds?: Record<string, number>;
    filtered?: number;
    prefiltered?: number;
    max_prefilter?: number;
    visual_similarity_index_count?: number;
    visual_similarity_hash_error_count?: number;
  };
  query_hash?: {
    phash?: string;
    dhash?: string;
    phash_algorithm?: string;
    dhash_algorithm?: string;
    normalized_width?: number;
    normalized_height?: number;
  };
  rows: FanxiuStaticVisualManifestRow[];
}

export interface FanxiuStaticAssetManifestRow {
  asset_id: string;
  asset_group: string;
  source_kind: string;
  category: string;
  name: string;
  stem: string;
  hash_suffix?: string;
  relative_path: string;
  bytes: number;
  suffix?: string;
  unity_magic?: string;
  unity_offset?: number;
  mesh_count?: number;
  mesh_vertices?: number;
  mesh_faces?: number;
  material_count?: number;
  texture_count?: number;
  animation_count?: number;
  ui_gameobject_count?: number;
  visible_data_type?: string;
  unity_object_count?: number;
  unity_object_types?: string;
  unity_primary_type?: string;
  unity_named_objects?: string;
  unity_script_names?: string;
  unity_read_error_count?: number;
  unity_parse_status?: string;
  unity_parse_error?: string;
  preview_url?: string;
  preview_manifest_url?: string;
  preview_kind?: string;
  detail_status?: string;
  semantic_id?: string;
  semantic_group?: string;
  semantic_type?: string;
  semantic_name?: string;
  semantic_summary?: string;
  semantic_refs?: string;
  semantic_visual_count?: number;
  semantic_visual_names?: string;
  semantic_visual_categories?: string;
  semantic_visual_media_paths?: string;
  semantic_visual_media_urls?: string[];
  semantic_variant_count?: number;
  semantic_variant_refs?: string;
  linked_asset_count?: number;
  linked_asset_groups?: string;
  linked_asset_names?: string;
  linked_asset_paths?: string;
  primary_asset_path?: string;
}

export interface FanxiuStaticAssetPreviewItem {
  name: string;
  kind: string;
  media_path: string;
  media_url?: string;
  object_type?: string;
  path_id?: number;
  width?: number;
  height?: number;
  is_original_image?: boolean;
}

export interface FanxiuStaticAssetPreviewManifestResponse {
  resource_root: string;
  relative_path: string;
  cached?: boolean;
  preview_kind: string;
  items: FanxiuStaticAssetPreviewItem[];
}

export interface FanxiuStaticAssetManifestResponse {
  manifest_root: string;
  manifest: string;
  query: string;
  catalog_view?: string;
  asset_group?: string;
  source_kind?: string;
  category?: string;
  total: number;
  filtered: number;
  offset: number;
  limit: number;
  stats: {
    total?: number;
    asset_groups?: Record<string, number>;
    source_kinds?: Record<string, number>;
    categories?: Record<string, number>;
    visible_data_types?: Record<string, number>;
    unity_primary_types?: Record<string, number>;
    catalog_views?: Record<string, number>;
    query_asset_groups?: Record<string, number>;
    query_source_kinds?: Record<string, number>;
    query_categories?: Record<string, number>;
    query_visible_data_types?: Record<string, number>;
    query_unity_primary_types?: Record<string, number>;
    query_catalog_views?: Record<string, number>;
    query_total?: number;
    raw_query_total?: number;
  };
  rows: FanxiuStaticAssetManifestRow[];
}

export interface FanxiuWwiseMp3ManifestRow {
  source_bank: string;
  kind: string;
  wem_id: string;
  entry_index: string;
  wem_size: string;
  sample_rate: string;
  channels: string;
  duration_seconds: string;
  encoding: string;
  mp3_path: string;
  relative_mp3_path: string;
  status: string;
  error: string;
  media_url?: string;
  player_url?: string;
}

export interface FanxiuWwiseMp3ManifestResponse {
  manifest: string;
  total: number;
  filtered: number;
  offset: number;
  limit: number;
  stats?: {
    kinds?: Record<string, number>;
    query_kinds?: Record<string, number>;
    query_total?: number;
  };
  rows: FanxiuWwiseMp3ManifestRow[];
}

export interface FanxiuWikiLinkIndexItem {
  alias: string;
  tab: 'item' | 'gongfa' | 'lingjie';
  id: string | number;
  title?: string;
  preview?: string;
  effect_text_preview?: string;
  effect_preview?: string;
  reward_preview?: string;
  kind?: string;
  priority?: number;
}

export interface FanxiuWikiLinkIndexResponse {
  items: FanxiuWikiLinkIndexItem[];
  total: number;
}

export const getFanxiuWikiCatalog = () => {
  return api.get<FanxiuWikiCatalog>('/fanxiu/resources/wiki/catalog').then(res => res.data);
};

export const getFanxiuWikiLinkIndex = () => {
  return api.get<FanxiuWikiLinkIndexResponse>('/fanxiu/resources/wiki/link-index').then(res => res.data);
};

export const getFanxiuWikiLinkTargets = (payload: { texts: string[]; limit?: number }) => {
  return api.post<FanxiuWikiLinkIndexResponse>('/fanxiu/resources/wiki/link-targets', payload).then(res => res.data);
};

export const searchFanxiuWikiTexts = (params: {
  query?: string;
  asset?: string;
  category?: string;
  display_kind?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuWikiTextSearchResponse>('/fanxiu/resources/wiki/texts', { params, timeout: 60000 })
    .then(res => res.data);
};

export const getFanxiuWikiText = (asset: string, key: string, options: { timeout?: number } = {}) => {
  return api
    .get<FanxiuWikiTextDetail>('/fanxiu/resources/wiki/text', {
      params: { asset, key },
      timeout: options.timeout ?? 30000,
    })
    .then(res => res.data);
};

export const searchFanxiuWikiGallery = (params: {
  query?: string;
  kind?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api.get<FanxiuWikiGalleryResponse>('/fanxiu/resources/wiki/gallery', { params }).then(res => res.data);
};

export const getFanxiuStaticVisualManifest = (params: {
  query?: string;
  category?: string;
  asset_group?: string;
  source_kind?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuStaticVisualManifestResponse>('/fanxiu/resources/visual/manifest', { params })
    .then(res => res.data);
};

export const searchFanxiuStaticVisualByImage = (image: File, params: {
  query?: string;
  category?: string;
  asset_group?: string;
  source_kind?: string;
  limit?: number;
  offset?: number;
  max_prefilter?: number;
} = {}) => {
  const form = new FormData();
  form.append('image', image);
  return api
    .post<FanxiuStaticVisualManifestResponse>('/fanxiu/resources/visual/similarity', form, { params, timeout: 60000 })
    .then(res => res.data);
};

export const getFanxiuStaticAssetManifest = (params: {
  query?: string;
  catalog_view?: string;
  asset_group?: string;
  source_kind?: string;
  category?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuStaticAssetManifestResponse>('/fanxiu/resources/asset/manifest', { params, timeout: 60000 })
    .then(res => res.data);
};

export const getFanxiuStaticAssetPreviewManifest = (params: {
  path: string;
  resource_root?: string;
  export_root?: string;
  force?: boolean;
}): Promise<FanxiuStaticAssetPreviewManifestResponse> => {
  return api
    .get<FanxiuStaticAssetPreviewManifestResponse>('/fanxiu/resources/asset/preview-manifest', { params, timeout: 60000 })
    .then(res => res.data);
};

export const getFanxiuWwiseMp3Manifest = (params: {
  query?: string;
  kind?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuWwiseMp3ManifestResponse>('/fanxiu/resources/wwise/mp3-manifest', { params })
    .then(res => res.data);
};

export const getFanxiuWikiMediaUrl = (path: string) => {
  return `/api/fanxiu/resources/wiki/media?path=${encodeURIComponent(path)}`;
};
