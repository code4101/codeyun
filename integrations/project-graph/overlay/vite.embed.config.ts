import { defineConfig, mergeConfig } from 'vite';
import upstream from './vite.config';
import path from 'node:path';

// Separate build entry: upstream rendering/editor sources remain untouched.
export default mergeConfig(upstream, defineConfig({
  base: './',
  // Add a presentation output to the pinned provider at build time. Its matching,
  // paging and key lifecycle remain authoritative; no private state is read by the host.
  plugins: [{
    name: 'codeyun-shared-keyboard-hints', enforce: 'pre',
    transform(source, id) {
      const file = id.replaceAll('\\', '/');
      const replace = (before: string, after: string) => {
        if (!source.includes(before)) throw new Error(`Keyboard hint integration needs updating: ${id}`);
        source = source.replace(before, after);
      };
      if (file.endsWith('/shortcutKeysEngine/KeyBindHintEngine.tsx')) {
        replace('if (!Settings.showKeyBindHint || !this.isShowingHint || this.cachedKeyBinds.length === 0) {',
          'if (!Settings.showKeyBindHint || !this.isShowingHint || this.cachedKeyBinds.length === 0) { relayShortcutHints([]);');
        replace('const pageItems = this.cachedKeyBinds.slice(startIndex, endIndex);',
          'const pageItems = this.cachedKeyBinds.slice(startIndex, endIndex); if (relayShortcutHints(pageItems, totalPages > 1 ? `${actualPage + 1}/${totalPages}` : "")) return;');
        return 'import { relayShortcutHints } from "@/codeyun/sharedKeyboardHints";\n' + source;
      }
      if (file.endsWith('/core/Tab.tsx')) {
        const start = source.indexOf('    const startTime = performance.now();');
        const end = source.indexOf('      this.rafHandle = requestAnimationFrame(animationFrame);', start);
        if (start < 0 || end < 0) throw new Error('Frame pacing integration needs updating');
        source = source.slice(0, start) + `    const advance = createFramePacer(() => this.tick(), () => document.hasFocus() ? Settings.maxFps : Settings.maxFpsUnfocused);
    const animationFrame = (time: number) => {
      advance(time);
` + source.slice(end);
        return 'import { createFramePacer } from "@/codeyun/framePacer";\n' + source;
      }
      if (file.endsWith('/render/canvas2d/renderer.tsx')) {
        // Canvas dimensions clear its bitmap. Finish the resize with an unconditional
        // frame inside the renderer itself, independent of focus and idle throttling.
        replace('this.project.canvas.ctx.scale(scale, scale);',
          'this.project.canvas.ctx.scale(scale, scale); this.tick_();');
        replace('private renderSpecialKeys() {',
          'private renderSpecialKeys() { if (relayPressedKeys([...this.project.controller.pressingKeySet])) return;');
        return 'import { relayPressedKeys } from "@/codeyun/sharedKeyboardHints";\n' + source;
      }
    },
  }],
  resolve: { alias: {
    '@tauri-apps/plugin-store': path.resolve('src/codeyun/store.ts'),
    '@tauri-apps/plugin-clipboard-manager': path.resolve('src/codeyun/clipboard.ts'),
    '@tauri-apps/api/image': path.resolve('src/codeyun/image.ts'),
    '@tauri-apps/plugin-shell': path.resolve('src/codeyun/shell.ts'),
  } },
  build: {
    target: 'esnext',
    outDir: 'dist-codeyun',
    rollupOptions: { input: { main: path.resolve('embed.html'), plate: path.resolve('plate.html') } },
  },
}));
