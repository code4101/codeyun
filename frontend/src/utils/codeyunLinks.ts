export type CodeyunLinkVariant = 'current' | 'public'

export const CODEYUN_PUBLIC_HOST = 'code4101.com'

export function resolveCodeyunUrl(source: string | URL | null | undefined) {
  if (!source) {
    return null
  }

  try {
    if (source instanceof URL) {
      return new URL(source.toString())
    }
    if (typeof window === 'undefined') {
      return null
    }
    return new URL(source, window.location.href)
  } catch {
    return null
  }
}

function getUrlHostname(url: URL) {
  return url.hostname.trim().toLowerCase().replace(/^\[|\]$/g, '')
}

function isLocalhostCodeyunHost(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
}

function isLanIpv4Host(hostname: string) {
  const parts = hostname.split('.').map((part) => Number(part))
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false
  }
  const [first, second] = parts
  return (
    first === 10
    || first === 192
    || (first === 172 && second >= 16 && second <= 31)
  )
}

function isSwitchableCodeyunHost(hostname: string) {
  return (
    isLocalhostCodeyunHost(hostname)
    || isLanIpv4Host(hostname)
    || hostname === CODEYUN_PUBLIC_HOST
  )
}

function isSwitchableCodeyunUrl(url: URL) {
  const currentUrl = getCurrentLocationUrl()
  return (
    (url.protocol === 'http:' || url.protocol === 'https:')
    && (
      isSwitchableCodeyunHost(getUrlHostname(url))
      || (!!currentUrl && url.protocol === currentUrl.protocol && url.host === currentUrl.host)
    )
  )
}

function getCurrentLocationUrl() {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return new URL(window.location.href)
  } catch {
    return null
  }
}

export function buildCodeyunUrlVariant(source: string | URL | null | undefined, variant: CodeyunLinkVariant) {
  const sourceUrl = resolveCodeyunUrl(source)
  if (!sourceUrl || !isSwitchableCodeyunUrl(sourceUrl)) {
    return ''
  }

  const targetUrl = new URL(sourceUrl.toString())
  if (variant === 'current') {
    const currentUrl = getCurrentLocationUrl()
    if (!currentUrl) {
      return ''
    }
    targetUrl.protocol = currentUrl.protocol
    targetUrl.host = currentUrl.host
    return targetUrl.toString()
  }

  targetUrl.protocol = 'https:'
  targetUrl.host = CODEYUN_PUBLIC_HOST
  return targetUrl.toString()
}

export function openUrlInNewWindow(url: string) {
  if (!url || typeof window === 'undefined') {
    return
  }

  window.open(url, '_blank', 'noopener,noreferrer')
}

export async function copyTextToClipboard(text: string) {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  if (typeof document === 'undefined') {
    throw new Error('Clipboard is unavailable')
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  document.body.removeChild(textarea)
  if (!copied) {
    throw new Error('Copy command failed')
  }
}
