import api from '@/api';
import type { NoteNode } from './notes';

const normalizeFanxiuNote = (raw: any): NoteNode => {
  const normalizeTimestamp = (value: unknown) => {
    const numeric = typeof value === 'number' ? value : Number(value ?? 0);
    if (!Number.isFinite(numeric)) return 0;
    return numeric < 10000000000 ? numeric * 1000 : numeric;
  };

  return {
    ...raw,
    id: String(raw.id),
    created_at: normalizeTimestamp(raw.created_at),
    updated_at: normalizeTimestamp(raw.updated_at),
    start_at: normalizeTimestamp(raw.start_at),
    private_level: typeof raw.private_level === 'number' ? raw.private_level : Number(raw.private_level ?? 0),
    can_edit: Boolean(raw.can_edit)
  };
};

const toFanxiuPayload = (data: Partial<NoteNode>) => {
  const payload: Record<string, any> = { ...data };
  if (typeof payload.start_at === 'number' && payload.start_at > 10000000000) {
    payload.start_at /= 1000;
  }
  return payload;
};

export const getFanxiuChars = () => {
  return api.get<NoteNode[]>('/fanxiu/chars').then(res => (res.data || []).map(normalizeFanxiuNote));
};

export const getFanxiuCharDetail = (charName: string) => {
  return api.get<NoteNode>(`/fanxiu/chars/${charName}`).then(res => normalizeFanxiuNote(res.data));
};

export const updateFanxiuChar = (charName: string, data: Partial<NoteNode>) => {
  return api.put<NoteNode>(`/fanxiu/chars/${charName}`, toFanxiuPayload(data)).then(res => normalizeFanxiuNote(res.data));
};
