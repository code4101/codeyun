export interface RecentTab { id: string; closable?: boolean }

/** LRU 顺序独立于显示顺序；拖动标签不改变活跃记录。 */
export class TabRecency {
  private recent: string[] = []

  sync(tabs: readonly RecentTab[], active: string) {
    const ids = new Set(tabs.map(tab => tab.id))
    this.recent = this.recent.filter(id => ids.has(id))
    for (const tab of tabs) if (!this.recent.includes(tab.id)) this.recent.push(tab.id)
    this.touch(active)
  }

  touch(id: string) {
    if (!this.recent.includes(id)) return
    this.recent = [...this.recent.filter(key => key !== id), id]
  }

  victim(tabs: readonly RecentTab[], active: string, limit = 10): string | undefined {
    if (tabs.length <= limit) return
    return this.recent.find(id => id !== active && tabs.some(tab => tab.id === id && tab.closable !== false))
  }
}
