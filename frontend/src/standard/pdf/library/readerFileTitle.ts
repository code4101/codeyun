/** 保留书名，以原始文件后缀优先标明格式；已有后缀不重复追加。 */
export function readerFileTitle(title: string, filename = '', format = ''): string {
  const extension = filename.match(/\.([a-z0-9]{1,12})$/i)?.[1]
    ?? (({ markdown: 'md', text: 'txt' } as Record<string, string>)[format.toLowerCase()] ?? format.toLowerCase())
  if (!extension || !/^[a-z0-9]{1,12}$/i.test(extension)) return title
  const suffix = `.${extension.toLowerCase()}`
  return title.toLowerCase().endsWith(suffix) ? title : `${title}${suffix}`
}
