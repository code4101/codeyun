/** One file-type catalog for every resource explorer. Unknown extensions keep a document icon. */
export function fileType(name: string) {
  const extension = name.split('.').pop()?.toLowerCase()
  if (!name.includes('.')) return 'file'
  if (extension === 'prg') return 'prg'
  if (extension === 'pdf') return 'pdf'
  if (['epub', 'mobi', 'azw', 'azw3'].includes(extension!)) return 'ebook'
  if (['html', 'htm'].includes(extension!)) return 'html'
  if (['md', 'markdown'].includes(extension!)) return 'markdown'
  if (['txt', 'text'].includes(extension!)) return 'text'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif'].includes(extension!)) return 'image'
  return 'file'
}
