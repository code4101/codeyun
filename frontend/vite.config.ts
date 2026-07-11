import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileViewerRenderers } from '@file-viewer/vite-plugin'
import path from 'path'

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
  '@wangeditor/editor-for-vue',
  'axios',
  'dagre',
  'dompurify',
  'echarts',
  'element-plus',
  'element-plus/es/components/dropdown-item/style/css',
  'element-plus/es/components/dropdown-menu/style/css',
  'element-plus/es/components/dropdown/style/css',
  'element-plus/es/components/link/style/css',
  'handsontable',
  'hyperformula',
  'javascript-lp-solver',
  'jwt-decode',
  'lunar-javascript',
  'marked',
  'pdfjs-dist',
  'pinia',
  'sortablejs',
  'vue',
  'vue-router',
]

// https://vitejs.dev/config/
export default defineConfig({
  cacheDir: 'node_modules/.vite-codeyun',
  plugins: [
    vue(),
    fileViewerRenderers({
      preset: 'auto',
      renderers: ['archive', 'email'],
      // Keep file-viewer renderers local to GenericFileViewer instead of
      // injecting them into every Vite HTML entry and unrelated page.
      inject: false,
      copyAssets: { mode: 'build' },
      missingRenderer: 'warn',
    }),
    Components({
      dts: 'src/components.d.ts',
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
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),
        attendanceFeedback: path.resolve(__dirname, 'attendance-feedback/index.html'),
      },
      output: {
        manualChunks(id) {
          const normalizedId = id.replace(/\\/g, '/')

          if (normalizedId.includes('vite/preload-helper')) {
            return 'vendor'
          }

          if (!normalizedId.includes('/node_modules/')) {
            return undefined
          }

          if (
            normalizedId.includes('/node_modules/@file-viewer/')
            || normalizedId.includes('/node_modules/@kenjiuno/msgreader/')
            || normalizedId.includes('/node_modules/postal-mime/')
            || normalizedId.includes('/node_modules/iconv-lite/')
            || normalizedId.includes('/node_modules/billboard.js/')
          ) {
            return 'file-viewer-vendor'
          }

          if (normalizedId.includes('/node_modules/pdfjs-dist/')) {
            return 'pdfjs-vendor'
          }

          if (normalizedId.includes('/node_modules/@handsontable/vue3/') || normalizedId.includes('/node_modules/handsontable/')) {
            return 'handsontable-vendor'
          }

          if (normalizedId.includes('/node_modules/echarts/') || normalizedId.includes('/node_modules/zrender/')) {
            return 'echarts-vendor'
          }

          if (normalizedId.includes('/node_modules/marked/') || normalizedId.includes('/node_modules/dompurify/')) {
            return 'markdown-vendor'
          }

          if (normalizedId.includes('/node_modules/javascript-lp-solver/')) {
            return 'solver-vendor'
          }

          if (
            normalizedId.includes('/node_modules/hyperformula/')
            || normalizedId.includes('/node_modules/chevrotain/')
            || normalizedId.includes('/node_modules/tiny-emitter/')
            || normalizedId.includes('/node_modules/regexp-to-ast/')
          ) {
            return 'formula-vendor'
          }

          if (normalizedId.includes('/node_modules/@element-plus/icons-vue/')) {
            return 'element-icons-vendor'
          }

          if (normalizedId.includes('/node_modules/element-plus/')) {
            return 'element-plus-core'
          }

          if (
            normalizedId.includes('/node_modules/vue/')
            || normalizedId.includes('/node_modules/@vue/')
            || normalizedId.includes('/node_modules/vue-router/')
            || normalizedId.includes('/node_modules/pinia/')
          ) {
            return 'vue-vendor'
          }

          if (normalizedId.includes('/node_modules/@vue-flow/core/')) {
            return 'vue-flow-core'
          }

          if (normalizedId.includes('/node_modules/@vue-flow/background/') || normalizedId.includes('/node_modules/@vue-flow/controls/')) {
            return 'vue-flow-addons'
          }

          if (normalizedId.includes('/node_modules/elkjs/')) {
            return 'elk-vendor'
          }

          if (normalizedId.includes('/node_modules/dagre/')) {
            return 'dagre-vendor'
          }

          if (normalizedId.includes('/node_modules/@wangeditor/editor-for-vue/')) {
            return 'editor-vue-vendor'
          }

          if (normalizedId.includes('/node_modules/@wangeditor/editor/') || normalizedId.includes('/node_modules/slate/') || normalizedId.includes('/node_modules/snabbdom/')) {
            return 'editor-core-vendor'
          }

          if (normalizedId.includes('/node_modules/lunar-javascript/')) {
            return 'calendar-vendor'
          }

          if (normalizedId.includes('/node_modules/dayjs/')) {
            return 'dayjs-vendor'
          }

          if (normalizedId.includes('/node_modules/axios/') || normalizedId.includes('/node_modules/jwt-decode/') || normalizedId.includes('/node_modules/sortablejs/')) {
            return 'data-vendor'
          }

          return 'vendor'
        },
      },
    },
  },
  optimizeDeps: {
    include: devOptimizedDeps,
  },
  server: {
    // Bind IPv6 as well so localhost -> ::1 does not stall before falling back to IPv4 on Windows.
    host: '::',
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
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
        // rewrite: (path) => path.replace(/^\/api/, ''), // Don't rewrite if backend uses /api prefix
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
