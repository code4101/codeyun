import { defineStore } from 'pinia';
import api from '@/api';
// import { jwtDecode } from 'jwt-decode';

interface User {
  id: number;
  username: string;
  email?: string;
  is_superuser: boolean;
}

interface UserState {
  token: string | null;
  refreshToken: string | null;
  user: User | null;
  loading: boolean;
  error: string | null;
}

export const useUserStore = defineStore('user', {
  state: (): UserState => ({
    token: localStorage.getItem('token'),
    refreshToken: localStorage.getItem('refresh_token'),
    user: null,
    loading: false,
    error: null,
  }),

  getters: {
    isAuthenticated: (state) => !!state.token,
    isAdmin: (state) => state.user?.is_superuser || false,
  },

  actions: {
    async login(username: string, password: string) {
      this.loading = true;
      this.error = null;
      try {
        // 使用 json 登录接口
        const response = await api.post('/auth/login/json', { username, password });
        const { access_token, refresh_token } = response.data;
        
        this.setTokens(access_token, refresh_token);
        
        await this.fetchUserProfile();
        return true;
      } catch (err: any) {
        this.error = err.response?.data?.detail || '登录失败';
        return false;
      } finally {
        this.loading = false;
      }
    },

    async register(username: string, password: string, email?: string) {
      this.loading = true;
      this.error = null;
      try {
        await api.post('/auth/register', { username, password, email });
        // 注册成功后自动登录
        return await this.login(username, password);
      } catch (err: any) {
        this.error = err.response?.data?.detail || '注册失败';
        return false;
      } finally {
        this.loading = false;
      }
    },

    async fetchUserProfile() {
      if (!this.token) return;
      try {
        // 确保 header 存在
        api.defaults.headers.common['Authorization'] = `Bearer ${this.token}`;
        const response = await api.get('/auth/me');
        this.user = response.data;
      } catch (err: any) {
        // 仅在明确的 401 未授权时登出
        // 如果是网络错误（后端重启中）或服务器错误，保留 Token，避免用户被强制登出
        if (err.response && err.response.status === 401) {
          this.logout();
        } else {
          console.warn('Failed to fetch user profile (session kept):', err.message || err);
        }
      }
    },

    logout() {
      this.token = null;
      this.refreshToken = null;
      this.user = null;
      localStorage.removeItem('token');
      localStorage.removeItem('refresh_token');
      delete api.defaults.headers.common['Authorization'];
    },

    setTokens(accessToken: string, refreshToken?: string) {
      this.token = accessToken;
      localStorage.setItem('token', accessToken);
      api.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;
      
      if (refreshToken) {
        this.refreshToken = refreshToken;
        localStorage.setItem('refresh_token', refreshToken);
      }
    },

    async refreshAccessToken() {
      if (!this.refreshToken) return false;
      try {
        // Use a new axios instance to avoid interceptor loop if we were to put this logic in interceptor
        // But here we just call the API.
        // NOTE: The interceptor logic in api/index.ts should handle the retry.
        // This action is just a helper.
        
        // However, we need to be careful. The interceptor in api/index.ts uses `api` instance.
        // If we use `api.post`, it might trigger the interceptor again if it fails?
        // Actually, for refresh token request, we don't need the access token header.
        
        // We can manually call axios or create a clean instance.
        // But `api` instance adds Authorization header automatically via interceptor if token exists.
        // If token is expired, that's fine for other requests, but for /refresh we don't need it.
        // The backend /refresh endpoint doesn't require Bearer auth on the access token, 
        // it requires the refresh token in the body.
        
        const response = await api.post('/auth/refresh', { refresh_token: this.refreshToken });
        const { access_token } = response.data;
        this.setTokens(access_token); // Update token
        return true;
      } catch (e: any) {
        console.error('Failed to refresh token', e);
        // 仅在明确的 Refresh Token 过期或无效时登出
        if (e.response && (e.response.status === 401 || e.response.status === 403)) {
             this.logout();
        }
        return false;
      }
    },

    // 初始化时恢复会话
    async initialize() {
      if (this.token) {
        api.defaults.headers.common['Authorization'] = `Bearer ${this.token}`;
        await this.fetchUserProfile();
      }
    }
  },
});
