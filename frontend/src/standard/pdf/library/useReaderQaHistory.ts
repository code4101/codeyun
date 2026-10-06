import { ref, watch } from 'vue'
import { fetchQaConversation, saveQaConversation, type QaScope, type QaTurn } from '@/api/libraryQa'

/** 服务器是对话权威；本地仅保留输入和未保存草稿，按账号、书籍隔离。 */
export function useReaderQaHistory(scope: () => QaScope, owner: () => number | undefined) {
  const turns = ref<QaTurn[]>([]), question = ref(''), loading = ref(false), error = ref('')
  type Draft = { key: string; scope: QaScope; owner: number; turns: QaTurn[]; question: string; revision: number; loaded: boolean; pending?: Promise<void> }
  let current: Draft | undefined, generation = 0, restoring = false
  const key = (id: number, value: QaScope) => `codeyun.reader-qa:${JSON.stringify([id, value.resource_type, value.resource_id])}`
  function remember(draft: Draft) {
    try { localStorage.setItem(draft.key, JSON.stringify({ turns: draft.turns, question: draft.question, revision: draft.revision })) } catch { /* 服务器保存不依赖浏览器存储。 */ }
  }
  function snapshot() {
    if (!current || restoring) return
    current.turns = JSON.parse(JSON.stringify(turns.value)) as QaTurn[]
    current.question = question.value
    remember(current)
  }
  watch([turns, question], snapshot, { deep: true, flush: 'sync' })

  async function load(discardDraft = false) {
    snapshot()
    const version = ++generation, id = owner(), value = scope()
    current = undefined; restoring = true; turns.value = []; question.value = ''; error.value = ''
    if (id === undefined || !value.resource_id) { loading.value = false; restoring = false; return }
    const draft: Draft = { key: key(id, value), scope: { ...value }, owner: id, turns: [], question: '', revision: 0, loaded: false }
    let local: { turns: QaTurn[]; question: string; revision: number } | undefined
    if (!discardDraft) {
      try {
        const saved = JSON.parse(localStorage.getItem(draft.key) || 'null')
        if (saved && Array.isArray(saved.turns) && Number.isInteger(saved.revision)
          && saved.turns.every((turn: QaTurn) => ['user', 'assistant'].includes(turn.role) && typeof turn.content === 'string' && typeof turn.location === 'string')) {
          local = saved; turns.value = saved.turns; question.value = typeof saved.question === 'string' ? saved.question : ''
        }
      } catch { /* 忽略损坏的本地副本。 */ }
    }
    current = draft; loading.value = true
    let shouldSave = false
    try {
      const page = await fetchQaConversation(value)
      if (version !== generation || owner() !== id) return
      draft.revision = page.revision; draft.loaded = true
      const changed = local && JSON.stringify(local.turns) !== JSON.stringify(page.turns)
      shouldSave = Boolean(changed && local!.revision === page.revision)
      turns.value = changed ? local!.turns : page.turns
      if (changed && local!.revision !== page.revision) {
        draft.revision = local!.revision
        error.value = '其他窗口已更新，本地草稿已恢复；请复制需要的内容后读取最新对话。'
      }
    } catch {
      if (version === generation) error.value = '对话读取失败，本地草稿已保留，请重试。'
    } finally {
      if (version === generation) {
        restoring = false; loading.value = false; snapshot()
        if (shouldSave && draft.loaded && !error.value) void save()
      }
    }
  }

  async function save() {
    snapshot()
    const draft = current
    if (!draft || !draft.loaded || draft.owner !== owner()) return
    const payload = JSON.parse(JSON.stringify(draft.turns)) as QaTurn[]
    while (draft.pending) await draft.pending
    if (draft.owner !== owner()) return
    draft.pending = (async () => {
      try {
        const page = await saveQaConversation({ ...draft.scope, turns: payload, revision: draft.revision })
        draft.revision = page.revision
        remember(draft)
        if (current === draft) error.value = ''
      } catch (cause) {
        remember(draft)
        if (current === draft) error.value = (cause as { response?: { data?: { detail?: string } } }).response?.data?.detail || '对话保存失败，本地草稿已保留，可重试保存。'
      }
    })()
    await draft.pending; draft.pending = undefined
  }
  watch(() => JSON.stringify([owner(), scope()]), () => { void load() }, { immediate: true, flush: 'sync' })
  return { turns, question, loading, error, save, reload: () => load(true), retry: () => current?.loaded ? save() : load() }
}
