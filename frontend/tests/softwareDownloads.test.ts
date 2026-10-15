import assert from 'node:assert/strict'
import test from 'node:test'
import { detectOperatingSystem, groupSoftware, recommendedBundle, type SoftwareBundle } from '../src/standard/tools/software-downloads/catalog.ts'

function bundle(id: string, title: string, platform: string): SoftwareBundle {
  return { id, title, platform, version: '2.5.2', description: id, created_at: '',
    files: [{ name: `${id}.zip`, bytes: 100, sha256: id }] }
}

test('one card per software with Apple silicon Mac and Windows options', () => {
  const intel = bundle('intel', 'Clash Verge Rev · Mac Intel 芯片', 'macOS 12+ · Intel 芯片')
  const apple = bundle('apple', 'Clash Verge Rev · Mac Apple 芯片（M 系列）', 'macOS 12+ · Apple 芯片（M 系列）')
  const windows = bundle('windows', 'Clash Verge Rev · 本机配置便携包', 'Windows 10/11 · x64')
  const other = bundle('other', 'Other Software', 'Linux x64')
  const groups = groupSoftware([intel, apple, windows, other])
  assert.equal(groups.length, 2)
  assert.equal(groups[0]!.name, 'Clash Verge Rev')
  assert.deepEqual(groups[0]!.bundles, [apple, windows])
  // Selection returns the entire matching bundle, keeping files and instructions together.
  assert.equal(recommendedBundle(groups[0]!.bundles, 'windows'), windows)
  assert.equal(recommendedBundle(groups[0]!.bundles, 'macos'), apple)
  assert.equal(recommendedBundle(groups[0]!.bundles, 'linux'), undefined)
  assert.equal(recommendedBundle(groups[0]!.bundles, ''), undefined)
  assert.deepEqual(groupSoftware([bundle('x64', 'Mac tool', 'macOS x64')]), [])
})

test('explicit software name groups packages with independent display titles', () => {
  const win = { ...bundle('win', 'Portable', 'Windows'), software_name: 'Tool' }
  const mac = { ...bundle('mac', 'Installer', 'macOS arm64'), software_name: 'Tool' }
  assert.deepEqual(groupSoftware([win, mac]), [{ name: 'Tool', bundles: [win, mac] }])
})

test('detect desktop systems while leaving mobile and unknown devices for manual selection', () => {
  assert.equal(detectOperatingSystem('Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Win32'), 'windows')
  // Apple silicon browsers also commonly report Intel in their user agent.
  assert.equal(detectOperatingSystem('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 'MacIntel'), 'macos')
  assert.equal(detectOperatingSystem('Mozilla/5.0 (X11; Linux x86_64)', 'Linux x86_64'), 'linux')
  assert.equal(detectOperatingSystem('Mozilla/5.0 (Linux; Android 14)', 'Linux armv8l'), '')
  assert.equal(detectOperatingSystem('Mozilla/5.0 (Macintosh; Intel Mac OS X)', 'MacIntel', 5), '')
  assert.equal(detectOperatingSystem('Mozilla/5.0 (iPhone)', 'iPhone'), '')
  assert.equal(detectOperatingSystem('', ''), '')
})
