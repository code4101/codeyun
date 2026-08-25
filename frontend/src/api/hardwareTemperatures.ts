import api from './index'

export type TemperatureDeviceKind = 'cpu' | 'gpu' | 'storage' | 'motherboard' | 'other'
export type TemperatureSnapshotStatus = 'ok' | 'partial' | 'stale' | 'error' | 'unavailable'

export interface TemperatureSensor {
  id: string
  name: string
  value: number
  source: string
}

export interface TemperatureDevice {
  id: string
  kind: TemperatureDeviceKind
  name: string
  drive_letters: string[]
  temperature: number | null
  sensors: TemperatureSensor[]
}

export interface TemperatureSnapshot {
  status: TemperatureSnapshotStatus
  observed_at: string | null
  source: 'CodeYun'
  elevated: boolean
  devices: TemperatureDevice[]
  message: string
}

export async function getHardwareTemperatures(): Promise<TemperatureSnapshot> {
  const response = await api.get<TemperatureSnapshot>('/hardware-temperatures')
  return response.data
}

export async function requestFullHardwareTemperatures(): Promise<void> {
  await api.post('/hardware-temperatures/elevate')
}
