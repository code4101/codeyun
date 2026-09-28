import { createRoot } from 'react-dom/client';
import { useLayoutEffect, type ReactNode } from 'react';
import { Provider } from 'jotai';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import type { Value } from 'platejs';
import { store } from '@/state';
import PlateDocumentEditor from './PlateDocumentEditor';
import '@/css/index.css';

const channel = 'codeyun.plate';
const session = new URLSearchParams(location.search).get('session');
const send = (type: string, payload?: unknown) => parent.postMessage({ channel, version: 1, session, type, payload }, location.origin);
const root = createRoot(document.getElementById('root')!);
let generation = 0;
// A ready bridge is not a painted editor. Reveal only after the themed content
// commits, so a freshly mounted iframe cannot expose its default light surface.
function Presented({ children }: { children: ReactNode }) {
  useLayoutEffect(() => { send('presented'); }, []);
  return children;
}
async function boot() {
  if (parent === window || !session) throw new Error('请从文档编辑器打开');
  await i18next.use(initReactI18next).init({ lng: 'zh_CN', defaultNS: '', resources: { zh_CN: (await import('@/locales/zh_CN.yml')).default } });
  // No canvas services, workspace, keyboard bindings or filesystem are started here.
  document.documentElement.style.colorScheme = 'light';
  window.addEventListener('message', event => {
    const message = event.data;
    if (event.source !== parent || event.origin !== location.origin || message?.channel !== channel || message.version !== 1 || message.session !== session) return;
    if (message.type === 'theme') {
      const theme = message.payload;
      document.documentElement.style.colorScheme = theme.dark ? 'dark' : 'light';
      document.documentElement.classList.toggle('dark', Boolean(theme.dark));
      // This entry does not start upstream Themes. Supply the interaction tokens
      // used by Plate selection, caret and focus rings as well as surface colors.
      for (const [name, color] of Object.entries({ background: theme.background, foreground: theme.text,
        primary: theme.text, 'primary-foreground': theme.background, ring: theme.text, brand: theme.text,
        card: theme.panel, 'card-foreground': theme.text, popover: theme.panel, 'popover-foreground': theme.text,
        muted: theme.panel, accent: theme.panel, 'accent-foreground': theme.text, border: theme.border, input: theme.border })) document.documentElement.style.setProperty(`--${name}`, String(color));
      return;
    }
    if (message.type === 'flush') { parent.postMessage({ channel, version: 1, session, type: 'flushed', id: message.id }, location.origin); return; }
    if (message.type !== 'load') return;
    try {
      const { value, readOnly, contentKey, loadId } = message.payload;
      if (!Array.isArray(value) || !value.length) throw new Error('Plate 正文格式无效');
      const readonly = Boolean(readOnly);
      let lastValue = JSON.stringify(value);
      const currentGeneration = ++generation;
      root.render(<Provider store={store}><Presented key={currentGeneration}><PlateDocumentEditor value={value as Value} readOnly={readonly} onChange={next => {
        if (currentGeneration !== generation) return;
        const serialized = JSON.stringify(next);
        if (readonly || serialized === lastValue) return;
        lastValue = serialized;
        send('change', { value: next, contentKey, loadId });
      }} /></Presented></Provider>);
    } catch (error) { send('error', { message: String(error) }); }
  });
  send('ready');
}
boot().catch(error => send('error', { message: String(error) }));
