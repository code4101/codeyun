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
      if (file.endsWith('/core/service/Settings.tsx')) {
        // Use the schema default so reset and first use agree; saved choices still apply.
        replace('showBackgroundCartesian: z.boolean().default(true)',
          'showBackgroundCartesian: z.boolean().default(false)');
        return source;
      }
      if (file.endsWith('/components/docked-area.tsx')) {
        // Vue owns the single tab strip (files and auxiliary views). Keep the
        // provider's content/focus/split behavior, but never render another strip.
        const heading = source.match(/        <ProjectTabs\s[\s\S]*?\n        \/>/g);
        if (heading?.length !== 1) throw new Error(`Embedded tab integration needs updating: ${id}`);
        replace(heading[0], '');
        return source;
      }
      if (file.endsWith('/controller/concrete/ControllerCamera.tsx')) {
        // The browser has no Tauri OS plugin, so upstream's Mac camera branch never ran.
        // Enable it for this controller only; other isMac shortcuts keep upstream behaviour.
        source = source.replaceAll('\r\n', '\n');
        replace('import { isIpad, isMac } from "@/utils/platform";',
          'import { isIpad, isMac as upstreamIsMac } from "@/utils/platform";\nimport { classifyMacWheel, isDesktopMacBrowser } from "@/codeyun/browserPlatform";\nimport { macInputDevice } from "@/codeyun/macInputDevice";\nconst isMac = upstreamIsMac || isDesktopMacBrowser;');
        // After the lock/draw gates, before upstream's integer/fractional wheel guess.
        replace('    // 禁用触控板在这里的滚动\n', `    if (isMac) {
      const kind = classifyMacWheel(event, this.project.controller.pressingKeySet.has("control"), macInputDevice());
      if (kind === "wheel") {
        this.mousewheelFunction(event);
        return;
      }
      if (!Settings.enableWindowsTouchPad) return;
      if (kind === "pinch") this.mac.handleTwoFingerScale(event);
      else this.moveCameraByTouchPadTwoFingerMove(event);
      return;
    }
    // 禁用触控板在这里的滚动\n`);
        return source;
      }
      if (file.endsWith('/controller/ControllerClass.tsx')) {
        replace('\n      now - this.lastClickTime < 300 &&',
          '\n      (event.pointerType === "codeyun-touch" ? event.detail === 2 : now - this.lastClickTime < 300) &&');
        // Explicit touch double taps already validate target, travel and timing.
        replace('\n      this.lastClickLocation.distance(new Vector(event.clientX, event.clientY)) < 20',
          '\n      (event.pointerType === "codeyun-touch" || this.lastClickLocation.distance(new Vector(event.clientX, event.clientY)) < 20)');
        return source;
      }
      if (file.endsWith('/concrete/ControllerPenStrokeDrawing.tsx')) {
        replace('const events = coalescedEvents ?? [event];', 'const events = coalescedEvents?.length ? coalescedEvents : [event];');
        return source;
      }
      if (file.endsWith('/concrete/ControllerEntityCreate.tsx')) {
        replace('this.project.controllerUtils.addTextNodeByLocation(pressLocation, true);',
          `this.project.controllerUtils.addTextNodeByLocation(pressLocation, true).then(uuid => {
            if ((event as PointerEvent).pointerType === "codeyun-touch") {
              const node = this.project.stageManager.getTextNodeByUUID(uuid);
              if (node) this.project.controllerUtils.editTextNode(node);
            }
          });`);
        return source;
      }
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
