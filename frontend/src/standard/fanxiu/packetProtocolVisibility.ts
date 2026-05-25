const STORAGE_KEY = 'fanxiu:packet-protocol:hidden'
export const FANXIU_PACKET_PROTOCOL_VISIBILITY_EVENT = 'fanxiu-packet-protocol-visibility-change'

const canUseStorage = () => typeof window !== 'undefined' && Boolean(window.localStorage)

export function getHiddenFanxiuPacketProtocols(): string[] {
  if (!canUseStorage()) return []
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    const items = value ? JSON.parse(value) : []
    return Array.isArray(items) ? items.filter(item => typeof item === 'string' && item.trim()) : []
  } catch {
    return []
  }
}

export function setHiddenFanxiuPacketProtocols(items: string[]) {
  const unique = [...new Set(items.map(item => item.trim()).filter(Boolean))].sort()
  if (!canUseStorage()) return
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(unique))
  window.dispatchEvent(new CustomEvent(FANXIU_PACKET_PROTOCOL_VISIBILITY_EVENT, { detail: unique }))
}

export function isFanxiuPacketProtocolVisible(name: string) {
  return !getHiddenFanxiuPacketProtocols().includes(name)
}

export function setFanxiuPacketProtocolVisible(name: string, visible: boolean) {
  const hidden = new Set(getHiddenFanxiuPacketProtocols())
  if (visible) {
    hidden.delete(name)
  } else if (name) {
    hidden.add(name)
  }
  setHiddenFanxiuPacketProtocols([...hidden])
}
