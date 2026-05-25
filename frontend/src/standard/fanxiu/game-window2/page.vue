<template>
  <div class="game-window-page">
    <section class="stage-pane">
      <div class="topbar">
        <div class="topbar-content">
          <h2>游戏窗口</h2>
          <div class="stream-controls">
            <div class="control-row">
              <div class="control-group source-controls">
                <div class="control-field">
                  <span class="control-label">设备</span>
                  <el-select
                    v-model="selectedEntryId"
                    class="device-select"
                    size="small"
                    placeholder="选择设备"
                    @change="handleEntryChange"
                  >
                    <el-option
                      v-for="device in devices"
                      :key="device.id"
                      :label="device.name"
                      :value="device.id"
                    />
                  </el-select>
                </div>
                <div class="control-field">
                  <span class="control-label">窗口</span>
                  <el-select
                    v-model="selectedWindowKey"
                    class="window-select"
                    size="small"
                    placeholder="选择窗口"
                    @change="handleWindowChange"
                  >
                    <el-option
                      v-for="scene in windowScenes"
                      :key="scene.key"
                      :label="scene.label"
                      :value="scene.key"
                    />
                  </el-select>
                </div>
                <el-button
                  class="connection-button"
                  :type="connectionButtonType"
                  :plain="!connectionReady"
                  :icon="Refresh"
                  size="small"
                  :loading="connectionButtonLoading"
                  :disabled="!selectedEntryId"
                  @click="connectWindow"
                >
                  {{ connectionButtonText }}
                </el-button>
                <el-button
                  class="save-frame-button"
                  :icon="Download"
                  size="small"
                  plain
                  :loading="saveFrameLoading"
                  :disabled="!selectedEntryId"
                  @click="saveCurrentFrame"
                >
                  保存帧
                </el-button>
              </div>
            </div>
            <div class="control-row option-row">
              <div class="control-group option-controls">
                <div class="control-field">
                  <span class="control-label">分辨率</span>
                  <span class="size-value">{{ naturalSizeText }}</span>
                </div>
                <div class="control-field">
                  <span class="control-label">画面裁边</span>
                  <el-input
                    v-model="trimBorderText"
                    class="crop-input"
                    size="small"
                    placeholder="左,上,右,下"
                    @keyup.enter="restartStream"
                  />
                </div>
                <div class="control-field">
                  <span class="control-label">旋转</span>
                  <el-select v-model="rotateDegrees" class="rotate-select" size="small" @change="restartStream">
                    <el-option label="0°" value="0" />
                    <el-option label="90°" value="90" />
                    <el-option label="180°" value="180" />
                    <el-option label="270°" value="270" />
                  </el-select>
                </div>
                <div class="control-field">
                  <span class="control-label">显示</span>
                <el-input-number v-model="displayScale" class="scale-input" size="small" :min="20" :max="500" :step="5" controls-position="right" @change="syncCanvasSoon" />
                  <span class="control-label">%</span>
                </div>
                <div class="control-field">
                  <span class="control-label">FPS</span>
                  <el-input-number v-model="fps" class="number-input" size="small" :min="1" :max="30" controls-position="right" @change="restartStream" />
                </div>
                <div class="control-field">
                  <span class="control-label">质量</span>
                  <el-input-number v-model="quality" class="number-input" size="small" :min="1" :max="100" controls-position="right" @change="restartStream" />
                </div>
              </div>
            </div>
            <div class="control-row switch-row">
              <div v-if="selectedWindowKey === 'sunlogin'" class="switch-field">
                <span class="control-label">自动关向日葵广告弹窗</span>
                <el-switch
                  v-model="autoDismissPopup"
                  size="small"
                  @change="restartStream"
                />
              </div>
              <div class="switch-field">
                <span class="control-label">模式</span>
                <el-select
                  v-model="windowViewMode"
                  class="mode-select"
                  size="small"
                  :disabled="!selectedEntryId"
                  @change="handleWindowViewModeChange"
                >
                  <el-option
                    v-for="mode in windowViewModes"
                    :key="mode.value"
                    :label="mode.label"
                    :value="mode.value"
                  />
                </el-select>
                <el-tooltip content="直播只显示画面；交互可在画面中点击/拖拽；关闭会停止当前页面取流。" placement="top">
                  <span class="help-mark">?</span>
                </el-tooltip>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="workspace">
        <div class="viewer-pane">
          <div class="live-workspace">
            <div
              v-show="windowViewMode !== 'off'"
              ref="liveViewportRef"
              class="live-viewport"
              :class="liveViewportClasses"
              :style="liveCanvasStyle"
              @wheel="handleLiveWheel"
              @mousedown.capture="handleLiveViewportMouseDown"
            >
              <div class="live-stage-workspace" :style="liveCanvasStyle">
                <div
                  ref="imageWrapRef"
                  class="image-wrap"
                  :class="{ 'is-control-enabled': controlEnabled }"
                  :style="liveContentStyle"
                >
                  <img
                    v-if="streamEnabled && streamUrl"
                    ref="streamImageRef"
                    class="stream-image"
                    :src="streamUrl"
                    :style="liveCanvasStyle"
                    alt="凡修云手机窗口"
                    draggable="false"
                    @load="handleImageLoad"
                    @error="handleStreamError"
                  />
                  <div v-else class="paused-placeholder">{{ placeholderText }}</div>
                  <canvas
                    ref="overlayCanvasRef"
                    class="overlay-canvas"
                    @pointerdown="handlePointerDown"
                    @pointermove="handlePointerMove"
                    @pointerup="handlePointerUp"
                    @pointerleave="handlePointerLeave"
                    @contextmenu.prevent="handleContextMenu"
                  />
                  <div v-if="streamError" class="stream-error">{{ streamError }}</div>
                </div>
              </div>
            </div>

            <aside class="code-panel">
              <div class="code-panel-head">
                <div class="code-panel-title">
                  <span>视觉宏</span>
                  <el-popover trigger="click" placement="bottom-start" width="260">
                    <template #reference>
                      <el-button
                        circle
                        plain
                        size="small"
                        :icon="Setting"
                        title="配置新视觉指令默认模板"
                        aria-label="配置新视觉指令默认模板"
                      />
                    </template>
                    <div class="visual-macro-config">
                      <div class="visual-macro-config-title">新指令默认模板</div>
                      <label class="visual-macro-config-row">
                        <span>点击半径</span>
                        <el-input-number
                          v-model="visualMacroDefaultPointRadius"
                          class="visual-number-input"
                          size="small"
                          :min="0"
                          :max="200"
                          :step="1"
                          controls-position="right"
                        />
                      </label>
                      <label class="visual-macro-config-row">
                        <span>图片相似度</span>
                        <el-input-number
                          v-model="visualMacroDefaultThreshold"
                          class="visual-number-input"
                          size="small"
                          :min="0.5"
                          :max="1"
                          :step="0.01"
                          controls-position="right"
                        />
                      </label>
                    </div>
                  </el-popover>
                </div>
                <div class="code-panel-actions">
                  <el-button size="small" :loading="pseudoCompileLoading" @click="compilePseudoCode">编译</el-button>
                  <el-button size="small" type="primary" plain :loading="pseudoStartLoading" @click="startPseudoCode">启动</el-button>
                </div>
              </div>
              <div class="code-scope-list">
                <section v-for="scopeInfo in codeCardScopes" :key="scopeInfo.scope" class="code-scope">
                  <div class="code-scope-head">
                    <span>{{ scopeInfo.label }}</span>
                    <button
                      type="button"
                      class="code-add"
                      :title="`新建${scopeInfo.label}`"
                      :aria-label="`新建${scopeInfo.label}`"
                      @click="addCodeCard(scopeInfo.scope)"
                    >
                      +
                    </button>
                  </div>
                  <div class="code-card-list">
                    <section
                      v-for="(card, index) in codeCardsByScope(scopeInfo.scope)"
                      :key="card.id"
                      class="code-card"
                      :class="{ 'is-expanded': isCodeCardExpanded(card.id) }"
                    >
                      <div v-if="isCodeCardExpanded(card.id)" class="code-card-head">
                        <el-input
                          v-model="card.title"
                          size="small"
                          placeholder="标题"
                          @input="scheduleCodeCardSave(card)"
                          @blur="saveCodeCardNow(card)"
                        />
                        <button
                          type="button"
                          class="code-card-collapse"
                          title="折叠卡片"
                          aria-label="折叠卡片"
                          @click="toggleCodeCard(card.id)"
                        >
                          ^
                        </button>
                        <button
                          type="button"
                          class="code-card-record"
                          :class="{ 'is-recording': activeVisualMacroCardId === card.id }"
                          :title="activeVisualMacroCardId === card.id ? '停止录制视觉指令' : '录制视觉指令'"
                          :aria-label="activeVisualMacroCardId === card.id ? '停止录制视觉指令' : '录制视觉指令'"
                          @click="toggleVisualMacroRecording(card.id)"
                        >
                          {{ activeVisualMacroCardId === card.id ? '停止录制' : '录制' }}
                        </button>
                        <button
                          type="button"
                          class="code-card-delete"
                          title="删除卡片"
                          aria-label="删除卡片"
                          @click="deleteCodeCard(card.id)"
                        >
                          -
                        </button>
                      </div>
                      <div v-else class="code-card-summary-row">
                        <button
                          type="button"
                          class="code-card-title-button"
                          @click="toggleCodeCard(card.id)"
                        >
                          <span>{{ codeCardTitle(card, scopeInfo.scope, index) }}</span>
                        </button>
                        <button
                          type="button"
                          class="code-card-delete"
                          title="删除卡片"
                          aria-label="删除卡片"
                          @click="deleteCodeCard(card.id)"
                        >
                          -
                        </button>
                      </div>
                      <div v-if="isCodeCardExpanded(card.id)" class="visual-action-editor">
                        <div v-if="activeVisualMacroCardId === card.id" class="visual-recording-hint">
                          {{ visualMacroCapturePending ? '正在保存点击前画面...' : '录制中：在左侧直播画面点击或拖拽，会追加一条视觉指令。' }}
                        </div>
                        <div
                          v-for="(instruction, instructionIndex) in visualInstructionsOf(card)"
                          :key="instruction.id"
                          class="visual-operation"
                          :class="{ 'is-selected': selectedVisualInstructionKey === visualInstructionKey(card.id, instruction.id) }"
                          @click="selectVisualInstructionFrame(card, instruction)"
                        >
                        <div class="visual-action-row">
                          <span class="visual-operation-index">{{ instructionIndex + 1 }}</span>
                          <el-select
                            :model-value="instruction.action"
                            class="visual-action-select"
                            size="small"
                            @change="value => updateVisualInstruction(card, instruction.id, { action: value as VisualActionKind })"
                          >
                            <el-option label="点击" value="click" />
                            <el-option label="拖拽" value="drag" />
                            <el-option label="等待" value="wait" />
                          </el-select>
                          <el-select
                            :model-value="instruction.target"
                            class="visual-target-select"
                            size="small"
                            @change="value => updateVisualInstruction(card, instruction.id, { target: value as VisualTargetKind })"
                          >
                            <el-option label="图片" value="image" />
                            <el-option label="文本" value="text" />
                            <el-option label="无目标" value="none" />
                          </el-select>
                          <el-select
                            v-if="instruction.target === 'image'"
                            :model-value="instruction.scan"
                            class="visual-scan-select"
                            size="small"
                            @change="value => updateVisualInstruction(card, instruction.id, { scan: value as VisualScanMode })"
                          >
                            <el-option label="固定位置" value="fixed" />
                            <el-option label="范围搜索" value="range" />
                            <el-option label="全图搜索" value="full" />
                          </el-select>
                          <button
                            type="button"
                            class="visual-operation-delete"
                            title="删除视觉指令"
                            aria-label="删除视觉指令"
                            @click.stop="deleteVisualInstruction(card, instruction.id)"
                          >
                            -
                          </button>
                        </div>

                        <div class="visual-action-row">
                          <template v-if="instruction.target === 'text'">
                            <el-input
                              :model-value="instruction.text"
                              class="visual-text-input"
                              size="small"
                              placeholder="识别文本"
                              @input="value => updateVisualInstruction(card, instruction.id, { text: String(value) })"
                              @blur="saveCodeCardNow(card)"
                            />
                            <el-select
                              :model-value="instruction.textMatch"
                              class="visual-scan-select"
                              size="small"
                              @change="value => updateVisualInstruction(card, instruction.id, { textMatch: value as VisualTextMatch })"
                            >
                              <el-option label="包含" value="contains" />
                              <el-option label="精确" value="exact" />
                              <el-option label="正则" value="regex" />
                            </el-select>
                          </template>
                        </div>

                        <div v-if="instruction.action !== 'click'" class="visual-action-row">
                          <template v-if="instruction.action === 'wait'">
                            <span class="visual-inline-label">条件</span>
                            <el-select
                              :model-value="instruction.condition"
                              class="visual-scan-select"
                              size="small"
                              @change="value => updateVisualInstruction(card, instruction.id, { condition: value as VisualCondition })"
                            >
                              <el-option label="出现" value="appear" />
                              <el-option label="消失" value="disappear" />
                              <el-option label="稳定" value="stable" />
                              <el-option label="变化" value="changed" />
                            </el-select>
                            <span class="visual-inline-label">超时</span>
                            <el-input-number
                              :model-value="instruction.timeout"
                              class="visual-small-number-input"
                              size="small"
                              :min="0"
                              :max="120"
                              controls-position="right"
                              @change="value => updateVisualInstruction(card, instruction.id, { timeout: Number(value) || 0 })"
                            />
                            <span class="visual-inline-label">秒</span>
                          </template>
                          <template v-else-if="instruction.action === 'drag'">
                            <span class="visual-inline-label">从</span>
                            <code>{{ visualPointText(instruction.point) }}</code>
                            <span class="visual-inline-label">到</span>
                            <code>{{ visualPointText(instruction.endPoint) }}</code>
                          </template>
                        </div>
                        </div>
                        <div v-if="!visualInstructionsOf(card).length" class="visual-empty">
                          点击本动作右上角“录制”后，在直播画面点击或拖拽，会追加视觉指令。
                        </div>
                      </div>
                    </section>
                    <div v-if="!codeCardsByScope(scopeInfo.scope).length && !codeCardsLoading" class="code-card-empty">
                      {{ scopeInfo.emptyText }}
                    </div>
                  </div>
                </section>
              </div>
              <div class="code-output-panel">
                <div class="code-output-tabs">
                  <button
                    type="button"
                    class="code-output-tab"
                    :class="{ 'is-active': pseudoOutputTab === 'log' }"
                    @click="pseudoOutputTab = 'log'"
                  >
                    日志
                  </button>
                  <button
                    type="button"
                    class="code-output-tab"
                    :class="{ 'is-active': pseudoOutputTab === 'result' }"
                    @click="pseudoOutputTab = 'result'"
                  >
                    结果
                  </button>
                </div>
                <pre class="code-output-box">{{ pseudoOutputText }}</pre>
              </div>
            </aside>
          </div>

          <section class="screenshot-panel">
            <div class="screenshot-head">
              <button type="button" class="screenshot-toggle" @click="toggleScreenshotPanel">
                <span class="screenshot-caret">{{ screenshotPanelOpen ? '▼' : '▶' }}</span>
                <span>指令截图</span>
              </button>
              <el-popover trigger="click" placement="right-start" width="260">
                <template #reference>
                  <button
                    type="button"
                    class="screenshot-help"
                    title="查看操作文档"
                    aria-label="查看指令截图操作文档"
                    @click.stop
                  >
                    ?
                  </button>
                </template>
                <div class="screenshot-help-doc">
                  <div>Ctrl + 滚轮：以鼠标位置缩放</div>
                  <div>Ctrl + + / -：放大或缩小</div>
                  <div>Ctrl + 0：适应视口</div>
                  <div>空格 + 左键拖拽：拖动画面</div>
                  <div>中键拖拽：拖动画面</div>
                  <div>左键拖拽：新建标注框</div>
                </div>
              </el-popover>
              <span class="screenshot-summary">{{ screenshotPanelSummary }}</span>
            </div>
            <div v-if="screenshotPanelOpen" class="screenshot-body">
              <div v-if="screenshotLoading && !screenshotImages.length" class="screenshot-empty">加载中</div>
              <div v-else-if="!selectedScreenshotFilename" class="screenshot-empty">选择一条带帧的视觉指令</div>
              <div v-else-if="!selectedScreenshotImage" class="screenshot-empty">未找到绑定截图</div>
              <div v-if="selectedScreenshotImage" class="screenshot-editor">
                <div
                  ref="screenshotViewportRef"
                  class="screenshot-preview"
                  :class="screenshotViewportClasses"
                  :style="screenshotCanvasStyle"
                  @wheel="handleScreenshotWheel"
                  @mousedown.capture="handleScreenshotViewportMouseDown"
                >
                  <div class="screenshot-workspace" :style="screenshotCanvasStyle">
                    <div ref="screenshotImageWrapRef" class="screenshot-image-wrap" :style="screenshotContentStyle">
                      <img
                        v-if="screenshotImageUrl"
                        ref="screenshotImageRef"
                        class="screenshot-image"
                        :src="screenshotImageUrl"
                        :style="screenshotCanvasStyle"
                        alt="截图"
                        draggable="false"
                        @load="handleScreenshotImageLoad"
                        @error="handleScreenshotImageError"
                      />
                      <div v-else class="screenshot-image-placeholder">加载图片</div>
                      <canvas
                        ref="screenshotOverlayCanvasRef"
                        class="screenshot-overlay-canvas"
                        @pointerdown="handleScreenshotPointerDown"
                        @pointermove="handleScreenshotPointerMove"
                        @pointerup="handleScreenshotPointerUp"
                        @pointerleave="handleScreenshotPointerLeave"
                        @contextmenu.prevent="handleScreenshotContextMenu"
                      />
                    </div>
                  </div>
                </div>

                <div class="screenshot-pre-panel" @contextmenu.prevent.stop="openScreenshotBoxListPanelContextMenu">
                  <div v-if="selectedVisualInstruction" class="screenshot-instruction-panel">
                    <div class="screenshot-panel-title">{{ visualActionLabel(selectedVisualInstruction.action) }}</div>
                    <div v-if="selectedVisualInstruction.action === 'click'" class="screenshot-instruction-metrics">
                      <label class="screenshot-box-metric">
                        <span>x</span>
                        <el-input-number
                          :model-value="selectedVisualInstruction.point?.x ?? 0"
                          size="small"
                          :controls="false"
                          :step="1"
                          @change="value => updateSelectedVisualInstructionPoint('point', 'x', value)"
                        />
                      </label>
                      <label class="screenshot-box-metric">
                        <span>y</span>
                        <el-input-number
                          :model-value="selectedVisualInstruction.point?.y ?? 0"
                          size="small"
                          :controls="false"
                          :step="1"
                          @change="value => updateSelectedVisualInstructionPoint('point', 'y', value)"
                        />
                      </label>
                      <label class="screenshot-box-metric">
                        <span>r</span>
                        <el-input-number
                          :model-value="selectedVisualInstruction.pointRadius"
                          size="small"
                          :controls="false"
                          :step="1"
                          @change="updateSelectedVisualInstructionRadius"
                        />
                      </label>
                    </div>
                    <div v-else-if="selectedVisualInstruction.action === 'drag'" class="screenshot-drag-metrics">
                      <div class="screenshot-instruction-metrics">
                        <span class="screenshot-metric-group-label">从</span>
                        <label class="screenshot-box-metric">
                          <span>x</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.point?.x ?? 0"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionPoint('point', 'x', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>y</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.point?.y ?? 0"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionPoint('point', 'y', value)"
                          />
                        </label>
                      </div>
                      <div class="screenshot-instruction-metrics">
                        <span class="screenshot-metric-group-label">到</span>
                        <label class="screenshot-box-metric">
                          <span>x</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.endPoint?.x ?? 0"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionPoint('endPoint', 'x', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>y</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.endPoint?.y ?? 0"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionPoint('endPoint', 'y', value)"
                          />
                        </label>
                      </div>
                    </div>
                    <div v-if="selectedVisualInstruction.target === 'image'" class="screenshot-advanced-metrics">
                      <label class="screenshot-box-metric">
                        <span>相似度</span>
                        <el-input-number
                          :model-value="selectedVisualInstruction.threshold"
                          size="small"
                          :min="0.5"
                          :max="1"
                          :step="0.01"
                          controls-position="right"
                          @change="updateSelectedVisualInstructionThreshold"
                        />
                      </label>
                    </div>
                  </div>
                  <div v-if="screenshotBoxes.length" class="screenshot-box-list">
                    <div
                      v-for="(box, index) in screenshotBoxes"
                      :key="box.id"
                      class="screenshot-box-row"
                      :class="{ 'is-active': selectedScreenshotBoxId === box.id }"
                      @click="selectScreenshotBox(box.id)"
                      @contextmenu.prevent.stop="openScreenshotBoxListContextMenu($event, box.id)"
                    >
                      <div class="screenshot-box-main">
                        <span class="screenshot-box-number">{{ index + 1 }}</span>
                        <el-input
                          v-model="box.name"
                          class="screenshot-box-name"
                          size="small"
                          placeholder="名称"
                          @focus="selectScreenshotBox(box.id)"
                          @input="handleScreenshotBoxNameInput"
                        />
                      </div>
                      <div class="screenshot-box-metrics">
                        <label class="screenshot-box-metric">
                          <span>x</span>
                          <el-input-number
                            :model-value="box.x"
                            size="small"
                            :controls="false"
                            :step="1"
                            @focus="selectScreenshotBox(box.id)"
                            @change="value => updateScreenshotBoxMetric(box.id, 'x', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>y</span>
                          <el-input-number
                            :model-value="box.y"
                            size="small"
                            :controls="false"
                            :step="1"
                            @focus="selectScreenshotBox(box.id)"
                            @change="value => updateScreenshotBoxMetric(box.id, 'y', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>w</span>
                          <el-input-number
                            :model-value="box.w"
                            size="small"
                            :controls="false"
                            :step="1"
                            @focus="selectScreenshotBox(box.id)"
                            @change="value => updateScreenshotBoxMetric(box.id, 'w', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>h</span>
                          <el-input-number
                            :model-value="box.h"
                            size="small"
                            :controls="false"
                            :step="1"
                            @focus="selectScreenshotBox(box.id)"
                            @change="value => updateScreenshotBoxMetric(box.id, 'h', value)"
                          />
                        </label>
                      </div>
                    </div>
                  </div>
                  <div v-else class="screenshot-empty">拖拽画框</div>
                </div>
              </div>
            </div>
          </section>

          <section v-if="matchResults.length" class="match-panel">
            <div class="match-head">
              <span>匹配</span>
              <span class="match-summary">共{{ matchResults.length }}次</span>
            </div>
            <div class="match-body">
              <div class="match-preview">
                <div ref="matchImageWrapRef" class="match-image-wrap">
                  <img
                    v-if="selectedMatchResult"
                    ref="matchImageRef"
                    class="match-image"
                    :src="selectedMatchResult.imageUrl"
                    alt="匹配帧"
                    draggable="false"
                    @load="handleMatchImageLoad"
                  />
                  <canvas ref="matchOverlayCanvasRef" class="match-overlay-canvas" />
                </div>
              </div>
              <div class="match-list">
                <button
                  v-for="(entry, index) in matchResultEntries"
                  :key="entry.id"
                  type="button"
                  class="match-row"
                  :class="{
                    'is-active': selectedMatchEntry?.id === entry.id,
                    'is-fixed': entry.kind === 'fixed',
                    'is-template': entry.kind === 'template',
                  }"
                  @click="selectMatchEntry(entry.id)"
                >
                  <span class="match-number">{{ index + 1 }}</span>
                  <span class="match-name">{{ entry.name }}</span>
                  <span class="match-kind">{{ entry.label }}</span>
                  <strong class="match-score">{{ entry.similarity }}%</strong>
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div
        v-if="screenshotBoxContextMenu.visible"
        class="screenshot-box-menu"
        :style="{ left: `${screenshotBoxContextMenu.x}px`, top: `${screenshotBoxContextMenu.y}px` }"
        @click.stop
        @pointerdown.stop
      >
        <button type="button" :disabled="matchingBoxId === screenshotBoxContextMenu.boxId" @click="runScreenshotBoxContextMatch">
          匹配
        </button>
      </div>

      <div
        v-if="screenshotBoxListContextMenu.visible"
        class="screenshot-box-menu"
        :style="{ left: `${screenshotBoxListContextMenu.x}px`, top: `${screenshotBoxListContextMenu.y}px` }"
        @click.stop
        @pointerdown.stop
      >
        <button v-if="screenshotBoxListContextMenu.boxId" type="button" @click="copyScreenshotBoxFromListContext">
          复制
        </button>
        <button type="button" :disabled="!copiedScreenshotBox" @click="pasteScreenshotBoxFromListContext">
          粘贴
        </button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ArrowLeft,
  ArrowRight,
  Download,
  Refresh,
  Setting,
} from '@element-plus/icons-vue';
import {
  clickFanxiuGameWindow2,
  compileFanxiuPseudoCode,
  createFanxiuPseudoCodeCard,
  createFanxiuGameWindow2StreamToken,
  deleteFanxiuPseudoCodeCard,
  deleteFanxiuGameWindow2Screenshot,
  dragFanxiuGameWindow2,
  getFanxiuGameWindow2MatchImage,
  getFanxiuGameWindow2Screenshot,
  getFanxiuGameWindow2PreLabel,
  listFanxiuPseudoCodeCards,
  listFanxiuGameWindow2Screenshots,
  matchFanxiuGameWindow2Screenshot,
  saveFanxiuGameWindow2Frame,
  saveFanxiuGameWindow2PreLabel,
  startFanxiuPseudoCode,
  updateFanxiuPseudoCodeCard,
  type FanxiuGameWindow2MatchBox,
  type FanxiuGameWindow2MatchResponse,
  type FanxiuGameWindow2ScreenshotItem,
  type FanxiuGameWindow2PreLabelBox,
  type FanxiuGameWindow2PreLabelPayload,
  type FanxiuPseudoCodeCard,
  type FanxiuPseudoCodeCardScope,
  type FanxiuPseudoCodeRunResponse,
} from '@/api/fanxiu';
import {
  fetchRuntimeStatus,
  triggerRuntimeItem,
  type RuntimeItem,
  type RuntimeStatusResponse,
} from '@/api/runtime';
import { taskStore, type Device } from '@/store/taskStore';

interface OverlayBox {
  id: string;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

type OverlayBoxMetric = 'x' | 'y' | 'w' | 'h';
type VisualPointField = 'point' | 'endPoint';
type VisualPointMetric = 'x' | 'y';

interface DraftState {
  pointerId: number;
  startX: number;
  startY: number;
}

type ScreenshotResizeHandle = 'top-left' | 'bottom-right';

interface ScreenshotResizeState {
  pointerId: number;
  boxId: string;
  handle: ScreenshotResizeHandle;
  original: OverlayBox;
}

interface ScreenshotPanState {
  startClientX: number;
  startClientY: number;
  startPanX: number;
  startPanY: number;
}

interface ControlClickState {
  pointerId: number;
  frameX: number;
  frameY: number;
  clientX: number;
  clientY: number;
  startedAt: number;
}

type WindowSceneKey = 'star-cloud-phone' | 'sunlogin' | 'mumu';
type CaptureArea = 'outer' | 'client';
type RotateDegrees = '0' | '90' | '180' | '270';
type WindowViewMode = 'live' | 'control' | 'off';

interface WindowSceneDefaults {
  targetTitle: string;
  cropText: string;
  captureArea: CaptureArea;
  trimBorderText: string;
  rotateDegrees: RotateDegrees;
  fps: number;
  quality: number;
  autoDismissPopup: boolean;
  displayScale: number;
}

interface WindowSceneConfig {
  trimBorderText: string;
  rotateDegrees: RotateDegrees;
  fps: number;
  quality: number;
  autoDismissPopup: boolean;
}

interface WindowScene {
  key: WindowSceneKey;
  label: string;
  defaults: WindowSceneDefaults;
}

type CodeCard = FanxiuPseudoCodeCard;
type PseudoOutputTab = 'log' | 'result';

interface CodeCardScopeInfo {
  scope: FanxiuPseudoCodeCardScope;
  label: string;
  emptyText: string;
}

interface LegacyCodeCard {
  title?: unknown;
  body?: unknown;
}

type VisualActionKind = 'click' | 'drag' | 'wait';
type VisualTargetKind = 'image' | 'text' | 'none';
type VisualScanMode = 'fixed' | 'range' | 'full';
type VisualTextMatch = 'contains' | 'exact' | 'regex';
type VisualCondition = 'appear' | 'disappear' | 'stable' | 'changed';

interface VisualPoint {
  x: number;
  y: number;
}

interface VisualBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface VisualMacroAction {
  version: 1;
  id: string;
  action: VisualActionKind;
  target: VisualTargetKind;
  label: string;
  frame: string;
  point: VisualPoint | null;
  endPoint: VisualPoint | null;
  pointRadius: number;
  box: VisualBox | null;
  scan: VisualScanMode;
  threshold: number;
  text: string;
  textMatch: VisualTextMatch;
  condition: VisualCondition;
  timeout: number;
}

interface VisualMacroProgram {
  version: 1;
  operations: VisualMacroAction[];
}

interface ScreenshotBoxContextMenu {
  visible: boolean;
  x: number;
  y: number;
  boxId: string;
}

interface CopiedScreenshotBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface MatchResult extends FanxiuGameWindow2MatchResponse {
  id: string;
  imageUrl: string;
  createdAt: number;
}

type MatchResultEntryKind = 'fixed' | 'template';

interface MatchResultEntry {
  id: string;
  kind: MatchResultEntryKind;
  label: string;
  name: string;
  similarity: number;
  result: MatchResult;
}

const route = useRoute();
const router = useRouter();
const DEVICE_STORAGE_KEY = 'fanxiu.gameWindow2.entryId';
const WINDOW_STORAGE_KEY = 'fanxiu.gameWindow2.windowKey';
const WINDOW_CONFIG_STORAGE_PREFIX = 'fanxiu.gameWindow2.windowConfig';
const SCREENSHOT_SELECTION_STORAGE_PREFIX = 'fanxiu.gameWindow2.screenshotFilename';
const LEGACY_CODE_CARDS_STORAGE_KEY = 'fanxiu.gameWindow2.codeCards';
const VISUAL_MACRO_DEFAULT_THRESHOLD_KEY = 'fanxiu.gameWindow2.visualMacro.defaultThreshold';
const VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY = 'fanxiu.gameWindow2.visualMacro.defaultPointRadius';
const SCREENSHOT_MIN_ZOOM_PERCENT = 20;
const SCREENSHOT_MAX_ZOOM_PERCENT = 500;
const SCREENSHOT_ZOOM_STEP = 10;
const MIN_CONTENT_VISIBLE_AREA_RATIO = 0.2;
const MIN_CONTENT_VISIBLE_AXIS_RATIO = Math.sqrt(MIN_CONTENT_VISIBLE_AREA_RATIO);
const GAME_WINDOW_SERVICE_KEY = 'fanxiu-game-window';
const VISUAL_ACTION_MARKER_START = '<!-- codeyun-visual-action-v1';
const VISUAL_ACTION_MARKER_END = '-->';
const windowViewModes: Array<{ value: WindowViewMode; label: string }> = [
  { value: 'live', label: '直播' },
  { value: 'control', label: '交互' },
  { value: 'off', label: '关闭' },
];
const codeCardScopes: CodeCardScopeInfo[] = [
  { scope: 'guard', label: '守护', emptyText: '暂无守护' },
  { scope: 'action', label: '动作', emptyText: '暂无动作' },
];
const windowScenes: WindowScene[] = [
  {
    key: 'star-cloud-phone',
    label: '星星云手机',
    defaults: {
      targetTitle: '云手机',
      cropText: '0,0,0,0',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 12,
      quality: 82,
      autoDismissPopup: false,
      displayScale: 100,
    },
  },
  {
    key: 'sunlogin',
    label: '向日葵',
    defaults: {
      targetTitle: '1249152866',
      cropText: '0,49,4,4',
      trimBorderText: '0,0,0,0',
      captureArea: 'outer',
      rotateDegrees: '90',
      fps: 10,
      quality: 80,
      autoDismissPopup: true,
      displayScale: 100,
    },
  },
  {
    key: 'mumu',
    label: 'MuMu模拟器',
    defaults: {
      targetTitle: 'MuMu模拟器',
      cropText: '0,60,0,0',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 12,
      quality: 82,
      autoDismissPopup: false,
      displayScale: 60,
    },
  },
];

const devices = computed(() => taskStore.devices);
const selectedEntryId = ref('');
const selectedWindowKey = ref<WindowSceneKey>('star-cloud-phone');
const runtimeStatus = ref<RuntimeStatusResponse | null>(null);
const runtimeLoading = ref(false);
const connectionLoading = ref(false);

const trimBorderText = ref('0,0,0,0');
const rotateDegrees = ref<RotateDegrees>('0');
const displayScale = ref(100);
const fps = ref(12);
const quality = ref(82);
const autoDismissPopup = ref(false);
const streamEnabled = ref(true);
const streamNonce = ref(Date.now());
const streamError = ref('');
const streamToken = ref('');
const streamTokenExpiresAt = ref(0);
const streamTokenLoading = ref(false);
const layerVisible = ref(true);
const windowViewMode = ref<WindowViewMode>('live');
const controlEnabled = ref(false);
const saveFrameLoading = ref(false);
const screenshotPanelOpen = ref(true);
const screenshotLoaded = ref(false);
const screenshotLoading = ref(false);
const screenshotSaving = ref(false);
const screenshotDeleting = ref(false);
const screenshotImages = ref<FanxiuGameWindow2ScreenshotItem[]>([]);
const selectedScreenshotFilename = ref('');
const screenshotImageUrl = ref('');
const screenshotNaturalWidth = ref(0);
const screenshotNaturalHeight = ref(0);
const screenshotZoomPercent = ref(100);
const screenshotPanX = ref(0);
const screenshotPanY = ref(0);
const screenshotBoxes = ref<OverlayBox[]>([]);
const selectedScreenshotBoxId = ref<string | null>(null);
const screenshotDraftState = ref<DraftState | null>(null);
const screenshotDraftBox = ref<OverlayBox | null>(null);
const screenshotResizeState = ref<ScreenshotResizeState | null>(null);
const screenshotPanState = ref<ScreenshotPanState | null>(null);
const screenshotSpacePressed = ref(false);
const screenshotDirty = ref(false);
const screenshotJumpEditing = ref(false);
const screenshotJumpText = ref('');
const screenshotBoxContextMenu = ref<ScreenshotBoxContextMenu>({
  visible: false,
  x: 0,
  y: 0,
  boxId: '',
});
const screenshotBoxListContextMenu = ref<ScreenshotBoxContextMenu>({
  visible: false,
  x: 0,
  y: 0,
  boxId: '',
});
const copiedScreenshotBox = ref<CopiedScreenshotBox | null>(null);
const matchingBoxId = ref<string | null>(null);
const matchResults = ref<MatchResult[]>([]);
const selectedMatchEntryId = ref('');
const codeCards = ref<CodeCard[]>([]);
const codeCardsLoading = ref(false);
const expandedCodeCardIds = ref<string[]>([]);
const activeVisualMacroCardId = ref<string | null>(null);
const visualMacroCapturePending = ref(false);
const visualMacroDefaultThreshold = ref(0.88);
const visualMacroDefaultPointRadius = ref(10);
const selectedVisualInstructionKey = ref('');
const pseudoCompileLoading = ref(false);
const pseudoStartLoading = ref(false);
const pseudoOutputTab = ref<PseudoOutputTab>('log');
const pseudoExecutionLog = ref('尚未执行');
const pseudoExecutionResult = ref('');

const streamImageRef = ref<HTMLImageElement | null>(null);
const imageWrapRef = ref<HTMLDivElement | null>(null);
const liveViewportRef = ref<HTMLDivElement | null>(null);
const overlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const screenshotImageRef = ref<HTMLImageElement | null>(null);
const screenshotViewportRef = ref<HTMLDivElement | null>(null);
const screenshotImageWrapRef = ref<HTMLDivElement | null>(null);
const screenshotOverlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const screenshotJumpInputRef = ref<{ focus: () => void } | null>(null);
const matchImageRef = ref<HTMLImageElement | null>(null);
const matchImageWrapRef = ref<HTMLDivElement | null>(null);
const matchOverlayCanvasRef = ref<HTMLCanvasElement | null>(null);
const naturalWidth = ref(0);
const naturalHeight = ref(0);
const liveContentZoomPercent = ref(100);
const livePanX = ref(0);
const livePanY = ref(0);
const selectedBoxId = ref<string | null>(null);
const boxes = ref<OverlayBox[]>([]);
const draftState = ref<DraftState | null>(null);
const draftBox = ref<OverlayBox | null>(null);
const controlClickState = ref<ControlClickState | null>(null);
const livePanState = ref<ScreenshotPanState | null>(null);

let resizeObserver: ResizeObserver | null = null;
let pollTimer: number | null = null;
let screenshotSaveTimer: number | null = null;
let tokenRequestSeq = 0;
let lastInputErrorAt = 0;
let isApplyingWindowConfig = false;
const codeCardSaveTimers = new Map<string, number>();

const selectedDevice = computed<Device | null>(() => (
  devices.value.find((device) => device.id === selectedEntryId.value) ?? null
));
const selectedWindowScene = computed(() => (
  windowScenes.find((scene) => scene.key === selectedWindowKey.value) ?? windowScenes[0]
));
const targetTitle = computed(() => selectedWindowScene.value.defaults.targetTitle);
const cropText = computed(() => selectedWindowScene.value.defaults.cropText);
const captureArea = computed(() => selectedWindowScene.value.defaults.captureArea);
const serviceItem = computed<RuntimeItem | null>(() => (
  runtimeStatus.value?.items.find((item) => item.source === 'builtin' && item.key === GAME_WINDOW_SERVICE_KEY) ?? null
));
const serviceActive = computed(() => Boolean(serviceItem.value?.active));
const selectedScreenshotImage = computed(() => (
  screenshotImages.value.find((item) => item.filename === selectedScreenshotFilename.value) ?? null
));
const selectedVisualInstructionContext = computed(() => {
  if (!selectedVisualInstructionKey.value) return null;
  for (const card of codeCards.value) {
    for (const instruction of visualInstructionsOf(card)) {
      if (visualInstructionKey(card.id, instruction.id) === selectedVisualInstructionKey.value) {
        return { card, instruction };
      }
    }
  }
  return null;
});
const selectedVisualInstruction = computed(() => selectedVisualInstructionContext.value?.instruction ?? null);
const selectedScreenshotIndex = computed(() => (
  screenshotImages.value.findIndex((item) => item.filename === selectedScreenshotFilename.value)
));
const canSelectPreviousScreenshotImage = computed(() => selectedScreenshotIndex.value > 0);
const canSelectNextScreenshotImage = computed(() => (
  selectedScreenshotIndex.value >= 0 && selectedScreenshotIndex.value < screenshotImages.value.length - 1
));
const matchResultEntries = computed<MatchResultEntry[]>(() => {
  return matchResults.value.flatMap((result) => {
    const name = result.box.name || '未命名';
    const entries: MatchResultEntry[] = [
      {
        id: `${result.id}:fixed`,
        kind: 'fixed',
        label: '原位',
        name,
        similarity: result.fixed_similarity ?? result.similarity,
        result,
      },
    ];
    if (result.template_similarity !== undefined) {
      entries.push({
        id: `${result.id}:template`,
        kind: 'template',
        label: '模板',
        name,
        similarity: result.template_similarity,
        result,
      });
    }
    return entries;
  });
});
const selectedMatchEntry = computed(() => (
  matchResultEntries.value.find((item) => item.id === selectedMatchEntryId.value) ?? matchResultEntries.value[0] ?? null
));
const selectedMatchResult = computed(() => selectedMatchEntry.value?.result ?? null);
const controlReady = computed(() => Boolean(
  controlEnabled.value
  && selectedEntryId.value
  && streamEnabled.value
  && naturalWidth.value
  && naturalHeight.value
));
const naturalSizeText = computed(() => {
  if (!selectedEntryId.value) return '未连接';
  if (!naturalWidth.value || !naturalHeight.value) return '等待画面';
  return `${naturalHeight.value} x ${naturalWidth.value}`;
});
const liveCanvasStyle = computed(() => {
  const width = naturalWidth.value || 0;
  const height = naturalHeight.value || 0;
  if (!width || !height) return {};
  const stageWidth = Math.max(1, Math.round(width * displayScale.value / 100));
  const stageHeight = Math.max(1, Math.round(height * displayScale.value / 100));
  return {
    width: `${stageWidth}px`,
    height: `${stageHeight}px`,
  };
});
const liveContentStyle = computed(() => ({
  ...liveCanvasStyle.value,
  transform: `translate(${livePanX.value}px, ${livePanY.value}px) scale(${liveContentZoomPercent.value / 100})`,
}));
const liveViewportClasses = computed(() => ({
  'is-pan-ready': screenshotSpacePressed.value && !livePanState.value,
  'is-panning': Boolean(livePanState.value),
}));
const screenshotCanvasStyle = computed(() => {
  const width = screenshotNaturalWidth.value || selectedScreenshotImage.value?.width || 0;
  const height = screenshotNaturalHeight.value || selectedScreenshotImage.value?.height || 0;
  if (!width || !height) return {};
  const stageWidth = Math.max(1, Math.round(width * displayScale.value / 100));
  const stageHeight = Math.max(1, Math.round(height * displayScale.value / 100));
  return {
    width: `${stageWidth}px`,
    height: `${stageHeight}px`,
  };
});
const screenshotContentStyle = computed(() => ({
  ...screenshotCanvasStyle.value,
  transform: `translate(${screenshotPanX.value}px, ${screenshotPanY.value}px) scale(${screenshotZoomPercent.value / 100})`,
}));
const screenshotViewportClasses = computed(() => ({
  'is-pan-ready': screenshotSpacePressed.value && !screenshotPanState.value,
  'is-panning': Boolean(screenshotPanState.value),
}));
const placeholderText = computed(() => {
  if (!selectedEntryId.value) return '选择设备';
  if (windowViewMode.value === 'off') return '直播已关闭';
  if (!streamEnabled.value) return '画面已暂停';
  if (!streamToken.value) return '正在准备画面流';
  return '等待画面';
});
const screenshotPanelSummary = computed(() => {
  if (!selectedEntryId.value) return '未选设备';
  if (!selectedVisualInstructionKey.value) return '未选指令';
  return selectedScreenshotFilename.value || '未绑定帧';
});
const pseudoOutputText = computed(() => {
  if (pseudoOutputTab.value === 'result') return pseudoExecutionResult.value || '暂无结果';
  return pseudoExecutionLog.value || '暂无日志';
});

const defaultVisualAction = (overrides: Partial<VisualMacroAction> = {}): VisualMacroAction => ({
  version: 1,
  id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
  action: 'click',
  target: 'image',
  label: '',
  frame: '',
  point: null,
  endPoint: null,
  pointRadius: visualMacroDefaultPointRadius.value,
  box: null,
  scan: 'fixed',
  threshold: visualMacroDefaultThreshold.value,
  text: '',
  textMatch: 'contains',
  condition: 'appear',
  timeout: 8,
  ...overrides,
});

const defaultVisualProgram = (operations: VisualMacroAction[] = []): VisualMacroProgram => ({
  version: 1,
  operations,
});

const normalizeVisualPoint = (value: unknown): VisualPoint | null => {
  if (!value || typeof value !== 'object') return null;
  const item = value as Partial<VisualPoint>;
  const x = Math.round(Number(item.x));
  const y = Math.round(Number(item.y));
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y };
};

const normalizeVisualBox = (value: unknown): VisualBox | null => {
  if (!value || typeof value !== 'object') return null;
  const item = value as Partial<VisualBox>;
  const x = Math.round(Number(item.x));
  const y = Math.round(Number(item.y));
  const w = Math.round(Number(item.w));
  const h = Math.round(Number(item.h));
  if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;
  return { x, y, w, h };
};

const normalizeVisualAction = (raw: unknown): VisualMacroAction => {
  const item = raw && typeof raw === 'object' ? raw as Partial<VisualMacroAction> : {};
  const action = ['click', 'drag', 'wait'].includes(String(item.action)) ? item.action as VisualActionKind : 'click';
  const target = ['image', 'text', 'none'].includes(String(item.target)) ? item.target as VisualTargetKind : 'image';
  const scan = ['fixed', 'range', 'full'].includes(String(item.scan)) ? item.scan as VisualScanMode : 'fixed';
  const textMatch = ['contains', 'exact', 'regex'].includes(String(item.textMatch)) ? item.textMatch as VisualTextMatch : 'contains';
  const condition = ['appear', 'disappear', 'stable', 'changed'].includes(String(item.condition)) ? item.condition as VisualCondition : 'appear';
  const threshold = Number(item.threshold);
  const timeout = Number(item.timeout);
  const pointRadius = Number(item.pointRadius);
  return defaultVisualAction({
    id: String(item.id || `${Date.now()}-${Math.random().toString(16).slice(2)}`),
    action,
    target,
    label: String(item.label || ''),
    frame: String(item.frame || ''),
    point: normalizeVisualPoint(item.point),
    endPoint: normalizeVisualPoint(item.endPoint),
    pointRadius: Number.isFinite(pointRadius) ? Math.max(0, Math.round(pointRadius)) : visualMacroDefaultPointRadius.value,
    box: normalizeVisualBox(item.box),
    scan,
    threshold: Number.isFinite(threshold) ? clamp(threshold, 0.5, 1) : visualMacroDefaultThreshold.value,
    text: String(item.text || ''),
    textMatch,
    condition,
    timeout: Number.isFinite(timeout) ? Math.max(0, Math.round(timeout)) : 8,
  });
};

const setVisualMacroDefaultThreshold = (value: unknown, persist = true) => {
  if (value === null || value === undefined || value === '') {
    visualMacroDefaultThreshold.value = 0.88;
    if (persist) window.localStorage.setItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY, String(visualMacroDefaultThreshold.value));
    return;
  }
  const nextValue = Number(value);
  visualMacroDefaultThreshold.value = Number.isFinite(nextValue) ? clamp(nextValue, 0.5, 1) : 0.88;
  if (persist) {
    window.localStorage.setItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY, String(visualMacroDefaultThreshold.value));
  }
};

const setVisualMacroDefaultPointRadius = (value: unknown, persist = true) => {
  if (value === null || value === undefined || value === '') {
    visualMacroDefaultPointRadius.value = 10;
    if (persist) window.localStorage.setItem(VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY, String(visualMacroDefaultPointRadius.value));
    return;
  }
  const nextValue = Math.round(Number(value));
  visualMacroDefaultPointRadius.value = Number.isFinite(nextValue) ? Math.max(0, nextValue) : 10;
  if (persist) {
    window.localStorage.setItem(VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY, String(visualMacroDefaultPointRadius.value));
  }
};

const migrateVisualMacroDefaultThreshold = (value: unknown) => {
  if (window.localStorage.getItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY) !== null) return;
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  setVisualMacroDefaultThreshold(nextValue);
};

const migrateVisualMacroDefaultPointRadius = (value: unknown) => {
  if (window.localStorage.getItem(VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY) !== null) return;
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  setVisualMacroDefaultPointRadius(nextValue);
};

const loadVisualMacroDefaults = () => {
  setVisualMacroDefaultThreshold(window.localStorage.getItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY), false);
  setVisualMacroDefaultPointRadius(window.localStorage.getItem(VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY), false);
};

const normalizeVisualProgram = (raw: unknown): VisualMacroProgram => {
  if (!raw || typeof raw !== 'object') return defaultVisualProgram();
  const item = raw as { defaultThreshold?: unknown; threshold?: unknown; operations?: unknown[] };
  const operations = Array.isArray(item.operations) ? item.operations.map(normalizeVisualAction) : [normalizeVisualAction(raw)];
  migrateVisualMacroDefaultThreshold(item.defaultThreshold ?? item.threshold ?? operations.find((operation) => operation.target === 'image')?.threshold);
  migrateVisualMacroDefaultPointRadius(operations.find((operation) => operation.action === 'click')?.pointRadius);
  return defaultVisualProgram(operations);
};

const parseVisualProgram = (body: string): VisualMacroProgram | null => {
  const start = body.indexOf(VISUAL_ACTION_MARKER_START);
  if (start < 0) return null;
  const jsonStart = start + VISUAL_ACTION_MARKER_START.length;
  const end = body.indexOf(VISUAL_ACTION_MARKER_END, jsonStart);
  if (end < 0) return null;
  try {
    const raw = JSON.parse(body.slice(jsonStart, end).trim());
    return normalizeVisualProgram(raw);
  } catch {
    return null;
  }
};

const visualProgramOf = (card: CodeCard): VisualMacroProgram => {
  const parsed = parseVisualProgram(card.body);
  if (parsed) return parsed;
  return defaultVisualProgram();
};

const visualInstructionsOf = (card: CodeCard) => visualProgramOf(card).operations;
const visualInstructionKey = (cardId: string, instructionId: string) => `${cardId}:${instructionId}`;

const selectVisualInstructionFrame = async (card: CodeCard, instruction: VisualMacroAction) => {
  selectedVisualInstructionKey.value = visualInstructionKey(card.id, instruction.id);
  if (!instruction.frame) {
    clearScreenshotSelection();
    return;
  }
  if (!screenshotPanelOpen.value) {
    screenshotPanelOpen.value = true;
    await nextTick();
  }
  if (!screenshotLoaded.value || !screenshotImages.value.some((item) => item.filename === instruction.frame)) {
    await loadScreenshotList(instruction.frame);
    return;
  }
  await selectScreenshotImage(instruction.frame);
};

const serializeVisualProgram = (program: VisualMacroProgram) => {
  const lines = program.operations.flatMap((operation, index) => {
    const frameNo = visualFrameNo(operation.frame);
    const refText = frameNo && operation.label ? `${frameNo}#${operation.label}` : '';
    return [
      `${index + 1}. ${visualActionSummary(operation)}`,
      operation.target === 'image' ? `   图片匹配：${visualScanLabel(operation.scan)}` : '',
      operation.target === 'text' ? `   文本匹配：${operation.textMatch} ${operation.text || operation.label}` : '',
      refText ? `   标注引用：${refText}` : '',
    ].filter(Boolean);
  });
  return `${VISUAL_ACTION_MARKER_START}\n${JSON.stringify(program, null, 2)}\n${VISUAL_ACTION_MARKER_END}\n${lines.join('\n')}`;
};

const visualFrameNo = (frame: string) => {
  const match = String(frame || '').match(/^(\d{1,4})\./);
  return match ? String(Number(match[1])) : '';
};

const visualActionLabel = (action: VisualActionKind) => ({
  click: '点击',
  drag: '拖拽',
  wait: '等待',
}[action]);

const visualTargetLabel = (target: VisualTargetKind) => ({
  image: '图片',
  text: '文本',
  none: '无目标',
}[target]);

const visualConditionLabel = (condition: VisualCondition) => ({
  appear: '出现',
  disappear: '消失',
  stable: '稳定',
  changed: '变化',
}[condition]);

const visualScanLabel = (scan: VisualScanMode) => ({
  fixed: '固定位置',
  range: '范围搜索',
  full: '全图搜索',
}[scan]);

const visualActionSummary = (action: VisualMacroAction) => {
  const target = action.target === 'text'
    ? `文本「${action.text || action.label || '未填写'}」`
    : action.target === 'image'
      ? `图片「${action.frame || '未绑定帧'}」`
      : '无目标';
  if (action.action === 'wait') {
    return `等待 ${target} ${visualConditionLabel(action.condition)}`;
  }
  return `${visualActionLabel(action.action)} ${target}`;
};

const updateVisualInstruction = (card: CodeCard, operationId: string, patch: Partial<VisualMacroAction>) => {
  const program = visualProgramOf(card);
  const operations = program.operations.map((operation) => (
    operation.id === operationId ? normalizeVisualAction({ ...operation, ...patch, id: operation.id }) : operation
  ));
  card.body = serializeVisualProgram(defaultVisualProgram(operations));
  scheduleCodeCardSave(card);
  if (selectedVisualInstructionKey.value === visualInstructionKey(card.id, operationId)) {
    void nextTick(drawScreenshotOverlay);
  }
};

const clampVisualPointValue = (metric: VisualPointMetric, value: number) => {
  const max = metric === 'x' ? screenshotNaturalWidth.value : screenshotNaturalHeight.value;
  if (!max) return Math.max(0, value);
  return Math.round(clamp(value, 0, Math.max(0, max - 1)));
};

const updateSelectedVisualInstructionPoint = (
  field: VisualPointField,
  metric: VisualPointMetric,
  value: number | undefined,
) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualInstructionContext.value;
  if (!context) return;
  const current = context.instruction[field] ?? { x: 0, y: 0 };
  updateVisualInstruction(context.card, context.instruction.id, {
    [field]: {
      ...current,
      [metric]: clampVisualPointValue(metric, nextValue),
    },
  });
};

const updateSelectedVisualInstructionRadius = (value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualInstructionContext.value;
  if (!context) return;
  const maxRadius = Math.max(screenshotNaturalWidth.value, screenshotNaturalHeight.value, 0);
  updateVisualInstruction(context.card, context.instruction.id, {
    pointRadius: Math.round(clamp(nextValue, 0, maxRadius || Number.MAX_SAFE_INTEGER)),
  });
};

const updateSelectedVisualInstructionThreshold = (value: number | undefined) => {
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    threshold: clamp(nextValue, 0.5, 1),
  });
};

const deleteVisualInstruction = (card: CodeCard, operationId: string) => {
  const program = visualProgramOf(card);
  card.body = serializeVisualProgram(defaultVisualProgram(program.operations.filter((operation) => operation.id !== operationId)));
  if (selectedVisualInstructionKey.value === visualInstructionKey(card.id, operationId)) {
    selectedVisualInstructionKey.value = '';
    clearScreenshotSelection();
  }
  scheduleCodeCardSave(card);
};

const visualPointText = (point: VisualPoint | null) => (
  point ? `${point.x},${point.y}` : '未设置'
);

const normalizeLegacyCodeCards = (raw: unknown): Array<{ title: string; body: string }> => {
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => {
    const card = item as LegacyCodeCard;
    return {
      title: typeof card.title === 'string' ? card.title : '',
      body: typeof card.body === 'string' ? card.body : '',
    };
  }).filter((card) => card.title.trim() || card.body.trim());
};

const sortCodeCards = (cards: CodeCard[]) => {
  return [...cards].sort((a, b) => (
    a.order_index - b.order_index
    || a.created_at - b.created_at
    || a.id.localeCompare(b.id)
  ));
};

const codeCardsByScope = (scope: FanxiuPseudoCodeCardScope) => (
  sortCodeCards(codeCards.value.filter((card) => card.scope === scope))
);

const nextCodeCardOrder = (scope: FanxiuPseudoCodeCardScope) => {
  const scopedCards = codeCardsByScope(scope);
  return scopedCards.length ? Math.max(...scopedCards.map((card) => card.order_index)) + 1 : 0;
};

const importLegacyCodeCards = async () => {
  const rawText = window.localStorage.getItem(LEGACY_CODE_CARDS_STORAGE_KEY);
  if (!rawText) {
    return;
  }
  try {
    const legacyCards = normalizeLegacyCodeCards(JSON.parse(rawText));
    if (!legacyCards.length) {
      window.localStorage.removeItem(LEGACY_CODE_CARDS_STORAGE_KEY);
      return;
    }
    const createdCards: CodeCard[] = [];
    for (const [index, card] of legacyCards.entries()) {
      createdCards.push(await createFanxiuPseudoCodeCard({
        scope: 'action',
        title: card.title || `动作${index + 1}`,
        body: card.body,
        order_index: index,
      }));
    }
    codeCards.value = sortCodeCards([...codeCards.value, ...createdCards]);
    window.localStorage.removeItem(LEGACY_CODE_CARDS_STORAGE_KEY);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const loadCodeCards = async () => {
  codeCardsLoading.value = true;
  try {
    const payload = await listFanxiuPseudoCodeCards();
    codeCards.value = payload.items;
    if (!payload.items.length) await importLegacyCodeCards();
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    codeCardsLoading.value = false;
  }
};

const clearCodeCardSaveTimer = (id: string) => {
  const timer = codeCardSaveTimers.get(id);
  if (!timer) return;
  window.clearTimeout(timer);
  codeCardSaveTimers.delete(id);
};

const applySavedCodeCard = (savedCard: CodeCard) => {
  const index = codeCards.value.findIndex((card) => card.id === savedCard.id);
  if (index >= 0) codeCards.value[index] = savedCard;
};

const saveCodeCardNow = async (card: CodeCard) => {
  clearCodeCardSaveTimer(card.id);
  try {
    const savedCard = await updateFanxiuPseudoCodeCard(card.id, {
      scope: card.scope,
      title: card.title,
      body: card.body,
      enabled: card.enabled,
      order_index: card.order_index,
    });
    applySavedCodeCard(savedCard);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const scheduleCodeCardSave = (card: CodeCard) => {
  clearCodeCardSaveTimer(card.id);
  codeCardSaveTimers.set(card.id, window.setTimeout(() => {
    void saveCodeCardNow(card);
  }, 600));
};

const flushCodeCardSaves = async () => {
  const ids = [...codeCardSaveTimers.keys()];
  const cards = ids
    .map((id) => codeCards.value.find((card) => card.id === id))
    .filter((card): card is CodeCard => Boolean(card));
  await Promise.all(cards.map((card) => saveCodeCardNow(card)));
};

const addCodeCard = async (scope: FanxiuPseudoCodeCardScope) => {
  const scopeInfo = codeCardScopes.find((item) => item.scope === scope);
  const index = codeCardsByScope(scope).length + 1;
  try {
    const card = await createFanxiuPseudoCodeCard({
      scope,
      title: `${scopeInfo?.label ?? '动作'}${index}`,
      body: serializeVisualProgram(defaultVisualProgram()),
      enabled: true,
      order_index: nextCodeCardOrder(scope),
    });
    codeCards.value = sortCodeCards([...codeCards.value, card]);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const deleteCodeCard = async (id: string) => {
  const card = codeCards.value.find((item) => item.id === id);
  if (!card) return;
  try {
    await ElMessageBox.confirm(`删除 ${card.title.trim() || '未命名卡片'}？`, '删除伪代码卡片', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }
  clearCodeCardSaveTimer(id);
  try {
    await deleteFanxiuPseudoCodeCard(id);
    codeCards.value = codeCards.value.filter((item) => item.id !== id);
    expandedCodeCardIds.value = expandedCodeCardIds.value.filter((expandedId) => expandedId !== id);
    if (activeVisualMacroCardId.value === id) activeVisualMacroCardId.value = null;
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const codeCardTitle = (card: CodeCard, scope: FanxiuPseudoCodeCardScope, index: number) => {
  const scopeInfo = codeCardScopes.find((item) => item.scope === scope);
  const instructionCount = visualInstructionsOf(card).length;
  if (instructionCount) return `${index + 1}. ${card.title.trim() || `${scopeInfo?.label ?? '动作'}${index + 1}`} · ${instructionCount} 指令`;
  return `${index + 1}. ${card.title.trim() || `${scopeInfo?.label ?? '卡片'}${index + 1}`}`;
};

const isCodeCardExpanded = (id: string) => expandedCodeCardIds.value.includes(id);

const toggleCodeCard = (id: string) => {
  if (isCodeCardExpanded(id)) {
    expandedCodeCardIds.value = expandedCodeCardIds.value.filter((expandedId) => expandedId !== id);
  } else {
    expandedCodeCardIds.value = [...expandedCodeCardIds.value, id];
  }
};

const applyPseudoCodeRunResponse = (actionLabel: string, response: FanxiuPseudoCodeRunResponse) => {
  const summary = [
    `${actionLabel}完成`,
    response.script_path ? `脚本：${response.script_path}` : '',
    response.compiled_cards ? `卡片：${response.compiled_cards}，缓存命中 ${response.cache_hits}，重新编译 ${response.cache_misses}` : '',
    response.log,
  ].filter(Boolean).join('\n');
  pseudoExecutionLog.value = summary || `${actionLabel}完成`;
  pseudoExecutionResult.value = response.result || '';
};

const compilePseudoCode = async () => {
  await flushCodeCardSaves();
  pseudoCompileLoading.value = true;
  pseudoOutputTab.value = 'log';
  pseudoExecutionLog.value = '编译中...';
  try {
    const response = await compileFanxiuPseudoCode({
      entry_id: selectedEntryId.value,
      timeout: 300,
    });
    applyPseudoCodeRunResponse('编译', response);
    ElMessage.success('伪代码已编译');
  } catch (error) {
    const message = getErrorMessage(error);
    pseudoExecutionLog.value = `编译失败：${message}`;
    ElMessage.error(message);
  } finally {
    pseudoCompileLoading.value = false;
  }
};

const startPseudoCode = async () => {
  pseudoStartLoading.value = true;
  pseudoOutputTab.value = 'log';
  pseudoExecutionLog.value = '启动中...';
  try {
    const response = await startFanxiuPseudoCode({ timeout: 120 });
    applyPseudoCodeRunResponse('启动', response);
    if (response.result) pseudoOutputTab.value = 'result';
    ElMessage.success('伪代码已启动');
  } catch (error) {
    const message = getErrorMessage(error);
    pseudoExecutionLog.value = `启动失败：${message}`;
    ElMessage.error(message);
  } finally {
    pseudoStartLoading.value = false;
  }
};

const streamUrl = computed(() => {
  if (windowViewMode.value === 'off') return '';
  if (!selectedEntryId.value || !streamToken.value) return '';
  const params = new URLSearchParams({
    token: streamToken.value,
    title: targetTitle.value.trim(),
    fps: String(fps.value),
    quality: String(quality.value),
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    auto_dismiss_popup: selectedWindowKey.value === 'sunlogin' && autoDismissPopup.value ? 'true' : 'false',
    popup_check_interval: '3',
    nonce: String(streamNonce.value),
  });
  return `/api/fanxiu/game-window2/stream?${params.toString()}`;
});
const connectionReady = computed(() => Boolean(
  selectedEntryId.value
  && serviceActive.value
  && streamEnabled.value
  && streamUrl.value
  && naturalWidth.value
  && naturalHeight.value
  && !streamError.value
));
const connectionButtonLoading = computed(() => connectionLoading.value || streamTokenLoading.value);
const connectionButtonType = computed(() => (connectionReady.value ? 'success' : 'info'));
const connectionButtonText = computed(() => {
  if (windowViewMode.value === 'off') return '已关闭';
  if (connectionReady.value) return '运行中';
  if (connectionButtonLoading.value || (streamEnabled.value && streamToken.value && !streamError.value)) return '连接中';
  return '连接';
});

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
const isDefaultScreenshotBoxName = (name: string, index = 0) => {
  const trimmed = name.trim();
  return trimmed === `框${index + 1}` || /^框\d+$/.test(trimmed);
};

const getErrorMessage = (error: unknown) => {
  if (typeof error === 'object' && error && 'response' in error) {
    const maybeError = error as { response?: { data?: { detail?: string } }, message?: string };
    return maybeError.response?.data?.detail || maybeError.message || '请求失败';
  }
  return error instanceof Error ? error.message : '请求失败';
};

const getQueryEntryId = () => {
  const raw = route.query.entry_id;
  return Array.isArray(raw) ? raw[0] || '' : raw || '';
};

const isWindowSceneKey = (value: string): value is WindowSceneKey => {
  return windowScenes.some((scene) => scene.key === value);
};

const getQueryWindowKey = () => {
  const raw = route.query.window;
  const value = Array.isArray(raw) ? raw[0] || '' : raw || '';
  return isWindowSceneKey(value) ? value : '';
};

const chooseDefaultEntryId = () => {
  const queryEntryId = getQueryEntryId();
  if (queryEntryId && devices.value.some((device) => device.id === queryEntryId)) return queryEntryId;
  const savedEntryId = window.localStorage.getItem(DEVICE_STORAGE_KEY) || '';
  if (savedEntryId && devices.value.some((device) => device.id === savedEntryId)) return savedEntryId;
  const mi15 = devices.value.find((device) => {
    const haystack = `${device.id} ${device.device_id} ${device.name}`.toLowerCase();
    return haystack.includes('mi15');
  });
  return mi15?.id || devices.value[0]?.id || '';
};

const chooseDefaultWindowKey = (): WindowSceneKey => {
  const queryWindowKey = getQueryWindowKey();
  if (queryWindowKey) return queryWindowKey;
  const savedWindowKey = window.localStorage.getItem(WINDOW_STORAGE_KEY) || '';
  if (isWindowSceneKey(savedWindowKey)) return savedWindowKey;
  return 'star-cloud-phone';
};

const normalizeWindowConfig = (raw: Partial<WindowSceneConfig>, fallback: WindowSceneDefaults): WindowSceneConfig => {
  const rotate = raw.rotateDegrees;
  const nextFps = Number(raw.fps ?? fallback.fps);
  const nextQuality = Number(raw.quality ?? fallback.quality);
  return {
    trimBorderText: raw.trimBorderText || fallback.trimBorderText,
    rotateDegrees: rotate === '0' || rotate === '90' || rotate === '180' || rotate === '270'
      ? rotate
      : fallback.rotateDegrees,
    fps: Number.isFinite(nextFps) ? Math.min(Math.max(Math.round(nextFps), 1), 30) : fallback.fps,
    quality: Number.isFinite(nextQuality) ? Math.min(Math.max(Math.round(nextQuality), 1), 100) : fallback.quality,
    autoDismissPopup: typeof raw.autoDismissPopup === 'boolean' ? raw.autoDismissPopup : fallback.autoDismissPopup,
  };
};

const getWindowConfigStorageKey = (entryId: string, windowKey: WindowSceneKey) => {
  return `${WINDOW_CONFIG_STORAGE_PREFIX}.${entryId || 'default'}.${windowKey}`;
};

const getScreenshotSelectionStorageKey = (entryId = selectedEntryId.value) => {
  return `${SCREENSHOT_SELECTION_STORAGE_PREFIX}.${entryId || 'default'}`;
};

const loadPersistedScreenshotFilename = (entryId = selectedEntryId.value) => {
  if (!entryId) return '';
  return window.localStorage.getItem(getScreenshotSelectionStorageKey(entryId)) || '';
};

const persistSelectedScreenshotFilename = (filename: string, entryId = selectedEntryId.value) => {
  if (!entryId) return;
  if (filename) {
    window.localStorage.setItem(getScreenshotSelectionStorageKey(entryId), filename);
  } else {
    window.localStorage.removeItem(getScreenshotSelectionStorageKey(entryId));
  }
};

const readWindowConfig = (entryId: string, windowKey: WindowSceneKey): WindowSceneConfig => {
  const scene = windowScenes.find((item) => item.key === windowKey) ?? windowScenes[0];
  const rawText = window.localStorage.getItem(getWindowConfigStorageKey(entryId, windowKey));
  if (!rawText) return normalizeWindowConfig({}, scene.defaults);
  try {
    const raw = JSON.parse(rawText) as Partial<WindowSceneConfig>;
    return normalizeWindowConfig(raw, scene.defaults);
  } catch {
    return normalizeWindowConfig({}, scene.defaults);
  }
};

const currentWindowConfig = (): WindowSceneConfig => ({
  trimBorderText: trimBorderText.value,
  rotateDegrees: rotateDegrees.value,
  fps: Number(fps.value) || selectedWindowScene.value.defaults.fps,
  quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
  autoDismissPopup: autoDismissPopup.value,
});

const persistWindowConfig = () => {
  if (isApplyingWindowConfig || !selectedWindowKey.value) return;
  const key = getWindowConfigStorageKey(selectedEntryId.value, selectedWindowKey.value);
  window.localStorage.setItem(key, JSON.stringify(currentWindowConfig()));
};

const applyWindowConfig = () => {
  isApplyingWindowConfig = true;
  const config = readWindowConfig(selectedEntryId.value, selectedWindowKey.value);
  trimBorderText.value = config.trimBorderText;
  rotateDegrees.value = config.rotateDegrees;
  resetLiveViewState();
  fps.value = config.fps;
  quality.value = config.quality;
  autoDismissPopup.value = config.autoDismissPopup;
  window.requestAnimationFrame(() => {
    isApplyingWindowConfig = false;
  });
};

const persistEntrySelection = (entryId: string) => {
  if (entryId) {
    window.localStorage.setItem(DEVICE_STORAGE_KEY, entryId);
  } else {
    window.localStorage.removeItem(DEVICE_STORAGE_KEY);
  }
  const nextQuery = { ...route.query };
  if (entryId) {
    nextQuery.entry_id = entryId;
  } else {
    delete nextQuery.entry_id;
  }
  nextQuery.window = selectedWindowKey.value;
  void router.replace({ path: route.path, query: nextQuery });
};

const persistWindowSelection = () => {
  window.localStorage.setItem(WINDOW_STORAGE_KEY, selectedWindowKey.value);
  const nextQuery = { ...route.query, window: selectedWindowKey.value };
  if (selectedEntryId.value) nextQuery.entry_id = selectedEntryId.value;
  void router.replace({ path: route.path, query: nextQuery });
};

const refreshStreamToken = async () => {
  const entryId = selectedEntryId.value;
  if (!entryId || windowViewMode.value === 'off') {
    streamToken.value = '';
    streamTokenExpiresAt.value = 0;
    return;
  }
  const requestSeq = ++tokenRequestSeq;
  streamTokenLoading.value = true;
  try {
    const payload = await createFanxiuGameWindow2StreamToken(entryId);
    if (requestSeq !== tokenRequestSeq || selectedEntryId.value !== entryId) return;
    streamToken.value = payload.token;
    streamTokenExpiresAt.value = Date.now() + Math.max(60, payload.expires_in_seconds - 30) * 1000;
  } catch (error) {
    if (requestSeq === tokenRequestSeq) {
      streamToken.value = '';
      streamTokenExpiresAt.value = 0;
      ElMessage.error(getErrorMessage(error));
    }
  } finally {
    if (requestSeq === tokenRequestSeq) streamTokenLoading.value = false;
  }
};

const ensureStreamToken = async () => {
  if (windowViewMode.value === 'off') return;
  if (streamToken.value && streamTokenExpiresAt.value > Date.now() + 60_000) return;
  await refreshStreamToken();
};

const loadRuntimeStatus = async (silent = false) => {
  const entryId = selectedEntryId.value;
  if (!entryId || windowViewMode.value === 'off') {
    runtimeStatus.value = null;
    runtimeLoading.value = false;
    return;
  }
  runtimeLoading.value = !silent;
  try {
    runtimeStatus.value = await fetchRuntimeStatus(entryId);
  } catch (error) {
    if (!silent) ElMessage.error(getErrorMessage(error));
  } finally {
    runtimeLoading.value = false;
  }
};

const handleEntryChange = async () => {
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  streamToken.value = '';
  streamTokenExpiresAt.value = 0;
  screenshotImages.value = [];
  screenshotLoaded.value = false;
  clearScreenshotSelection();
  clearMatchResults();
  persistEntrySelection(selectedEntryId.value);
  applyWindowConfig();
  if (windowViewMode.value !== 'off') {
    await Promise.all([refreshStreamToken(), loadRuntimeStatus()]);
  }
  if (screenshotPanelOpen.value) await loadScreenshotList();
  restartStream();
};

const handleWindowChange = async () => {
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  clearMatchResults();
  persistWindowSelection();
  applyWindowConfig();
  await restartStream();
};

const handleWindowViewModeChange = async () => {
  controlClickState.value = null;
  if (windowViewMode.value === 'off') {
    streamError.value = '';
    streamEnabled.value = false;
    controlEnabled.value = false;
    runtimeStatus.value = null;
    streamToken.value = '';
    streamTokenExpiresAt.value = 0;
    if (streamImageRef.value) streamImageRef.value.src = '';
    await nextTick(syncCanvas);
    return;
  }
  await restartStream();
};

const normalizeBox = (box: OverlayBox): OverlayBox => {
  const x1 = clamp(Math.min(box.x, box.x + box.w), 0, naturalWidth.value || Number.MAX_SAFE_INTEGER);
  const y1 = clamp(Math.min(box.y, box.y + box.h), 0, naturalHeight.value || Number.MAX_SAFE_INTEGER);
  const x2 = clamp(Math.max(box.x, box.x + box.w), 0, naturalWidth.value || Number.MAX_SAFE_INTEGER);
  const y2 = clamp(Math.max(box.y, box.y + box.h), 0, naturalHeight.value || Number.MAX_SAFE_INTEGER);
  return {
    ...box,
    x: Math.round(x1),
    y: Math.round(y1),
    w: Math.round(Math.max(0, x2 - x1)),
    h: Math.round(Math.max(0, y2 - y1)),
  };
};

const getCanvasDisplaySize = () => {
  const canvas = overlayCanvasRef.value;
  if (!canvas) return { width: 0, height: 0 };
  return { width: canvas.offsetWidth, height: canvas.offsetHeight };
};

const drawBox = (
  ctx: CanvasRenderingContext2D,
  box: OverlayBox,
  displayWidth: number,
  displayHeight: number,
  options: { active?: boolean; draft?: boolean } = {},
) => {
  if (!naturalWidth.value || !naturalHeight.value) return;
  const scaleX = displayWidth / naturalWidth.value;
  const scaleY = displayHeight / naturalHeight.value;
  const x = box.x * scaleX;
  const y = box.y * scaleY;
  const w = box.w * scaleX;
  const h = box.h * scaleY;

  ctx.save();
  ctx.lineWidth = options.active ? 2 : 1.5;
  ctx.strokeStyle = options.draft ? '#e6a23c' : (options.active ? '#ff4d4f' : '#409eff');
  ctx.fillStyle = options.active ? 'rgba(255, 77, 79, 0.12)' : 'rgba(64, 158, 255, 0.08)';
  if (options.draft) ctx.setLineDash([6, 4]);
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
  if (!options.draft) {
    const label = box.name || '未命名';
    ctx.font = '12px sans-serif';
    const labelWidth = ctx.measureText(label).width + 10;
    ctx.fillStyle = options.active ? '#ff4d4f' : '#409eff';
    ctx.fillRect(x, Math.max(0, y - 20), labelWidth, 18);
    ctx.fillStyle = '#fff';
    ctx.fillText(label, x + 5, Math.max(13, y - 7));
  }
  ctx.restore();
};

const drawOverlay = () => {
  const canvas = overlayCanvasRef.value;
  if (!canvas) return;

  const { width, height } = getCanvasDisplaySize();
  const dpr = window.devicePixelRatio || 1;
  const pixelWidth = Math.max(1, Math.round(width * dpr));
  const pixelHeight = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!layerVisible.value) return;

  boxes.value.forEach((box) => {
    drawBox(ctx, box, width, height, { active: selectedBoxId.value === box.id });
  });
  if (draftBox.value) {
    drawBox(ctx, normalizeBox(draftBox.value), width, height, { draft: true });
  }
};

const syncCanvas = () => {
  const canvas = overlayCanvasRef.value;
  const wrap = imageWrapRef.value;
  if (!canvas || !wrap) return;
  canvas.style.width = `${wrap.offsetWidth}px`;
  canvas.style.height = `${wrap.offsetHeight}px`;
  drawOverlay();
};

const syncCanvasSoon = () => {
  void nextTick(() => {
    clampLivePan();
    clampScreenshotPan();
    syncCanvas();
    syncScreenshotCanvas();
  });
};

const resetLiveViewState = () => {
  livePanState.value = null;
  liveContentZoomPercent.value = 100;
  livePanX.value = 0;
  livePanY.value = 0;
  displayScale.value = selectedWindowScene.value.defaults.displayScale;
  void nextTick(() => {
    syncCanvas();
  });
};

const resetScreenshotViewState = () => {
  screenshotPanState.value = null;
  screenshotZoomPercent.value = normalizeScreenshotZoomPercent(100);
  screenshotPanX.value = 0;
  screenshotPanY.value = 0;
  void nextTick(() => {
    syncScreenshotCanvas();
  });
};

const clampContentPan = (
  panX: number,
  panY: number,
  viewport: HTMLElement | null,
  zoomPercent: number,
) => {
  if (!viewport) return { x: panX, y: panY };
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  if (!width || !height) return { x: panX, y: panY };

  const scale = Math.max(0.01, zoomPercent / 100);
  const contentWidth = width * scale;
  const contentHeight = height * scale;
  const maxX = width * (1 - MIN_CONTENT_VISIBLE_AXIS_RATIO);
  const maxY = height * (1 - MIN_CONTENT_VISIBLE_AXIS_RATIO);
  const minX = width * MIN_CONTENT_VISIBLE_AXIS_RATIO - contentWidth;
  const minY = height * MIN_CONTENT_VISIBLE_AXIS_RATIO - contentHeight;

  return {
    x: clamp(panX, Math.min(minX, maxX), Math.max(minX, maxX)),
    y: clamp(panY, Math.min(minY, maxY), Math.max(minY, maxY)),
  };
};

const clampLivePan = () => {
  const nextPan = clampContentPan(
    livePanX.value,
    livePanY.value,
    liveViewportRef.value,
    liveContentZoomPercent.value,
  );
  livePanX.value = nextPan.x;
  livePanY.value = nextPan.y;
};

const clampScreenshotPan = () => {
  const nextPan = clampContentPan(
    screenshotPanX.value,
    screenshotPanY.value,
    screenshotViewportRef.value,
    screenshotZoomPercent.value,
  );
  screenshotPanX.value = nextPan.x;
  screenshotPanY.value = nextPan.y;
};

const setLiveContentZoomPercent = async (
  value: number,
  options?: { anchorClientX?: number; anchorClientY?: number },
) => {
  const nextScale = clamp(Math.round(Number(value) / 5) * 5, 20, 500);
  if (!Number.isFinite(nextScale) || nextScale === liveContentZoomPercent.value) return;
  const viewport = liveViewportRef.value;
  if (!viewport) {
    liveContentZoomPercent.value = nextScale;
    await nextTick(syncCanvas);
    return;
  }

  const viewportRect = viewport.getBoundingClientRect();
  const anchorClientX = options?.anchorClientX ?? (viewportRect.left + viewportRect.width / 2);
  const anchorClientY = options?.anchorClientY ?? (viewportRect.top + viewportRect.height / 2);
  const currentZoom = liveContentZoomPercent.value / 100;
  const nextZoom = nextScale / 100;
  const anchorX = anchorClientX - viewportRect.left;
  const anchorY = anchorClientY - viewportRect.top;
  const contentX = (anchorX - livePanX.value) / currentZoom;
  const contentY = (anchorY - livePanY.value) / currentZoom;

  liveContentZoomPercent.value = nextScale;
  livePanX.value = anchorX - contentX * nextZoom;
  livePanY.value = anchorY - contentY * nextZoom;
  clampLivePan();
  await nextTick();
  clampLivePan();
  syncCanvas();
};

const resetLiveContentView = async () => {
  liveContentZoomPercent.value = 100;
  livePanX.value = 0;
  livePanY.value = 0;
  await nextTick();
  syncCanvas();
};

const handleLiveWheel = (event: WheelEvent) => {
  if (!(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const delta = event.deltaY > 0 ? -5 : 5;
  void setLiveContentZoomPercent(liveContentZoomPercent.value + delta, {
    anchorClientX: event.clientX,
    anchorClientY: event.clientY,
  });
};

const beginLivePan = (event: MouseEvent) => {
  const viewport = liveViewportRef.value;
  if (!viewport) return;
  livePanState.value = {
    startClientX: event.clientX,
    startClientY: event.clientY,
    startPanX: livePanX.value,
    startPanY: livePanY.value,
  };
  document.addEventListener('mousemove', handleLiveWindowPanMove);
  document.addEventListener('mouseup', stopLivePan);
};

function handleLiveWindowPanMove(event: MouseEvent) {
  const viewport = liveViewportRef.value;
  const state = livePanState.value;
  if (!viewport || !state) return;
  livePanX.value = state.startPanX + (event.clientX - state.startClientX);
  livePanY.value = state.startPanY + (event.clientY - state.startClientY);
  clampLivePan();
}

function stopLivePan() {
  livePanState.value = null;
  document.removeEventListener('mousemove', handleLiveWindowPanMove);
  document.removeEventListener('mouseup', stopLivePan);
}

const handleLiveViewportMouseDown = (event: MouseEvent) => {
  const shouldPan = event.button === 1 || (event.button === 0 && screenshotSpacePressed.value);
  if (!shouldPan) return;
  event.preventDefault();
  event.stopPropagation();
  beginLivePan(event);
};

const drawScreenshotBox = (
  ctx: CanvasRenderingContext2D,
  box: OverlayBox,
  displayWidth: number,
  displayHeight: number,
  options: { active?: boolean; draft?: boolean } = {},
) => {
  if (!screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return;
  const scaleX = displayWidth / screenshotNaturalWidth.value;
  const scaleY = displayHeight / screenshotNaturalHeight.value;
  const x = box.x * scaleX;
  const y = box.y * scaleY;
  const w = box.w * scaleX;
  const h = box.h * scaleY;

  ctx.save();
  ctx.lineWidth = options.active ? 2 : 1.5;
  ctx.strokeStyle = options.draft ? '#e6a23c' : (options.active ? '#ff4d4f' : '#f97316');
  ctx.fillStyle = options.active ? 'rgba(255, 77, 79, 0.12)' : 'rgba(249, 115, 22, 0.08)';
  if (options.draft) ctx.setLineDash([6, 4]);
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
  if (options.active && !options.draft) {
    const handleSize = 8;
    ctx.fillStyle = '#fff';
    ctx.strokeStyle = '#ff4d4f';
    ctx.lineWidth = 1.5;
    ctx.fillRect(x - handleSize / 2, y - handleSize / 2, handleSize, handleSize);
    ctx.strokeRect(x - handleSize / 2, y - handleSize / 2, handleSize, handleSize);
    ctx.fillRect(x + w - handleSize / 2, y + h - handleSize / 2, handleSize, handleSize);
    ctx.strokeRect(x + w - handleSize / 2, y + h - handleSize / 2, handleSize, handleSize);
  }
  ctx.restore();
};

const screenshotDisplayPoint = (point: VisualPoint, displayWidth: number, displayHeight: number) => {
  const scaleX = displayWidth / screenshotNaturalWidth.value;
  const scaleY = displayHeight / screenshotNaturalHeight.value;
  return {
    x: point.x * scaleX,
    y: point.y * scaleY,
  };
};

const screenshotDisplayRadius = (radius: number, displayWidth: number, displayHeight: number) => {
  if (!screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return 0;
  const scaleX = displayWidth / screenshotNaturalWidth.value;
  const scaleY = displayHeight / screenshotNaturalHeight.value;
  return Math.max(0, radius) * Math.max(scaleX, scaleY);
};

const drawScreenshotClickPoint = (
  ctx: CanvasRenderingContext2D,
  point: VisualPoint,
  radius: number,
  displayWidth: number,
  displayHeight: number,
) => {
  if (!screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return;
  const p = screenshotDisplayPoint(point, displayWidth, displayHeight);
  const displayRadius = screenshotDisplayRadius(radius, displayWidth, displayHeight);
  ctx.save();
  ctx.strokeStyle = '#2563eb';
  ctx.lineWidth = 2;
  if (displayRadius > 0) {
    ctx.fillStyle = 'rgba(37, 99, 235, 0.12)';
    ctx.beginPath();
    ctx.arc(p.x, p.y, displayRadius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.beginPath();
  const crossSize = 12;
  ctx.moveTo(p.x - crossSize, p.y);
  ctx.lineTo(p.x + crossSize, p.y);
  ctx.moveTo(p.x, p.y - crossSize);
  ctx.lineTo(p.x, p.y + crossSize);
  ctx.stroke();
  ctx.restore();
};

const drawScreenshotDragPoints = (
  ctx: CanvasRenderingContext2D,
  start: VisualPoint,
  end: VisualPoint,
  displayWidth: number,
  displayHeight: number,
) => {
  if (!screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return;
  const from = screenshotDisplayPoint(start, displayWidth, displayHeight);
  const to = screenshotDisplayPoint(end, displayWidth, displayHeight);
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const headSize = 10;

  ctx.save();
  ctx.strokeStyle = '#2563eb';
  ctx.fillStyle = '#2563eb';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(to.x, to.y);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(to.x - headSize * Math.cos(angle - Math.PI / 6), to.y - headSize * Math.sin(angle - Math.PI / 6));
  ctx.lineTo(to.x - headSize * Math.cos(angle + Math.PI / 6), to.y - headSize * Math.sin(angle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.arc(from.x, from.y, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
};

const drawSelectedVisualInstructionInput = (
  ctx: CanvasRenderingContext2D,
  displayWidth: number,
  displayHeight: number,
) => {
  const instruction = selectedVisualInstruction.value;
  if (!instruction || instruction.frame !== selectedScreenshotFilename.value || !instruction.point) return;
  if (instruction.action === 'drag' && instruction.endPoint) {
    drawScreenshotDragPoints(ctx, instruction.point, instruction.endPoint, displayWidth, displayHeight);
    return;
  }
  drawScreenshotClickPoint(ctx, instruction.point, instruction.pointRadius, displayWidth, displayHeight);
};

const drawScreenshotOverlay = () => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas) return;

  const width = canvas.offsetWidth;
  const height = canvas.offsetHeight;
  const dpr = window.devicePixelRatio || 1;
  const pixelWidth = Math.max(1, Math.round(width * dpr));
  const pixelHeight = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  screenshotBoxes.value.forEach((box) => {
    drawScreenshotBox(ctx, box, width, height, { active: selectedScreenshotBoxId.value === box.id });
  });
  if (screenshotDraftBox.value) {
    drawScreenshotBox(ctx, normalizeScreenshotBox(screenshotDraftBox.value), width, height, { draft: true });
  }
  drawSelectedVisualInstructionInput(ctx, width, height);
};

const syncScreenshotCanvas = () => {
  const canvas = screenshotOverlayCanvasRef.value;
  const wrap = screenshotImageWrapRef.value;
  if (!canvas || !wrap) return;
  canvas.style.width = `${wrap.offsetWidth}px`;
  canvas.style.height = `${wrap.offsetHeight}px`;
  drawScreenshotOverlay();
};

const normalizeScreenshotZoomPercent = (value: number) => {
  if (!Number.isFinite(value)) return 100;
  return clamp(Math.round(value / SCREENSHOT_ZOOM_STEP) * SCREENSHOT_ZOOM_STEP, SCREENSHOT_MIN_ZOOM_PERCENT, SCREENSHOT_MAX_ZOOM_PERCENT);
};

const resetScreenshotContentView = async () => {
  screenshotZoomPercent.value = normalizeScreenshotZoomPercent(100);
  screenshotPanX.value = 0;
  screenshotPanY.value = 0;
  await nextTick();
  syncScreenshotCanvas();
};

const setScreenshotZoomPercent = async (
  value: number,
  options?: { anchorClientX?: number; anchorClientY?: number },
) => {
  const nextZoom = normalizeScreenshotZoomPercent(value);
  const currentZoom = normalizeScreenshotZoomPercent(screenshotZoomPercent.value);
  if (nextZoom === currentZoom) return;
  const viewport = screenshotViewportRef.value;
  if (!viewport) {
    screenshotZoomPercent.value = nextZoom;
    await nextTick(syncScreenshotCanvas);
    return;
  }

  const viewportRect = viewport.getBoundingClientRect();
  const anchorClientX = options?.anchorClientX ?? (viewportRect.left + viewportRect.width / 2);
  const anchorClientY = options?.anchorClientY ?? (viewportRect.top + viewportRect.height / 2);
  const currentScale = currentZoom / 100;
  const nextScale = nextZoom / 100;
  const anchorX = anchorClientX - viewportRect.left;
  const anchorY = anchorClientY - viewportRect.top;
  const contentX = (anchorX - screenshotPanX.value) / currentScale;
  const contentY = (anchorY - screenshotPanY.value) / currentScale;

  screenshotZoomPercent.value = nextZoom;
  screenshotPanX.value = anchorX - contentX * nextScale;
  screenshotPanY.value = anchorY - contentY * nextScale;
  clampScreenshotPan();
  await nextTick();
  clampScreenshotPan();
  syncScreenshotCanvas();
};

const handleScreenshotWheel = (event: WheelEvent) => {
  if (!(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const delta = event.deltaY > 0 ? -SCREENSHOT_ZOOM_STEP : SCREENSHOT_ZOOM_STEP;
  void setScreenshotZoomPercent(screenshotZoomPercent.value + delta, {
    anchorClientX: event.clientX,
    anchorClientY: event.clientY,
  });
};

const beginScreenshotPan = (event: MouseEvent) => {
  const viewport = screenshotViewportRef.value;
  if (!viewport) return;
  screenshotPanState.value = {
    startClientX: event.clientX,
    startClientY: event.clientY,
    startPanX: screenshotPanX.value,
    startPanY: screenshotPanY.value,
  };
  document.addEventListener('mousemove', handleScreenshotWindowPanMove);
  document.addEventListener('mouseup', stopScreenshotPan);
};

function handleScreenshotWindowPanMove(event: MouseEvent) {
  const viewport = screenshotViewportRef.value;
  const state = screenshotPanState.value;
  if (!viewport || !state) return;
  screenshotPanX.value = state.startPanX + (event.clientX - state.startClientX);
  screenshotPanY.value = state.startPanY + (event.clientY - state.startClientY);
  clampScreenshotPan();
}

function stopScreenshotPan() {
  screenshotPanState.value = null;
  document.removeEventListener('mousemove', handleScreenshotWindowPanMove);
  document.removeEventListener('mouseup', stopScreenshotPan);
}

const handleScreenshotViewportMouseDown = (event: MouseEvent) => {
  const shouldPan = event.button === 1 || (event.button === 0 && screenshotSpacePressed.value);
  if (!shouldPan) return;
  event.preventDefault();
  event.stopPropagation();
  beginScreenshotPan(event);
};

const drawMatchOverlay = () => {
  const canvas = matchOverlayCanvasRef.value;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const dpr = window.devicePixelRatio || 1;
  const pixelWidth = Math.max(1, Math.round(width * dpr));
  const pixelHeight = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const result = selectedMatchResult.value;
  if (!result || !result.width || !result.height) return;

  const drawMatchBox = (box: FanxiuGameWindow2MatchBox, color: string, fill: string, dashed = false) => {
    const scaleX = width / result.width;
    const scaleY = height / result.height;
    const x = box.x * scaleX;
    const y = box.y * scaleY;
    const w = box.w * scaleX;
    const h = box.h * scaleY;
    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.fillStyle = fill;
    if (dashed) ctx.setLineDash([6, 4]);
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
    ctx.restore();
  };

  const fixedBox = result.current_box || result.box;
  drawMatchBox(fixedBox, '#10b981', 'rgba(16, 185, 129, 0.12)');
  if (result.template_box) {
    drawMatchBox(result.template_box, '#f97316', 'rgba(249, 115, 22, 0.1)', true);
  }
};

const syncMatchCanvas = () => {
  const canvas = matchOverlayCanvasRef.value;
  const wrap = matchImageWrapRef.value;
  if (!canvas || !wrap) return;
  const rect = wrap.getBoundingClientRect();
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  drawMatchOverlay();
};

const handleImageLoad = () => {
  const image = streamImageRef.value;
  if (!image) return;
  streamError.value = '';
  naturalWidth.value = image.naturalWidth;
  naturalHeight.value = image.naturalHeight;
  void nextTick(syncCanvas);
};

const handleStreamError = () => {
  if (windowViewMode.value === 'off') return;
  const message = '未获取到画面，检查设备入口、画面流服务和窗口场景。';
  streamError.value = message;
  ElMessage.error(message);
};

const restartStream = async () => {
  streamError.value = '';
  if (windowViewMode.value === 'off') {
    streamEnabled.value = false;
    controlEnabled.value = false;
    runtimeStatus.value = null;
    streamToken.value = '';
    streamTokenExpiresAt.value = 0;
    if (streamImageRef.value) streamImageRef.value.src = '';
    await nextTick(syncCanvas);
    return;
  }
  streamEnabled.value = true;
  controlEnabled.value = windowViewMode.value === 'control';
  await ensureStreamToken();
  streamNonce.value = Date.now();
  void nextTick(syncCanvas);
};

const connectWindow = async () => {
  if (!selectedEntryId.value) return;
  if (windowViewMode.value === 'off') {
    streamEnabled.value = false;
    controlEnabled.value = false;
    runtimeStatus.value = null;
    streamToken.value = '';
    streamTokenExpiresAt.value = 0;
    if (streamImageRef.value) streamImageRef.value.src = '';
    return;
  }
  connectionLoading.value = true;
  try {
    if (!serviceActive.value) {
      await triggerRuntimeItem(selectedEntryId.value, 'builtin', GAME_WINDOW_SERVICE_KEY);
    }
    await loadRuntimeStatus(true);
    await restartStream();
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    connectionLoading.value = false;
  }
};

const saveCurrentFrame = async () => {
  if (!selectedEntryId.value) return;
  saveFrameLoading.value = true;
  try {
    const result = await saveFanxiuGameWindow2Frame({
      entry_id: selectedEntryId.value,
      title: targetTitle.value.trim(),
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: 0,
      fixed_height: 0,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    });
    ElMessage.success(`已保存 截图/${result.filename}`);
    if (screenshotPanelOpen.value) {
      await loadScreenshotList(result.filename);
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    saveFrameLoading.value = false;
  }
};

const captureVisualMacroFrame = async () => {
  const result = await saveFanxiuGameWindow2Frame({
    entry_id: selectedEntryId.value,
    title: targetTitle.value.trim(),
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    fixed_width: 0,
    fixed_height: 0,
    quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
  });
  if (screenshotPanelOpen.value) {
    void loadScreenshotList(result.filename);
  }
  return result;
};

const buildDefaultImageBox = (point: VisualPoint, size = 50): VisualBox => {
  const half = Math.round(size / 2);
  const maxWidth = Math.max(1, naturalWidth.value || size);
  const maxHeight = Math.max(1, naturalHeight.value || size);
  const x = Math.round(clamp(point.x - half, 0, Math.max(0, maxWidth - size)));
  const y = Math.round(clamp(point.y - half, 0, Math.max(0, maxHeight - size)));
  return {
    x,
    y,
    w: Math.min(size, maxWidth),
    h: Math.min(size, maxHeight),
  };
};

const appendVisualMacroAction = async (
  cardId: string,
  action: VisualActionKind,
  frame: string,
  point: VisualPoint | null,
  endPoint: VisualPoint | null = null,
  durationMs = 0,
) => {
  const card = codeCards.value.find((item) => item.id === cardId);
  if (!card) return;
  const program = visualProgramOf(card);
  const index = program.operations.length + 1;
  const visualAction = defaultVisualAction({
    action,
    target: action === 'drag' ? 'none' : 'image',
    label: '',
    frame,
    point,
    endPoint,
    box: point ? buildDefaultImageBox(point) : null,
    timeout: action === 'drag' ? Math.round(clamp(durationMs / 1000, 0, 120)) : 8,
  });
  card.body = serializeVisualProgram(defaultVisualProgram([...program.operations, visualAction]));
  await saveCodeCardNow(card);
  await selectVisualInstructionFrame(card, visualAction);
};

const recordVisualMacroInput = async (
  action: VisualActionKind,
  point: VisualPoint | null,
  endPoint: VisualPoint | null = null,
  durationMs = 0,
) => {
  if (!activeVisualMacroCardId.value || !selectedEntryId.value) return;
  if (visualMacroCapturePending.value) return;
  visualMacroCapturePending.value = true;
  try {
    const frame = await captureVisualMacroFrame();
    await appendVisualMacroAction(activeVisualMacroCardId.value, action, frame.filename, point, endPoint, durationMs);
    ElMessage.success('已追加视觉指令');
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    visualMacroCapturePending.value = false;
  }
};

const toggleVisualMacroRecording = (cardId: string) => {
  if (!selectedEntryId.value) {
    ElMessage.warning('先选择设备并连接画面');
    return;
  }
  activeVisualMacroCardId.value = activeVisualMacroCardId.value === cardId ? null : cardId;
  if (!activeVisualMacroCardId.value) {
    visualMacroCapturePending.value = false;
    return;
  }
  if (!controlEnabled.value) {
    windowViewMode.value = 'control';
    controlEnabled.value = true;
  }
  const card = codeCards.value.find((item) => item.id === cardId);
  if (card && !isCodeCardExpanded(card.id)) {
    expandedCodeCardIds.value = [...expandedCodeCardIds.value, card.id];
  }
};

const toMatchBoxPayload = (box: OverlayBox): FanxiuGameWindow2MatchBox => ({
  name: box.name.trim(),
  x: Math.round(box.x),
  y: Math.round(box.y),
  w: Math.round(box.w),
  h: Math.round(box.h),
});

const appendMatchResult = (response: FanxiuGameWindow2MatchResponse, imageUrl: string) => {
  const item: MatchResult = {
    ...response,
    id: `${response.match_filename}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    imageUrl,
    createdAt: Date.now(),
  };
  matchResults.value = [item, ...matchResults.value];
  selectedMatchEntryId.value = `${item.id}:fixed`;
};

const clearMatchResults = () => {
  matchResults.value.forEach((item) => URL.revokeObjectURL(item.imageUrl));
  matchResults.value = [];
  selectedMatchEntryId.value = '';
};

const handleMatchImageLoad = () => {
  void nextTick(syncMatchCanvas);
};

const selectMatchEntry = (id: string) => {
  selectedMatchEntryId.value = id;
  void nextTick(syncMatchCanvas);
};

const runScreenshotBoxMatch = async (box: OverlayBox) => {
  if (!selectedEntryId.value || !selectedScreenshotFilename.value) return;
  matchingBoxId.value = box.id;
  try {
    await flushScreenshotAutosave();
    const response = await matchFanxiuGameWindow2Screenshot({
      entry_id: selectedEntryId.value,
      filename: selectedScreenshotFilename.value,
      box: toMatchBoxPayload(box),
      title: targetTitle.value.trim(),
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: 0,
      fixed_height: 0,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    });
    const blob = await getFanxiuGameWindow2MatchImage(selectedEntryId.value, response.match_filename);
    appendMatchResult(response, URL.createObjectURL(blob));
    await nextTick();
    syncMatchCanvas();
    const fixedText = `原位${response.fixed_similarity ?? response.similarity}%`;
    const templateText = response.template_similarity === undefined ? '' : ` 模板${response.template_similarity}%`;
    ElMessage.success(`${fixedText}${templateText}`);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    matchingBoxId.value = null;
  }
};

const revokeScreenshotImageUrl = () => {
  if (!screenshotImageUrl.value) return;
  URL.revokeObjectURL(screenshotImageUrl.value);
  screenshotImageUrl.value = '';
};

const toScreenshotOverlayBoxes = (boxesPayload: FanxiuGameWindow2PreLabelBox[], filename: string): OverlayBox[] => {
  return boxesPayload.map((box, index) => ({
    id: `${filename}-${index}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: isDefaultScreenshotBoxName(box.name || '', index) ? '' : box.name || '',
    x: Number(box.x) || 0,
    y: Number(box.y) || 0,
    w: Number(box.w) || 0,
    h: Number(box.h) || 0,
  }));
};

const toPreLabelPayload = (): FanxiuGameWindow2PreLabelPayload => ({
  version: 1,
  image: selectedScreenshotFilename.value,
  size: {
    width: screenshotNaturalWidth.value || selectedScreenshotImage.value?.width || 0,
    height: screenshotNaturalHeight.value || selectedScreenshotImage.value?.height || 0,
  },
  boxes: screenshotBoxes.value.map((box) => ({
    name: box.name,
    x: Math.round(box.x),
    y: Math.round(box.y),
    w: Math.round(box.w),
    h: Math.round(box.h),
  })),
});

const clearScreenshotSelection = () => {
  cancelScreenshotJump();
  closeScreenshotContextMenus();
  selectedScreenshotFilename.value = '';
  screenshotNaturalWidth.value = 0;
  screenshotNaturalHeight.value = 0;
  screenshotBoxes.value = [];
  selectedScreenshotBoxId.value = null;
  screenshotDraftState.value = null;
  screenshotDraftBox.value = null;
  screenshotResizeState.value = null;
  resetScreenshotViewState();
  screenshotDirty.value = false;
  revokeScreenshotImageUrl();
  drawScreenshotOverlay();
};

const extractScreenshotNumber = (filename: string) => {
  const match = /^0*(\d+)\.[^.]+$/i.exec(filename);
  return match ? Number(match[1]) : NaN;
};

const findScreenshotByJumpText = (text: string) => {
  const value = text.trim();
  if (!value) return null;
  const lowerValue = value.toLowerCase();
  const exact = screenshotImages.value.find((item) => item.filename.toLowerCase() === lowerValue);
  if (exact) return exact;

  if (!/^\d+$/.test(value)) return null;
  const targetNumber = Number(value);
  if (!Number.isSafeInteger(targetNumber) || targetNumber <= 0) return null;
  const paddedFilename = `${String(targetNumber).padStart(4, '0')}.jpg`;
  return screenshotImages.value.find((item) => item.filename.toLowerCase() === paddedFilename)
    ?? screenshotImages.value.find((item) => extractScreenshotNumber(item.filename) === targetNumber)
    ?? null;
};

const startScreenshotJumpEdit = async () => {
  if (!screenshotImages.value.length || screenshotJumpEditing.value) return;
  const currentNumber = extractScreenshotNumber(selectedScreenshotFilename.value);
  screenshotJumpText.value = Number.isFinite(currentNumber) ? String(currentNumber) : selectedScreenshotFilename.value;
  screenshotJumpEditing.value = true;
  await nextTick();
  screenshotJumpInputRef.value?.focus();
};

const cancelScreenshotJump = () => {
  screenshotJumpEditing.value = false;
  screenshotJumpText.value = '';
};

const confirmScreenshotJump = async () => {
  const target = findScreenshotByJumpText(screenshotJumpText.value);
  if (!target) {
    ElMessage.warning('未找到对应截图');
    return;
  }
  cancelScreenshotJump();
  await selectScreenshotImage(target.filename);
};

const loadScreenshotList = async (preferFilename = '') => {
  if (!selectedEntryId.value) {
    clearScreenshotSelection();
    screenshotImages.value = [];
    screenshotLoaded.value = false;
    return;
  }
  screenshotLoading.value = true;
  try {
    const payload = await listFanxiuGameWindow2Screenshots(selectedEntryId.value);
    screenshotImages.value = payload.items;
    screenshotLoaded.value = true;
    const persistedFilename = loadPersistedScreenshotFilename();
    const targetFilename = preferFilename
      || (screenshotImages.value.some((item) => item.filename === selectedScreenshotFilename.value) ? selectedScreenshotFilename.value : '')
      || (screenshotImages.value.some((item) => item.filename === persistedFilename) ? persistedFilename : '')
      || screenshotImages.value[0]?.filename
      || '';
    if (targetFilename) {
      await selectScreenshotImage(targetFilename, true);
    } else {
      clearScreenshotSelection();
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    screenshotLoading.value = false;
  }
};

const toggleScreenshotPanel = async () => {
  screenshotPanelOpen.value = !screenshotPanelOpen.value;
  if (screenshotPanelOpen.value) {
    await nextTick();
    if (!screenshotImages.value.length) {
      await loadScreenshotList();
    } else {
      syncScreenshotCanvas();
    }
  }
};

const clearScreenshotSaveTimer = () => {
  if (!screenshotSaveTimer) return;
  window.clearTimeout(screenshotSaveTimer);
  screenshotSaveTimer = null;
};

const flushScreenshotAutosave = async () => {
  clearScreenshotSaveTimer();
  if (screenshotDirty.value) await saveScreenshotPreLabel(true);
};

const selectScreenshotImage = async (filename: string, forceReload = false) => {
  if (!selectedEntryId.value || !filename) return;
  if (!forceReload && selectedScreenshotFilename.value === filename && screenshotImageUrl.value) return;
  closeScreenshotContextMenus();
  await flushScreenshotAutosave();
  selectedScreenshotFilename.value = filename;
  selectedScreenshotBoxId.value = null;
  screenshotDraftState.value = null;
  screenshotDraftBox.value = null;
  screenshotResizeState.value = null;
  screenshotDirty.value = false;
  resetScreenshotViewState();
  revokeScreenshotImageUrl();
  try {
    const [blob, preLabel] = await Promise.all([
      getFanxiuGameWindow2Screenshot(selectedEntryId.value, filename),
      getFanxiuGameWindow2PreLabel(selectedEntryId.value, filename),
    ]);
    screenshotImageUrl.value = URL.createObjectURL(blob);
    screenshotBoxes.value = toScreenshotOverlayBoxes(preLabel.payload.boxes || [], filename);
    screenshotNaturalWidth.value = preLabel.payload.size?.width || selectedScreenshotImage.value?.width || 0;
    screenshotNaturalHeight.value = preLabel.payload.size?.height || selectedScreenshotImage.value?.height || 0;
    persistSelectedScreenshotFilename(filename);
    await nextTick();
    syncScreenshotCanvas();
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const selectAdjacentScreenshotImage = async (delta: -1 | 1) => {
  if (!screenshotImages.value.length) return;
  const currentIndex = selectedScreenshotIndex.value >= 0 ? selectedScreenshotIndex.value : 0;
  const nextIndex = clamp(currentIndex + delta, 0, screenshotImages.value.length - 1);
  const nextItem = screenshotImages.value[nextIndex];
  if (!nextItem || nextItem.filename === selectedScreenshotFilename.value) return;
  await selectScreenshotImage(nextItem.filename);
};

const deleteCurrentScreenshotImage = async () => {
  if (!selectedEntryId.value || !selectedScreenshotFilename.value || screenshotDeleting.value) return;
  const filename = selectedScreenshotFilename.value;
  try {
    await ElMessageBox.confirm(`删除 ${filename}？`, '删除截图', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return;
  }

  const currentIndex = selectedScreenshotIndex.value;
  const nextFilename = screenshotImages.value[currentIndex + 1]?.filename
    || screenshotImages.value[currentIndex - 1]?.filename
    || '';
  screenshotDeleting.value = true;
  clearScreenshotSaveTimer();
  try {
    await deleteFanxiuGameWindow2Screenshot(selectedEntryId.value, filename);
    clearScreenshotSelection();
    await loadScreenshotList(nextFilename);
    ElMessage.success(`已删除 ${filename}`);
  } catch (error) {
    if (screenshotDirty.value) markScreenshotDirty();
    ElMessage.error(getErrorMessage(error));
  } finally {
    screenshotDeleting.value = false;
  }
};

const saveScreenshotPreLabel = async (silent = false) => {
  if (!selectedEntryId.value || !selectedScreenshotFilename.value) return;
  if (silent && !screenshotDirty.value) return;
  if (!silent) screenshotSaving.value = true;
  try {
    await saveFanxiuGameWindow2PreLabel(
      selectedEntryId.value,
      selectedScreenshotFilename.value,
      toPreLabelPayload(),
    );
    screenshotDirty.value = false;
    const item = selectedScreenshotImage.value;
    if (item) item.pre_label_exists = true;
    if (!silent) ElMessage.success('已保存标注');
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    if (!silent) screenshotSaving.value = false;
  }
};

const getFramePoint = (event: PointerEvent | MouseEvent) => {
  const canvas = overlayCanvasRef.value;
  if (!canvas || !naturalWidth.value || !naturalHeight.value) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: clamp((event.clientX - rect.left) * naturalWidth.value / rect.width, 0, naturalWidth.value),
    y: clamp((event.clientY - rect.top) * naturalHeight.value / rect.height, 0, naturalHeight.value),
  };
};

const normalizeScreenshotBox = (box: OverlayBox): OverlayBox => {
  const maxX = screenshotNaturalWidth.value || Number.MAX_SAFE_INTEGER;
  const maxY = screenshotNaturalHeight.value || Number.MAX_SAFE_INTEGER;
  const x1 = clamp(Math.min(box.x, box.x + box.w), 0, maxX);
  const y1 = clamp(Math.min(box.y, box.y + box.h), 0, maxY);
  const x2 = clamp(Math.max(box.x, box.x + box.w), 0, maxX);
  const y2 = clamp(Math.max(box.y, box.y + box.h), 0, maxY);
  return {
    ...box,
    x: Math.round(x1),
    y: Math.round(y1),
    w: Math.round(Math.max(0, x2 - x1)),
    h: Math.round(Math.max(0, y2 - y1)),
  };
};

const getScreenshotFramePoint = (event: PointerEvent | MouseEvent) => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas || !screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: clamp((event.clientX - rect.left) * screenshotNaturalWidth.value / rect.width, 0, screenshotNaturalWidth.value),
    y: clamp((event.clientY - rect.top) * screenshotNaturalHeight.value / rect.height, 0, screenshotNaturalHeight.value),
  };
};

const findBoxAt = (x: number, y: number) => {
  for (let index = boxes.value.length - 1; index >= 0; index -= 1) {
    const box = boxes.value[index];
    if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h) {
      return index;
    }
  }
  return -1;
};

const findScreenshotBoxAt = (x: number, y: number) => {
  for (let index = screenshotBoxes.value.length - 1; index >= 0; index -= 1) {
    const box = screenshotBoxes.value[index];
    if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h) {
      return index;
    }
  }
  return -1;
};

const findScreenshotResizeHandleAt = (x: number, y: number): { index: number; handle: ScreenshotResizeHandle } | null => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas || !screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const toleranceX = 10 * screenshotNaturalWidth.value / rect.width;
  const toleranceY = 10 * screenshotNaturalHeight.value / rect.height;

  for (let index = screenshotBoxes.value.length - 1; index >= 0; index -= 1) {
    const box = screenshotBoxes.value[index];
    const corners: Array<{ handle: ScreenshotResizeHandle; x: number; y: number }> = [
      { handle: 'top-left', x: box.x, y: box.y },
      { handle: 'bottom-right', x: box.x + box.w, y: box.y + box.h },
    ];
    for (const corner of corners) {
      if (Math.abs(x - corner.x) <= toleranceX && Math.abs(y - corner.y) <= toleranceY) {
        return { index, handle: corner.handle };
      }
    }
  }
  return null;
};

const setScreenshotCanvasCursor = (cursor: string) => {
  const canvas = screenshotOverlayCanvasRef.value;
  if (!canvas || canvas.style.cursor === cursor) return;
  canvas.style.cursor = cursor;
};

const updateScreenshotCanvasCursor = (event?: PointerEvent | MouseEvent) => {
  if (screenshotResizeState.value) {
    setScreenshotCanvasCursor('nwse-resize');
    return;
  }
  if (screenshotDraftState.value) {
    setScreenshotCanvasCursor('crosshair');
    return;
  }
  if (!event) {
    setScreenshotCanvasCursor('crosshair');
    return;
  }
  const point = getScreenshotFramePoint(event);
  if (!point) {
    setScreenshotCanvasCursor('crosshair');
    return;
  }
  if (findScreenshotResizeHandleAt(point.x, point.y)) {
    setScreenshotCanvasCursor('nwse-resize');
    return;
  }
  setScreenshotCanvasCursor(findScreenshotBoxAt(point.x, point.y) >= 0 ? 'pointer' : 'crosshair');
};

const normalizeControlPoint = (point: { x: number; y: number }) => ({
  x: Math.round(clamp(point.x, 0, Math.max(0, naturalWidth.value - 1))),
  y: Math.round(clamp(point.y, 0, Math.max(0, naturalHeight.value - 1))),
});

const buildRemoteInputPayloadBase = () => ({
  entry_id: selectedEntryId.value,
  title: targetTitle.value.trim(),
  mode: 'screen' as const,
  area: captureArea.value,
  crop: cropText.value.trim(),
  trim_border: trimBorderText.value.trim(),
  rotate: rotateDegrees.value,
  fixed_width: 0,
  fixed_height: 0,
  frame_width: naturalWidth.value,
  frame_height: naturalHeight.value,
});

const sendRemoteClick = async (point: { x: number; y: number }) => {
  if (!selectedEntryId.value || !naturalWidth.value || !naturalHeight.value) return;
  const normalized = normalizeControlPoint(point);
  try {
    await clickFanxiuGameWindow2({
      ...buildRemoteInputPayloadBase(),
      x: normalized.x,
      y: normalized.y,
    });
  } catch (error) {
    const now = Date.now();
    if (now - lastInputErrorAt > 1500) {
      lastInputErrorAt = now;
      ElMessage.error(getErrorMessage(error));
    }
  }
};

const sendRemoteDrag = async (
  startPoint: { x: number; y: number },
  endPoint: { x: number; y: number },
  durationMs: number,
) => {
  if (!selectedEntryId.value || !naturalWidth.value || !naturalHeight.value) return;
  const start = normalizeControlPoint(startPoint);
  const end = normalizeControlPoint(endPoint);
  try {
    await dragFanxiuGameWindow2({
      ...buildRemoteInputPayloadBase(),
      start_x: start.x,
      start_y: start.y,
      end_x: end.x,
      end_y: end.y,
      duration_ms: Math.round(clamp(durationMs, 80, 2000)),
    });
  } catch (error) {
    const now = Date.now();
    if (now - lastInputErrorAt > 1500) {
      lastInputErrorAt = now;
      ElMessage.error(getErrorMessage(error));
    }
  }
};

const beginControlClick = (event: PointerEvent) => {
  if (event.button !== 0 || !controlReady.value) return;
  const point = getFramePoint(event);
  if (!point) return;
  event.preventDefault();
  event.stopPropagation();
  controlClickState.value = {
    pointerId: event.pointerId,
    frameX: point.x,
    frameY: point.y,
    clientX: event.clientX,
    clientY: event.clientY,
    startedAt: Date.now(),
  };
  overlayCanvasRef.value?.setPointerCapture(event.pointerId);
};

const finishControlClick = async (event: PointerEvent) => {
  const state = controlClickState.value;
  if (!state || state.pointerId !== event.pointerId) return;
  event.preventDefault();
  event.stopPropagation();
  overlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  controlClickState.value = null;
  const moveDistance = Math.hypot(event.clientX - state.clientX, event.clientY - state.clientY);
  const point = getFramePoint(event) ?? { x: state.frameX, y: state.frameY };
  if (moveDistance > 8) {
    const startPoint = normalizeControlPoint({ x: state.frameX, y: state.frameY });
    const endPoint = normalizeControlPoint(point);
    const durationMs = Date.now() - state.startedAt;
    await recordVisualMacroInput('drag', startPoint, endPoint, durationMs);
    void sendRemoteDrag(startPoint, endPoint, durationMs);
    return;
  }
  const clickPoint = normalizeControlPoint(point);
  await recordVisualMacroInput('click', clickPoint);
  void sendRemoteClick(clickPoint);
};

const selectBox = (id: string | null) => {
  selectedBoxId.value = id;
  drawOverlay();
};

const handlePointerDown = (event: PointerEvent) => {
  if (screenshotSpacePressed.value || livePanState.value) return;
  if (controlEnabled.value) {
    beginControlClick(event);
    return;
  }
  if (event.button !== 0) return;
  const point = getFramePoint(event);
  if (!point) return;

  const hitIndex = findBoxAt(point.x, point.y);
  if (hitIndex >= 0) {
    selectBox(boxes.value[hitIndex].id);
    return;
  }

  selectedBoxId.value = null;
  draftState.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
  };
  draftBox.value = {
    id: 'draft',
    name: '',
    x: point.x,
    y: point.y,
    w: 0,
    h: 0,
  };
  overlayCanvasRef.value?.setPointerCapture(event.pointerId);
  drawOverlay();
};

const handlePointerMove = (event: PointerEvent) => {
  if (controlClickState.value?.pointerId === event.pointerId) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  const state = draftState.value;
  if (!state || state.pointerId !== event.pointerId) return;
  const point = getFramePoint(event);
  if (!point) return;
  draftBox.value = {
    id: 'draft',
    name: '',
    x: state.startX,
    y: state.startY,
    w: point.x - state.startX,
    h: point.y - state.startY,
  };
  drawOverlay();
};

const finishDraft = () => {
  const normalized = draftBox.value ? normalizeBox(draftBox.value) : null;
  draftState.value = null;
  draftBox.value = null;
  if (!normalized || normalized.w < 4 || normalized.h < 4) {
    drawOverlay();
    return;
  }

  const nextBox = {
    ...normalized,
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: `框${boxes.value.length + 1}`,
  };
  boxes.value.push(nextBox);
  selectedBoxId.value = nextBox.id;
  drawOverlay();
};

const handlePointerUp = (event: PointerEvent) => {
  if (controlClickState.value?.pointerId === event.pointerId) {
    void finishControlClick(event);
    return;
  }
  if (draftState.value?.pointerId !== event.pointerId) return;
  overlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  finishDraft();
};

const handlePointerLeave = (event: PointerEvent) => {
  if (controlClickState.value?.pointerId === event.pointerId) {
    return;
  }
  if (draftState.value?.pointerId !== event.pointerId) return;
  finishDraft();
};

const handleContextMenu = (event: MouseEvent) => {
  if (controlEnabled.value) return;
  const point = getFramePoint(event);
  if (!point) return;
  const hitIndex = findBoxAt(point.x, point.y);
  if (hitIndex < 0) return;
  const [removed] = boxes.value.splice(hitIndex, 1);
  if (selectedBoxId.value === removed.id) selectedBoxId.value = null;
  drawOverlay();
};

const handleScreenshotImageLoad = () => {
  const image = screenshotImageRef.value;
  if (!image) return;
  screenshotNaturalWidth.value = image.naturalWidth;
  screenshotNaturalHeight.value = image.naturalHeight;
  void nextTick(syncScreenshotCanvas);
};

const handleScreenshotImageError = () => {
  ElMessage.error('截图加载失败');
};

const markScreenshotDirty = () => {
  screenshotDirty.value = true;
  clearScreenshotSaveTimer();
  screenshotSaveTimer = window.setTimeout(() => {
    screenshotSaveTimer = null;
    void saveScreenshotPreLabel(true);
  }, 600);
};

const selectScreenshotBox = (id: string | null) => {
  selectedScreenshotBoxId.value = id;
  drawScreenshotOverlay();
};

const closeScreenshotBoxContextMenu = () => {
  if (!screenshotBoxContextMenu.value.visible) return;
  screenshotBoxContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    boxId: '',
  };
};

const closeScreenshotBoxListContextMenu = () => {
  if (!screenshotBoxListContextMenu.value.visible) return;
  screenshotBoxListContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    boxId: '',
  };
};

const closeScreenshotContextMenus = () => {
  closeScreenshotBoxContextMenu();
  closeScreenshotBoxListContextMenu();
};

const openScreenshotBoxContextMenu = (event: MouseEvent, boxId: string) => {
  event.preventDefault();
  closeScreenshotBoxListContextMenu();
  const box = screenshotBoxes.value.find((item) => item.id === boxId);
  if (!box) {
    closeScreenshotBoxContextMenu();
    return;
  }
  selectedScreenshotBoxId.value = box.id;
  screenshotBoxContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    boxId: box.id,
  };
  drawScreenshotOverlay();
};

const openScreenshotBoxListContextMenu = (event: MouseEvent, boxId: string) => {
  event.preventDefault();
  closeScreenshotBoxContextMenu();
  const box = screenshotBoxes.value.find((item) => item.id === boxId);
  if (!box) {
    closeScreenshotBoxListContextMenu();
    return;
  }
  selectedScreenshotBoxId.value = box.id;
  screenshotBoxListContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    boxId: box.id,
  };
  drawScreenshotOverlay();
};

const openScreenshotBoxListPanelContextMenu = (event: MouseEvent) => {
  event.preventDefault();
  closeScreenshotBoxContextMenu();
  if (!copiedScreenshotBox.value) {
    closeScreenshotBoxListContextMenu();
    return;
  }
  screenshotBoxListContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    boxId: '',
  };
};

const copyScreenshotBoxFromListContext = () => {
  const box = screenshotBoxes.value.find((item) => item.id === screenshotBoxListContextMenu.value.boxId);
  closeScreenshotBoxListContextMenu();
  if (!box) return;
  copiedScreenshotBox.value = {
    name: box.name,
    x: Math.round(box.x),
    y: Math.round(box.y),
    w: Math.round(box.w),
    h: Math.round(box.h),
  };
};

const pasteScreenshotBoxFromListContext = () => {
  const copied = copiedScreenshotBox.value;
  closeScreenshotBoxListContextMenu();
  if (!copied || !selectedScreenshotFilename.value) return;
  const maxWidth = screenshotNaturalWidth.value || Math.max(copied.x + copied.w, copied.w);
  const maxHeight = screenshotNaturalHeight.value || Math.max(copied.y + copied.h, copied.h);
  const x = Math.round(clamp(copied.x, 0, Math.max(0, maxWidth - 4)));
  const y = Math.round(clamp(copied.y, 0, Math.max(0, maxHeight - 4)));
  const w = Math.round(clamp(copied.w, 4, Math.max(4, maxWidth - x)));
  const h = Math.round(clamp(copied.h, 4, Math.max(4, maxHeight - y)));
  const nextBox: OverlayBox = {
    id: `${selectedScreenshotFilename.value}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: copied.name,
    x,
    y,
    w,
    h,
  };
  screenshotBoxes.value.push(nextBox);
  selectedScreenshotBoxId.value = nextBox.id;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const runScreenshotBoxContextMatch = async () => {
  const box = screenshotBoxes.value.find((item) => item.id === screenshotBoxContextMenu.value.boxId);
  closeScreenshotBoxContextMenu();
  if (!box) return;
  await runScreenshotBoxMatch(box);
};

const handleScreenshotBoxNameInput = () => {
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const updateScreenshotBoxMetric = (boxId: string, metric: OverlayBoxMetric, value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const box = screenshotBoxes.value.find((item) => item.id === boxId);
  if (!box) return;

  const maxWidth = screenshotNaturalWidth.value || Math.max(box.x + box.w, nextValue + box.w, 4);
  const maxHeight = screenshotNaturalHeight.value || Math.max(box.y + box.h, nextValue + box.h, 4);
  if (metric === 'x') {
    box.x = Math.round(clamp(nextValue, 0, Math.max(0, maxWidth - 4)));
    box.w = Math.round(clamp(box.w, 4, Math.max(4, maxWidth - box.x)));
  } else if (metric === 'y') {
    box.y = Math.round(clamp(nextValue, 0, Math.max(0, maxHeight - 4)));
    box.h = Math.round(clamp(box.h, 4, Math.max(4, maxHeight - box.y)));
  } else if (metric === 'w') {
    box.w = Math.round(clamp(nextValue, 4, Math.max(4, maxWidth - box.x)));
  } else {
    box.h = Math.round(clamp(nextValue, 4, Math.max(4, maxHeight - box.y)));
  }

  selectedScreenshotBoxId.value = box.id;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const handleScreenshotPointerDown = (event: PointerEvent) => {
  closeScreenshotContextMenus();
  if (event.button !== 0) return;
  if (screenshotSpacePressed.value || screenshotPanState.value) return;
  const point = getScreenshotFramePoint(event);
  if (!point) return;

  const resizeHit = findScreenshotResizeHandleAt(point.x, point.y);
  if (resizeHit) {
    event.preventDefault();
    setScreenshotCanvasCursor('nwse-resize');
    const box = screenshotBoxes.value[resizeHit.index];
    selectedScreenshotBoxId.value = box.id;
    screenshotResizeState.value = {
      pointerId: event.pointerId,
      boxId: box.id,
      handle: resizeHit.handle,
      original: { ...box },
    };
    screenshotOverlayCanvasRef.value?.setPointerCapture(event.pointerId);
    drawScreenshotOverlay();
    return;
  }

  const hitIndex = findScreenshotBoxAt(point.x, point.y);
  if (hitIndex >= 0) {
    setScreenshotCanvasCursor('pointer');
    selectScreenshotBox(screenshotBoxes.value[hitIndex].id);
    return;
  }

  setScreenshotCanvasCursor('crosshair');
  selectedScreenshotBoxId.value = null;
  screenshotDraftState.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
  };
  screenshotDraftBox.value = {
    id: 'draft',
    name: '',
    x: point.x,
    y: point.y,
    w: 0,
    h: 0,
  };
  screenshotOverlayCanvasRef.value?.setPointerCapture(event.pointerId);
  drawScreenshotOverlay();
};

const handleScreenshotPointerMove = (event: PointerEvent) => {
  const resizeState = screenshotResizeState.value;
  if (resizeState?.pointerId === event.pointerId) {
    setScreenshotCanvasCursor('nwse-resize');
    const point = getScreenshotFramePoint(event);
    if (!point) return;
    const box = screenshotBoxes.value.find((item) => item.id === resizeState.boxId);
    if (!box) return;
    const original = resizeState.original;
    if (resizeState.handle === 'top-left') {
      const right = original.x + original.w;
      const bottom = original.y + original.h;
      const nextX = clamp(point.x, 0, right - 4);
      const nextY = clamp(point.y, 0, bottom - 4);
      box.x = Math.round(nextX);
      box.y = Math.round(nextY);
      box.w = Math.round(right - nextX);
      box.h = Math.round(bottom - nextY);
    } else {
      const nextRight = clamp(point.x, original.x + 4, screenshotNaturalWidth.value || Number.MAX_SAFE_INTEGER);
      const nextBottom = clamp(point.y, original.y + 4, screenshotNaturalHeight.value || Number.MAX_SAFE_INTEGER);
      box.w = Math.round(nextRight - original.x);
      box.h = Math.round(nextBottom - original.y);
    }
    drawScreenshotOverlay();
    return;
  }

  const state = screenshotDraftState.value;
  if (!state || state.pointerId !== event.pointerId) {
    updateScreenshotCanvasCursor(event);
    return;
  }
  setScreenshotCanvasCursor('crosshair');
  const point = getScreenshotFramePoint(event);
  if (!point) return;
  screenshotDraftBox.value = {
    id: 'draft',
    name: '',
    x: state.startX,
    y: state.startY,
    w: point.x - state.startX,
    h: point.y - state.startY,
  };
  drawScreenshotOverlay();
};

const finishScreenshotDraft = () => {
  const normalized = screenshotDraftBox.value ? normalizeScreenshotBox(screenshotDraftBox.value) : null;
  screenshotDraftState.value = null;
  screenshotDraftBox.value = null;
  if (!normalized || normalized.w < 4 || normalized.h < 4) {
    drawScreenshotOverlay();
    return;
  }

  const nextBox = {
    ...normalized,
    id: `${selectedScreenshotFilename.value}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: '',
  };
  screenshotBoxes.value.push(nextBox);
  selectedScreenshotBoxId.value = nextBox.id;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const finishScreenshotResize = (event: PointerEvent) => {
  const resizeState = screenshotResizeState.value;
  if (!resizeState || resizeState.pointerId !== event.pointerId) return false;
  screenshotOverlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  screenshotResizeState.value = null;
  markScreenshotDirty();
  drawScreenshotOverlay();
  updateScreenshotCanvasCursor(event);
  return true;
};

const handleScreenshotPointerUp = (event: PointerEvent) => {
  if (finishScreenshotResize(event)) return;
  if (screenshotDraftState.value?.pointerId !== event.pointerId) return;
  screenshotOverlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  finishScreenshotDraft();
  updateScreenshotCanvasCursor(event);
};

const handleScreenshotPointerLeave = (event: PointerEvent) => {
  if (finishScreenshotResize(event)) return;
  if (screenshotDraftState.value?.pointerId !== event.pointerId) {
    updateScreenshotCanvasCursor();
    return;
  }
  finishScreenshotDraft();
  updateScreenshotCanvasCursor();
};

const handleScreenshotContextMenu = (event: MouseEvent) => {
  const point = getScreenshotFramePoint(event);
  if (!point) {
    closeScreenshotContextMenus();
    return;
  }
  const hitIndex = findScreenshotBoxAt(point.x, point.y);
  if (hitIndex < 0) {
    closeScreenshotContextMenus();
    return;
  }
  openScreenshotBoxContextMenu(event, screenshotBoxes.value[hitIndex].id);
};

const deleteSelectedScreenshotBox = () => {
  if (!selectedScreenshotBoxId.value) return;
  screenshotBoxes.value = screenshotBoxes.value.filter((box) => box.id !== selectedScreenshotBoxId.value);
  selectedScreenshotBoxId.value = null;
  markScreenshotDirty();
  drawScreenshotOverlay();
};

const undoLastScreenshotBox = () => {
  const removed = screenshotBoxes.value.pop();
  if (removed && selectedScreenshotBoxId.value === removed.id) selectedScreenshotBoxId.value = null;
  if (removed) markScreenshotDirty();
  drawScreenshotOverlay();
};

const handleKeydown = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null;
  const isTextEditing =
    !!target &&
    (target.tagName === 'TEXTAREA' || target.isContentEditable);

  if (!isTextEditing && event.code === 'Space') {
    screenshotSpacePressed.value = true;
    event.preventDefault();
    return;
  }
  if (target && ['INPUT', 'SELECT'].includes(target.tagName)) return;
  if (isTextEditing) return;
  if ((event.ctrlKey || event.metaKey) && !event.altKey) {
    if (event.key === '+' || event.key === '=' || event.code === 'NumpadAdd') {
      event.preventDefault();
      if (screenshotPanelOpen.value && selectedScreenshotImage.value) {
        void setScreenshotZoomPercent(screenshotZoomPercent.value + SCREENSHOT_ZOOM_STEP);
      } else {
        void setLiveContentZoomPercent(liveContentZoomPercent.value + 5);
      }
      return;
    }
    if (event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract') {
      event.preventDefault();
      if (screenshotPanelOpen.value && selectedScreenshotImage.value) {
        void setScreenshotZoomPercent(screenshotZoomPercent.value - SCREENSHOT_ZOOM_STEP);
      } else {
        void setLiveContentZoomPercent(liveContentZoomPercent.value - 5);
      }
      return;
    }
    if (event.key === '0' || event.code === 'Numpad0') {
      event.preventDefault();
      if (screenshotPanelOpen.value && selectedScreenshotImage.value) {
        void resetScreenshotContentView();
      } else {
        void resetLiveContentView();
      }
      return;
    }
  }
  if (!screenshotPanelOpen.value || !selectedScreenshotImage.value) return;
  if (event.key === 'Delete' || event.key === 'Backspace') {
    deleteSelectedScreenshotBox();
    event.preventDefault();
    return;
  }
  if ((event.ctrlKey && event.key.toLowerCase() === 'z') || event.key.toLowerCase() === 'z') {
    undoLastScreenshotBox();
    event.preventDefault();
  }
};

const handleKeyup = (event: KeyboardEvent) => {
  if (event.code === 'Space') screenshotSpacePressed.value = false;
};

const handleWindowBlur = () => {
  screenshotSpacePressed.value = false;
  stopLivePan();
  stopScreenshotPan();
};

const handleWindowResize = () => {
  syncCanvas();
  syncScreenshotCanvas();
  syncMatchCanvas();
};

const startPolling = () => {
  stopPolling();
  pollTimer = window.setInterval(() => {
    if (windowViewMode.value === 'off') return;
    void loadRuntimeStatus(true);
  }, 5000);
};

const stopPolling = () => {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
};

watch(layerVisible, drawOverlay);
watch(boxes, drawOverlay, { deep: true });
watch(
  [trimBorderText, rotateDegrees, fps, quality, autoDismissPopup],
  persistWindowConfig,
);
watch(displayScale, syncCanvasSoon);
watch(selectedVisualInstructionKey, drawScreenshotOverlay);
watch(visualMacroDefaultThreshold, (value) => {
  setVisualMacroDefaultThreshold(value);
});
watch(visualMacroDefaultPointRadius, (value) => {
  setVisualMacroDefaultPointRadius(value);
});

onMounted(async () => {
  loadVisualMacroDefaults();
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('keyup', handleKeyup);
  window.addEventListener('blur', handleWindowBlur);
  window.addEventListener('resize', handleWindowResize);
  window.addEventListener('click', closeScreenshotContextMenus);
  void loadCodeCards();
  resizeObserver = new ResizeObserver(syncCanvas);
  if (imageWrapRef.value) resizeObserver.observe(imageWrapRef.value);

  await taskStore.fetchDevices();
  selectedEntryId.value = chooseDefaultEntryId();
  selectedWindowKey.value = chooseDefaultWindowKey();
  applyWindowConfig();
  if (selectedEntryId.value) {
    persistEntrySelection(selectedEntryId.value);
    persistWindowSelection();
    if (windowViewMode.value !== 'off') {
      await Promise.all([refreshStreamToken(), loadRuntimeStatus()]);
    }
    if (screenshotPanelOpen.value) void loadScreenshotList();
  }
  startPolling();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  stopPolling();
  if (screenshotDirty.value) void flushScreenshotAutosave();
  void flushCodeCardSaves();
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('keyup', handleKeyup);
  window.removeEventListener('blur', handleWindowBlur);
  window.removeEventListener('resize', handleWindowResize);
  stopLivePan();
  stopScreenshotPan();
  window.removeEventListener('click', closeScreenshotContextMenus);
  resizeObserver?.disconnect();
  if (streamImageRef.value) streamImageRef.value.src = '';
  revokeScreenshotImageUrl();
  clearMatchResults();
});
</script>

<style scoped>
.game-window-page {
  min-height: 100%;
  background: #f6f8fb;
  color: #1f2937;
}

.stage-pane {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

.topbar {
  min-height: 78px;
  padding: 10px 16px 12px;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.topbar-content {
  width: 100%;
  min-width: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.topbar-content h2 {
  margin: 0;
  color: #111827;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  white-space: nowrap;
}

.muted {
  color: #6b7280;
}

.stream-controls {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.control-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px 16px;
  flex-wrap: wrap;
  justify-content: flex-start;
}

.control-group {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.switch-field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.compact-checkbox {
  height: 22px;
  margin-right: 0;
  color: #374151;
  font-size: 12px;
}

.help-mark {
  width: 14px;
  height: 14px;
  display: inline-grid;
  place-items: center;
  color: #6b7280;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  font-size: 10px;
  line-height: 1;
  cursor: help;
}

.source-controls {
  padding-right: 0;
}

.control-field {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.control-label {
  flex: none;
  color: #6b7280;
  font-size: 12px;
  white-space: nowrap;
}

.size-value {
  min-width: 64px;
  color: #374151;
  font-size: 13px;
  white-space: nowrap;
}

.device-select {
  width: 132px;
}

.window-select {
  width: 126px;
}

.mode-select {
  width: 76px;
}

.connection-button {
  min-width: 74px;
}

.crop-input {
  width: 128px;
}

.rotate-select {
  width: 56px;
}

.number-input {
  width: 58px;
}

.scale-input {
  width: 66px;
}

.number-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 24px;
}

.scale-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 24px;
}

.number-input :deep(.el-input-number__increase),
.number-input :deep(.el-input-number__decrease),
.scale-input :deep(.el-input-number__increase),
.scale-input :deep(.el-input-number__decrease) {
  width: 18px;
}

.workspace {
  flex: 1;
  min-height: 0;
  display: block;
}

.viewer-pane {
  min-width: 0;
  min-height: 0;
  padding: 16px;
  overflow: auto;
}

.live-workspace {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.live-viewport {
  flex: 0 0 auto;
  overflow: hidden;
  overscroll-behavior: contain;
  border: 1px solid #d1d5db;
}

.live-viewport.is-pan-ready,
.live-viewport.is-pan-ready * {
  cursor: grab !important;
}

.live-viewport.is-panning,
.live-viewport.is-panning * {
  cursor: grabbing !important;
  user-select: none;
}

.live-stage-workspace {
  position: relative;
  flex: 0 0 auto;
}

.image-wrap {
  position: relative;
  flex: 0 0 auto;
  display: block;
  line-height: 0;
  background: #111827;
  transform-origin: 0 0;
}

.stream-image {
  display: block;
  object-fit: fill;
  user-select: none;
}

.paused-placeholder {
  width: min(520px, calc(100vw - 260px));
  height: min(760px, calc(100dvh - 160px));
  display: grid;
  place-items: center;
  color: #9ca3af;
  background: #111827;
}

.overlay-canvas {
  position: absolute;
  inset: 0;
  cursor: default;
  pointer-events: none;
  touch-action: none;
}

.image-wrap.is-control-enabled .overlay-canvas {
  cursor: pointer;
  pointer-events: auto;
}

.stream-error {
  position: absolute;
  left: 12px;
  bottom: 12px;
  line-height: 1.4;
  padding: 8px 10px;
  color: #fef2f2;
  background: rgba(185, 28, 28, 0.88);
  border-radius: 4px;
}

.code-panel {
  flex: 1 1 360px;
  min-width: 320px;
  max-width: 560px;
  padding-left: 14px;
  border-left: 1px solid #e5e7eb;
}

.code-panel-head {
  min-height: 28px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.code-panel-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.code-panel-title {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.visual-macro-config {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.visual-macro-config-title {
  color: #111827;
  font-size: 13px;
  font-weight: 700;
}

.visual-macro-config-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #475569;
  font-size: 12px;
}

.visual-macro-config-row span {
  white-space: nowrap;
}

.code-scope-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.code-scope {
  min-width: 0;
}

.code-scope-head {
  min-height: 24px;
  margin-bottom: 6px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #374151;
  font-size: 13px;
  font-weight: 700;
}

.code-add,
.code-card-collapse,
.code-card-record,
.code-card-delete {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  line-height: 1;
  cursor: pointer;
}

.code-add {
  color: #2563eb;
  font-size: 18px;
}

.code-add:hover {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.code-card-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.code-card {
  padding: 8px;
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 4px;
}

.code-card-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 24px 68px 24px;
  align-items: center;
  gap: 8px;
}

.code-card-summary-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 24px;
  align-items: center;
  gap: 8px;
}

.code-card-title-button {
  min-width: 0;
  height: 24px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  color: #1f2937;
  background: transparent;
  border: none;
  text-align: left;
  cursor: pointer;
}

.code-card-title-button span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.code-card-title-button:hover {
  color: #2563eb;
}

.code-card-collapse {
  color: #64748b;
  font-size: 14px;
}

.code-card-collapse:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.code-card-record {
  width: 68px;
  color: #dc2626;
  font-size: 12px;
  white-space: nowrap;
}

.code-card-record.is-recording {
  color: #dc2626;
  background: #fef2f2;
  border-color: #fecaca;
}

.code-card-delete {
  color: #dc2626;
  font-size: 18px;
}

.code-card-delete:hover {
  background: #fef2f2;
  border-color: #fecaca;
}

.code-card-body {
  display: block;
  margin-top: 8px;
}

.code-card-body :deep(.el-textarea__inner) {
  line-height: 1.5;
}

.visual-action-editor {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.visual-operation {
  padding: 6px 0;
  border-top: 1px solid #eef2f7;
  cursor: pointer;
}

.visual-operation:first-child {
  padding-top: 0;
  border-top: none;
}

.visual-operation.is-selected {
  margin: 0 -6px;
  padding-right: 6px;
  padding-left: 6px;
  background: #eff6ff;
  border-radius: 4px;
}

.visual-recording-hint {
  padding: 6px 8px;
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
}

.visual-action-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.visual-action-select {
  width: 64px;
}

.visual-operation-index {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #475569;
  background: #eef2f7;
  border-radius: 4px;
  font-size: 12px;
}

.visual-target-select {
  width: 76px;
}

.visual-scan-select {
  width: 92px;
}

.visual-text-input {
  width: 150px;
}

.visual-number-input {
  width: 86px;
}

.visual-small-number-input {
  width: 68px;
}

.visual-inline-label {
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.visual-action-row code {
  padding: 2px 5px;
  color: #374151;
  background: #f3f4f6;
  border-radius: 3px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
}

.visual-operation-delete {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #dc2626;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 16px;
  cursor: pointer;
}

.visual-operation-delete:hover {
  background: #fef2f2;
  border-color: #fecaca;
}

.visual-empty {
  min-height: 28px;
  display: flex;
  align-items: center;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.5;
}

.code-card-empty {
  min-height: 28px;
  display: flex;
  align-items: center;
  color: #94a3b8;
  font-size: 12px;
}

.code-output-panel {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}

.code-output-tabs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.code-output-tab {
  height: 26px;
  padding: 0 10px;
  color: #475569;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.code-output-tab.is-active {
  color: #1d4ed8;
  background: #eff6ff;
  border-color: #bfdbfe;
}

.code-output-box {
  min-height: 120px;
  max-height: 260px;
  margin: 8px 0 0;
  padding: 10px;
  overflow: auto;
  color: #1f2937;
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.screenshot-panel {
  width: min(980px, calc(100vw - 260px));
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}

.screenshot-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.screenshot-toggle {
  min-height: 28px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: #111827;
  background: transparent;
  border: none;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
}

.screenshot-summary {
  color: #6b7280;
  font-size: 12px;
  font-weight: 400;
  white-space: nowrap;
}

.screenshot-help {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}

.screenshot-help:hover {
  color: #2563eb;
  border-color: #93c5fd;
  background: #eff6ff;
}

.screenshot-help-doc {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #334155;
  font-size: 13px;
  line-height: 1.5;
}

.screenshot-caret {
  width: 12px;
  color: #64748b;
  font-size: 10px;
}

.screenshot-body {
  margin-top: 10px;
}

.screenshot-empty {
  margin-top: 10px;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
}

.screenshot-nav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.screenshot-current-item {
  min-width: 76px;
  max-width: 140px;
  height: 24px;
  padding: 0 2px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #374151;
}

.screenshot-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.screenshot-jump-input {
  width: 70px;
}

.screenshot-jump-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 6px;
}

.screenshot-delete {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #dc2626;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

.screenshot-delete:hover:not(:disabled) {
  background: #fef2f2;
  border-color: #fecaca;
}

.screenshot-delete:disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}

.screenshot-editor {
  margin-top: 12px;
  display: grid;
  grid-template-columns: max-content 340px;
  gap: 14px;
  align-items: start;
}

.screenshot-preview {
  flex: 0 0 auto;
  overflow: hidden;
  overscroll-behavior: contain;
  border: 1px solid #d1d5db;
}

.screenshot-preview.is-pan-ready,
.screenshot-preview.is-pan-ready * {
  cursor: grab !important;
}

.screenshot-preview.is-panning,
.screenshot-preview.is-panning * {
  cursor: grabbing !important;
  user-select: none;
}

.screenshot-workspace {
  position: relative;
  flex: 0 0 auto;
}

.screenshot-image-wrap {
  position: relative;
  display: block;
  line-height: 0;
  background: #111827;
  transform-origin: 0 0;
}

.screenshot-image {
  display: block;
  object-fit: fill;
  user-select: none;
}

.screenshot-image-placeholder {
  width: 360px;
  height: 640px;
  display: grid;
  place-items: center;
  color: #9ca3af;
  background: #111827;
}

.screenshot-overlay-canvas {
  position: absolute;
  inset: 0;
  cursor: crosshair;
  touch-action: none;
}

.screenshot-pre-panel {
  min-width: 0;
}

.screenshot-instruction-panel {
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e5e7eb;
}

.screenshot-panel-title {
  margin-bottom: 6px;
  color: #1f2937;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.screenshot-instruction-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 72px));
  gap: 6px;
  align-items: center;
}

.screenshot-drag-metrics {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.screenshot-drag-metrics .screenshot-instruction-metrics {
  grid-template-columns: 18px repeat(2, minmax(0, 72px));
}

.screenshot-advanced-metrics {
  margin-top: 8px;
  display: grid;
  grid-template-columns: minmax(0, 120px);
  gap: 6px;
}

.screenshot-metric-group-label {
  color: #64748b;
  font-size: 12px;
}

.screenshot-box-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.screenshot-box-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.screenshot-box-row:hover {
  background: #f8fafc;
}

.screenshot-box-row.is-active {
  border-color: #f97316;
  background: #fff7ed;
}

.screenshot-box-main {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.screenshot-box-number {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: #eef2f7;
  color: #1f2937;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.screenshot-box-name {
  min-width: 0;
}

.screenshot-box-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
}

.screenshot-box-metric {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 3px;
  color: #64748b;
  font-size: 12px;
}

.screenshot-box-metric :deep(.el-input-number) {
  width: 100%;
}

.screenshot-box-metric :deep(.el-input__wrapper) {
  padding: 0 6px;
}

.screenshot-box-metric :deep(.el-input__inner) {
  text-align: left;
}

.screenshot-box-menu {
  position: fixed;
  z-index: 2000;
  min-width: 76px;
  padding: 4px;
  background: #fff;
  border: 1px solid #d8e0ea;
  border-radius: 4px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.14);
}

.screenshot-box-menu button {
  width: 100%;
  min-height: 26px;
  padding: 0 12px;
  color: #1f2937;
  background: transparent;
  border: none;
  border-radius: 3px;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.screenshot-box-menu button:hover:not(:disabled) {
  background: #f3f6fb;
}

.screenshot-box-menu button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
}

.match-panel {
  width: min(980px, calc(100vw - 260px));
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #e5e7eb;
}

.match-head {
  min-height: 28px;
  display: flex;
  align-items: center;
  gap: 12px;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.match-summary {
  color: #6b7280;
  font-size: 12px;
  font-weight: 400;
  white-space: nowrap;
}

.match-body {
  margin-top: 10px;
  display: grid;
  grid-template-columns: minmax(320px, max-content) 240px;
  gap: 14px;
  align-items: start;
}

.match-preview {
  min-width: 0;
  overflow: auto;
}

.match-image-wrap {
  position: relative;
  display: inline-block;
  line-height: 0;
  background: #111827;
  border: 1px solid #d1d5db;
}

.match-image {
  display: block;
  max-width: min(720px, calc(100vw - 520px));
  user-select: none;
}

.match-overlay-canvas {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.match-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.match-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 36px 44px;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  color: #1f2937;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.match-row:hover {
  background: #f8fafc;
}

.match-row.is-active {
  border-color: #10b981;
  background: #ecfdf5;
}

.match-row.is-template.is-active {
  border-color: #f97316;
  background: #fff7ed;
}

.match-number {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: #eef2f7;
  color: #1f2937;
  font-size: 12px;
  line-height: 1;
}

.match-name {
  min-width: 0;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.match-row strong {
  font-weight: 700;
}

.match-kind,
.match-score {
  white-space: nowrap;
  text-align: right;
  font-size: 12px;
}

.match-row.is-fixed .match-kind,
.match-row.is-fixed .match-score {
  color: #047857;
}

.match-row.is-template .match-kind,
.match-row.is-template .match-score {
  color: #c2410c;
}


@media (max-width: 980px) {
  .topbar {
    align-items: stretch;
    flex-direction: column;
  }

  .stream-controls {
    justify-content: flex-start;
  }

  .live-workspace {
    flex-direction: column;
  }

  .stream-image,
  .paused-placeholder {
    max-width: calc(100vw - 32px);
  }

  .code-panel {
    width: calc(100vw - 32px);
    min-width: 0;
    max-width: none;
    padding-top: 12px;
    padding-left: 0;
    border-top: 1px solid #e5e7eb;
    border-left: none;
  }

  .screenshot-panel {
    width: calc(100vw - 32px);
  }

  .match-panel {
    width: calc(100vw - 32px);
  }

  .screenshot-editor {
    grid-template-columns: 1fr;
  }

  .match-body {
    grid-template-columns: 1fr;
  }

  .match-image {
    max-width: calc(100vw - 32px);
  }
}
</style>
