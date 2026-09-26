import { atom, useAtom } from 'jotai';
import { useEffect, useRef, useState, type ReactNode } from 'react';

type Layout = { position: 'right' | 'bottom'; width: number; height: number };
const storageKey = 'codeyun.pg.inspector-layout.v1';
function readLayout(): Layout {
  const fallback: Layout = { position: 'right', width: Math.min(420, Math.max(280, innerWidth * .26)), height: 300 };
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved && ['right', 'bottom'].includes(saved.position) && Number.isFinite(saved.width) && saved.width > 0 && Number.isFinite(saved.height) && saved.height > 0) return saved;
  } catch { /* Ignore malformed UI preferences; document content is stored separately. */ }
  return fallback;
}
const layoutAtom = atom<Layout>(readLayout());
function persist(layout: Layout) { localStorage.setItem(storageKey, JSON.stringify(layout)); }

export function InspectorPositionControl() {
  const [layout, setLayout] = useAtom(layoutAtom);
  return <select aria-label="正文位置" className="rounded border border-border bg-background px-1 py-0.5 text-xs"
    value={layout.position} onChange={event => {
      const next = { ...layout, position: event.target.value as Layout['position'] };
      setLayout(next); persist(next);
    }}>
    <option value="right">右侧</option><option value="bottom">底部</option>
  </select>;
}

/** Resizing changes container geometry only: keep both editor trees mounted so
 * docking never resets the current document, selection, or rich-text undo history. */
export function InspectorSplit({ children, panel }: { children: ReactNode; panel: ReactNode }) {
  const [layout, setLayout] = useAtom(layoutAtom);
  const root = useRef<HTMLDivElement>(null);
  const drag = useRef<{ start: number; size: number; latest: Layout } | null>(null);
  const [bounds, setBounds] = useState({ width: innerWidth, height: innerHeight });
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setBounds({ width: entry.contentRect.width, height: entry.contentRect.height }));
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const right = layout.position === 'right';
  const total = right ? bounds.width : bounds.height;
  const min = Math.min(right ? 220 : 120, total * .3);
  const max = Math.max(min, total - Math.min(right ? 260 : 200, total * .4) - 6);
  const clamp = (size: number) => Math.round(Math.min(max, Math.max(min, size)));
  const size = clamp(right ? layout.width : layout.height);
  const change = (size: number) => {
    const next = { ...layout, [right ? 'width' : 'height']: clamp(size) };
    setLayout(next);
    return next;
  };
  return <div ref={root} className="absolute inset-0 flex" style={{ flexDirection: right ? 'row' : 'column' }}>
    <div className="relative min-h-0 min-w-0 flex-1">{children}</div>
    <div role="separator" tabIndex={0} aria-label={right ? '调整正文宽度' : '调整正文高度'}
      aria-orientation={right ? 'vertical' : 'horizontal'} aria-valuemin={Math.round(min)} aria-valuemax={Math.round(max)} aria-valuenow={size}
      className="z-30 shrink-0 bg-border hover:bg-primary focus:bg-primary focus:outline-none"
      style={{ flexBasis: 6, cursor: right ? 'col-resize' : 'row-resize', touchAction: 'none' }}
      onPointerDown={event => {
        event.preventDefault(); event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { start: right ? event.clientX : event.clientY, size, latest: layout };
      }}
      onPointerMove={event => {
        if (!drag.current) return;
        event.stopPropagation();
        drag.current.latest = change(drag.current.size + drag.current.start - (right ? event.clientX : event.clientY));
      }}
      onPointerUp={event => {
        if (!drag.current) return;
        persist(drag.current.latest); drag.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onLostPointerCapture={() => { if (drag.current) { persist(drag.current.latest); drag.current = null; } }}
      onKeyDown={event => {
        const increase = right ? 'ArrowLeft' : 'ArrowUp';
        const decrease = right ? 'ArrowRight' : 'ArrowDown';
        if (![increase, decrease, 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        persist(change(event.key === 'Home' ? min : event.key === 'End' ? max : size + (event.key === increase ? 20 : -20)));
      }} />
    <div className="relative min-h-0 min-w-0 shrink-0" style={{ flexBasis: size }}>{panel}</div>
  </div>;
}
