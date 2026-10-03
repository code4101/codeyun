/** Measure intrinsic menu content, independent of the sidebar's current width. */
export function measureMenuEntryWidth(element: HTMLElement): number {
  const clone = element.cloneNode(true) as HTMLElement;
  // Keep the same parent so nested-menu indentation and scoped styles still apply.
  // A normal row fills its parent: scrollWidth + padding would feed that width
  // back into the sidebar on every navigation/open/close event.
  clone.setAttribute('aria-hidden', 'true');
  clone.inert = true;
  Object.assign(clone.style, {
    position: 'fixed',
    visibility: 'hidden',
    pointerEvents: 'none',
    width: 'max-content',
    minWidth: '0',
    maxWidth: 'none',
    whiteSpace: 'nowrap',
    transition: 'none',
  });
  // Route title spans can have width: 100% and wrapping; measure a single line.
  for (const span of clone.querySelectorAll<HTMLElement>('span')) {
    Object.assign(span.style, { width: 'auto', whiteSpace: 'nowrap' });
  }
  element.parentElement!.appendChild(clone);
  try {
    return Math.ceil(clone.getBoundingClientRect().width);
  } finally {
    clone.remove();
  }
}
