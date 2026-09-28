import type { LocationQuery, LocationQueryRaw } from 'vue-router'

/** 展示层级只控制界面，不参与权限或资源定位。未定制纯内容的页面将 0 回退为 1。 */
export type UiLevel = 0 | 1 | 2
export interface UiPresentationOptions {
  /** 缺省或非法 ui 参数使用页面默认值；未配置时为 2。考勤资源入口为 0。 */
  defaultUi?: UiLevel
  /** 仅在业务已接入 showWorkbench 并能独立展示核心内容时启用。 */
  supportsContentOnly?: boolean
}

declare module 'vue-router' {
  interface RouteMeta extends UiPresentationOptions {}
}

export function parseUiLevel(value: unknown): UiLevel | undefined {
  return value === '0' ? 0 : value === '1' ? 1 : value === '2' ? 2 : undefined
}

export function resolveUiPresentation(query: LocationQuery, options: UiPresentationOptions = {}) {
  const requestedUi = parseUiLevel(query.ui) ?? options.defaultUi ?? 2
  const ui = requestedUi === 0 && !options.supportsContentOnly ? 1 : requestedUi
  return { requestedUi, ui, showAppNavigation: ui === 2, showWorkbench: ui !== 0 }
}

/** 新链接使用正式路径；旧 standalone 路径仅作为输入兼容。保留参数和片段。 */
export function withUiLevel(href: string, level: UiLevel): string {
  const url = new URL(href, 'https://codeyun.invalid')
  url.pathname = stripStandalonePrefix(url.pathname)
  url.searchParams.set('ui', String(level))
  return /^[a-z][a-z\d+.-]*:/i.test(href) ? url.href : `${url.pathname}${url.search}${url.hash}`
}

export function stripStandalonePrefix(path: string): string {
  return path === '/standalone' ? '/' : path.startsWith('/standalone/') ? path.slice('/standalone'.length) : path
}

export function legacyStandaloneLocation(to: { path: string; query: LocationQuery; hash: string }) {
  return {
    path: stripStandalonePrefix(to.path),
    query: { ...to.query, ui: String(parseUiLevel(to.query.ui) ?? 1) } as LocationQueryRaw,
    hash: to.hash,
    replace: true,
  }
}
