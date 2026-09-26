import { toast } from 'sonner';
export async function open(value: string) {
  let url: URL;
  try { url = new URL(value); } catch { toast.info('浏览器体验版暂不支持打开桌面文件路径'); return; }
  if (!['http:', 'https:', 'mailto:'].includes(url.protocol)) { toast.info('浏览器体验版暂不支持此链接协议'); return; }
  window.open(url.href, '_blank', 'noopener,noreferrer');
}
