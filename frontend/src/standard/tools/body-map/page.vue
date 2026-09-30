<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import ModelViewer3D from '@/components/ModelViewer3D.vue'

// The old 2D notes remain accessible without participating in the 3D model.
const LegacyBodyMap = defineAsyncComponent(() => import('./LegacyBodyMap.vue'))
const route = useRoute()
const legacy = computed(() => route.query.mode === 'legacy')
const viewer = ref<InstanceType<typeof ModelViewer3D>>()
const ready = ref(false)
const region = ref('全身')
const status = ref('')
const sourceOpen = ref(false)
const sex = ref<'male' | 'female'>('male')
// Intentionally session-local: a fresh visit always starts covered. Neither URL
// parameters nor persisted preferences can silently enable complete surfaces.
const complete = ref(false)
const disclosure = ref<HTMLDialogElement>()
const sexLabel = computed(() => sex.value === 'male' ? '男性' : '女性')
const modelSrc = computed(() => `/models/human-body/${sex.value}-${complete.value ? 'surface' : 'ordinary'}.glb?v=hm08-v2`)
watch(modelSrc, () => { ready.value = false; region.value = '全身'; status.value = '' }, { flush: 'sync' })
function enableComplete() { complete.value = true; disclosure.value?.close() }
const perspectives = [
  { name: '正面', theta: 0 }, { name: '背面', theta: 180 },
  { name: '左侧', theta: 90 }, { name: '右侧', theta: -90 },
  { name: '俯视', theta: 0, phi: 12 },
]
function focusHead() { region.value = '头部'; viewer.value?.focus(0.93, 0.36) }
function fullBody() { region.value = '全身'; viewer.value?.focus(0.5, 2.15, -25, 85) }
async function download() {
  try {
    const blob = await viewer.value!.snapshot()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `人体模型-${region.value}.png`; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    status.value = '已下载当前视角图片。'
  } catch { status.value = '图片导出失败，请重试或直接截图。' }
}
</script>

<template>
  <LegacyBodyMap v-if="legacy" />
  <main v-else class="human-model">
    <header>
      <div><p class="eyebrow">HUMAN BODY / 3D EXPLORER</p><h1>人体模型 <span>3D</span></h1></div>
      <div class="model-label"><i></i>{{ sexLabel }} · MakeHuman</div>
    </header>
    <section class="workspace" aria-label="3D 人体查看器">
      <div class="model-options">
        <div class="button-group" aria-label="模型性别"><button :aria-pressed="sex === 'male'" @click="sex = 'male'">男性</button><button :aria-pressed="sex === 'female'" @click="sex = 'female'">女性</button></div>
        <div class="button-group" aria-label="展示模式"><button :aria-pressed="!complete" @click="complete = false">普通模式</button><button :aria-pressed="complete" @click="!complete && disclosure?.showModal()">完整体表</button></div>
        <span class="mode-hint">{{ complete ? '完整体表显示中，含私密部位' : '默认遮盖私密部位' }}</span>
      </div>
      <div class="toolbar">
        <div class="button-group"><button :disabled="!ready" :class="{active:region==='全身'}" @click="fullBody">全身</button><button :disabled="!ready" :class="{active:region==='头部'}" @click="focusHead">放大头部</button></div>
        <div class="button-group"><button v-for="item in perspectives" :key="item.name" :disabled="!ready" @click="viewer?.angle(item.theta, item.phi ?? 90)">{{ item.name }}</button></div>
        <div class="button-group"><button :disabled="!ready" aria-label="缩小" @click="viewer?.zoom(-1)">−</button><button :disabled="!ready" aria-label="放大" @click="viewer?.zoom(1)">＋</button></div>
        <button :disabled="!ready" class="export" @click="download">下载当前图</button>
      </div>
      <div class="viewport">
        <ModelViewer3D :key="modelSrc" ref="viewer" :src="modelSrc" :alt="`可旋转和缩放的成年${sexLabel}三维人体模型，${complete ? '完整体表' : '普通模式，遮盖私密部位'}`" @ready="ready = true" @error="ready = false">
          <div class="stage-label"><span>BODY SURFACE</span><strong>{{ region }}</strong></div>
          <div class="gesture-hint"><span>拖动旋转</span><span>滚轮 / 双指缩放</span><span>右键 / 双指拖动平移</span></div>
        </ModelViewer3D>
      </div>
      <footer><span>任意角度旋转查看 · 双击模型可调整观察中心</span><button @click="sourceOpen = !sourceOpen">模型来源与许可 {{ sourceOpen ? '−' : '＋' }}</button></footer>
    </section>
    <dialog ref="disclosure" class="disclosure" aria-labelledby="disclosure-title" @click="($event.target === disclosure) && disclosure?.close()">
      <h2 id="disclosure-title">显示完整体表？</h2>
      <p>包含胸部、外生殖器区域等私密部位的体表形态，用于人体结构观察。当前资产为通用体表模型，不含内部器官或精细生殖系统解剖。</p>
      <p>刷新页面后将恢复普通模式。</p>
      <div><button autofocus @click="disclosure?.close()">取消</button><button class="confirm" @click="enableComplete">显示完整体表</button></div>
    </dialog>
    <p v-if="status" class="status" role="status">{{ status }}</p>
    <div v-if="sourceOpen" class="sources">
      <p>人体数据：<a href="https://github.com/makehumancommunity/makehuman" target="_blank" rel="noopener">MakeHuman 官方资产</a>，成年男性、女性体表，保留五官和耳部细节。普通模式使用不透明三维服装遮盖；完整体表不含内部器官或精细生殖系统解剖。</p>
      <p>查看器：<a href="https://github.com/google/model-viewer" target="_blank" rel="noopener">Google Model Viewer</a>。模型已存放在本站，来源及转换说明见 <a href="/models/human-body/source.json" target="_blank" rel="noopener">数据记录</a>；版权与授权见 <a href="/models/human-body/LICENSE.txt" target="_blank" rel="noopener">许可说明</a>。</p>
      <p>模型来自 MakeHuman 社区，使用 <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noopener">CC0</a> 资产授权。本站进行了预设应用、格式转换、平滑和材质设置。</p>
    </div>
  </main>
</template>

<style scoped>
.model-options{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid #e7ece3}.model-options button,.disclosure button{font:inherit;font-size:12px;padding:8px 12px;border:1px solid #dae4d8;background:white;border-radius:7px;color:#42594d;cursor:pointer}.model-options button[aria-pressed=true]{background:#365d4c;color:white;border-color:#365d4c}.mode-hint{font-size:11px;color:#7a897e}.disclosure{max-width:420px;width:calc(100% - 64px);padding:24px;border:1px solid #d9e2d7;border-radius:16px;color:#263e34;background:#f8faf6;box-shadow:0 18px 60px #14281c30}.disclosure::backdrop{background:#15291e66}.disclosure h2{font-size:18px;margin:0 0 14px}.disclosure p{font-size:13px;line-height:1.8}.disclosure>div{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}.disclosure .confirm{background:#365d4c;color:white}
.human-model{padding:24px 28px;background:#f4f6f2;color:#263e34;min-height:100%;box-sizing:border-box;font-family:inherit}
header{display:flex;align-items:center;justify-content:space-between;margin:0 auto 18px;max-width:1500px;gap:12px}.eyebrow{font-size:10px;letter-spacing:2.3px;color:#6a8276;margin:0 0 7px}h1{margin:0;font-size:27px;letter-spacing:1px;font-weight:600}h1 span{font-size:12px;letter-spacing:0;vertical-align:middle;background:#dfeadf;color:#38654e;padding:4px 7px;border-radius:5px;margin-left:7px}.model-label{font-size:12px;display:flex;align-items:center;gap:8px;color:#647b6c}.model-label i{height:6px;width:6px;background:#61836d;border-radius:50%}.workspace{max-width:1500px;margin:auto;border:1px solid #d9e2d7;border-radius:18px;background:white;overflow:hidden}.toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:15px;padding:12px 16px}.button-group{display:flex;gap:4px}.toolbar button,footer button{font:inherit;font-size:12px;border:1px solid transparent;padding:8px 12px;border-radius:7px;background:white;color:#42594d;cursor:pointer}.toolbar button:hover,footer button:hover{background:#eef3ec}.toolbar button.active{background:#e3ede1;color:#315d46}.toolbar button:disabled{opacity:.4;cursor:default}.toolbar .export{margin-left:auto;border-color:#dce5d8}button:focus-visible{outline:2px solid #426e51;outline-offset:2px}.viewport{height:clamp(480px,calc(100dvh - 215px),1000px);min-height:480px}.stage-label{position:absolute;left:24px;top:22px;pointer-events:none;display:grid;gap:5px}.stage-label span{font-size:9px;letter-spacing:2px;color:#8d9c90}.stage-label strong{font-size:13px;font-weight:500;color:#526b5b}.gesture-hint{position:absolute;left:0;right:0;bottom:20px;display:flex;justify-content:center;flex-wrap:wrap;gap:18px;pointer-events:none;font-size:11px;color:#70816f}footer{padding:8px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:11px;color:#839180}footer button{font-size:11px;padding:6px 8px}.sources{max-width:1460px;margin:12px auto;padding:14px 20px;font-size:12px;line-height:1.8;background:#eaf0e7;border-radius:10px}.sources p{margin:4px 0}.sources a{color:#34624b}.status{font-size:12px;color:#557860;margin:10px auto;max-width:1500px}
@media(max-width:720px){.human-model{padding:15px 9px}h1{font-size:23px}.eyebrow{font-size:9px;letter-spacing:1px}.model-label{font-size:10px}.toolbar{padding:9px;gap:7px}.toolbar button{padding:7px 10px}.toolbar .export{margin-left:0}.viewport{height:calc(100dvh - 250px);min-height:480px}.gesture-hint{font-size:10px;gap:10px}.stage-label{top:16px;left:16px}footer>span{max-width:55%;line-height:1.6}footer{padding:8px}.sources{padding:12px}}
</style>
