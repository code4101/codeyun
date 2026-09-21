// iPadOS 桌面网站模式会报告 MacIntel；仅用于界面软隐藏。
export const isIPad = typeof navigator !== 'undefined' && (
  /iPad/i.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
);
