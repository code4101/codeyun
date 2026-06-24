import { reactive } from 'vue';
import api from '@/api';

export interface Task {
  id: string;
  name: string;
  command: string;
  description?: string;
  cwd?: string;
  device_id?: string;
  entry_id?: string;
  schedule?: string;
  timeout?: number;
  status: {
    running: boolean;
    pid?: number;
    message?: string;
    [key: string]: any;
  };
  actionLoading?: boolean;
}

export interface Device {
  id: string; // entry_id
  device_id: string; // actual device identity
  name: string;
  server_url?: string;
  mode: 'local' | 'remote';
  type: string;
  token?: string;
  owner_id?: number;
}

export const taskStore = reactive({
    tasks: {} as Record<string, Task[]>,
    devices: [] as Device[],
    lastDeviceFetch: 0,
    
    async fetchDevices() {
        try {
            const response = await api.get('/devices/');
            this.devices = response.data.map((item: any) => {
                const devInfo = item.device || {};
                return {
                    id: item.id,
                    device_id: item.device_id,
                    name: item.name || item.alias || devInfo.name || "Unknown",
                    server_url: item.server_url ?? devInfo.server_url,
                    mode: item.mode || (devInfo.type === 'LocalDevice' ? 'local' : 'remote'),
                    type: devInfo.type || 'RemoteDevice',
                    token: item.token,
                    owner_id: item.user_id
                };
            });
            
            this.lastDeviceFetch = Date.now();
        } catch (error) {
            console.error('Failed to fetch devices:', error);
            this.devices = [];
        }
    },

    async addDevice(device: Partial<Device>) {
        try {
            const payload = {
                mode: device.mode,
                device_id: device.device_id,
                token: device.token,
                alias: device.name,
                name: device.name,
                server_url: device.server_url
            };
            const response = await api.post('/devices/add', payload);
            await this.fetchDevices();
            return response.data;
        } catch (error) {
            console.error('Failed to add device:', error);
            throw error;
        }
    },
    
    async updateDevice(entryId: string, updates: Partial<Device>) {
        try {
            const payload: any = {};
            if (updates.token !== undefined) payload.token = updates.token;
            if (updates.name) {
                payload.alias = updates.name;
                payload.name = updates.name;
            }
            if (updates.server_url !== undefined) payload.server_url = updates.server_url;
            
            const response = await api.put(`/devices/${entryId}`, payload);
            await this.fetchDevices();
            return response.data;
        } catch (error) {
            console.error('Failed to update device:', error);
            throw error;
        }
    },
    
    async removeDevice(entryId: string) {
        try {
            await api.delete(`/devices/${entryId}`);
            await this.fetchDevices();
        } catch (error) {
            console.error('Failed to remove device:', error);
            throw error;
        }
    },
    
    async reorderDevices(entryIds: string[]) {
        try {
            await api.post('/devices/reorder', entryIds);
        } catch (error) {
            console.error('Failed to reorder devices:', error);
            throw error;
        }
    }
});
