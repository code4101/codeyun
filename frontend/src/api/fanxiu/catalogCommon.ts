/** 目录共享展示类型与资源图标的前端契约；请求由统一 api 客户端发送。 */



export interface FanxiuGameRichTextSegment {
  text: string;
  color: string;
  role: '' | 'skill' | 'value' | 'quality' | 'attribute' | 'accent';
}

export interface FanxiuTimelineHint {
  date?: string;
  time?: string;
  kind?: string;
  confidence?: string;
  label?: string;
  source?: string;
  relation?: string;
  evidence?: string;
  time_code?: string;
  activity_id?: string | number;
  activity_name?: string;
  activity_little_name?: string;
  activity_base_id?: string | number;
  via_item_id?: string | number;
  via_item_name?: string;
  reward_row_id?: string | number;
  merged_count?: number;
  activity_ids?: Array<string | number>;
  sources?: string[];
  evidences?: string[];
}

export interface FanxiuFacetIndex {
  object_ids: string[];
  rows: Record<string, Record<string, string[]>>;
}

export const getFanxiuResourceIconUrl = (name: string | null | undefined) => {
  const iconName = String(name || '').trim();
  return iconName ? `/api/fanxiu/resources/icon?name=${encodeURIComponent(iconName)}` : '';
};
