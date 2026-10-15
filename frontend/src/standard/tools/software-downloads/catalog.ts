export interface SoftwareBundle {
  id: string
  title: string
  software_name?: string
  description: string
  version: string
  platform: string
  created_at: string
  files: { name: string; bytes: number; sha256: string }[]
}

export type OperatingSystem = 'windows' | 'macos' | 'linux' | ''

export function detectOperatingSystem(userAgent: string, platform: string, maxTouchPoints = 0): OperatingSystem {
  // iPad desktop mode reports MacIntel. Mobile devices should select manually.
  if (/Android|iPhone|iPad|iPod/i.test(userAgent) || (/Mac/i.test(platform) && maxTouchPoints > 1)) return ''
  const identity = `${platform} ${userAgent}`
  if (/Windows|Win32|Win64/i.test(identity)) return 'windows'
  if (/Macintosh|MacIntel|MacPPC|macOS/i.test(identity)) return 'macos'
  if (/Linux/i.test(identity)) return 'linux'
  return ''
}

export function bundleOperatingSystem(bundle: SoftwareBundle): OperatingSystem {
  if (/macOS|\bMac\b|Darwin/i.test(bundle.platform)) return 'macos'
  if (/Windows/i.test(bundle.platform)) return 'windows'
  if (/Linux/i.test(bundle.platform)) return 'linux'
  return ''
}

export function groupSoftware(items: SoftwareBundle[]) {
  const groups = new Map<string, { name: string; bundles: SoftwareBundle[] }>()
  for (const item of items) {
    if (bundleOperatingSystem(item) === 'macos' && /Intel|\bx64\b|x86|amd64/i.test(item.platform)) continue
    // Legacy manifests use “software name · package variant” as their title.
    const name = item.software_name?.trim() || item.title.split(' · ')[0]!.trim()
    const key = name.toLocaleLowerCase()
    const group = groups.get(key) ?? { name, bundles: [] }
    group.bundles.push(item)
    groups.set(key, group)
  }
  return [...groups.values()]
}

export function recommendedBundle(bundles: SoftwareBundle[], system: OperatingSystem) {
  return bundles.find(bundle => system && bundleOperatingSystem(bundle) === system)
}
