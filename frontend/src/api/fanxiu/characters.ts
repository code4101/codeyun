/** 人物笔记的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';
import type { NoteNode } from '../notes';
import { loadFanxiuNoteHelpers } from './noteAccess';


export const getFanxiuChars = () => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote }) => (
    api.get<NoteNode[]>('/fanxiu/chars').then(res => (res.data || []).map(normalizeFanxiuNote))
  ));
};

export const getFanxiuCharDetail = (charName: string) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote }) => (
    api.get<NoteNode>(`/fanxiu/chars/${charName}`).then(res => normalizeFanxiuNote(res.data))
  ));
};

export const updateFanxiuChar = (charName: string, data: Partial<NoteNode>) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote, toFanxiuPayload }) => (
    api.put<NoteNode>(`/fanxiu/chars/${charName}`, toFanxiuPayload(data)).then(res => normalizeFanxiuNote(res.data))
  ));
};
