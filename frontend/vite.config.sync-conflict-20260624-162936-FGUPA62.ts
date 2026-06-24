import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    Components({
      dts: 'src/components.d.ts',
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
      output: {
        manualChunks(id) {
          const normalizedId = id.replace(/\\/g, '/')

          if (!normalizedId.includes('/node_modules/')) {
            return undefined
          }

          if (normalizedId.includes('/node_modules/@element-plus/icons-vue/')) {
            return 'element-icons-vendor'
          }

          if (normalizedId.includes('/node_modules/element-plus/')) {
            return 'element-plus-core'
          }

          if (normalizedId.includes('/node_modules/vue/') || normalizedId.includes('/node_modules/vue-router/') || normalizedId.includes('/node_modules/pinia/')) {
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
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['code4101.com'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // rewrite: (path) => path.replace(/^\/api/, ''), // Don't rewrite if backend uses /api prefix
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
