export interface PublicAttendanceFeedbackCourseOption {
  name: string
  attendance_sheet_url?: string | null
}

export interface PublicAttendanceFeedbackFormMeta {
  course_names: string[]
  course_options?: PublicAttendanceFeedbackCourseOption[]
  data_sheet_url?: string | null
}

export interface PublicAttendanceFeedbackSubmitRequest {
  course_name: string
  student_id_text: string
  student_name: string
  correction_request: string
  extra_note?: string
}

export interface PublicAttendanceWjxDataItem {
  id: number
  activity_id: string
  seq: number
  submitted_at_text: string
  correction_request: string
  extra_note: string
  process_status: string
  process_note: string
}

export interface PublicAttendanceFeedbackHistoryResponse {
  items: PublicAttendanceWjxDataItem[]
  total: number
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let detail = ''
    try {
      const data = await response.json()
      detail = typeof data?.detail === 'string' ? data.detail : ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `请求失败：${response.status}`)
  }

  return response.json() as Promise<T>
}

export function fetchPublicAttendanceFeedbackFormMeta() {
  return requestJson<PublicAttendanceFeedbackFormMeta>('/attendance/wjx-feedback-form')
}

export function submitPublicAttendanceFeedback(payload: PublicAttendanceFeedbackSubmitRequest) {
  return requestJson<PublicAttendanceWjxDataItem>('/attendance/wjx-feedback/submissions', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchPublicAttendanceFeedbackHistory(params: {
  course_name: string
  student_id_text?: string
  student_name?: string
  limit?: number
}) {
  const query = new URLSearchParams()
  query.set('course_name', params.course_name)
  if (params.student_id_text) query.set('student_id_text', params.student_id_text)
  if (params.student_name) query.set('student_name', params.student_name)
  if (params.limit) query.set('limit', String(params.limit))
  return requestJson<PublicAttendanceFeedbackHistoryResponse>(`/attendance/wjx-feedback/history?${query}`)
}
