import api from '@/api';
import type { NoteNode } from './notes';

export const getFanxiuChars = () => {
  return api.get<NoteNode[]>('/fanxiu/chars').then(res => res.data);
};

export const getFanxiuCharDetail = (charName: string) => {
  return api.get<NoteNode>(`/fanxiu/chars/${charName}`).then(res => res.data);
};

export const updateFanxiuChar = (charName: string, data: Partial<NoteNode>) => {
  return api.put<NoteNode>(`/fanxiu/chars/${charName}`, data).then(res => res.data);
};
