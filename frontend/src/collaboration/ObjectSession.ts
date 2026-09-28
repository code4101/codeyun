/** CodeYun protocol 1 client. No editor, rendering or document-format dependency.
 * Providers own capture/apply/rebase and choose lease granularity. The shared
 * session owns authentication refresh, receipts, reconnect drafts and safe undo.
 * Browser drafts are recovery assistance, not a guarantee against tab/device loss.
 */
import { changed, differences, equal, rebaseObjects, type Objects, type Change } from './objectState.ts';
export type Credentials = { url: string; token: string };
type Peer = { id: string; userId: number; name: string; color: string; cursor?: { x: number; y: number }; selection: string[] };
type Commit = { type: 'commit'; mutationId: string; changes: Change[]; tokens: Record<string, string> };
type Draft = { base: Objects; working: Objects; pending?: Commit };


export interface ObjectDocument {
  capture(): Promise<Objects>;
  apply(before: Objects, after: Objects): void;
  rebase?(base: Objects, local: Change[], remote: Objects): Change[];
  isStructuralObject?(id: string): boolean;
  setEditable(editable: boolean): void;
  setSaveState(state: 'saved' | 'unsaved'): void;
}

export class ObjectSession {
  protected socket?: WebSocket;
  protected disposed = false;
  protected connected = false;
  protected blocked = false;
  protected peerId = '';
  protected revision = 0;
  protected base: Objects = {};
  protected tokens: Record<string, string> = {};
  protected peers: Peer[] = [];
  protected locks: Record<string, { peerId: string; expiresIn: number }> = {};
  protected queue = Promise.resolve();
  protected saving?: Promise<void>;
  protected pending?: Commit;
  protected draft?: Draft;
  protected undoStack: Change[][] = [];
  protected redoStack: Change[][] = [];
  protected requests = new Map<string, { resolve: (value: any) => void; reject: (reason: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  protected interval!: ReturnType<typeof setInterval>;
  protected heartbeat!: ReturnType<typeof setInterval>;
  protected retry?: ReturnType<typeof setTimeout>;
  protected message = '正在连接协作服务…';
  protected dirty = false;
  protected restoringHistory = false;
  private initialized = false;
  private resetting = false;
  get hasUnsaved() { return this.dirty || !!this.pending || this.blocked; }
  markDirty() { if (!this.readOnly) { this.dirty = true; void this.preserve().catch(error => this.fail(error)); } }


  constructor(protected readonly readOnly: boolean,
    private readonly credentials: () => Promise<Credentials>, private readonly draftKey: string,
    protected readonly publish: (type: string, payload: any) => void,
    private readonly document: ObjectDocument) {
    try { this.draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null') ?? undefined; this.pending = this.draft?.pending; }
    catch { this.message = '本地草稿读取失败，请保留当前页面并下载文件'; this.blocked = true; }
    this.document.setEditable(false);
  }
  /** Call after the provider's UI has initialized; dispose when the document closes. */
  start() {
    window.addEventListener('offline', this.offline);
    this.interval = setInterval(() => { this.render(); if (this.canFlush()) void this.flush().catch(() => {}); }, 300);
    this.heartbeat = setInterval(() => {
      if (this.connected && !this.readOnly) void this.request({ type: 'renew', tokens: this.tokens }).then(result => {
        this.tokens = result.tokens; this.publishAccess();
      }).catch(() => {});
    }, 4000);
    void this.connect();
  }
  protected render() {}
  protected retainedObjects(): string[] { return []; }
  protected canFlush() { return true; }
  protected rebase(base: Objects, local: Change[], remote: Objects) {
    return (this.document.rebase ?? rebaseObjects)(base, local, remote);
  }
  access(id: string) {
    const lock = this.locks[id], owner = this.peers.find(peer => peer.id === lock?.peerId);
    return { readOnly: this.readOnly || !this.connected || this.blocked || !this.tokens[id],
      lockedBy: owner && owner.id !== this.peerId ? owner.name : undefined };
  }
  protected publishAccess() { this.publish('collaboration-access', { connected: this.connected, blocked: this.blocked }); }
  private async connect() {
    if (this.disposed) return;
    if (!navigator.onLine) { this.retry = setTimeout(() => void this.connect(), 1500); return; }
    try {
      const credentials = await this.credentials();
      if (this.disposed) return;
      const socket = this.socket = new WebSocket(credentials.url);
      socket.onopen = () => socket.send(JSON.stringify({ type: 'auth', token: credentials.token,
        pendingMutationIds: this.pending ? [this.pending.mutationId] : [] }));
      socket.onmessage = event => {
        this.queue = this.queue.then(() => this.receive(JSON.parse(event.data))).catch(error => this.fail(error));
      };
      socket.onclose = () => {
        if (this.socket !== socket) return;
        this.connected = false; this.tokens = {}; this.document.setEditable(false);
        this.message = '连接已断开，编辑已暂停；正在重连…';
        for (const item of this.requests.values()) { clearTimeout(item.timer); item.reject(new Error('协作连接中断，本地修改已保留')); }
        this.requests.clear(); if (!this.resetting) void this.preserve(); this.publishAccess(); this.render();
        if (!this.disposed) this.retry = setTimeout(() => void this.connect(), 1500);
      };
    } catch (error) {
      this.message = String(error); this.render();
      if (!this.disposed) this.retry = setTimeout(() => void this.connect(), 3000);
    }
  }

  private async receive(message: any) {
    if (message.type === 'joined') {
      this.peerId = message.peerId; this.revision = message.revision;
      const current = await this.document.capture(), server = message.objects as Objects;
      let projection = server;
      if (this.draft) {
        const baseline = this.draft.pending && message.accepted?.includes(this.draft.pending.mutationId)
          ? changed(this.draft.base, this.draft.pending.changes) : this.draft.base;
        try { projection = changed(server, this.rebase(baseline, differences(baseline, this.draft.working), server)); }
        catch {
          this.blocked = true; projection = this.draft.working;
          this.message = '重连后发现对象冲突。本地草稿已保留，请下载草稿后重新同步。';
        }
      }
      this.document.apply( current, projection);
      this.base = server; this.pending = undefined; this.connected = true; this.initialized = true; this.resetting = false;
      this.document.setEditable(!this.readOnly && !this.blocked);
      if (!this.blocked) this.message = this.readOnly ? '只读 · 已连接协作' : '协作已连接';
      await this.preserve(); this.publishAccess(); this.render();
    } else if (message.type === 'presence') {
      this.peers = message.peers; this.locks = message.locks; this.render();
    } else if (message.type === 'cursor') {
      const peer = this.peers.find(item => item.id === message.peerId);
      if (peer) { peer.cursor = message.cursor; peer.selection = message.selection; }
    } else if (message.type === 'commit' && message.revision > this.revision) {
      if (message.revision !== this.revision + 1) { this.socket?.close(); return; }
      const own = this.pending?.mutationId === message.mutationId;
      if (!own && !this.blocked) {
        const current = await this.document.capture(), local = differences(this.base, current);
        const remote = changed(this.base, message.changes);
        try { this.document.apply( current, changed(remote, this.rebase(this.base, local, remote))); }
        catch {
          await this.fail(new Error('远端修改与本地草稿冲突，请下载草稿后重新同步'));
        }
      }
      this.base = changed(this.base, message.changes); this.revision = message.revision;
      if (own) this.pending = undefined;
    }
    const request = this.requests.get(message.id);
    if (request && ['locked', 'renewed', 'commit', 'error'].includes(message.type)) {
      clearTimeout(request.timer); this.requests.delete(message.id);
      message.type === 'error' ? request.reject(Object.assign(new Error(typeof message.detail === 'string' ? message.detail : message.detail?.message ?? '协作操作失败'),
        { code: message.code, objectId: message.detail?.objectId })) : request.resolve(message);
    } else if (message.type === 'error') {
      this.message = typeof message.detail === 'string' ? message.detail : '协作操作失败'; this.render();
    }
  }

  private request(payload: Record<string, any>): Promise<any> {
    if (!this.connected || this.socket?.readyState !== WebSocket.OPEN) return Promise.reject(new Error('协作连接尚未恢复'));
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.requests.delete(id); reject(new Error('协作响应超时，正在重新同步')); this.socket?.close(); }, 10000);
      this.requests.set(id, { resolve, reject, timer }); this.socket!.send(JSON.stringify({ ...payload, id }));
    });
  }
  protected send(payload: Record<string, any>) {
    if (this.connected && this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(payload));
  }
  async acquire(ids: string[]) {
    if (this.readOnly || this.blocked || !this.connected) throw new Error('当前无法编辑，请等待协作连接恢复');
    const missing = [...new Set(ids)].filter(id => !this.tokens[id]);
    if (missing.length) {
      for (let attempt = 0; ; attempt++) {
        try { Object.assign(this.tokens, (await this.request({ type: 'acquire', objects: missing })).tokens); break; }
        catch (error) {
          const conflict = error as Error & { code?: number; objectId?: string };
          if (conflict.code !== 423 || !conflict.objectId || !this.document.isStructuralObject?.(conflict.objectId) || attempt >= 40) throw error;
          await new Promise(resolve => setTimeout(resolve, 50));
        }
      }
    }
    this.publishAccess();
  }
  async flush() {
    // A navigation flush must include edits made while its first ACK was in
    // flight. Autosave and host navigation share the same serialized writer.
    do { await this.flushOnce(); }
    while (this.connected && !this.blocked && !this.readOnly &&
      differences(this.base, await this.document.capture()).length > 0);
  }
  private async flushOnce() {
    if (this.saving) return this.saving;
    if (this.readOnly) return;
    if (!this.connected || this.blocked) {
      if (this.hasUnsaved) throw new Error('协作尚未同步，本地修改已保留，请重连或下载草稿');
      return;
    }
    this.saving = (async () => {
      let current = await this.document.capture(), changes = differences(this.base, current);
      if (!changes.length) { this.dirty = false; this.releaseUnselected(); return; }
      await this.preserve(current);
      if (this.blocked) throw new Error(this.message);
      this.publish('status', { state: 'saving' });
      await this.acquire(changes.map(change => change.id));
      // While awaiting a short structural lease, remote disjoint insertions may
      // have been rebased into the working document. Use those new preconditions.
      current = await this.document.capture(); changes = differences(this.base, current);
      if (!changes.length) { this.dirty = false; this.releaseUnselected(); return; }
      await this.acquire(changes.map(change => change.id));
      this.pending = { type: 'commit', mutationId: crypto.randomUUID(), changes, tokens: { ...this.tokens } };
      await this.preserve(current);
      await this.request(this.pending);
      this.undoStack.push(changes); this.redoStack = [];
      await this.preserve(); this.releaseUnselected();
      this.dirty = differences(this.base, await this.document.capture()).length > 0;
      this.document.setSaveState(this.dirty ? 'unsaved' : 'saved');
      this.publish('status', { state: this.dirty ? 'unsaved' : 'saved' });
    })().catch(async error => { if (this.connected) await this.fail(error); throw error; }).finally(() => { this.saving = undefined; });
    return this.saving;
  }
  private releaseUnselected() {
    const selected = new Set(this.retainedObjects());
    const keys = Object.keys(this.tokens).filter(key => !selected.has(key));
    if (keys.length) { this.send({ type: 'release', objects: keys }); keys.forEach(key => delete this.tokens[key]); }
  }
  protected async preserve(working?: Objects) {
    if (!this.initialized || this.readOnly || this.resetting) return;
    working ??= await this.document.capture();
    if (!this.blocked && !this.pending && !differences(this.base, working).length) {
      sessionStorage.removeItem(this.draftKey); this.draft = undefined; return;
    }
    this.draft = { base: this.base, working, pending: this.pending };
    try { sessionStorage.setItem(this.draftKey, JSON.stringify(this.draft)); }
    catch { this.message = '浏览器草稿空间不足，请立即下载当前文件'; this.blocked = true; this.document.setEditable(false); this.render(); }
  }
  private async fail(error: unknown) {
    this.blocked = true; this.document.setEditable(false); this.message = String(error);
    await this.preserve(); this.publishAccess(); this.publish('error', { message: this.message }); this.render();
  }
  private async history(from: Change[][], to: Change[][]) {
    if (this.restoringHistory) return;
    this.restoringHistory = true;
    try {
    await this.flush(); const previous = from[from.length - 1]; if (!previous) return;
    const action = (async () => {
    const inverse = previous.map(change => ({ id: change.id, before: change.after, after: change.before }));
    if (inverse.some(change => !equal(this.base[change.id] ?? null, change.before))) throw new Error('对象已被其他人修改，不能直接撤销；当前内容已保留');
    await this.acquire(inverse.map(change => change.id));
    if (inverse.some(change => !equal(this.base[change.id] ?? null, change.before))) throw new Error('对象在获取编辑锁时已更新，撤销未执行');
    const current = await this.document.capture();
    this.document.apply( current, changed(current, inverse));
    this.pending = { type: 'commit', mutationId: crypto.randomUUID(), changes: inverse, tokens: { ...this.tokens } };
    await this.preserve(); await this.request(this.pending); from.pop(); to.push(inverse); await this.preserve();
    })();
    this.saving = action;
    try { await action; }
    catch (error) { if (this.pending && this.connected) await this.fail(error); throw error; }
    finally { this.saving = undefined; }
    } finally { this.restoringHistory = false; }
  }
  undo() { return this.history(this.undoStack, this.redoStack); }
  redo() { return this.history(this.redoStack, this.undoStack); }
  private offline = () => { this.socket?.close(); };


  /** The provider must export/preserve the working copy before discarding it. */
  protected resetFromServer() {
    this.resetting = true;
    this.draft = undefined; this.pending = undefined; this.blocked = false;
    sessionStorage.removeItem(this.draftKey); this.socket?.close();
  }
  dispose() {
    this.disposed = true; clearInterval(this.interval); clearInterval(this.heartbeat); clearTimeout(this.retry);
    window.removeEventListener('offline', this.offline);
    void this.preserve(); this.socket?.close();
  }
}
