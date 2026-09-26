import { defineConfig, mergeConfig } from 'vite';
import upstream from './vite.config';
import path from 'node:path';

// Separate build entry: upstream rendering/editor sources remain untouched.
export default mergeConfig(upstream, defineConfig({
  base: './',
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
