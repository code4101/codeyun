export const isAbsolutePath = (value: string) => /^(?:[a-zA-Z]:[\\/]|\\\\|\/|~(?:[\\/]|$))/.test((value || '').trim());

export const isDeviceRootPath = (value: string, deviceRootSentinel: string) =>
  (value || '').trim() === deviceRootSentinel;

export const formatPathInput = (value: string, deviceRootSentinel: string, deviceRootLabel: string) =>
  isDeviceRootPath(value, deviceRootSentinel) ? deviceRootLabel : value;

export const normalizePathInput = (value: string, deviceRootSentinel: string, deviceRootLabel: string) => {
  const trimmed = (value || '').trim();
  if (!trimmed || trimmed === deviceRootLabel || trimmed === deviceRootSentinel) {
    return deviceRootSentinel;
  }
  return isAbsolutePath(trimmed) ? trimmed : '';
};

export const normalizeComparablePath = (value: string) => {
  let normalized = (value || '').trim().replace(/\//g, '\\').replace(/\\+/g, '\\');
  if (/^[a-zA-Z]:\\?$/.test(normalized)) {
    return `${normalized.slice(0, 2)}\\`.toLowerCase();
  }
  normalized = normalized.replace(/\\+$/, '');
  return normalized.toLowerCase();
};

export const isSameOrSubPath = (candidate: string, root: string) => {
  const normalizedCandidate = normalizeComparablePath(candidate);
  const normalizedRoot = normalizeComparablePath(root);
  return normalizedCandidate === normalizedRoot || normalizedCandidate.startsWith(`${normalizedRoot}\\`);
};
