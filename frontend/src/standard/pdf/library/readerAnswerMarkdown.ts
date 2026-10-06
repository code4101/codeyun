import DOMPurify from 'dompurify'
import { marked } from 'marked'

/** 模型 Markdown 经过清洗再进入 DOM；外部链接另开页面，代码与表格保留语义。 */
export function renderReaderAnswer(markdown: string): string {
  const html = marked.parse(markdown, { async: false, gfm: true, breaks: false })
  const clean = DOMPurify.sanitize(html, { FORBID_TAGS: ['img', 'iframe', 'style', 'form', 'input', 'button'], FORBID_ATTR: ['style', 'id', 'name'] })
  const wrapper = document.createElement('div')
  wrapper.innerHTML = clean
  wrapper.querySelectorAll('a').forEach(link => {
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
  })
  return wrapper.innerHTML
}
