import api from './index'

export type LibraryAnnotationKind = 'highlight' | 'comment'
export type LibraryAnnotationColor = 'yellow' | 'green' | 'blue' | 'pink'

export interface LibraryAnnotation {
  id: string
  resource_type: string
  resource_id: string
  chapter_id: string
  kind: LibraryAnnotationKind
  color: LibraryAnnotationColor
  quote_text: string
  prefix_text: string
  suffix_text: string
  start_offset: number
  end_offset: number
  source_revision: string
  comment_text: string
  position_json: Record<string, unknown>
  created_at: number
  updated_at: number
}

export interface LibraryAnnotationCreate {
  resource_type: string
  resource_id: string
  chapter_id: string
  kind: LibraryAnnotationKind
  color: LibraryAnnotationColor
  quote_text: string
  prefix_text: string
  suffix_text: string
  start_offset: number
  end_offset: number
  source_revision: string
  comment_text: string
  position_json?: Record<string, unknown>
}

export async function fetchLibraryAnnotations(
  resourceType: string,
  resourceId: string,
  chapterId?: string,
) {
  const response = await api.get<LibraryAnnotation[]>('/library-annotations', {
    params: {
      resource_type: resourceType,
      resource_id: resourceId,
      ...(chapterId == null ? {} : { chapter_id: chapterId }),
    },
  })
  return response.data
}

export async function createLibraryAnnotation(payload: LibraryAnnotationCreate) {
  const response = await api.post<LibraryAnnotation>('/library-annotations', payload)
  return response.data
}

export async function updateLibraryAnnotation(
  annotationId: string,
  payload: Partial<Pick<LibraryAnnotation, 'color' | 'comment_text'>>,
) {
  const response = await api.patch<LibraryAnnotation>(
    `/library-annotations/${encodeURIComponent(annotationId)}`,
    payload,
  )
  return response.data
}

export async function deleteLibraryAnnotation(annotationId: string) {
  await api.delete(`/library-annotations/${encodeURIComponent(annotationId)}`)
}
