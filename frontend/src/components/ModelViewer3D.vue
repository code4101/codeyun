<script setup lang="ts">
import { ref } from 'vue'
import '@google/model-viewer'
import type { ModelViewerElement } from '@google/model-viewer'

/** Model Viewer owns rendering and mouse/touch/keyboard controls. This adapter
 * only exposes camera presets and image export through its public API. */
defineProps<{ src: string; alt: string }>()
const emit = defineEmits<{ ready: []; error: [] }>()
const element = ref<ModelViewerElement>()
const loaded = ref(false)
const failed = ref(false)
const progress = ref(0)
const generation = ref(0)

function onLoad() { loaded.value = true; failed.value = false; emit('ready') }
function onError() { failed.value = true; loaded.value = false; emit('error') }
function retry() { failed.value = false; progress.value = 0; generation.value++ }
function focus(height: number, distance: number, theta = 0, phi = 90) {
  const model = element.value
  if (!model?.loaded) return
  const size = model.getDimensions(), center = model.getBoundingBoxCenter()
  model.cameraTarget = `${center.x}m ${center.y + size.y * (height - 0.5)}m ${center.z}m`
  model.cameraOrbit = `${theta}deg ${phi}deg ${size.y * distance}m`
}
function angle(theta: number, phi = 90) {
  const model = element.value
  if (!model?.loaded) return
  // Preserve both the user's panned target and zoom when changing direction.
  model.cameraTarget = model.getCameraTarget().toString()
  model.cameraOrbit = `${theta}deg ${phi}deg ${model.getCameraOrbit().radius}m`
}
function zoom(steps: number) { element.value?.zoom(steps) }
async function snapshot() {
  if (!element.value?.loaded) throw new Error('模型尚未加载')
  return element.value.toBlob({ mimeType: 'image/png', idealAspect: false })
}
defineExpose({ focus, angle, zoom, snapshot })
</script>

<template>
  <div class="model-stage" :data-ready="loaded">
    <!-- Dynamic tag preserves the native web component, without changing Vue's
         global compiler configuration or reimplementing Model Viewer's controls. -->
    <component :is="'model-viewer'" :key="generation" ref="element" :src="src" :alt="alt"
      camera-controls touch-action="none" interaction-prompt="none" loading="eager"
      camera-orbit="-25deg 85deg 105%" camera-target="auto auto auto"
      min-camera-orbit="auto 0.1deg 0.12m" max-camera-orbit="auto 179.9deg 10m"
      field-of-view="30deg" environment-image="neutral" exposure="1"
      shadow-intensity="0.6" shadow-softness="1" tone-mapping="neutral"
      @load="onLoad" @error="onError"
      @progress="progress = Math.round(($event as CustomEvent).detail.totalProgress * 100)">
      <div slot="progress-bar"></div>
    </component>
    <div v-if="!loaded" class="load-overlay" role="status">
      <template v-if="failed"><strong>3D 模型加载失败</strong><p>请确认网络可用、浏览器支持 WebGL。</p><button @click="retry">重新加载</button></template>
      <template v-else><span class="spinner"></span><strong>正在加载 3D 人体 · {{ progress }}%</strong></template>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.model-stage{position:relative;width:100%;height:100%;min-height:440px;background:radial-gradient(ellipse at 50% 42%,#f8f9f6 0%,#e9eee8 100%);border-radius:18px;overflow:hidden}
model-viewer{display:block;width:100%;height:100%;min-height:440px;--poster-color:transparent;outline:none}
.load-overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px;color:#52675d;background:#edf1eb;font-size:14px}
.load-overlay p{font-size:12px}.load-overlay button{padding:8px 15px;border:1px solid #a3b7aa;border-radius:7px;background:white;color:#285c4f;cursor:pointer}
.spinner{width:28px;height:28px;border:2px solid #cbd6cc;border-top-color:#376951;border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
</style>
