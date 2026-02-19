import { reactive } from 'vue';
import api, { getDeviceApi } from '@/api';
import { jwtDecode } from 'jwt-decode';

export interface Task {
  id: string;
  name: string;
  command: string;
  description?: string;
  cwd?: string;
  device_id?: string;
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
  id: string;
  name: string;
  url?: string;
  type: string;
  access_token?: string; // Added access_token
  refresh_token?: string; // Added refresh_token
  // python_exec removed
  owner_id?: number; // Added owner_id
}

export const taskStore = reactive({
    tasks: {} as Record<string, Task[]>,
    devices: [] as Device[],
    lastDeviceFetch: 0,
    
    async fetchDevices() {
        try {
            // Add trailing slash to avoid 307 redirect which might drop auth headers
            const response = await api.get('/devices/');
            // Backend returns UserDeviceRead[] which contains:
            // { user_id, device_id, alias, token, device: { id, name, type, ... } }
            // But now UserDeviceRead HAS device info directly or embedded.
            // Let's check backend schema:
            // UserDeviceRead: { user_id, device_id, token, alias, name, is_active, device: DeviceRead }
            // DeviceRead: { id, name, type, url, python_exec }
            
            // We need to map this to our Device interface
            this.devices = response.data.map((item: any) => {
                // Compatibility: prefer top-level fields from UserDevice, fallback to item.device
                const devInfo = item.device || {};
                return {
                    id: item.device_id,
                    name: item.name || item.alias || devInfo.name || "Unknown",
                    url: devInfo.url, // URL comes from DeviceRead (which is constructed from UserDevice in backend now)
                    type: devInfo.type || 'RemoteDevice',
                    // python_exec removed
                    access_token: item.token, 
                    owner_id: item.user_id
                };
            });
            
            this.lastDeviceFetch = Date.now();
            
            // Check tokens
            this.devices.forEach(d => {
                if (d.type === 'RemoteDevice') {
                    this.checkAndRefreshToken(d);
                }
            });
        } catch (error) {
            console.error('Failed to fetch devices:', error);
            this.devices = [];
        }
    },
    
    async checkAndRefreshToken(device: Device) {
        if (!device.access_token || !device.refresh_token || !device.url) return;
        
        try {
            const decoded: any = jwtDecode(device.access_token);
            // If expires in less than 1 hour (3600s), refresh
            const now = Date.now() / 1000;
            if (decoded.exp && (decoded.exp - now) < 3600) {
                console.log(`Refreshing token for device ${device.name}`);
                // Use default axios (no auth) to hit refresh endpoint
                // Wait, getDeviceApi(url) uses no auth if no token passed
                // But we want to call /pairing/refresh with body
                const client = getDeviceApi(device.url);
                
                const resp = await client.post('/pairing/refresh', {
                    refresh_token: device.refresh_token
                });
                
                const newAccessToken = resp.data.access_token;
                
                console.log('Token refreshed successfully');
                
                // Update local store and backend
                await this.updateDevice(device.id, {
                    access_token: newAccessToken
                });
            }
        } catch (e) {
            console.error(`Failed to refresh token for device ${device.name}`, e);
        }
    },

    async addDevice(device: Partial<Device>) {
        try {
            // The backend expects: device_id, token, alias, url
            // The frontend provides: id, access_token, name, url
            
            // Note: We don't pass 'device_id' (or pass null/random) because the backend 
            // will now fetch the REAL device ID from the remote agent.
            // We only need to pass the URL and Token.
            
            const payload = {
                // device.id from frontend is random/temporary. 
                // We can pass it, but backend will ignore it if it successfully connects to remote.
                // However, backend schema requires 'device_id'.
                // So we pass the random ID, but backend will overwrite it with real ID.
                device_id: device.id, 
                token: device.access_token,
                alias: device.name,
                url: device.url
            };
            // Correct endpoint is /devices/add
            const response = await api.post('/devices/add', payload);
            // Refresh list
            await this.fetchDevices();
            return response.data;
        } catch (error) {
            console.error('Failed to add device:', error);
            throw error;
        }
    },
    
    async updateDevice(deviceId: string, updates: Partial<Device>) {
        try {
            // Frontend updates: access_token, name, python_exec
            // Backend update_user_device accepts: token, alias, is_active
            
            const payload: any = {};
            if (updates.access_token) payload.token = updates.access_token;
            if (updates.name) {
                payload.alias = updates.name;
                payload.name = updates.name; // Also send as name to trigger global rename
            }
            // python_exec removed
            
            const response = await api.put(`/devices/${deviceId}`, payload);
            // Refresh list
            await this.fetchDevices();
            return response.data;
        } catch (error) {
            console.error('Failed to update device:', error);
            throw error;
        }
    },
    
    async removeDevice(deviceId: string) {
        try {
            await api.delete(`/devices/${deviceId}`);
            // Refresh list
            await this.fetchDevices();
        } catch (error) {
            console.error('Failed to remove device:', error);
            throw error;
        }
    },
    
    async reorderDevices(deviceIds: string[]) {
        try {
            await api.post('/devices/reorder', deviceIds);
            // Optionally refresh or just trust local reorder
        } catch (error) {
            console.error('Failed to reorder devices:', error);
            throw error;
        }
    }
});
