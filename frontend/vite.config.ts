import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileViewerRenderers } from '@file-viewer/vite-plugin'
import path from 'path'
import { existsSync, readdirSync } from 'node:fs'
import { offlineLibraryPlugin } from './tooling/offlineLibraryPlugin'
import { viewportUnitFallback } from './tooling/viewportUnitFallback'
import { publicViewportFallback } from './tooling/publicViewportFallback'

const devBackendTarget = process.env.CODEYUN_DEV_BACKEND_TARGET || 'http://127.0.0.1:8000'

// 自动导入的组件样式不在源码 import 扫描中。启动时一次性预构建，避免切换
// 到尚未访问的页面时反复优化依赖，导致已转换模块中的 hash 过期（504）。
const elementPlusComponents = path.resolve(__dirname, 'node_modules/element-plus/es/components')
const elementPlusStyles = readdirSync(elementPlusComponents).filter(name =>
  existsSync(path.join(elementPlusComponents, name, 'style/css.mjs')),
).map(name => `element-plus/es/components/${name}/style/css`)

const devOptimizedDeps = [
  '@element-plus/icons-vue',
  '@file-viewer/preset-lite',
  '@file-viewer/preset-office',
  '@file-viewer/renderer-archive',
  '@file-viewer/renderer-email',
  '@file-viewer/vue3',
  '@handsontable/vue3',
  '@vue-flow/background',
  '@vue-flow/controls',
  '@vue-flow/core',
  '@wangeditor/editor',
  'axios',
  'dagre',
  'dompurify',
  'echarts',
  'element-plus',
  ...elementPlusStyles,
  'handsontable',
  'hyperformula',
  'javascript-lp-solver',
  'jwt-decode',
  'lunar-javascript',
  'marked',
  'md-editor-v3',
  'pdfjs-dist',
  'pinia',
  'sortablejs',
  'turndown',
  'vue',
  'vue-router',
]

// https://vitejs.dev/config/
export default defineConfig(({ command }) => ({
  cacheDir: 'node_modules/.vite-codeyun',
  css: { postcss: { plugins: [viewportUnitFallback()] } },
  // Keep classic worker bundles self-contained; administrative data stays off the page's initial bundle.
  worker: { rollupOptions: { output: { inlineDynamicImports: true } } },
  plugins: [
    vue(),
    publicViewportFallback(),
    offlineLibraryPlugin(__dirname),
    fileViewerRenderers({
      preset: 'auto',
      renderers: ['archive', 'email'],
      // Keep file-viewer renderers local to GenericFileViewer instead of
      // injecting them into every Vite HTML entry and unrelated page.
      inject: false,
      // Its renderer-oriented manual chunks also absorb shared runtime helpers,
      // which can make the app entry import Office renderers through those helpers.
      chunkStrategy: 'none',
      copyAssets: { mode: 'build' },
      missingRenderer: 'warn',
    }),
    Components({
      // Declarations are a development/typecheck aid. Writing the same file while
      // Rollup transforms multiple production inputs is racy on Windows sync roots.
      dts: command === 'serve' ? 'src/components.d.ts' : false,
      exclude: [/sync-conflict/],
      globsExclude: ['**/*sync-conflict*'],
      resolvers: [
        ElementPlusResolver({
          importStyle: 'css',
          directives: true,
        }),
      ],
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    manifest: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),
        attendanceFeedback: path.resolve(__dirname, 'attendance-feedback/index.html'),
      },
      // Let Rollup split shared dependencies by their actual import graph.
      // A catch-all manual vendor chunk joins unrelated lazy features into
      // the homepage's static dependencies (including editors and viewers).
    },
  },
  optimizeDeps: {
    include: devOptimizedDeps,
  },
  server: {
    // CodeYun is used from other devices on the LAN; keep the dev entry reachable by IPv4.
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: ['code4101.com'],
    watch: {
      ignored: [
        '**/dist/**',
        '**/node_modules/.vite/**',
        '**/node_modules/.vite-codeyun/**',
        '**/public/flyfish-viewer-assets.json',
        '**/public/vendor/**',
      ],
    },
    proxy: {
      '/api': {
        target: devBackendTarget,
        changeOrigin: true,
        ws: true,
        // rewrite: (path) => path.replace(/^\/api/, ''), // Don't rewrite if backend uses /api prefix
      },
      '/static': {
        target: devBackendTarget,
        changeOrigin: true,
      },
    },
  },
}))
