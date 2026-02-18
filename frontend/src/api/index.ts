import axios from 'axios';

// Default instance (Local Backend)
const api = axios.create({
  baseURL: '/api', // Proxied to http://localhost:8000 via Vite proxy or similar
  timeout: 10000,
});

// Helper to get API client for a specific device
export const getDeviceApi = (deviceUrl?: string) => {
  if (!deviceUrl) {
    return api;
  }
  
  // Ensure we don't double-slash or miss /api
  // deviceUrl is expected to be origin like "http://192.168.1.X:8000"
  // We want to target "http://192.168.1.X:8000/api"
  
  // Clean trailing slash
  const cleanUrl = deviceUrl.replace(/\/+$/, '');
  
  return axios.create({
    baseURL: `${cleanUrl}/api`,
    timeout: 10000,
  });
};

export default api;
