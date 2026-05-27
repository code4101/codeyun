import axios from 'axios';
import { useUserStore } from '@/store/userStore';

export const getDeviceEntryPath = (entryId: string, path = '') => {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `/device-entries/${entryId}${normalizedPath}`;
};

// Default instance (Local Backend)
const api = axios.create({
  baseURL: '/api', // Proxied to http://localhost:8000 via Vite proxy or similar
  timeout: 10000,
});

// Request interceptor for API calls
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor for auto-refresh
let isRefreshing = false;
let failedQueue: any[] = [];

const processQueue = (error: any, token: string | null = null) => {
  failedQueue.forEach(prom => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });
  
  failedQueue = [];
};

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    
    // Check if error is 401 and request was not a retry
    if (error.response?.status === 401 && !originalRequest._retry) {
      
      // If it's the login or refresh endpoint itself that failed, don't loop
      if (originalRequest.url?.includes('/auth/login') || originalRequest.url?.includes('/auth/refresh')) {
        return Promise.reject(error);
      }

      if (isRefreshing) {
        return new Promise(function(resolve, reject) {
          failedQueue.push({resolve, reject});
        }).then(token => {
          originalRequest.headers['Authorization'] = 'Bearer ' + token;
          return api(originalRequest);
        }).catch(err => {
          return Promise.reject(err);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        // Use store
        const userStore = useUserStore();
        
        // Try refresh
        const success = await userStore.refreshAccessToken();
        
        if (success && userStore.token) {
          processQueue(null, userStore.token);
          originalRequest.headers['Authorization'] = 'Bearer ' + userStore.token;
          return api(originalRequest);
        }
        
        // If refresh failed
        processQueue(error, null);
        return Promise.reject(error);
        
      } catch (err) {
        processQueue(err, null);
        return Promise.reject(err);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);
export default api;
