import { reactive } from 'vue';

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
  python_exec?: string;
}

export const taskStore = reactive({
    tasks: {} as Record<string, Task[]>,
    devices: [] as Device[],
    lastDeviceFetch: 0
});
