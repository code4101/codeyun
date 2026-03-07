const MAX_KEEPALIVE_BYTES = 60 * 1024;

export const putJsonKeepalive = (url: string, payload: unknown) => {
  if (typeof window === 'undefined' || typeof fetch !== 'function') return false;

  try {
    const body = JSON.stringify(payload);
    const size = new Blob([body]).size;
    if (size > MAX_KEEPALIVE_BYTES) {
      return false;
    }

    const headers: Record<string, string> = {
      'Content-Type': 'application/json'
    };
    const token = window.localStorage.getItem('token');
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    void fetch(url, {
      method: 'PUT',
      headers,
      body,
      keepalive: true,
      credentials: 'same-origin'
    });
    return true;
  } catch {
    return false;
  }
};
