import { defineAsyncComponent } from 'vue'
import type { ReaderTab } from './readerWorkspaceState'

/** 阅读器插件只接收书籍参数；标签、持久化、外壳和图书馆由工作区提供。 */
export const readerPlugins = {
  pdf: { component: defineAsyncComponent(() => import('../resource-view/page.vue')),
    props: (tab: ReaderTab) => ({ documentId: Number(tab.id) }) },
  ebook: { component: defineAsyncComponent(() => import('./LinuxDoBookReaderDialog.vue')),
    props: (tab: ReaderTab) => ({ modelValue: true, standalone: true, bookId: tab.id, logicalPageTargetCharacters: tab.pageSize ?? 1600, readingMode: tab.readingMode ?? 'scroll' }) },
  skill: { component: defineAsyncComponent(() => import('./SkillBookReaderDialog.vue')),
    props: (tab: ReaderTab) => ({ modelValue: true, standalone: true, bookshelfId: tab.bookshelfId ?? '' }) },
}
