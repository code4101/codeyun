import { createRoot } from 'react-dom/client';
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
let readonly = true;
let lastValue = '';
async function boot() {
  if (parent === window || !session) throw new Error('请从文档编辑器打开');
  await i18next.use(initReactI18next).init({ lng: 'zh_CN', defaultNS: '', resources: { zh_CN: (await import('@/locales/zh_CN.yml')).default } });
  // No canvas services, workspace, keyboard bindings or filesystem are started here.
  document.documentElement.style.colorScheme = 'light';
  window.addEventListener('message', event => {
    const message = event.data;
    if (event.source !== parent || event.origin !== location.origin || message?.channel !== channel || message.version !== 1 || message.session !== session || message.type !== 'load') return;
    try {
      const { value, readOnly } = message.payload;
      if (!Array.isArray(value) || !value.length) throw new Error('Plate 正文格式无效');
      readonly = Boolean(readOnly);
      lastValue = JSON.stringify(value);
      root.render(<Provider store={store}><PlateDocumentEditor key={++generation} value={value as Value} readOnly={readonly} onChange={next => {
        const serialized = JSON.stringify(next);
        if (readonly || serialized === lastValue) return;
        lastValue = serialized;
        send('change', { value: next });
      }} /></Provider>);
    } catch (error) { send('error', { message: String(error) }); }
  });
  send('ready');
}
boot().catch(error => send('error', { message: String(error) }));
