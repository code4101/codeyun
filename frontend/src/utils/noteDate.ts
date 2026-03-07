const DATE_SEPARATOR = '/';

const normalizeDateOutput = (value: string) => value.replace(/-/g, DATE_SEPARATOR);

export const formatNoteDateTime = (timestamp: number) => {
  if (!timestamp) return '-';

  return normalizeDateOutput(new Date(timestamp).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }));
};

export const formatNoteDateTimeDetailed = (timestamp: number) => {
  if (!timestamp) return '-';

  const date = new Date(timestamp);
  const now = new Date();
  const isCurrentYear = date.getFullYear() === now.getFullYear();
  const options: Intl.DateTimeFormatOptions = {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  };

  if (!isCurrentYear) {
    options.year = 'numeric';
  }

  return normalizeDateOutput(date.toLocaleString('zh-CN', options));
};

export const formatNoteDateShort = (timestamp: number) => {
  if (!timestamp) return '-';

  return new Date(timestamp).toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  });
};

export const formatNoteMonthLabel = (date: Date) => (
  `${date.getFullYear()}年${String(date.getMonth() + 1).padStart(2, '0')}月`
);
