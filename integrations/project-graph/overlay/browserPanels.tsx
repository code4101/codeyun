import { useEffect, useState } from 'react';
import { Project, ProjectState } from '@/core/Project';
import { TabWorkspace } from '@/core/TabWorkspace';
import { store, tabsAtom } from '@/state';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { getBuiltInToolWindowEntries } from '@/core/service/dataManageService/aiEngine/BuiltInToolWindowAdapter';
import { downloadBlob, pickFiles } from './browserFileCommands';
import mime from 'mime';
import { toast } from 'sonner';
import z from 'zod/v4';

function Attachments({ project }: { project: Project }) {
  const [entries, setEntries] = useState(() => [...project.attachments]);
  const [urls, setUrls] = useState(new Map<string, string>());
  const refresh = () => setEntries([...project.attachments]);
  useEffect(() => {
    const next = new Map(entries.map(([id, blob]) => [id, URL.createObjectURL(blob)]));
    setUrls(next); return () => next.forEach(url => URL.revokeObjectURL(url));
  }, [entries]);
  const remove = (ids: string[]) => {
    ids.forEach(id => project.attachments.delete(id));
    if (ids.length) project.projectState = ProjectState.Unsaved;
    refresh();
  };
  const run = (action: () => Promise<void>) => { void action().catch(error => toast.error(String(error))); };
  return <div style={{ padding: 20, height: '100%', overflow: 'auto' }}>
    <h1>附件管理器</h1>
    <div className="flex gap-2 my-3">
      <Button onClick={() => run(async () => {
        for (const file of await pickFiles('')) project.addAttachment(file);
        refresh();
      })}>添加附件</Button>
      <Button variant="outline" onClick={refresh}>刷新</Button>
      <Button variant="outline" onClick={() => run(async () => {
        const used = new Set(project.stageManager.getEntities().flatMap(node => 'attachmentId' in node ? [node.attachmentId] : []));
        const unused = [...project.attachments.keys()].filter(id => !used.has(id));
        if (unused.length && await Dialog.confirm('清理附件', `删除 ${unused.length} 个未被节点引用的附件？此操作不可撤销。`, { destructive: true })) remove(unused);
      })}>清理未引用附件</Button>
    </div>
    {!entries.length && <p>暂无附件</p>}
    <div className="flex flex-wrap gap-3">{entries.map(([id, blob]) => <article key={id} className="border rounded p-3" style={{ width: 260 }}>
      {blob.type.startsWith('image/') && <img src={urls.get(id)} alt="附件预览" style={{ width: '100%', height: 140, objectFit: 'contain' }} />}
      <p style={{ overflowWrap: 'anywhere' }}>{id}</p><p>{blob.type || '文件'} · {blob.size} B</p>
      <div className="flex gap-2 mt-2"><Button variant="outline" onClick={() => downloadBlob(blob, `${id}.${mime.getExtension(blob.type) || 'bin'}`)}>下载</Button>
      <Button variant="outline" onClick={() => run(async () => {
        if (await Dialog.confirm('删除附件', '引用此附件的节点将无法显示。此操作不可撤销。', { destructive: true })) remove([id]);
      })}>删除</Button></div>
    </article>)}</div>
  </div>;
}

function AITools() {
  const [query, setQuery] = useState('');
  const entries = getBuiltInToolWindowEntries();
  return <div style={{ padding: 20, height: '100%', overflow: 'auto' }}>
    <h1>AI 工具 · 内置工具目录</h1>
    <p>原版的 {entries.length} 个画布工具。AI 会话服务尚未接入，桌面 MCP 和本地 Skills 暂不可用。</p>
    <input aria-label="搜索 AI 内置工具" placeholder="搜索工具" value={query} onChange={event => setQuery(event.target.value)} className="border rounded p-2 my-3 w-full" />
    {entries.filter(entry => `${entry.name} ${entry.description}`.toLowerCase().includes(query.toLowerCase())).map(entry => <details key={entry.name} className="border rounded p-3 mb-2">
      <summary>{entry.name} — {entry.description}</summary>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(z.toJSONSchema(entry.parameters), null, 2)}</pre>
    </details>)}
  </div>;
}

/** Panels use the same public dock/tab contract as browser settings. */
export function openBrowserPanel(project: Project, kind: 'attachments' | 'ai-tools') {
  const title = kind === 'attachments' ? '附件管理器' : 'AI 工具';
  const existing = store.get(tabsAtom).find(tab => !tab.closing && tab.title === title);
  if (existing) { TabWorkspace.focus(existing.id); return; }
  TabWorkspace.create({ title, children: kind === 'attachments' ? <Attachments project={project} /> : <AITools />,
    layout: 'docked', contextResourceTab: project, closeOnEscape: false });
}
