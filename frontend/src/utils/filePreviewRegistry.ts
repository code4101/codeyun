export type CodeyunPreviewKind = 'pdf' | 'image' | 'media' | 'generic' | 'unsupported';

const IMAGE_EXTENSIONS = new Set([
  'avif',
  'bmp',
  'gif',
  'heic',
  'jpeg',
  'jpg',
  'png',
  'svg',
  'tif',
  'tiff',
  'webp',
]);

const MEDIA_EXTENSIONS = new Set([
  'avi',
  'm4a',
  'm4v',
  'midi',
  'mkv',
  'mov',
  'mp3',
  'mp4',
  'mpeg',
  'mpg',
  'ogg',
  'ogv',
  'wav',
  'webm',
]);

const GENERIC_VIEWER_EXTENSIONS = new Set([
  'csv',
  'doc',
  'docm',
  'docx',
  'dot',
  'dotm',
  'dotx',
  'eml',
  'gz',
  'htm',
  'html',
  'json',
  'log',
  'md',
  'mbox',
  'msg',
  'odp',
  'ods',
  'odt',
  'ofd',
  'ppt',
  'pptm',
  'pptx',
  'rar',
  'rtf',
  'tar',
  'text',
  'tsv',
  'txt',
  'xls',
  'xlsb',
  'xlsm',
  'xlsx',
  'xml',
  'yaml',
  'yml',
  'zip',
  '7z',
]);

const CODE_EXTENSIONS = new Set([
  'bat',
  'c',
  'cc',
  'cpp',
  'cs',
  'css',
  'diff',
  'go',
  'h',
  'hpp',
  'ini',
  'java',
  'js',
  'jsx',
  'kt',
  'less',
  'lua',
  'patch',
  'php',
  'ps1',
  'py',
  'rs',
  'sass',
  'scss',
  'sh',
  'sql',
  'toml',
  'ts',
  'tsx',
  'vue',
]);

export function getFileExtension(nameOrPath: string): string {
  const normalized = String(nameOrPath || '').split(/[\\/]/).pop() || '';
  const dotIndex = normalized.lastIndexOf('.');
  if (dotIndex < 0 || dotIndex === normalized.length - 1) {
    return '';
  }
  return normalized.slice(dotIndex + 1).toLowerCase();
}

export function resolveCodeyunPreviewKind(nameOrPath: string): CodeyunPreviewKind {
  const extension = getFileExtension(nameOrPath);
  if (!extension) {
    return 'unsupported';
  }
  if (extension === 'pdf') {
    return 'pdf';
  }
  if (IMAGE_EXTENSIONS.has(extension)) {
    return 'image';
  }
  if (MEDIA_EXTENSIONS.has(extension)) {
    return 'media';
  }
  if (GENERIC_VIEWER_EXTENSIONS.has(extension) || CODE_EXTENSIONS.has(extension)) {
    return 'generic';
  }
  return 'unsupported';
}

export function formatPreviewKindLabel(kind: CodeyunPreviewKind): string {
  switch (kind) {
    case 'pdf':
      return 'PDF';
    case 'image':
      return '图片';
    case 'media':
      return '媒体';
    case 'generic':
      return '通用预览';
    default:
      return '不支持预览';
  }
}
