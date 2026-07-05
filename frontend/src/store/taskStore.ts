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
  runtime_kind?: 'service' | 'job';
  schedule?: string;
  schedule_policy?: Record<string, any> | null;
  schedule_state?: Record<string, any> | null;
  schedule_status?: {
    next_run_at?: string | null;
    configured?: boolean;
  } | null;
  next_run_at?: string | null;
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

const DEVICE_CACHE_KEY = 'codeyun.devices.v1';
const DEVICE_CACHE_TTL_MS = 10 * 60 * 1000;

type DeviceCachePayload = {
  savedAt: number;
  devices: Device[];
};

const cloneDevices = (devices: Device[]): Device[] => devices.map(device => ({ ...device }));

const restoreCachedDevices = (): Device[] => {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(DEVICE_CACHE_KEY);
    if (!raw) return [];
    const payload = JSON.parse(raw) as Partial<DeviceCachePayload>;
    if (
      !payload
      || typeof payload.savedAt !== 'number'
      || !Array.isArray(payload.devices)
      || Date.now() - payload.savedAt > DEVICE_CACHE_TTL_MS
    ) {
      window.localStorage.removeItem(DEVICE_CACHE_KEY);
      return [];
    }
    return cloneDevices(payload.devices as Device[]);
  } catch (error) {
    console.warn('Failed to restore cached devices', error);
    return [];
  }
};

const persistDevices = (devices: Device[]) => {
  if (typeof window === 'undefined') return;
  try {
    const payload: DeviceCachePayload = {
      savedAt: Date.now(),
      devices: cloneDevices(devices),
    };
    window.localStorage.setItem(DEVICE_CACHE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Failed to persist device cache', error);
  }
};

export const taskStore = reactive({
    tasks: {} as Record<string, Task[]>,
    devices: restoreCachedDevices() as Device[],
    lastDeviceFetch: 0,
    lastDeviceFetchError: '',
    
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
                    owner_id: item.user_id
                };
            });
            persistDevices(this.devices);
            this.lastDeviceFetch = Date.now();
            this.lastDeviceFetchError = '';
        } catch (error) {
            console.error('Failed to fetch devices:', error);
            const detail = (error as any)?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (error as any)?.message;
            this.lastDeviceFetchError = typeof message === 'string' && message.trim()
                ? message
                : '读取设备列表失败';
            if (!this.devices.length) {
                this.devices = [];
            }
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

    async fetchDeviceToken(entryId: string): Promise<string> {
        try {
            const response = await api.get(`/devices/${entryId}/token`);
            return response.data.token || '';
        } catch (error) {
            console.error('Failed to fetch device token:', error);
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
            persistDevices(this.devices);
        } catch (error) {
            console.error('Failed to reorder devices:', error);
            throw error;
        }
    }
});
