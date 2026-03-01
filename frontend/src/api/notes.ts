import api from '@/api';
import { ref } from 'vue';
import { defineStore } from 'pinia';
import { ElMessage } from 'element-plus';

export interface NoteNode {
  id: string;
  user_id?: number;
  title: string;
  content?: string; // Optional because of on-demand loading
  weight: number; // Default 100
  node_type?: string | null;
  created_at: number;
  updated_at: number;
  start_at: number;
  history?: { ts: number; f: string; v: any }[];
}

// Convert backend snake_case to frontend if needed, but here we can just use snake_case
// Or map it. Let's stick to what the API returns which is usually JSON with matching keys.
// The backend returns Pydantic models which are snake_case by default.

export interface NoteEdge {
  id: string;
  source_id: string;
  target_id: string;
  source_handle?: string;
  target_handle?: string;
  label?: string;
  created_at: number;
}

export const useNoteStore = defineStore('notes', () => {
  const notes = ref<NoteNode[]>([]);
  const edges = ref<NoteEdge[]>([]); // Store edges
  const loading = ref(false);

  const fetchNotes = async (params: { 
    created_start?: number; 
    created_end?: number; 
    updated_start?: number; 
    updated_end?: number;
    limit?: number;
  } = {}) => {
    loading.value = true;
    try {
      // Convert ms to seconds for backend
      const queryParams: any = { ...params };
      if (params.created_start) queryParams.created_start /= 1000;
      if (params.created_end) queryParams.created_end /= 1000;
      if (params.updated_start) queryParams.updated_start /= 1000;
      if (params.updated_end) queryParams.updated_end /= 1000;

      // Fetch nodes (titles and metadata, no content)
      const response = await api.get('/notes/', { params: queryParams });
      notes.value = response.data.map((n: any) => ({
        ...n,
        id: String(n.id),
        created_at: n.created_at * 1000,
        updated_at: n.updated_at * 1000,
        start_at: n.start_at * 1000
      }));
      
      // Fetch edges
      const edgesResponse = await api.get('/notes/edges/');
      const nodeIds = new Set(notes.value.map(n => n.id));
      edges.value = edgesResponse.data
        .map((e: any) => ({
          ...e,
          id: String(e.id),
          source_id: String(e.source_id),
          target_id: String(e.target_id),
          created_at: e.created_at * 1000
        }))
        .filter((e: any) => nodeIds.has(e.source_id) && nodeIds.has(e.target_id));
      
    } catch (error) {
      console.error('Failed to fetch notes or edges:', error);
      ElMessage.error('获取数据失败');
    } finally {
      loading.value = false;
    }
  };

  const fetchNoteDetail = async (id: string) => {
    try {
      const response = await api.get(`/notes/${id}`);
      const detailedNote = response.data;
      detailedNote.created_at *= 1000;
      detailedNote.updated_at *= 1000;
      detailedNote.start_at *= 1000;
      
      const index = notes.value.findIndex(n => n.id === id);
      if (index !== -1) {
          // Merge detailed data into existing note in store
          notes.value[index] = { ...notes.value[index], ...detailedNote };
      }
      return detailedNote;
    } catch (error) {
      console.error('Failed to fetch note detail:', error);
      ElMessage.error('获取节点内容失败');
      return null;
    }
  };

  const createNote = async (title: string, content: string, weight: number = 100, start_at?: number) => {
    try {
      // Backend expects seconds
      const data: any = { title, content, weight };
      if (start_at) data.start_at = start_at / 1000;
      
      const response = await api.post('/notes/', data);
      const newNote = response.data;
      newNote.created_at *= 1000;
      newNote.updated_at *= 1000;
      newNote.start_at *= 1000;
      notes.value.unshift(newNote); // Add to top
      return newNote;
    } catch (error) {
      console.error('Failed to create note:', error);
      ElMessage.error('创建任务失败');
      return null;
    }
  };

  const updateNote = async (id: string, data: { title?: string; content?: string; weight?: number; start_at?: number; node_type?: string | null }) => {
    try {
      const updateData: any = { ...data };
      if (data.start_at) updateData.start_at = data.start_at / 1000;

      const response = await api.put(`/notes/${id}`, updateData);
      const updatedNote = response.data;
      updatedNote.created_at *= 1000;
      updatedNote.updated_at *= 1000;
      updatedNote.start_at *= 1000;
      
      const index = notes.value.findIndex(n => n.id === id);
      if (index !== -1) {
        notes.value[index] = updatedNote;
      }
      return updatedNote;
    } catch (error) {
      console.error('Failed to update note:', error);
      ElMessage.error('保存任务失败');
      return null;
    }
  };

  const deleteNote = async (id: string) => {
    try {
      await api.delete(`/notes/${id}`);
      notes.value = notes.value.filter(n => n.id !== id);
      // Also remove edges connected to this note
      edges.value = edges.value.filter(e => e.source_id !== id && e.target_id !== id);
      return true;
    } catch (error) {
      console.error('Failed to delete note:', error);
      ElMessage.error('删除任务失败');
      return false;
    }
  };
  
  // Edge operations
  const createEdge = async (sourceId: string, targetId: string, sourceHandle?: string, targetHandle?: string) => {
      try {
          const response = await api.post('/notes/edges/', { 
              source_id: sourceId, 
              target_id: targetId,
              source_handle: sourceHandle,
              target_handle: targetHandle
          });
          const newEdge = response.data;
          // Check if already exists in store (idempotency)
          if (!edges.value.find(e => e.id === newEdge.id)) {
              edges.value.push({
                  ...newEdge,
                  id: String(newEdge.id),
                  source_id: String(newEdge.source_id),
                  target_id: String(newEdge.target_id),
                  created_at: newEdge.created_at * 1000
              });
          }
          return newEdge;
      } catch (error) {
          console.error('Failed to create edge:', error);
          // Don't show error for duplicate edge as it might be common in UI
          return null;
      }
  };
  
  const deleteEdge = async (sourceId: string, targetId: string) => {
      try {
          // Delete by source-target pair
          await api.delete(`/notes/edges/?source=${sourceId}&target=${targetId}`);
          edges.value = edges.value.filter(e => !(e.source_id === sourceId && e.target_id === targetId));
          return true;
      } catch (error) {
          console.error('Failed to delete edge:', error);
          return false;
      }
  };

  return {
    notes,
    edges,
    loading,
    fetchNotes,
    fetchNoteDetail,
    createNote,
    updateNote,
    deleteNote,
    createEdge,
    deleteEdge
  };
});
