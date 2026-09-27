import { ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { fetchPdfBookshelves, fetchPdfDocuments, fetchLibraryFolders } from '@/api/pdfDocuments'
import { fetchLinuxDoBooks } from '@/api/linuxDoBooks'
import { fetchLocalSkillBookCatalog } from '@/api/skillBooks'
import { useUserStore } from '@/store/userStore'
import { readerFileTitle } from './readerFileTitle'
import type { ReaderTab } from './readerWorkspaceState'

export interface LibraryTreeNode { id: string; title: string; tab?: ReaderTab; children?: LibraryTreeNode[]; loaded?: boolean; loading?: boolean; expanded?: boolean; error?: string; pageSize?: number; readingMode?: 'scroll' | 'paginated' }
export const useLibraryTree = defineStore('reader-library-tree', () => {
  const nodes = ref<LibraryTreeNode[]>([])
  const error = ref('')
  const loading = ref(false)
  let generation = 0
  const user = useUserStore()
  watch(() => user.user?.id, () => { generation++; nodes.value = []; error.value = ''; loading.value = false })
  async function load(force = false) {
    if (loading.value || (nodes.value.length && !force)) return
    const epoch = generation
    loading.value = true
    try {
      const groups = await Promise.all([fetchPdfBookshelves('mine'), fetchPdfBookshelves('shared')])
      if (epoch !== generation) return
      nodes.value = [...new Map(groups.flat().map(shelf => [shelf.id, { id: shelf.id, title: shelf.name, pageSize: shelf.logical_page_target_characters, readingMode: shelf.article_reading_mode, children: [] }])).values()]
      error.value = ''
    } catch { if (epoch === generation) error.value = '图书馆加载失败，请重试' }
    finally { if (epoch === generation) loading.value = false }
  }
  async function expand(node: LibraryTreeNode) {
    if (node.loaded || node.loading || node.tab) return
    node.loading = true
    node.error = ''
    try {
      const [pdfs, ebooks, folders, skill] = await Promise.all([
        fetchPdfDocuments(node.id), fetchLinuxDoBooks(node.id), fetchLibraryFolders(node.id),
        fetchLocalSkillBookCatalog(node.id).catch(() => null),
      ])
      const children: LibraryTreeNode[] = folders.map(folder => ({ id: `folder:${folder.id}`, title: folder.name, children: [], loaded: true }))
      const append = (tab: ReaderTab, folderId?: string | null) => {
        const target = children.find(child => child.id === `folder:${folderId}`)?.children ?? children
        target.push({ id: `${tab.kind}:${tab.id}`, title: tab.title || tab.id, tab: { pageSize: node.pageSize, readingMode: node.readingMode, ...tab } })
      }
      for (const pdf of pdfs) append({ kind: 'pdf', id: String(pdf.id), title: readerFileTitle(pdf.display_title || pdf.title, pdf.imported_filename, 'pdf'), bookshelfId: node.id }, pdf.bookshelf_placement?.folder_id)
      for (const book of ebooks) append({ kind: 'ebook', id: book.id, title: readerFileTitle(book.title, book.original_filename, book.format), bookshelfId: node.id, readingMode: book.bookshelf_placement.article_reading_mode ?? node.readingMode ?? 'scroll' }, book.bookshelf_placement.folder_id)
      if (skill && skill.bookshelf_placement.bookshelf_id === node.id) append({ kind: 'skill', id: 'local-skill', title: skill.title, bookshelfId: node.id }, skill.bookshelf_placement.folder_id)
      node.children = children
      node.loaded = true
    } catch { node.error = '书架加载失败，点击重试' }
    finally { node.loading = false }
  }
  return { nodes, loading, error, load, expand }
})
