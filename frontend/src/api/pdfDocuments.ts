import axios from 'axios';

import api from '@/api';
import type { DeviceFileSelector } from '@/api/deviceFiles';

export type PdfResourceRole = 'none' | 'deny' | 'viewer' | 'editor' | 'manager';
export type PdfAccessSubjectType = 'anonymous' | 'user';

export interface PdfAccessCapabilities {
  can_read: boolean;
  can_update_state: boolean;
  can_update_page_notes: boolean;
  can_copy_to_library: boolean;
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

export interface PdfDocumentMetadata {
  status: 'pending' | 'ready' | 'error';
  page_count?: number | null;
  page_width_points?: number | null;
  page_height_points?: number | null;
  cover_average_color?: string | null;
  unit: 'pt';
  scanned_at?: number | null;
}

export interface PdfBookshelfPlacement {
  pdf_id: number;
  shelf_index: number;
  position_index: number;
  orientation: PdfBookshelfOrientation;
  folder_id?: string | null;
}

export interface LibraryBookshelfLayoutItem {
  resource_type: 'pdf' | 'book_asset' | 'folder';
  resource_id: string;
  shelf_index: number;
  position_index: number;
}

export interface PdfLibraryBookshelf {
  id: string;
  name: string;
  sort_index: number;
  logical_page_target_characters: number;
  article_reading_mode: 'scroll' | 'paginated';
  book_count: number;
  folder_count: number;
  owner_user_id: number;
  owner_username: string;
  is_owned: boolean;
  access: PdfResourceAccess;
}

export type PdfBookshelfOrientation = 'spine_vertical' | 'spine_horizontal' | 'cover_front';

export interface PdfDocumentDetail {
  id: number;
  title: string;
  display_title: string;
  display_author: string;
  start_date: string;
  display_subtitle: string;
  display_translator: string;
  display_edition: string;
  display_volume: string;
  imported_filename: string;
  description: string;
  tags: string[];
  appearance: PdfBookAppearance;
  display_title_status: 'pending' | 'ready';
  owner_user_id?: number | null;
  source_device_id: string;
  source_absolute_path: string;
  mime_type: string;
  size_bytes?: number | null;
  content_hash?: string | null;
  metadata: PdfDocumentMetadata;
  bookshelf_placement?: PdfBookshelfPlacement | null;
  created_at: number;
  updated_at: number;
  access: PdfResourceAccess;
  my_state?: PdfUserState | null;
}

export type PdfDocumentSummary = PdfDocumentDetail;

export interface PdfDocumentFromDeviceFileRequest extends DeviceFileSelector {
  entry_id: string;
}

export interface PdfDocumentLocalImportRequest {
  absolute_path: string;
}

interface PdfUploadSession {
  upload_id: string;
  chunk_size: number;
  received_bytes: number;
}

export interface PdfMetadataUpdateRequest {
  display_title: string;
  display_author: string;
  start_date: string;
  display_subtitle: string;
  display_translator: string;
  display_edition: string;
  display_volume: string;
  source_display_name?: string | null;
  description: string;
  tags: string[];
  cover_color_override?: string | null;
}

export interface PdfBookAppearance {
  cover_color_override?: string | null;
}

export interface LibraryFolder {
  id: string;
  bookshelf_id: string;
  name: string;
  color_override?: string | null;
  min_thickness_mm?: number | null;
  fixed_thickness_mm?: number | null;
  shelf_index: number;
  position_index: number;
  orientation: PdfBookshelfOrientation;
  member_count: number;
}

export interface LibraryFolderUpdateRequest {
  name: string;
  color_override?: string | null;
  min_thickness_mm?: number | null;
  fixed_thickness_mm?: number | null;
}

export interface PdfBookCopyRequest {
  target_bookshelf_id: string;
  shelf_index: number;
  include_notes: boolean;
  include_reading_progress: boolean;
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

export interface PdfBookshelfAccessResponse {
  resource_type: 'library-bookshelf';
  resource_id: string;
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

export async function fetchPdfDocuments(bookshelfId?: string) {
  const response = await api.get<PdfDocumentSummary[]>('/pdf-documents', {
    params: bookshelfId ? { bookshelf_id: bookshelfId } : undefined,
  });
  return response.data;
}

export async function fetchPdfBookshelves(scope: 'mine' | 'shared' = 'mine') {
  const response = await api.get<PdfLibraryBookshelf[]>('/pdf-documents/bookshelves', {
    params: { scope },
  });
  return response.data;
}

export async function createPdfBookshelf(name: string) {
  const response = await api.post<PdfLibraryBookshelf>('/pdf-documents/bookshelves', { name });
  return response.data;
}

export async function renamePdfBookshelf(bookshelfId: string, name: string) {
  const response = await api.put<PdfLibraryBookshelf>(`/pdf-documents/bookshelves/${bookshelfId}`, { name });
  return response.data;
}

export async function updatePdfBookshelf(
  bookshelfId: string,
  payload: {
    name: string;
    logical_page_target_characters: number;
    article_reading_mode: 'scroll' | 'paginated';
  },
) {
  const response = await api.put<PdfLibraryBookshelf>(
    `/pdf-documents/bookshelves/${bookshelfId}`,
    payload,
  );
  return response.data;
}

export async function deletePdfBookshelf(bookshelfId: string) {
  await api.delete(`/pdf-documents/bookshelves/${bookshelfId}`);
}

export async function fetchPdfBookshelfAccess(bookshelfId: string) {
  const response = await api.get<PdfBookshelfAccessResponse>(
    `/pdf-documents/bookshelves/${bookshelfId}/access`,
  );
  return response.data;
}

export async function updatePdfBookshelfAccess(bookshelfId: string, grants: PdfAccessGrantUpdate[]) {
  const response = await api.put<PdfBookshelfAccessResponse>(
    `/pdf-documents/bookshelves/${bookshelfId}/access`,
    { grants },
  );
  return response.data;
}

export async function leaveSharedPdfBookshelf(bookshelfId: string) {
  await api.delete(`/pdf-documents/bookshelves/${bookshelfId}/my-access`);
}

export async function movePdfToBookshelf(pdfId: number, bookshelfId: string) {
  const response = await api.put<PdfBookshelfPlacement>(`/pdf-documents/${pdfId}/bookshelf`, {
    bookshelf_id: bookshelfId,
  });
  return response.data;
}

export async function updatePdfBookshelfLayout(placements: PdfBookshelfPlacement[]) {
  const response = await api.put<PdfBookshelfPlacement[]>('/pdf-documents/bookshelf-layout', { placements });
  return response.data;
}

export async function updateLibraryBookshelfLayout(
  bookshelfId: string,
  items: LibraryBookshelfLayoutItem[],
) {
  const response = await api.put<LibraryBookshelfLayoutItem[]>('/pdf-documents/library-layout', {
    bookshelf_id: bookshelfId,
    items,
  });
  return response.data;
}

export async function updatePdfDocumentMetadata(pdfId: number, payload: PdfMetadataUpdateRequest) {
  const response = await api.put<PdfDocumentDetail>(`/pdf-documents/${pdfId}/metadata`, payload);
  return response.data;
}

export async function deletePdfDocument(pdfId: number) {
  await api.delete(`/pdf-documents/${pdfId}`);
}

export async function removePdfDocumentFromMyLibrary(pdfId: number) {
  await api.delete(`/pdf-documents/${pdfId}/my-placement`);
}

export async function fetchLibraryFolders(bookshelfId: string) {
  const response = await api.get<LibraryFolder[]>(`/pdf-documents/bookshelves/${bookshelfId}/folders`);
  return response.data;
}

export async function createLibraryFolder(bookshelfId: string, name: string, shelfIndex: number) {
  const response = await api.post<LibraryFolder>(`/pdf-documents/bookshelves/${bookshelfId}/folders`, {
    name,
    shelf_index: shelfIndex,
  });
  return response.data;
}

export async function updateLibraryFolder(folderId: string, payload: LibraryFolderUpdateRequest) {
  const response = await api.put<LibraryFolder>(`/pdf-documents/folders/${folderId}`, payload);
  return response.data;
}

export async function deleteLibraryFolder(folderId: string) {
  await api.delete(`/pdf-documents/folders/${folderId}`);
}

export async function movePdfToLibraryFolder(pdfId: number, folderId: string | null, shelfIndex = 0) {
  const response = await api.put<PdfBookshelfPlacement>(`/pdf-documents/${pdfId}/folder`, {
    folder_id: folderId,
    shelf_index: shelfIndex,
  });
  return response.data;
}

export async function copyPdfToOwnLibrary(pdfId: number, payload: PdfBookCopyRequest) {
  const response = await api.post<PdfDocumentDetail>(`/pdf-documents/${pdfId}/copy-to-library`, payload);
  return response.data;
}

export async function importPdfDocumentFromLocalPath(payload: PdfDocumentLocalImportRequest) {
  const response = await api.post<PdfDocumentDetail>('/pdf-documents/import-local-path', payload);
  return response.data;
}

export async function uploadPdfDocument(file: File) {
  const sessionResponse = await api.post<PdfUploadSession>('/pdf-documents/upload-sessions', {
    filename: file.name,
    size_bytes: file.size,
  });
  const uploadSession = sessionResponse.data;
  let offset = uploadSession.received_bytes;
  try {
    while (offset < file.size) {
      const chunk = file.slice(offset, Math.min(file.size, offset + uploadSession.chunk_size));
      const chunkResponse = await api.put<PdfUploadSession>(
        `/pdf-documents/upload-sessions/${uploadSession.upload_id}/chunk`,
        chunk,
        {
          params: { offset },
          headers: { 'Content-Type': 'application/octet-stream' },
          timeout: 2 * 60 * 1000,
        },
      );
      offset = chunkResponse.data.received_bytes;
    }
    const completeResponse = await api.post<PdfDocumentDetail>(
      `/pdf-documents/upload-sessions/${uploadSession.upload_id}/complete`,
      undefined,
      { timeout: 10 * 60 * 1000 },
    );
    return completeResponse.data;
  } catch (error) {
    try {
      await api.delete(`/pdf-documents/upload-sessions/${uploadSession.upload_id}`);
    } catch {
      // The server also removes stale upload sessions automatically.
    }
    throw error;
  }
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

export async function fetchPdfPagePreview(pdfId: number, pageNumber: number, signal?: AbortSignal) {
  const response = await api.get<Blob>(`/pdf-documents/${pdfId}/pages/${pageNumber}/preview`, {
    responseType: 'blob',
    signal,
    // The library owns a short-lived object-URL cache. Bypass the browser's longer HTTP
    // cache here so a server-side renderer upgrade cannot keep serving an old broken page.
    params: { preview_request: Date.now() },
  });
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

export async function clearMyPdfPageNotes(pdfId: number) {
  const response = await api.delete<{ deleted_count: number }>(`/pdf-documents/${pdfId}/my-page-notes`);
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
