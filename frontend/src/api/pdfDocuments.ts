import axios from 'axios';

import api from '@/api';
import type { DeviceFileSelector } from '@/api/deviceFiles';

export type PdfResourceRole = 'none' | 'deny' | 'viewer' | 'editor' | 'manager';
export type PdfAccessSubjectType = 'anonymous' | 'user';

export interface PdfAccessCapabilities {
  can_read: boolean;
  can_update_state: boolean;
  can_update_page_notes: boolean;
  can_manage_access: boolean;
}

export interface PdfResourceAccess {
  role: PdfResourceRole;
  capabilities: PdfAccessCapabilities;
}

export interface PdfUserState {
  current_page: number;
  zoom: string;
  sidebar_open: boolean;
  state_json: Record<string, unknown>;
  updated_at?: number | null;
}

export interface PdfDocumentDetail {
  id: number;
  title: string;
  mime_type: string;
  size_bytes?: number | null;
  content_hash?: string | null;
  created_at: number;
  updated_at: number;
  access: PdfResourceAccess;
  my_state?: PdfUserState | null;
}

export interface PdfDocumentFromDeviceFileRequest extends DeviceFileSelector {
  entry_id: string;
}

export interface PdfContentUrlResponse {
  url: string;
  expires_in: number;
}

export interface PdfAccessGrantItem {
  subject_type: PdfAccessSubjectType;
  subject_key: string;
  subject_user_id?: number | null;
  username: string;
  nickname: string;
  role: Exclude<PdfResourceRole, 'none'>;
}

export interface PdfAccessGrantUpdate {
  subject_type: PdfAccessSubjectType;
  username?: string;
  subject_user_id?: number | null;
  role: PdfResourceRole;
}

export interface PdfAccessResponse {
  resource_type: 'pdf';
  resource_id: number;
  access: PdfResourceAccess;
  grants: PdfAccessGrantItem[];
}

export interface PdfPageNote {
  id?: string | null;
  pdf_id: number;
  page_number: number;
  content_html: string;
  exists: boolean;
  can_edit: boolean;
  created_at?: number | null;
  updated_at?: number | null;
}

export interface PdfPageNoteUpdateRequest {
  content_html: string;
}

export async function createPdfDocumentFromDeviceFile(payload: PdfDocumentFromDeviceFileRequest) {
  const response = await api.post<PdfDocumentDetail>('/pdf-documents/from-device-file', payload);
  return response.data;
}

export async function fetchPdfDocument(pdfId: number) {
  try {
    const response = await api.get<PdfDocumentDetail>(`/pdf-documents/${pdfId}`);
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function fetchPdfContentUrl(pdfId: number) {
  const response = await api.post<PdfContentUrlResponse>(`/pdf-documents/${pdfId}/content-url`);
  return response.data;
}

export async function updatePdfUserState(pdfId: number, payload: PdfUserState) {
  const response = await api.put<PdfUserState>(`/pdf-documents/${pdfId}/my-state`, payload);
  return response.data;
}

export async function fetchPdfPageNote(pdfId: number, pageNumber: number) {
  const response = await api.get<PdfPageNote>(`/pdf-documents/${pdfId}/page-notes/${pageNumber}`);
  return response.data;
}

export async function updatePdfPageNote(pdfId: number, pageNumber: number, payload: PdfPageNoteUpdateRequest) {
  const response = await api.put<PdfPageNote>(`/pdf-documents/${pdfId}/page-notes/${pageNumber}`, payload);
  return response.data;
}

export async function fetchPdfAccess(pdfId: number) {
  const response = await api.get<PdfAccessResponse>(`/pdf-documents/${pdfId}/access`);
  return response.data;
}

export async function updatePdfAccess(pdfId: number, grants: PdfAccessGrantUpdate[]) {
  const response = await api.put<PdfAccessResponse>(`/pdf-documents/${pdfId}/access`, { grants });
  return response.data;
}
