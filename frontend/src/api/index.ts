import axios from 'axios';
import { useUserStore } from '@/store/userStore';

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

// Helper to get API client for a specific device
export const getDeviceApi = (deviceUrl?: string, accessToken?: string | null) => {
  // If no URL provided, we target the local backend.
  // BUT, if an accessToken is explicitly provided (e.g. Device Token), we must use it
  // instead of the default User Token from localStorage.
  
  if (!deviceUrl && accessToken === undefined) {
    // No URL and no specific token -> Use default User Token client
    return api;
  }
  
  // Determine Base URL
  let baseURL = '/api';
  if (deviceUrl) {
    const cleanUrl = deviceUrl.replace(/\/+$/, '');
    baseURL = `${cleanUrl}/api`;
  }
  
  const instance = axios.create({
    baseURL,
    timeout: 10000,
  });

  // Add interceptor to inject token
  instance.interceptors.request.use(
    (config) => {
      if (accessToken) {
        // Use the specific Device Token provided
        config.headers.Authorization = `Bearer ${accessToken}`;
        // Also set X-Device-Token for compatibility if backend checks it
        config.headers['X-Device-Token'] = accessToken;
      } else if (accessToken === undefined) {
        // Fallback to current user token ONLY if accessToken is undefined
        // If accessToken is null, we intentionally send no auth header
        const token = localStorage.getItem('token');
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
      }
      return config;
    },
    (error) => Promise.reject(error)
  );

  return instance;
};

export default api;
