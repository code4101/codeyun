import api from '@/api';

export interface NoteCategoryPaletteItem {
  key: string;
  label: string;
  color: string | null;
  description?: string | null;
  order: number;
  builtin: boolean;
  source: 'builtin' | 'custom' | 'legacy' | 'import';
  generated_from_color?: string | null;
  usage_count?: number;
}

export interface NoteCategoryPaletteResponse {
  items: NoteCategoryPaletteItem[];
}

export const fetchNoteCategoryPalette = async (): Promise<NoteCategoryPaletteResponse> => {
  const response = await api.get<NoteCategoryPaletteResponse>('/notes/category-palette');
  return response.data;
};

export const updateNoteCategoryPalette = async (items: NoteCategoryPaletteItem[]): Promise<NoteCategoryPaletteResponse> => {
  const response = await api.put<NoteCategoryPaletteResponse>('/notes/category-palette', { items });
  return response.data;
};

export const mergeNoteCategoryPaletteItem = async (sourceKey: string, targetKey: string): Promise<NoteCategoryPaletteResponse> => {
  const response = await api.post<NoteCategoryPaletteResponse>('/notes/category-palette/merge', {
    source_key: sourceKey,
    target_key: targetKey,
  });
  return response.data;
};

export const checkNoteCategoryPaletteItemCanDelete = async (key: string): Promise<boolean> => {
  const response = await api.get<{ can_delete: boolean }>(`/notes/category-palette/${encodeURIComponent(key)}/can-delete`);
  return Boolean(response.data?.can_delete);
};

// Backward-compatible aliases for remaining call sites.
export type NoteTypePaletteItem = NoteCategoryPaletteItem;
export type NoteTypePaletteResponse = NoteCategoryPaletteResponse;
export const fetchNoteTypePalette = fetchNoteCategoryPalette;
export const updateNoteTypePalette = updateNoteCategoryPalette;
export const mergeNoteTypePaletteItem = mergeNoteCategoryPaletteItem;
export const checkNoteTypePaletteItemCanDelete = checkNoteCategoryPaletteItemCanDelete;
