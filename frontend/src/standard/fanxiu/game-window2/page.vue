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
                  @click="connectWindow({ allowStartService: true })"
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
                  <el-popover trigger="click" placement="bottom-start" width="360">
                    <template #reference>
                      <el-button
                        circle
                        plain
                        size="small"
                        :icon="Setting"
                    title="配置新指令集默认模板"
                    aria-label="配置新指令集默认模板"
                      />
                    </template>
                    <div class="visual-macro-config">
                      <div class="visual-macro-config-title">新指令集默认模板</div>
                      <label class="visual-macro-config-row">
                        <span>点击位置允许随机波动半径r</span>
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
                        <span>图像匹配相似度阈值</span>
                        <el-input-number
                          :model-value="thresholdRatioToPercent(visualMacroDefaultThreshold)"
                          class="visual-number-input"
                          size="small"
                          :min="50"
                          :max="100"
                          :step="1"
                          controls-position="right"
                          @change="value => setVisualMacroDefaultThreshold(thresholdPercentToRatio(value))"
                        >
                          <template #suffix>%</template>
                        </el-input-number>
                      </label>
                      <label class="visual-macro-config-row">
                        <span>单通道像素容差</span>
                        <el-input-number
                          v-model="visualMacroDefaultPixelTolerance"
                          class="visual-number-input"
                          size="small"
                          :min="0"
                          :max="255"
                          :step="1"
                          controls-position="right"
                        />
                      </label>
                    </div>
                  </el-popover>
                </div>
              </div>
              <div class="code-scope-list">
                <section class="code-scope">
                  <div class="code-scope-head">
                    <span>脚本</span>
                    <button
                      type="button"
                      class="code-add"
                      title="新建脚本"
                      aria-label="新建脚本"
                      @click="addCodeCard"
                    >
                      +
                    </button>
                  </div>
                  <div ref="codeCardListRef" class="code-card-list">
                    <section
                      v-for="(card, index) in sortedCodeCards"
                      :key="card.id"
                      class="code-card"
                      :class="{ 'is-expanded': isCodeCardExpanded(card.id) }"
                      :data-card-id="card.id"
                    >
                      <div v-if="isCodeCardExpanded(card.id)" class="code-card-head">
                        <SortableOrderHandle
                          class="code-card-order-handle"
                          :index="index"
                          :total="sortedCodeCards.length"
                          size="sm"
                          :pad="false"
                        />
                        <el-input
                          v-model="card.title"
                          class="code-card-title-input"
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
                        <el-button
                          class="code-card-run"
                          :class="{ 'is-running': visualScriptRunningCardId === card.id }"
                          :icon="visualScriptRunningCardId === card.id ? VideoPause : VideoPlay"
                          text
                          size="small"
                          :disabled="Boolean(visualScriptRunningCardId) && visualScriptRunningCardId !== card.id"
                          :title="visualScriptRunningCardId === card.id ? '停止脚本' : '执行脚本'"
                          :aria-label="visualScriptRunningCardId === card.id ? '停止脚本' : '执行脚本'"
                          @click.stop="visualScriptRunningCardId === card.id ? stopVisualScript(card) : runVisualScript(card)"
                        />
                        <button
                          type="button"
                          class="code-card-record"
                          :class="{ 'is-recording': activeVisualMacroCardId === card.id }"
                          :title="activeVisualMacroCardId === card.id ? '停止录制指令集' : '录制指令集'"
                          :aria-label="activeVisualMacroCardId === card.id ? '停止录制指令集' : '录制指令集'"
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
                        <SortableOrderHandle
                          class="code-card-order-handle"
                          :index="index"
                          :total="sortedCodeCards.length"
                          size="sm"
                          :pad="false"
                        />
                        <button
                          type="button"
                          class="code-card-title-button"
                          @click="toggleCodeCard(card.id)"
                        >
                          <span>{{ codeCardTitle(card) }}</span>
                        </button>
                        <el-button
                          class="code-card-run"
                          :class="{ 'is-running': visualScriptRunningCardId === card.id }"
                          :icon="visualScriptRunningCardId === card.id ? VideoPause : VideoPlay"
                          text
                          size="small"
                          :disabled="Boolean(visualScriptRunningCardId) && visualScriptRunningCardId !== card.id"
                          :title="visualScriptRunningCardId === card.id ? '停止脚本' : '执行脚本'"
                          :aria-label="visualScriptRunningCardId === card.id ? '停止脚本' : '执行脚本'"
                          @click.stop="visualScriptRunningCardId === card.id ? stopVisualScript(card) : runVisualScript(card)"
                        />
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
                          {{ visualMacroCapturePending ? '正在保存点击前画面...' : '录制中：在左侧直播画面点击或拖拽，会追加一组指令集。' }}
                        </div>
                        <div
                          :ref="element => setVisualInstructionSetListRef(element, card.id)"
                          class="visual-instruction-set-list"
                          :data-card-id="card.id"
                        >
                          <div
                            v-for="(instructionSet, instructionSetIndex) in visualInstructionSetsOf(card)"
                            :key="instructionSet.id"
                            class="visual-instruction-set"
                            :data-set-id="instructionSet.id"
                            @contextmenu.prevent.stop="openVisualInstructionSetContextMenu($event, card, instructionSet)"
                          >
                            <div
                              v-if="firstInstructionOfSet(instructionSet)"
                              :key="firstInstructionOfSet(instructionSet)?.id"
                              class="visual-operation"
                              :class="{ 'is-selected': selectedVisualInstructionSetKey === visualInstructionSetKey(card.id, instructionSet.id) }"
                              @click.stop="selectVisualInstructionFrame(card, firstInstructionOfSet(instructionSet)!)"
                            >
                              <div class="visual-action-row">
                                <SortableOrderHandle
                                  :index="instructionSetIndex"
                                  :total="visualInstructionSetsOf(card).length"
                                  size="sm"
                                  :pad="false"
                                />
                                <span
                                  class="visual-summary-text"
                                  :class="{ 'is-unique-title': isVisualInstructionSetLabelUnique(instructionSet) || (!instructionSet.label.trim() && isVisualInstructionLabelUnique(firstInstructionOfSet(instructionSet)!)) }"
                                >
                                  {{ visualInstructionSetDisplayTitle(instructionSet) }}
                                </span>
                              </div>
                            </div>
                          </div>
                        </div>
                        <div v-if="!visualInstructionSetsOf(card).length" class="visual-empty">
                          点击本脚本右上角“录制”后，在直播画面点击或拖拽，会追加指令集。
                        </div>
                      </div>
                    </section>
                    <div v-if="!sortedCodeCards.length && !codeCardsLoading" class="code-card-empty">
                      暂无脚本
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
                <span>指令集详情</span>
              </button>
              <el-popover trigger="click" placement="right-start" width="260">
                <template #reference>
                  <button
                    type="button"
                    class="screenshot-help"
                    title="查看操作文档"
                    aria-label="查看指令集详情操作文档"
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
              <div v-else-if="!selectedRawVisualInstruction && !selectedScreenshotFilename" class="screenshot-empty">选择一组带帧的指令集</div>
              <div v-else-if="selectedScreenshotFilename && !selectedScreenshotImage" class="screenshot-empty">未找到绑定截图</div>
              <div v-if="selectedRawVisualInstruction || selectedScreenshotImage" class="screenshot-editor">
                <div class="screenshot-preview-column">
                  <div class="screenshot-detail-head">
                    <div class="screenshot-detail-title">截图</div>
                    <button
                      type="button"
                      class="screenshot-rebind-frame"
                      :disabled="saveFrameLoading || !selectedVisualEditInstructionContext"
                      @click="rebindSelectedVisualInstructionFrame"
                    >
                      重绑当前帧
                    </button>
                  </div>
                  <div v-if="selectedScreenshotGeometryText" class="screenshot-geometry-warning">
                    <span>{{ selectedScreenshotGeometryText }}</span>
                  </div>
                  <div
                    v-if="selectedScreenshotImage"
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
                  <div v-else class="screenshot-empty">未绑定截图</div>
                </div>

                <div class="screenshot-pre-panel" @contextmenu.prevent.stop="openScreenshotBoxListPanelContextMenu">
                  <div v-if="selectedVisualInstruction" class="screenshot-instruction-panel">
                    <div v-if="selectedVisualInstructionSet" class="screenshot-config-group">
                      <div class="screenshot-panel-title-row">
                        <div class="screenshot-panel-title">指令集</div>
                        <button
                          type="button"
                          class="instruction-sequence-add"
                          title="添加指令"
                          aria-label="添加指令"
                          @click="selectedVisualInstructionSetContext && addInstructionToSet(selectedVisualInstructionSetContext.card, selectedVisualInstructionSet.id)"
                        >
                          +
                        </button>
                      </div>
                      <div class="screenshot-config-row">
                        <label class="screenshot-box-metric">
                          <span>名称</span>
                          <input
                            class="instruction-sequence-title instruction-set-title-input"
                            :class="{ 'is-unique-title': isVisualInstructionSetLabelUnique(selectedVisualInstructionSet) && !isVisualInstructionSetLabelEditing(selectedVisualInstructionSet.id) }"
                            :value="visualInstructionSetLabelInputValue(selectedVisualInstructionSet)"
                            placeholder="指令集名称"
                            @click.stop
                            @mousedown.stop
                            @keydown.stop
                            @keyup.stop
                            @compositionstart="beginVisualTitleComposition(`set:${selectedVisualInstructionSet.id}`)"
                            @compositionend="event => selectedVisualInstructionSetContext && commitVisualInstructionSetLabelInput(selectedVisualInstructionSetContext.card, selectedVisualInstructionSet.id, event)"
                            @input="event => selectedVisualInstructionSetContext && handleVisualInstructionSetLabelInput(selectedVisualInstructionSetContext.card, selectedVisualInstructionSet.id, event)"
                            @focus="beginVisualInstructionSetLabelEdit(selectedVisualInstructionSet)"
                            @keydown.enter.stop.prevent="event => selectedVisualInstructionSetContext && commitVisualInstructionSetLabelByEnter(selectedVisualInstructionSetContext.card, selectedVisualInstructionSet.id, event)"
                            @blur="selectedVisualInstructionSetContext && commitVisualInstructionSetLabelDraft(selectedVisualInstructionSetContext.card, selectedVisualInstructionSet.id)"
                          />
                        </label>
                      </div>
                      <div class="instruction-sequence">
                        <div
                          v-for="(instruction, index) in selectedVisualInstructionSet.instructions"
                          :key="instruction.id"
                          role="button"
                          tabindex="0"
                          class="instruction-sequence-row"
                          :class="{ 'is-active': selectedVisualInstructionSetContext && selectedVisualInstructionKey === visualInstructionKey(selectedVisualInstructionSetContext.card.id, instruction.id) }"
                          @click="selectVisualInstructionFromSelectedSet(instruction)"
                          @keydown.enter.prevent="selectVisualInstructionFromSelectedSet(instruction)"
                          @keydown.space.prevent="selectVisualInstructionFromSelectedSet(instruction)"
                          >
                            <span>{{ index + 1 }}</span>
                            <input
                              v-if="instruction.kind !== 'ref'"
                              class="instruction-sequence-title"
                              :class="{ 'is-unique-title': isVisualInstructionLabelUnique(instruction) && !isVisualInstructionTitleEditing(instruction.id) }"
                              :value="visualInstructionTitleInputValue(instruction)"
                              :placeholder="visualInstructionFallbackTitle(instruction)"
                              @click.stop
                              @mousedown.stop
                              @keydown.stop
                              @keyup.stop
                              @compositionstart="beginVisualTitleComposition(`instruction:${instruction.id}`)"
                              @compositionend="event => commitVisualInstructionTitleComposition(instruction, event)"
                              @input="event => handleVisualInstructionTitleInput(instruction.id, event)"
                              @focus="beginVisualInstructionTitleEdit(instruction)"
                              @keydown.enter.stop.prevent="event => commitVisualInstructionTitleDraftByEnter(instruction, event)"
                              @blur="commitVisualInstructionTitleDraft(instruction)"
                            />
                            <template v-if="instruction.kind === 'ref'">
                              <span class="instruction-reference-title">调用：{{ instruction.refName || '未选择' }}</span>
                            </template>
                          <button
                            type="button"
                            class="instruction-sequence-delete"
                            title="删除指令"
                            aria-label="删除指令"
                            @click.stop="selectedVisualInstructionSetContext && confirmDeleteVisualInstruction(selectedVisualInstructionSetContext.card, instruction)"
                          >
                            -
                          </button>
                        </div>
                      </div>
                    </div>
                    <div class="screenshot-config-group">
                      <div class="screenshot-config-row visual-call-config-row">
                        <label class="screenshot-box-metric">
                          <span class="visual-primary-label">类型</span>
                          <el-select
                            :model-value="selectedRawVisualInstruction?.kind ?? 'normal'"
                            class="visual-target-kind-select"
                            size="small"
                            @change="value => updateSelectedVisualInstructionKind(value as VisualInstructionKind)"
                          >
                            <el-option label="普通" value="normal" />
                            <el-option label="调用" value="ref" />
                          </el-select>
                        </label>
                        <label v-if="selectedVisualReferenceInstruction" class="screenshot-box-metric">
                          <span>目标</span>
                          <el-select
                            :model-value="selectedVisualReferenceInstruction.refTargetKind"
                            class="visual-target-kind-select"
                            size="small"
                            @change="value => updateSelectedVisualInstructionReferenceTargetKind(value as VisualReferenceTargetKind)"
                          >
                            <el-option label="指令" value="instruction" />
                            <el-option label="指令集" value="instructionSet" />
                          </el-select>
                        </label>
                        <label v-if="selectedVisualReferenceInstruction" class="screenshot-box-metric">
                          <span>调用</span>
                          <el-select
                            :model-value="selectedVisualReferenceInstruction.refName"
                            class="visual-reference-select"
                            size="small"
                            filterable
                            placeholder="选择唯一名称"
                            @change="value => updateSelectedVisualInstructionReference(String(value))"
                          >
                            <el-option
                              v-for="candidate in selectedVisualReferenceCandidates"
                              :key="candidate.id"
                              :label="candidate.name"
                              :value="candidate.name"
                            />
                          </el-select>
                        </label>
                        <span v-if="selectedVisualReferenceInstruction" class="visual-reference-note">
                          修改下方参数会影响所有调用
                        </span>
                      </div>
                      <div class="screenshot-config-row">
                        <label class="screenshot-box-metric">
                          <span class="visual-primary-label">动作</span>
                          <el-select
                            :model-value="selectedVisualInstruction.action"
                            class="visual-action-kind-select"
                            size="small"
                            @change="value => updateSelectedVisualInstruction({ action: value as VisualActionKind })"
                          >
                            <el-option label="等待点击" value="waitClick" />
                            <el-option label="守护点击" value="guardClick" />
                            <el-option label="点击" value="click" />
                            <el-option label="拖拽" value="drag" />
                            <el-option label="等待" value="wait" />
                            <el-option label="查找" value="find" />
                            <el-option label="批量查找" value="findAll" />
                          </el-select>
                        </label>
                      </div>
                      <div v-if="visualActionUsesPointer(selectedVisualInstruction.action)" class="pointer-config-table">
                        <div class="pointer-config-row">
                          <span class="screenshot-metric-group-label">起点</span>
                          <label class="screenshot-box-metric">
                            <span>x1</span>
                            <el-input-number
                              :model-value="selectedVisualInstruction.pointer.start?.x ?? 0"
                              size="small"
                              :controls="false"
                              :step="1"
                              @change="value => updateSelectedVisualInstructionPointerPoint('start', 'x', value)"
                            />
                          </label>
                          <label class="screenshot-box-metric">
                            <span>y1</span>
                            <el-input-number
                              :model-value="selectedVisualInstruction.pointer.start?.y ?? 0"
                              size="small"
                              :controls="false"
                              :step="1"
                              @change="value => updateSelectedVisualInstructionPointerPoint('start', 'y', value)"
                            />
                          </label>
                          <label class="screenshot-box-metric">
                            <span>r1</span>
                            <el-input-number
                              :model-value="selectedVisualInstruction.pointer.start?.r ?? visualMacroDefaultPointRadius"
                              size="small"
                              :controls="false"
                              :step="1"
                              @change="value => updateSelectedVisualInstructionPointerRadius('start', value)"
                            />
                          </label>
                        </div>
                        <div v-if="selectedVisualInstruction.action === 'drag'" class="pointer-config-row">
                          <span class="screenshot-metric-group-label">终点</span>
                          <label class="screenshot-box-metric">
                            <span>x2</span>
                            <el-input-number
                              :model-value="selectedVisualInstruction.pointer.end?.x ?? 0"
                              size="small"
                              :controls="false"
                              :step="1"
                              @change="value => updateSelectedVisualInstructionPointerPoint('end', 'x', value)"
                            />
                          </label>
                          <label class="screenshot-box-metric">
                            <span>y2</span>
                            <el-input-number
                              :model-value="selectedVisualInstruction.pointer.end?.y ?? 0"
                              size="small"
                              :controls="false"
                              :step="1"
                              @change="value => updateSelectedVisualInstructionPointerPoint('end', 'y', value)"
                            />
                          </label>
                          <label class="screenshot-box-metric">
                            <span>r2</span>
                            <el-input-number
                              :model-value="selectedVisualInstruction.pointer.end?.r ?? visualMacroDefaultPointRadius"
                              size="small"
                              :controls="false"
                              :step="1"
                              @change="value => updateSelectedVisualInstructionPointerRadius('end', value)"
                            />
                          </label>
                        </div>
                        <label v-if="selectedVisualInstruction.action === 'drag'" class="screenshot-config-line">
                          <span>拖拽时间ms</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.pointer.durationMs"
                            class="screenshot-duration-input"
                            size="small"
                            :min="0"
                            :max="5000"
                            :step="50"
                            controls-position="right"
                            @change="value => updateSelectedVisualInstructionPointerDuration(value)"
                          />
                        </label>
                      </div>
                      <div v-else-if="selectedVisualInstruction.action === 'wait'" class="screenshot-config-row">
                        <label class="screenshot-box-metric">
                          <span>条件</span>
                          <el-select
                            :model-value="selectedVisualInstruction.condition"
                            class="visual-scan-select"
                            size="small"
                            @change="value => updateSelectedVisualInstruction({ condition: value as VisualCondition })"
                          >
                            <el-option label="出现" value="appear" />
                            <el-option label="消失" value="disappear" />
                            <el-option label="稳定" value="stable" />
                            <el-option label="变化" value="changed" />
                          </el-select>
                        </label>
                        <label class="screenshot-box-metric">
                          <span>超时</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.timeout"
                            class="visual-small-number-input"
                            size="small"
                            :min="0"
                            :max="120"
                            controls-position="right"
                            @change="value => updateSelectedVisualInstruction({ timeout: Number(value) || 0 })"
                          />
                        </label>
                        <span class="visual-inline-label">秒</span>
                      </div>
                    </div>

                    <div class="screenshot-config-group">
                      <div class="screenshot-config-row">
                        <label class="screenshot-box-metric">
                          <span class="visual-primary-label">对象</span>
                          <el-select
                            :model-value="selectedVisualInstruction.target"
                            class="visual-target-kind-select"
                            size="small"
                            @change="value => updateSelectedVisualInstruction({ target: value as VisualTargetKind })"
                          >
                            <el-option label="图片" value="image" />
                            <el-option label="文本" value="text" />
                            <el-option label="坐标" value="coordinate" />
                          </el-select>
                        </label>
                      </div>
                      <label v-if="visualInstructionUsesTargetConfig(selectedVisualInstruction)" class="screenshot-config-line">
                        <span>搜索范围</span>
                        <el-select
                          :model-value="selectedVisualInstruction.scan"
                          class="visual-scan-select"
                          size="small"
                          @change="value => updateSelectedVisualInstructionScan(value as VisualScanMode)"
                        >
                          <el-option label="固定位置" value="fixed" />
                          <el-option label="范围搜索" value="range" />
                          <el-option label="全图搜索" value="full" />
                        </el-select>
                      </label>
                      <div
                        v-if="selectedVisualInstruction.scan === 'range'"
                        class="visual-box-config"
                        @focusin="activeVisualShapeRole = 'scan'"
                        @pointerdown="activeVisualShapeRole = 'scan'"
                      >
                        <label class="screenshot-box-metric visual-box-mode-field">
                          <span>搜索区域</span>
                        </label>
                        <label class="screenshot-box-metric">
                          <span>x</span>
                          <el-input-number
                            :model-value="visualScanBoxOrDefault(selectedVisualInstruction).x"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionScanBoxMetric('x', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>y</span>
                          <el-input-number
                            :model-value="visualScanBoxOrDefault(selectedVisualInstruction).y"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionScanBoxMetric('y', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>w</span>
                          <el-input-number
                            :model-value="visualScanBoxOrDefault(selectedVisualInstruction).w"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionScanBoxMetric('w', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>h</span>
                          <el-input-number
                            :model-value="visualScanBoxOrDefault(selectedVisualInstruction).h"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionScanBoxMetric('h', value)"
                          />
                        </label>
                      </div>
                      <label v-if="selectedVisualInstruction.target === 'text'" class="screenshot-config-line">
                        <span>识别文本</span>
                        <el-input
                          :model-value="selectedVisualInstruction.text"
                          class="visual-text-input"
                          size="small"
                          placeholder="文本"
                          @input="value => updateSelectedVisualInstruction({ text: String(value) })"
                          @blur="saveSelectedVisualInstructionCardNow"
                        />
                      </label>
                      <label v-if="selectedVisualInstruction.target === 'text'" class="screenshot-config-line">
                        <span>文本匹配</span>
                        <el-select
                          :model-value="selectedVisualInstruction.textMatch"
                          class="visual-scan-select"
                          size="small"
                          @change="value => updateSelectedVisualInstruction({ textMatch: value as VisualTextMatch })"
                        >
                          <el-option label="包含" value="contains" />
                          <el-option label="精确" value="exact" />
                          <el-option label="正则" value="regex" />
                        </el-select>
                      </label>
                      <div
                        v-if="selectedVisualInstruction.target === 'image'"
                        class="visual-box-config"
                        @focusin="activeVisualShapeRole = 'target'"
                        @pointerdown="activeVisualShapeRole = 'target'"
                      >
                        <label class="screenshot-box-metric visual-box-mode-field">
                          <span>图片区域</span>
                          <el-select
                            :model-value="selectedVisualInstruction.imageBoxMode"
                            class="visual-image-box-mode-select"
                            size="small"
                            @change="value => updateSelectedVisualInstructionImageBoxMode(value as VisualImageBoxMode)"
                          >
                            <el-option label="中心矩形" value="anchor" />
                            <el-option label="手动画框" value="manual" />
                          </el-select>
                        </label>
                        <label v-if="selectedVisualInstruction.imageBoxMode === 'manual'" class="screenshot-box-metric">
                          <span>x</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.box?.x ?? 0"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionBoxMetric('x', value)"
                          />
                        </label>
                        <label v-if="selectedVisualInstruction.imageBoxMode === 'manual'" class="screenshot-box-metric">
                          <span>y</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.box?.y ?? 0"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionBoxMetric('y', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>w</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.box?.w ?? 50"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionBoxMetric('w', value)"
                          />
                        </label>
                        <label class="screenshot-box-metric">
                          <span>h</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.box?.h ?? 50"
                            size="small"
                            :controls="false"
                            :step="1"
                            @change="value => updateSelectedVisualInstructionBoxMetric('h', value)"
                          />
                        </label>
                      </div>
                      <div v-if="selectedVisualInstruction.target === 'image'" class="visual-match-config">
                        <label class="screenshot-config-line visual-threshold-line">
                          <span>图像匹配相似度阈值</span>
                          <el-input-number
                            :model-value="thresholdRatioToPercent(selectedVisualInstruction.threshold)"
                            class="screenshot-wide-number-input"
                            size="small"
                            :min="50"
                            :max="100"
                            :step="1"
                            controls-position="right"
                            @change="updateSelectedVisualInstructionThreshold"
                          >
                            <template #suffix>%</template>
                          </el-input-number>
                        </label>
                        <label
                          v-if="selectedVisualInstruction.scan === 'fixed'"
                          class="screenshot-config-line"
                        >
                          <span>单通道像素容差</span>
                          <el-input-number
                            :model-value="selectedVisualInstruction.pixelTolerance"
                            class="screenshot-wide-number-input"
                            size="small"
                            :min="0"
                            :max="255"
                            :step="1"
                            controls-position="right"
                            @change="updateSelectedVisualInstructionPixelTolerance"
                          />
                        </label>
                      </div>
                      <div v-if="selectedVisualInstruction.target === 'image'" class="visual-probe-line">
                        <button
                          type="button"
                          class="visual-threshold-probe"
                          :class="{ 'is-active': visualSimilarityProbeActive }"
                          :disabled="visualSimilarityProbeLoading"
                          @click="toggleVisualSimilarityProbe"
                        >
                          {{ visualSimilarityProbeActive ? '停止' : '检测' }}
                        </button>
                        <span v-if="visualSimilarityProbeText" class="visual-threshold-probe-result">
                          {{ visualSimilarityProbeText }}
                        </span>
                      </div>
                    </div>

                  </div>
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

      <div
        v-if="visualInstructionSetContextMenu.visible"
        class="screenshot-box-menu visual-instruction-set-menu"
        :style="{ left: `${visualInstructionSetContextMenu.x}px`, top: `${visualInstructionSetContextMenu.y}px` }"
        @click.stop
        @pointerdown.stop
      >
        <button type="button" class="is-danger" @click="confirmDeleteVisualInstructionSetFromContext">
          删除指令集
        </button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ArrowLeft,
  ArrowRight,
  Download,
  Refresh,
  Setting,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue';
import Sortable from 'sortablejs';
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
  getFanxiuGameWindow2ServiceStatus,
  listFanxiuPseudoCodeCards,
  listFanxiuGameWindow2Screenshots,
  matchFanxiuGameWindow2Screenshot,
  runFanxiuVisualScript,
  saveFanxiuGameWindow2Frame,
  saveFanxiuGameWindow2PreLabel,
  startFanxiuGameWindow2Service,
  startFanxiuPseudoCode,
  stopFanxiuVisualScript,
  updateFanxiuPseudoCodeCard,
  type FanxiuGameWindow2MatchBox,
  type FanxiuGameWindow2MatchResponse,
  type FanxiuGameWindow2ScreenshotItem,
  type FanxiuGameWindow2PreLabelBox,
  type FanxiuGameWindow2PreLabelPayload,
  type FanxiuGameWindow2ServiceStatus,
  type FanxiuPseudoCodeCard,
  type FanxiuPseudoCodeCardScope,
  type FanxiuPseudoCodeRunResponse,
} from '@/api/fanxiu';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { taskStore, type Device } from '@/store/taskStore';
import { useSortableList } from '@/utils/useSortableList';

interface OverlayBox {
  id: string;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

type OverlayBoxMetric = 'x' | 'y' | 'w' | 'h';
type VisualPointerPointField = 'start' | 'end';
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

type WindowSceneKey = 'sunlogin' | 'mumu';
type CaptureArea = 'outer' | 'client';
type RotateDegrees = '0' | '90' | '180' | '270';
type WindowViewMode = 'live' | 'control' | 'off';
type WindowTitleMatch = 'contains' | 'exact';

interface WindowSceneDefaults {
  targetTitle: string;
  titleMatch: WindowTitleMatch;
  cropText: string;
  captureArea: CaptureArea;
  trimBorderText: string;
  rotateDegrees: RotateDegrees;
  fps: number;
  quality: number;
  autoDismissPopup: boolean;
  displayScale: number;
  fixedWidth: number;
  fixedHeight: number;
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

interface LegacyCodeCard {
  title?: unknown;
  body?: unknown;
}

type VisualActionKind = 'waitClick' | 'guardClick' | 'click' | 'drag' | 'wait' | 'find' | 'findAll';
type VisualTargetKind = 'image' | 'text' | 'coordinate';
type VisualInstructionKind = 'normal' | 'ref';
type VisualReferenceTargetKind = 'instruction' | 'instructionSet';
type VisualScanMode = 'fixed' | 'range' | 'full';
type VisualTextMatch = 'contains' | 'exact' | 'regex';
type VisualCondition = 'appear' | 'disappear' | 'stable' | 'changed';
type VisualImageBoxMode = 'anchor' | 'manual';
type VisualShapeRole = 'target' | 'scan';

interface VisualPoint {
  x: number;
  y: number;
}

interface VisualPointerPoint extends VisualPoint {
  r: number;
}

interface VisualPointer {
  start: VisualPointerPoint | null;
  end: VisualPointerPoint | null;
  durationMs: number;
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
  setId: string;
  kind: VisualInstructionKind;
  refTargetKind: VisualReferenceTargetKind;
  refId: string;
  refName: string;
  setLabel: string;
  action: VisualActionKind;
  target: VisualTargetKind;
  label: string;
  frame: string;
  pointer: VisualPointer;
  box: VisualBox | null;
  scan: VisualScanMode;
  scanBox: VisualBox | null;
  imageBoxMode: VisualImageBoxMode;
  threshold: number;
  pixelTolerance: number;
  text: string;
  textMatch: VisualTextMatch;
  condition: VisualCondition;
  timeout: number;
}

interface VisualMacroProgram {
  version: 1;
  operations: VisualMacroAction[];
}

interface VisualInstructionSet {
  id: string;
  label: string;
  instructions: VisualMacroAction[];
}

interface ScreenshotBoxContextMenu {
  visible: boolean;
  x: number;
  y: number;
  boxId: string;
}

interface VisualInstructionSetContextMenu {
  visible: boolean;
  x: number;
  y: number;
  cardId: string;
  setId: string;
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

interface VisualMacroUiState {
  screenshotPanelOpen?: boolean;
  expandedCodeCardIds?: string[];
  selectedVisualInstructionSetKey?: string;
  selectedVisualInstructionKey?: string;
  pseudoOutputTab?: PseudoOutputTab;
}

const route = useRoute();
const router = useRouter();
const DEVICE_STORAGE_KEY = 'fanxiu.gameWindow2.entryId';
const WINDOW_STORAGE_KEY = 'fanxiu.gameWindow2.windowKey';
const WINDOW_CONFIG_STORAGE_PREFIX = 'fanxiu.gameWindow2.windowConfig';
const SCREENSHOT_SELECTION_STORAGE_PREFIX = 'fanxiu.gameWindow2.screenshotFilename';
const LEGACY_CODE_CARDS_STORAGE_KEY = 'fanxiu.gameWindow2.codeCards';
const VISUAL_MACRO_UI_STATE_STORAGE_KEY = 'fanxiu.gameWindow2.visualMacro.uiState.v1';
const VISUAL_MACRO_DEFAULT_THRESHOLD_KEY = 'fanxiu.gameWindow2.visualMacro.defaultThreshold';
const VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY = 'fanxiu.gameWindow2.visualMacro.defaultPointRadius';
const VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY = 'fanxiu.gameWindow2.visualMacro.defaultPixelTolerance';
const SCREENSHOT_MIN_ZOOM_PERCENT = 20;
const SCREENSHOT_MAX_ZOOM_PERCENT = 500;
const SCREENSHOT_ZOOM_STEP = 10;
const MIN_CONTENT_VISIBLE_AREA_RATIO = 0.2;
const MIN_CONTENT_VISIBLE_AXIS_RATIO = Math.sqrt(MIN_CONTENT_VISIBLE_AREA_RATIO);
const SERVICE_STATUS_SILENT_POLL_INTERVAL_MS = 120_000;
const VISUAL_ACTION_MARKER_START = '<!-- codeyun-visual-action-v1';
const VISUAL_ACTION_MARKER_END = '-->';
const windowViewModes: Array<{ value: WindowViewMode; label: string }> = [
  { value: 'live', label: '直播' },
  { value: 'control', label: '交互' },
  { value: 'off', label: '关闭' },
];
const windowScenes: WindowScene[] = [
  {
    key: 'sunlogin',
    label: '向日葵',
    defaults: {
      targetTitle: '1249152866',
      titleMatch: 'contains',
      cropText: '0,49,4,4',
      trimBorderText: '0,0,0,0',
      captureArea: 'outer',
      rotateDegrees: '90',
      fps: 10,
      quality: 80,
      autoDismissPopup: true,
      displayScale: 100,
      fixedWidth: 0,
      fixedHeight: 0,
    },
  },
  {
    key: 'mumu',
    label: 'MuMu模拟器',
    defaults: {
      targetTitle: 'MuMu',
      titleMatch: 'contains',
      cropText: '0,60,0,0',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 2,
      quality: 82,
      autoDismissPopup: false,
      displayScale: 60,
      fixedWidth: 900,
      fixedHeight: 1600,
    },
  },
];

const devices = computed(() => taskStore.devices);
const selectedEntryId = ref('');
const selectedWindowKey = ref<WindowSceneKey>('mumu');
const serviceStatus = ref<FanxiuGameWindow2ServiceStatus | null>(null);
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
const visualInstructionSetContextMenu = ref<VisualInstructionSetContextMenu>({
  visible: false,
  x: 0,
  y: 0,
  cardId: '',
  setId: '',
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
const visualMacroDefaultPixelTolerance = ref(5);
const selectedVisualInstructionKey = ref('');
const selectedVisualInstructionSetKey = ref('');
const visualInstructionTitleDrafts = ref<Record<string, string>>({});
const visualInstructionSetLabelDrafts = ref<Record<string, string>>({});
const visualTitleComposingKeys = ref<Set<string>>(new Set());
const activeVisualShapeRole = ref<VisualShapeRole>('target');
const visualSimilarityProbeActive = ref(false);
const visualSimilarityProbeLoading = ref(false);
const visualSimilarityProbeText = ref('');
const pseudoCompileLoading = ref(false);
const pseudoStartLoading = ref(false);
const visualScriptRunningCardId = ref('');
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
let visualSimilarityProbeTimer: number | null = null;
let visualSimilarityProbeSeq = 0;
let tokenRequestSeq = 0;
let lastInputErrorAt = 0;
let isApplyingWindowConfig = false;
let isApplyingVisualMacroUiState = false;
let serviceStatusRequestInFlight = false;
let serviceStatusLastLoadedAt = 0;
const codeCardSaveTimers = new Map<string, number>();
const codeCardListRef = ref<HTMLElement | null>(null);
const visualInstructionSetListRefs = new Map<string, HTMLElement>();
const visualInstructionSetSortables = new Map<string, Sortable>();

const selectedDevice = computed<Device | null>(() => (
  devices.value.find((device) => device.id === selectedEntryId.value) ?? null
));
const selectedWindowScene = computed(() => (
  windowScenes.find((scene) => scene.key === selectedWindowKey.value) ?? windowScenes[0]
));
const targetTitle = computed(() => selectedWindowScene.value.defaults.targetTitle);
const titleMatch = computed(() => selectedWindowScene.value.defaults.titleMatch);
const cropText = computed(() => selectedWindowScene.value.defaults.cropText);
const captureArea = computed(() => selectedWindowScene.value.defaults.captureArea);
const fixedFrameWidth = computed(() => selectedWindowScene.value.defaults.fixedWidth);
const fixedFrameHeight = computed(() => selectedWindowScene.value.defaults.fixedHeight);
const serviceActive = computed(() => Boolean(serviceStatus.value?.running));
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
const visualInstructionContextById = (instructionId: string) => {
  for (const card of codeCards.value) {
    for (const instruction of visualInstructionsOf(card)) {
      if (instruction.id === instructionId) return { card, instruction };
    }
  }
  return null;
};
const selectedRawVisualInstruction = computed(() => selectedVisualInstructionContext.value?.instruction ?? null);
const selectedVisualReferenceInstruction = computed(() => (
  selectedRawVisualInstruction.value?.kind === 'ref' ? selectedRawVisualInstruction.value : null
));
const selectedVisualReferenceCandidates = computed(() => (
  selectedVisualReferenceInstruction.value?.refTargetKind === 'instructionSet'
    ? visualInstructionSetReferenceCandidates.value
    : visualInstructionReferenceCandidates.value
));
const selectedVisualEditInstruction = computed(() => {
  const raw = selectedRawVisualInstruction.value;
  if (!raw) return null;
  return findVisualInstructionByReference(raw) ?? raw;
});
const selectedVisualEditInstructionContext = computed(() => (
  selectedVisualEditInstruction.value ? visualInstructionContextById(selectedVisualEditInstruction.value.id) : null
));
const selectedVisualInstruction = computed(() => selectedVisualEditInstruction.value);
const selectedVisualInstructionSetContext = computed(() => {
  if (!selectedVisualInstructionSetKey.value) return null;
  for (const card of codeCards.value) {
    for (const instructionSet of visualInstructionSetsOf(card)) {
      if (visualInstructionSetKey(card.id, instructionSet.id) === selectedVisualInstructionSetKey.value) {
        return { card, instructionSet };
      }
    }
  }
  return null;
});
const selectedVisualInstructionSet = computed(() => selectedVisualInstructionSetContext.value?.instructionSet ?? null);
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
  return `${naturalWidth.value} x ${naturalHeight.value}`;
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
  if (!selectedVisualInstructionSetKey.value) return '未选指令集';
  return selectedScreenshotFilename.value || '未绑定帧';
});
const selectedScreenshotGeometryText = computed(() => {
  const image = selectedScreenshotImage.value;
  if (!image || !naturalWidth.value || !naturalHeight.value) return '';
  const width = image.width || screenshotNaturalWidth.value;
  const height = image.height || screenshotNaturalHeight.value;
  if (!width || !height) return '';
  if (width === naturalWidth.value && height === naturalHeight.value) return '';
  return `截图 ${formatFrameSize(width, height)} / 直播 ${formatFrameSize(naturalWidth.value, naturalHeight.value)}`;
});
const pseudoOutputText = computed(() => {
  if (pseudoOutputTab.value === 'result') return pseudoExecutionResult.value || '暂无结果';
  return pseudoExecutionLog.value || '暂无日志';
});

const createVisualId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;

const defaultVisualAction = (overrides: Partial<VisualMacroAction> = {}): VisualMacroAction => {
  const id = overrides.id || createVisualId();
  return {
    version: 1,
    kind: 'normal',
    refTargetKind: 'instruction',
    refId: '',
    refName: '',
    setLabel: '',
    action: 'click',
    target: 'image',
    label: '',
    frame: '',
    pointer: {
      start: null,
      end: null,
      durationMs: 0,
    },
    box: null,
    scan: 'fixed',
    scanBox: null,
    imageBoxMode: 'anchor',
    threshold: visualMacroDefaultThreshold.value,
    pixelTolerance: visualMacroDefaultPixelTolerance.value,
    text: '',
    textMatch: 'contains',
    condition: 'appear',
    timeout: 8,
    ...overrides,
    id,
    setId: overrides.setId || id,
  };
};

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

const normalizeVisualPointerPoint = (value: unknown, fallbackRadius = visualMacroDefaultPointRadius.value): VisualPointerPoint | null => {
  const point = normalizeVisualPoint(value);
  if (!point) return null;
  const item = value && typeof value === 'object' ? value as Partial<VisualPointerPoint> : {};
  const radius = Number(item.r);
  return {
    ...point,
    r: Number.isFinite(radius) ? Math.max(0, Math.round(radius)) : fallbackRadius,
  };
};

const normalizeVisualPointer = (item: Partial<VisualMacroAction> & {
  point?: unknown;
  endPoint?: unknown;
  pointRadius?: unknown;
  timeout?: unknown;
}): VisualPointer => {
  const rawPointer = item.pointer && typeof item.pointer === 'object' ? item.pointer as Partial<VisualPointer> : {};
  const legacyRadius = Number(item.pointRadius);
  const fallbackRadius = Number.isFinite(legacyRadius)
    ? Math.max(0, Math.round(legacyRadius))
    : visualMacroDefaultPointRadius.value;
  const timeout = Number(item.timeout);
  const durationMs = Number(rawPointer.durationMs);
  return {
    start: normalizeVisualPointerPoint(rawPointer.start, fallbackRadius) ?? normalizeVisualPointerPoint(item.point, fallbackRadius),
    end: normalizeVisualPointerPoint(rawPointer.end, fallbackRadius) ?? normalizeVisualPointerPoint(item.endPoint, fallbackRadius),
    durationMs: Number.isFinite(durationMs)
      ? Math.max(0, Math.round(durationMs))
      : Number.isFinite(timeout)
        ? Math.max(0, Math.round(timeout * 1000))
        : 0,
  };
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

const visualPointerPointFromBox = (box: VisualBox | null): VisualPointerPoint | null => {
  if (!box) return null;
  return {
    x: Math.round(box.x + box.w / 2),
    y: Math.round(box.y + box.h / 2),
    r: visualMacroDefaultPointRadius.value,
  };
};

const normalizeVisualAction = (raw: unknown): VisualMacroAction => {
  const item = raw && typeof raw === 'object' ? raw as Partial<VisualMacroAction> : {};
  const id = String(item.id || createVisualId());
  const kind = String(item.kind) === 'ref' ? 'ref' : 'normal';
  const refTargetKind = String(item.refTargetKind) === 'instructionSet' ? 'instructionSet' : 'instruction';
  const action = ['waitClick', 'guardClick', 'click', 'drag', 'wait', 'find', 'findAll'].includes(String(item.action)) ? item.action as VisualActionKind : 'click';
  const rawTarget = String(item.target);
  const target = rawTarget === 'none'
    ? 'coordinate'
    : ['image', 'text', 'coordinate'].includes(rawTarget)
      ? rawTarget as VisualTargetKind
      : 'image';
  const scan = ['fixed', 'range', 'full'].includes(String(item.scan)) ? item.scan as VisualScanMode : 'fixed';
  const imageBoxMode = ['anchor', 'manual'].includes(String(item.imageBoxMode)) ? item.imageBoxMode as VisualImageBoxMode : 'anchor';
  const textMatch = ['contains', 'exact', 'regex'].includes(String(item.textMatch)) ? item.textMatch as VisualTextMatch : 'contains';
  const condition = ['appear', 'disappear', 'stable', 'changed'].includes(String(item.condition)) ? item.condition as VisualCondition : 'appear';
  const threshold = Number(item.threshold);
  const pixelTolerance = Number(item.pixelTolerance ?? (item as { pixel_tolerance?: unknown }).pixel_tolerance);
  const timeout = Number(item.timeout);
  const box = normalizeVisualBox(item.box);
  const pointer = normalizeVisualPointer(item as Partial<VisualMacroAction> & {
    point?: unknown;
    endPoint?: unknown;
    pointRadius?: unknown;
    timeout?: unknown;
  });
  if (!pointer.start && visualActionUsesPointer(action)) {
    pointer.start = visualPointerPointFromBox(box);
  }
  const rawLabel = String(item.label || '').trim();
  const label = rawLabel && /^点击\d+$/.test(rawLabel) && action === 'waitClick' && target === 'image'
    ? '等待点击 图片'
    : rawLabel;
  return defaultVisualAction({
    id,
    setId: String(item.setId || id),
    kind,
    refTargetKind,
    refId: String(item.refId || ''),
    refName: String(item.refName || ''),
    setLabel: String(item.setLabel || ''),
    action,
    target,
    label,
    frame: String(item.frame || ''),
    pointer,
    box,
    scan,
    scanBox: normalizeVisualBox(item.scanBox),
    imageBoxMode,
    threshold: Number.isFinite(threshold) ? clamp(threshold, 0.5, 1) : visualMacroDefaultThreshold.value,
    pixelTolerance: Number.isFinite(pixelTolerance) ? clamp(Math.round(pixelTolerance), 0, 255) : visualMacroDefaultPixelTolerance.value,
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

const thresholdRatioToPercent = (value: number) => Math.round(clamp(Number(value) || 0, 0, 1) * 100);

const thresholdPercentToRatio = (value: unknown) => {
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return visualMacroDefaultThreshold.value;
  return clamp(Math.round(nextValue), 50, 100) / 100;
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

const setVisualMacroDefaultPixelTolerance = (value: unknown, persist = true) => {
  if (value === null || value === undefined || value === '') {
    visualMacroDefaultPixelTolerance.value = 5;
    if (persist) window.localStorage.setItem(VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY, String(visualMacroDefaultPixelTolerance.value));
    return;
  }
  const nextValue = Math.round(Number(value));
  visualMacroDefaultPixelTolerance.value = Number.isFinite(nextValue) ? clamp(nextValue, 0, 255) : 5;
  if (persist) {
    window.localStorage.setItem(VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY, String(visualMacroDefaultPixelTolerance.value));
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

const migrateVisualMacroDefaultPixelTolerance = (value: unknown) => {
  if (window.localStorage.getItem(VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY) !== null) return;
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  setVisualMacroDefaultPixelTolerance(nextValue);
};

const loadVisualMacroDefaults = () => {
  setVisualMacroDefaultThreshold(window.localStorage.getItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY), false);
  setVisualMacroDefaultPointRadius(window.localStorage.getItem(VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY), false);
  setVisualMacroDefaultPixelTolerance(window.localStorage.getItem(VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY), false);
};

const normalizeVisualProgram = (raw: unknown): VisualMacroProgram => {
  if (!raw || typeof raw !== 'object') return defaultVisualProgram();
  const item = raw as { defaultThreshold?: unknown; threshold?: unknown; operations?: unknown[] };
  const operations = Array.isArray(item.operations) ? item.operations.map(normalizeVisualAction) : [normalizeVisualAction(raw)];
  migrateVisualMacroDefaultThreshold(item.defaultThreshold ?? item.threshold ?? operations.find((operation) => operation.target === 'image')?.threshold);
  migrateVisualMacroDefaultPointRadius(operations.find((operation) => operation.action === 'click')?.pointer.start?.r);
  migrateVisualMacroDefaultPixelTolerance(operations.find((operation) => operation.target === 'image')?.pixelTolerance);
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

const visualInstructionSetsOf = (card: CodeCard): VisualInstructionSet[] => {
  const sets = new Map<string, VisualMacroAction[]>();
  for (const instruction of visualInstructionsOf(card)) {
    const setId = instruction.setId || instruction.id;
    const instructions = sets.get(setId) ?? [];
    instructions.push(instruction);
    sets.set(setId, instructions);
  }
  return [...sets.entries()].map(([id, instructions]) => ({
    id,
    label: instructions.find((instruction) => instruction.setLabel.trim())?.setLabel.trim() ?? '',
    instructions,
  }));
};

const firstInstructionOfSet = (instructionSet: VisualInstructionSet) => instructionSet.instructions[0] ?? null;

const visualInstructionKey = (cardId: string, instructionId: string) => `${cardId}:${instructionId}`;
const visualInstructionSetKey = (cardId: string, setId: string) => `${cardId}:${setId}`;

const normalizeVisualMacroUiState = (raw: unknown): VisualMacroUiState => {
  if (!raw || typeof raw !== 'object') return {};
  const item = raw as VisualMacroUiState;
  const tab = item.pseudoOutputTab === 'result' ? 'result' : item.pseudoOutputTab === 'log' ? 'log' : undefined;
  return {
    screenshotPanelOpen: typeof item.screenshotPanelOpen === 'boolean' ? item.screenshotPanelOpen : undefined,
    expandedCodeCardIds: Array.isArray(item.expandedCodeCardIds)
      ? [...new Set(item.expandedCodeCardIds.filter((id): id is string => typeof id === 'string' && Boolean(id)))]
      : undefined,
    selectedVisualInstructionSetKey: typeof item.selectedVisualInstructionSetKey === 'string' ? item.selectedVisualInstructionSetKey : undefined,
    selectedVisualInstructionKey: typeof item.selectedVisualInstructionKey === 'string' ? item.selectedVisualInstructionKey : undefined,
    pseudoOutputTab: tab,
  };
};

const currentVisualMacroUiState = (): VisualMacroUiState => ({
  screenshotPanelOpen: screenshotPanelOpen.value,
  expandedCodeCardIds: expandedCodeCardIds.value,
  selectedVisualInstructionSetKey: selectedVisualInstructionSetKey.value,
  selectedVisualInstructionKey: selectedVisualInstructionKey.value,
  pseudoOutputTab: pseudoOutputTab.value,
});

const persistVisualMacroUiState = () => {
  if (isApplyingVisualMacroUiState) return;
  window.localStorage.setItem(VISUAL_MACRO_UI_STATE_STORAGE_KEY, JSON.stringify(currentVisualMacroUiState()));
};

const applyVisualMacroUiState = () => {
  isApplyingVisualMacroUiState = true;
  try {
    const rawText = window.localStorage.getItem(VISUAL_MACRO_UI_STATE_STORAGE_KEY);
    if (!rawText) return;
    const state = normalizeVisualMacroUiState(JSON.parse(rawText));
    if (typeof state.screenshotPanelOpen === 'boolean') screenshotPanelOpen.value = state.screenshotPanelOpen;
    if (state.expandedCodeCardIds) expandedCodeCardIds.value = state.expandedCodeCardIds;
    if (state.selectedVisualInstructionSetKey) selectedVisualInstructionSetKey.value = state.selectedVisualInstructionSetKey;
    if (state.selectedVisualInstructionKey) selectedVisualInstructionKey.value = state.selectedVisualInstructionKey;
    if (state.pseudoOutputTab) pseudoOutputTab.value = state.pseudoOutputTab;
  } catch {
    window.localStorage.removeItem(VISUAL_MACRO_UI_STATE_STORAGE_KEY);
  } finally {
    window.requestAnimationFrame(() => {
      isApplyingVisualMacroUiState = false;
    });
  }
};

const pruneVisualMacroUiState = () => {
  const cardIds = new Set(codeCards.value.map((card) => card.id));
  const nextExpandedIds = expandedCodeCardIds.value.filter((id) => cardIds.has(id));
  if (nextExpandedIds.length !== expandedCodeCardIds.value.length) expandedCodeCardIds.value = nextExpandedIds;
  if (selectedVisualInstructionSetKey.value && !selectedVisualInstructionSetContext.value) {
    selectedVisualInstructionSetKey.value = '';
  }
  if (selectedVisualInstructionKey.value && !selectedVisualInstructionContext.value) {
    selectedVisualInstructionKey.value = '';
  }
  persistVisualMacroUiState();
};

const selectVisualInstructionFrame = async (card: CodeCard, instruction: VisualMacroAction) => {
  selectedVisualInstructionSetKey.value = visualInstructionSetKey(card.id, instruction.setId || instruction.id);
  selectedVisualInstructionKey.value = visualInstructionKey(card.id, instruction.id);
  const displayInstruction = findVisualInstructionByReference(instruction) ?? instruction;
  if (!displayInstruction.frame) {
    clearScreenshotSelection();
    return;
  }
  if (!screenshotPanelOpen.value) {
    screenshotPanelOpen.value = true;
    await nextTick();
  }
  if (!screenshotLoaded.value || !screenshotImages.value.some((item) => item.filename === displayInstruction.frame)) {
    await loadScreenshotList(displayInstruction.frame);
    return;
  }
  await selectScreenshotImage(displayInstruction.frame);
};

const selectVisualInstructionSetFrame = async (card: CodeCard, instructionSet: VisualInstructionSet) => {
  selectedVisualInstructionSetKey.value = visualInstructionSetKey(card.id, instructionSet.id);
  const selectedInstruction = instructionSet.instructions.find(
    (instruction) => selectedVisualInstructionKey.value === visualInstructionKey(card.id, instruction.id),
  ) ?? instructionSet.instructions[0];
  if (!selectedInstruction) {
    selectedVisualInstructionKey.value = '';
    clearScreenshotSelection();
    return;
  }
  await selectVisualInstructionFrame(card, selectedInstruction);
};

const selectVisualInstructionFromSelectedSet = (instruction: VisualMacroAction) => {
  const context = selectedVisualInstructionSetContext.value;
  if (!context) return;
  void selectVisualInstructionFrame(context.card, instruction);
};

const compactVisualInstruction = (operation: VisualMacroAction) => {
  const compact: Partial<VisualMacroAction> = {
    version: operation.version,
    id: operation.id,
    setId: operation.setId,
    kind: operation.kind,
    refTargetKind: operation.refTargetKind,
    refId: operation.refId,
    refName: operation.refName,
    setLabel: operation.setLabel,
    action: operation.action,
    target: operation.target,
  };
  const label = operation.label.trim();
  if (label) compact.label = label;
  if (operation.frame) compact.frame = operation.frame;
  if (operation.pointer.start || operation.pointer.end || operation.pointer.durationMs) {
    compact.pointer = {
      start: operation.pointer.start,
      end: operation.pointer.end,
      durationMs: operation.pointer.durationMs,
    };
  }
  if (operation.action === 'wait') {
    compact.condition = operation.condition;
    compact.timeout = operation.timeout;
  }
  if (operation.target === 'image') {
    compact.scan = operation.scan;
    if (operation.scan === 'range' && operation.scanBox) compact.scanBox = operation.scanBox;
    compact.imageBoxMode = operation.imageBoxMode;
    compact.threshold = operation.threshold;
    compact.pixelTolerance = operation.pixelTolerance;
    if (operation.box) compact.box = operation.box;
  }
  if (operation.target === 'text') {
    compact.scan = operation.scan;
    if (operation.scan === 'range' && operation.scanBox) compact.scanBox = operation.scanBox;
    compact.text = operation.text;
    compact.textMatch = operation.textMatch;
    if (operation.box) compact.box = operation.box;
  }
  return compact;
};

const compactVisualProgram = (program: VisualMacroProgram) => ({
  version: program.version,
  operations: program.operations.map(compactVisualInstruction),
});

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
  return `${VISUAL_ACTION_MARKER_START}\n${JSON.stringify(compactVisualProgram(program), null, 2)}\n${VISUAL_ACTION_MARKER_END}\n${lines.join('\n')}`;
};

const visualFrameNo = (frame: string) => {
  const match = String(frame || '').match(/^(\d{1,4})\./);
  return match ? String(Number(match[1])) : '';
};

const visualActionLabel = (action: VisualActionKind) => ({
  waitClick: '等待点击',
  guardClick: '守护点击',
  click: '点击',
  drag: '拖拽',
  wait: '等待',
  find: '查找',
  findAll: '批量查找',
}[action]);

const visualActionUsesPointer = (action: VisualActionKind) => action === 'waitClick' || action === 'guardClick' || action === 'click' || action === 'drag';

const visualInstructionUsesTargetConfig = (instruction: VisualMacroAction) => instruction.target === 'image' || instruction.target === 'text';

const visualInstructionUsesShape = (instruction: VisualMacroAction) => visualInstructionUsesTargetConfig(instruction);

const visualTargetLabel = (target: VisualTargetKind) => ({
  image: '图片',
  text: '文本',
  coordinate: '坐标',
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
      : '坐标';
  if (action.action === 'wait') {
    return `等待 ${target} ${visualConditionLabel(action.condition)}`;
  }
  if (action.action === 'waitClick') {
    return `等待点击 ${target}`;
  }
  if (action.action === 'guardClick') {
    return `守护点击 ${target}`;
  }
  return `${visualActionLabel(action.action)} ${target}`;
};

const visualInstructionDisplayTitle = (action: VisualMacroAction) => (
  action.kind === 'ref' ? `调用：${action.refName || '未选择'}` : action.label.trim() || visualInstructionFallbackTitle(action)
);

const visualInstructionSetDisplayTitle = (instructionSet: VisualInstructionSet) => (
  instructionSet.label.trim() || (firstInstructionOfSet(instructionSet) ? visualInstructionDisplayTitle(firstInstructionOfSet(instructionSet)!) : '空指令集')
);

const isVisualInstructionSetLabelEditing = (setId: string) => Object.hasOwn(visualInstructionSetLabelDrafts.value, setId);

const visualInstructionSetLabelInputValue = (instructionSet: VisualInstructionSet) => (
  isVisualInstructionSetLabelEditing(instructionSet.id) ? visualInstructionSetLabelDrafts.value[instructionSet.id] : instructionSet.label
);

const isVisualInstructionTitleEditing = (instructionId: string) => Object.hasOwn(visualInstructionTitleDrafts.value, instructionId);

const visualInstructionTitleInputValue = (instruction: VisualMacroAction) => (
  isVisualInstructionTitleEditing(instruction.id) ? visualInstructionTitleDrafts.value[instruction.id] : instruction.label
);

const isVisualTitleComposing = (key: string) => visualTitleComposingKeys.value.has(key);

const beginVisualTitleComposition = (key: string) => {
  visualTitleComposingKeys.value = new Set([...visualTitleComposingKeys.value, key]);
};

const endVisualTitleComposition = (key: string) => {
  if (!visualTitleComposingKeys.value.has(key)) return;
  const nextKeys = new Set(visualTitleComposingKeys.value);
  nextKeys.delete(key);
  visualTitleComposingKeys.value = nextKeys;
};

const beginVisualInstructionTitleEdit = (instruction: VisualMacroAction) => {
  selectVisualInstructionFromSelectedSet(instruction);
  visualInstructionTitleDrafts.value = {
    ...visualInstructionTitleDrafts.value,
    [instruction.id]: instruction.label,
  };
};

const beginVisualInstructionSetLabelEdit = (instructionSet: VisualInstructionSet) => {
  visualInstructionSetLabelDrafts.value = {
    ...visualInstructionSetLabelDrafts.value,
    [instructionSet.id]: instructionSet.label,
  };
};

const setVisualInstructionSetLabelDraft = (setId: string, value: string) => {
  visualInstructionSetLabelDrafts.value = {
    ...visualInstructionSetLabelDrafts.value,
    [setId]: value,
  };
};

const commitVisualInstructionSetLabelDraft = (card: CodeCard, setId: string) => {
  if (!isVisualInstructionSetLabelEditing(setId)) return;
  const nextLabel = visualInstructionSetLabelDrafts.value[setId] ?? '';
  const { [setId]: _removed, ...rest } = visualInstructionSetLabelDrafts.value;
  visualInstructionSetLabelDrafts.value = rest;
  const currentLabel = visualInstructionSetsOf(card).find((instructionSet) => instructionSet.id === setId)?.label ?? '';
  if (nextLabel === currentLabel) return;
  updateVisualInstructionSetLabel(card, setId, nextLabel);
};

const commitVisualInstructionSetLabelByEnter = (card: CodeCard, setId: string, event: Event) => {
  if ((event as KeyboardEvent).isComposing || isVisualTitleComposing(`set:${setId}`)) return;
  commitVisualInstructionSetLabelDraft(card, setId);
  (event.target as HTMLInputElement).blur();
};

const setVisualInstructionTitleDraft = (instructionId: string, value: string) => {
  visualInstructionTitleDrafts.value = {
    ...visualInstructionTitleDrafts.value,
    [instructionId]: value,
  };
};

const commitVisualInstructionTitleDraft = (instruction: VisualMacroAction) => {
  if (!isVisualInstructionTitleEditing(instruction.id)) return;
  const nextLabel = visualInstructionTitleDrafts.value[instruction.id] ?? '';
  const { [instruction.id]: _removed, ...rest } = visualInstructionTitleDrafts.value;
  visualInstructionTitleDrafts.value = rest;
  if (nextLabel === instruction.label) return;
  const context = visualInstructionContextById(instruction.id);
  if (!context) return;
  updateVisualInstruction(context.card, instruction.id, { label: nextLabel });
};

const commitVisualInstructionTitleDraftByEnter = (instruction: VisualMacroAction, event: Event) => {
  if ((event as KeyboardEvent).isComposing || isVisualTitleComposing(`instruction:${instruction.id}`)) return;
  commitVisualInstructionTitleDraft(instruction);
  (event.target as HTMLInputElement).blur();
};

const handleVisualInstructionTitleInput = (instructionId: string, event: Event) => {
  if (isVisualTitleComposing(`instruction:${instructionId}`)) return;
  setVisualInstructionTitleDraft(instructionId, (event.target as HTMLInputElement).value);
};

const commitVisualInstructionTitleComposition = (instruction: VisualMacroAction, event: Event) => {
  endVisualTitleComposition(`instruction:${instruction.id}`);
  setVisualInstructionTitleDraft(instruction.id, (event.target as HTMLInputElement).value);
};

const isVisualInstructionLabelUnique = (instruction: VisualMacroAction) => {
  if (instruction.kind === 'ref') return false;
  const label = instruction.label.trim();
  if (!label) return false;
  let count = 0;
  for (const card of codeCards.value) {
    for (const item of visualInstructionsOf(card)) {
      if (item.label.trim() === label) count += 1;
      if (count > 1) return false;
    }
  }
  return count === 1;
};

const isVisualInstructionSetLabelUnique = (instructionSet: VisualInstructionSet) => {
  const label = instructionSet.label.trim();
  if (!label) return false;
  let count = 0;
  for (const card of codeCards.value) {
    for (const item of visualInstructionSetsOf(card)) {
      if (item.label.trim() === label) count += 1;
      if (count > 1) return false;
    }
  }
  return count === 1;
};

const visualInstructionReferenceCandidates = computed(() => (
  codeCards.value.flatMap((card) => (
    visualInstructionsOf(card)
      .filter((instruction) => instruction.kind !== 'ref' && isVisualInstructionLabelUnique(instruction))
      .map((instruction) => ({
        id: instruction.id,
        name: instruction.label.trim(),
        instruction,
      }))
  ))
));

const visualInstructionSetReferenceCandidates = computed(() => (
  codeCards.value.flatMap((card) => (
    visualInstructionSetsOf(card)
      .filter((instructionSet) => isVisualInstructionSetLabelUnique(instructionSet))
      .map((instructionSet) => ({
        id: instructionSet.id,
        name: instructionSet.label.trim(),
        instructionSet,
      }))
  ))
));

const findVisualInstructionByReference = (instruction: VisualMacroAction) => {
  if (instruction.kind !== 'ref') return null;
  if (instruction.refTargetKind === 'instructionSet') {
    const candidates = visualInstructionSetReferenceCandidates.value;
    const instructionSet = candidates.find((item) => instruction.refId && item.id === instruction.refId)?.instructionSet
      ?? candidates.find((item) => instruction.refName && item.name === instruction.refName)?.instructionSet
      ?? null;
    return instructionSet?.instructions[0] ?? null;
  }
  const candidates = visualInstructionReferenceCandidates.value;
  return candidates.find((item) => instruction.refId && item.id === instruction.refId)?.instruction
    ?? candidates.find((item) => instruction.refName && item.name === instruction.refName)?.instruction
    ?? null;
};


const updateVisualInstruction = (card: CodeCard, operationId: string, patch: Partial<VisualMacroAction>) => {
  const program = visualProgramOf(card);
  const operations = program.operations.map((operation) => {
    if (operation.id !== operationId) return operation;
    const nextPointer = patch.pointer ? { ...operation.pointer, ...patch.pointer } : operation.pointer;
    const previousFallbackTitle = visualInstructionFallbackTitle(operation);
    const mergedOperation = {
      ...operation,
      ...patch,
      pointer: nextPointer,
    };
    if ((patch.action || patch.target) && operation.label.trim() === previousFallbackTitle) {
      mergedOperation.label = visualInstructionFallbackTitle(mergedOperation);
    }
    if (patch.target === 'image' && !mergedOperation.box) {
      mergedOperation.box = mergedOperation.pointer.start ? buildDefaultImageBox(mergedOperation.pointer.start) : visualBoxOrDefault(mergedOperation);
    }
    if (patch.action && visualActionUsesPointer(patch.action) && !mergedOperation.pointer.start) {
      mergedOperation.pointer = {
        ...mergedOperation.pointer,
        start: visualPointerPointFromBox(mergedOperation.box),
      };
    }
    if (patch.action === 'waitClick' || patch.action === 'guardClick' || patch.action === 'click') {
      return normalizeVisualAction({
        ...mergedOperation,
        id: operation.id,
        pointer: { ...nextPointer, end: null, durationMs: 0 },
      });
    }
    if (patch.action === 'drag') {
      const fallbackEnd = nextPointer.end ?? (nextPointer.start ? { ...nextPointer.start } : null);
      return normalizeVisualAction({
        ...mergedOperation,
        id: operation.id,
        pointer: { ...nextPointer, end: fallbackEnd },
      });
    }
    return normalizeVisualAction({ ...mergedOperation, id: operation.id });
  });
  card.body = serializeVisualProgram(defaultVisualProgram(operations));
  scheduleCodeCardSave(card);
  if (selectedVisualInstructionKey.value === visualInstructionKey(card.id, operationId)) {
    void nextTick(drawScreenshotOverlay);
  }
};

const updateSelectedVisualInstruction = (patch: Partial<VisualMacroAction>) => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, patch);
};

const updateSelectedVisualInstructionKind = (kind: VisualInstructionKind) => {
  const context = selectedVisualInstructionContext.value;
  if (!context) return;
  if (kind === 'normal') {
    updateVisualInstruction(context.card, context.instruction.id, {
      kind: 'normal',
      refTargetKind: 'instruction',
      refId: '',
      refName: '',
    });
    return;
  }
  const candidate = visualInstructionReferenceCandidates.value.find((item) => item.id !== context.instruction.id);
  updateVisualInstruction(context.card, context.instruction.id, {
    kind: 'ref',
    refTargetKind: 'instruction',
    refId: candidate?.id ?? '',
    refName: candidate?.name ?? '',
  });
};

const updateSelectedVisualInstructionReferenceTargetKind = (refTargetKind: VisualReferenceTargetKind) => {
  const context = selectedVisualInstructionContext.value;
  if (!context) return;
  const candidate = refTargetKind === 'instructionSet'
    ? visualInstructionSetReferenceCandidates.value.find((item) => item.id !== (context.instruction.setId || context.instruction.id))
    : visualInstructionReferenceCandidates.value.find((item) => item.id !== context.instruction.id);
  updateVisualInstruction(context.card, context.instruction.id, {
    kind: 'ref',
    refTargetKind,
    refId: candidate?.id ?? '',
    refName: candidate?.name ?? '',
  });
};

const updateSelectedVisualInstructionReference = (refName: string) => {
  const context = selectedVisualInstructionContext.value;
  if (!context) return;
  const refTargetKind = context.instruction.refTargetKind;
  const candidate = refTargetKind === 'instructionSet'
    ? visualInstructionSetReferenceCandidates.value.find((item) => item.name === refName)
    : visualInstructionReferenceCandidates.value.find((item) => item.name === refName);
  updateVisualInstruction(context.card, context.instruction.id, {
    kind: 'ref',
    refTargetKind,
    refId: candidate?.id ?? '',
    refName,
  });
  void nextTick(() => {
    const latest = selectedVisualInstructionContext.value;
    if (latest) void selectVisualInstructionFrame(latest.card, latest.instruction);
  });
};

const updateVisualInstructionSetLabel = (card: CodeCard, setId: string, setLabel: string) => {
  const program = visualProgramOf(card);
  const operations = program.operations.map((operation) => (
    (operation.setId || operation.id) === setId
      ? normalizeVisualAction({ ...operation, setLabel })
      : operation
  ));
  card.body = serializeVisualProgram(defaultVisualProgram(operations));
  scheduleCodeCardSave(card);
};

const handleVisualInstructionSetLabelInput = (card: CodeCard, setId: string, event: Event) => {
  if (isVisualTitleComposing(`set:${setId}`)) return;
  setVisualInstructionSetLabelDraft(setId, (event.target as HTMLInputElement).value);
};

const commitVisualInstructionSetLabelInput = (card: CodeCard, setId: string, event: Event) => {
  endVisualTitleComposition(`set:${setId}`);
  setVisualInstructionSetLabelDraft(setId, (event.target as HTMLInputElement).value);
};

const saveSelectedVisualInstructionCardNow = () => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  saveCodeCardNow(context.card);
};

const clampVisualPointValue = (metric: VisualPointMetric, value: number) => {
  const max = metric === 'x' ? screenshotNaturalWidth.value : screenshotNaturalHeight.value;
  if (!max) return Math.max(0, value);
  return Math.round(clamp(value, 0, Math.max(0, max - 1)));
};

const visualPointerPointOrDefault = (point: VisualPointerPoint | null): VisualPointerPoint => ({
  x: point?.x ?? 0,
  y: point?.y ?? 0,
  r: point?.r ?? visualMacroDefaultPointRadius.value,
});

const updateSelectedVisualInstructionPointerPoint = (
  field: VisualPointerPointField,
  metric: VisualPointMetric,
  value: number | undefined,
) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  const current = visualPointerPointOrDefault(context.instruction.pointer[field]);
  const nextPoint = {
    ...current,
    [metric]: clampVisualPointValue(metric, nextValue),
  };
  const nextPointer = {
    ...context.instruction.pointer,
    [field]: nextPoint,
  };
  const patch: Partial<VisualMacroAction> = { pointer: nextPointer };
  if (field === 'start' && context.instruction.target === 'image' && context.instruction.imageBoxMode === 'anchor') {
    const currentBox = visualBoxOrDefault(context.instruction);
    patch.box = buildAnchoredVisualBox({
      ...context.instruction,
      pointer: nextPointer,
    }, currentBox.w || 50, currentBox.h || currentBox.w || 50);
  }
  updateVisualInstruction(context.card, context.instruction.id, {
    ...patch,
  });
};

const updateSelectedVisualInstructionPointerRadius = (field: VisualPointerPointField, value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  const maxRadius = Math.max(screenshotNaturalWidth.value, screenshotNaturalHeight.value, 0);
  const current = visualPointerPointOrDefault(context.instruction.pointer[field]);
  updateVisualInstruction(context.card, context.instruction.id, {
    pointer: {
      ...context.instruction.pointer,
      [field]: {
        ...current,
        r: Math.round(clamp(nextValue, 0, maxRadius || Number.MAX_SAFE_INTEGER)),
      },
    },
  });
};

const updateSelectedVisualInstructionPointerDuration = (value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    pointer: {
      ...context.instruction.pointer,
      durationMs: Math.round(clamp(nextValue, 0, 5000)),
    },
  });
};

const updateSelectedVisualInstructionThreshold = (value: number | undefined) => {
  const nextValue = thresholdPercentToRatio(value);
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    threshold: clamp(nextValue, 0.5, 1),
  });
};

const updateSelectedVisualInstructionPixelTolerance = (value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  updateSelectedVisualInstruction({
    pixelTolerance: clamp(nextValue, 0, 255),
  });
};

const buildDefaultScanBox = (instruction: VisualMacroAction): VisualBox => {
  const base = instruction.box ?? (instruction.pointer.start ? buildDefaultImageBox(instruction.pointer.start) : visualBoxOrDefault(instruction));
  const padding = 80;
  const maxWidth = Math.max(1, screenshotNaturalWidth.value || naturalWidth.value || base.x + base.w + padding);
  const maxHeight = Math.max(1, screenshotNaturalHeight.value || naturalHeight.value || base.y + base.h + padding);
  const x = Math.round(clamp(base.x - padding, 0, Math.max(0, maxWidth - 4)));
  const y = Math.round(clamp(base.y - padding, 0, Math.max(0, maxHeight - 4)));
  const right = clamp(base.x + base.w + padding, x + 4, maxWidth);
  const bottom = clamp(base.y + base.h + padding, y + 4, maxHeight);
  return {
    x,
    y,
    w: Math.round(right - x),
    h: Math.round(bottom - y),
  };
};

const visualScanBoxOrDefault = (instruction: VisualMacroAction): VisualBox => (
  instruction.scanBox ?? buildDefaultScanBox(instruction)
);

const updateSelectedVisualInstructionScan = (scan: VisualScanMode) => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    scan,
    scanBox: scan === 'range' ? visualScanBoxOrDefault(context.instruction) : context.instruction.scanBox,
  });
  if (scan === 'range') activeVisualShapeRole.value = 'scan';
};

const updateSelectedVisualInstructionScanBox = (scanBox: VisualBox) => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    scan: 'range',
    scanBox: clampVisualBox(scanBox),
  });
};

const updateSelectedVisualInstructionScanBoxMetric = (metric: OverlayBoxMetric, value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateSelectedVisualInstructionScanBox({
    ...visualScanBoxOrDefault(context.instruction),
    [metric]: nextValue,
  });
};

const visualBoxOrDefault = (instruction: VisualMacroAction): VisualBox => {
  if (instruction.box) return instruction.box;
  if (instruction.pointer.start) return buildDefaultImageBox(instruction.pointer.start);
  return {
    x: 0,
    y: 0,
    w: Math.min(50, Math.max(1, screenshotNaturalWidth.value || 50)),
    h: Math.min(50, Math.max(1, screenshotNaturalHeight.value || 50)),
  };
};

const clampVisualBox = (box: VisualBox): VisualBox => {
  const maxWidth = screenshotNaturalWidth.value || Math.max(box.x + box.w, 50);
  const maxHeight = screenshotNaturalHeight.value || Math.max(box.y + box.h, 50);
  const x = Math.round(clamp(box.x, 0, Math.max(0, maxWidth - 4)));
  const y = Math.round(clamp(box.y, 0, Math.max(0, maxHeight - 4)));
  const w = Math.round(clamp(box.w, 4, Math.max(4, maxWidth - x)));
  const h = Math.round(clamp(box.h, 4, Math.max(4, maxHeight - y)));
  return { x, y, w, h };
};

const buildAnchoredVisualBox = (instruction: VisualMacroAction, width: number, height = width) => {
  const start = instruction.pointer.start;
  if (!start) return clampVisualBox({ ...visualBoxOrDefault(instruction), w: width, h: height });
  const maxWidth = Math.max(1, screenshotNaturalWidth.value || naturalWidth.value || width);
  const maxHeight = Math.max(1, screenshotNaturalHeight.value || naturalHeight.value || height);
  const w = Math.round(clamp(width, 4, maxWidth));
  const h = Math.round(clamp(height, 4, maxHeight));
  return clampVisualBox({
    x: Math.round(start.x - w / 2),
    y: Math.round(start.y - h / 2),
    w,
    h,
  });
};

const updateSelectedVisualInstructionBox = (box: VisualBox, imageBoxMode?: VisualImageBoxMode) => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    box: clampVisualBox(box),
    ...(imageBoxMode ? { imageBoxMode } : {}),
  });
};

const updateSelectedVisualInstructionBoxMetric = (metric: OverlayBoxMetric, value: number | undefined) => {
  const nextValue = Math.round(Number(value));
  if (!Number.isFinite(nextValue)) return;
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  if (context.instruction.imageBoxMode === 'anchor') {
    const current = visualBoxOrDefault(context.instruction);
    const width = metric === 'w' ? nextValue : current.w;
    const height = metric === 'h' ? nextValue : current.h;
    updateSelectedVisualInstructionBox(buildAnchoredVisualBox(context.instruction, width, height));
    return;
  }
  updateSelectedVisualInstructionBox({
    ...visualBoxOrDefault(context.instruction),
    [metric]: nextValue,
  });
};

const updateSelectedVisualInstructionImageBoxMode = (imageBoxMode: VisualImageBoxMode) => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context) return;
  const current = visualBoxOrDefault(context.instruction);
  updateVisualInstruction(context.card, context.instruction.id, {
    imageBoxMode,
    box: imageBoxMode === 'anchor'
      ? buildAnchoredVisualBox(context.instruction, current.w || 50, current.h || current.w || 50)
      : clampVisualBox(current),
  });
};

const resetSelectedVisualInstructionBox = (size = 50) => {
  const context = selectedVisualEditInstructionContext.value;
  const point = context?.instruction.pointer.start;
  if (!context || !point) return;
  updateVisualInstruction(context.card, context.instruction.id, {
    imageBoxMode: 'anchor',
    box: buildDefaultImageBox(point, size),
  });
};

const deleteVisualInstruction = (card: CodeCard, operationId: string) => {
  const program = visualProgramOf(card);
  const deletedIndex = program.operations.findIndex((operation) => operation.id === operationId);
  const deletedInstruction = deletedIndex >= 0 ? program.operations[deletedIndex] : null;
  const operations = program.operations.filter((operation) => operation.id !== operationId);
  card.body = serializeVisualProgram(defaultVisualProgram(operations));
  if (selectedVisualInstructionKey.value === visualInstructionKey(card.id, operationId)) {
    const siblingInstructions = deletedInstruction
      ? operations.filter((operation) => operation.setId === deletedInstruction.setId)
      : [];
    const fallbackInstruction = siblingInstructions[Math.max(0, Math.min(deletedIndex, siblingInstructions.length - 1))] ?? siblingInstructions[0] ?? null;
    if (fallbackInstruction) {
      selectedVisualInstructionSetKey.value = visualInstructionSetKey(card.id, fallbackInstruction.setId || fallbackInstruction.id);
      selectedVisualInstructionKey.value = visualInstructionKey(card.id, fallbackInstruction.id);
    } else {
      selectedVisualInstructionKey.value = '';
      selectedVisualInstructionSetKey.value = '';
      clearScreenshotSelection();
    }
  }
  scheduleCodeCardSave(card);
};

const confirmDeleteVisualInstruction = async (card: CodeCard, instruction: VisualMacroAction) => {
  try {
    await ElMessageBox.confirm(
      `删除指令「${visualActionLabel(instruction.action)} ${visualTargetLabel(instruction.target)}」？`,
      '删除指令',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    );
  } catch {
    return;
  }
  deleteVisualInstruction(card, instruction.id);
};

const deleteVisualInstructionSet = (card: CodeCard, setId: string) => {
  const program = visualProgramOf(card);
  const instructionSets = visualInstructionSetsOf(card);
  const deletedIndex = instructionSets.findIndex((instructionSet) => instructionSet.id === setId);
  if (deletedIndex < 0) return;
  const deletedSet = instructionSets[deletedIndex];
  const deletedInstructionIds = new Set(deletedSet.instructions.map((instruction) => instruction.id));
  const operations = program.operations.filter((operation) => (operation.setId || operation.id) !== setId);
  card.body = serializeVisualProgram(defaultVisualProgram(operations));

  const deletedSetSelected = selectedVisualInstructionSetKey.value === visualInstructionSetKey(card.id, setId);
  const deletedInstructionSelected = selectedVisualInstructionContext.value
    ? deletedInstructionIds.has(selectedVisualInstructionContext.value.instruction.id)
    : false;
  if (deletedSetSelected || deletedInstructionSelected) {
    const nextInstructionSets = visualInstructionSetsOf(card);
    const fallbackSet = nextInstructionSets[Math.max(0, Math.min(deletedIndex, nextInstructionSets.length - 1))]
      ?? nextInstructionSets[0]
      ?? null;
    const fallbackInstruction = fallbackSet?.instructions[0] ?? null;
    if (fallbackSet && fallbackInstruction) {
      selectedVisualInstructionSetKey.value = visualInstructionSetKey(card.id, fallbackSet.id);
      selectedVisualInstructionKey.value = visualInstructionKey(card.id, fallbackInstruction.id);
      void selectVisualInstructionFrame(card, fallbackInstruction);
    } else {
      selectedVisualInstructionSetKey.value = '';
      selectedVisualInstructionKey.value = '';
      clearScreenshotSelection();
    }
  }
  scheduleCodeCardSave(card);
};

const confirmDeleteVisualInstructionSet = async (card: CodeCard, instructionSet: VisualInstructionSet) => {
  const title = visualInstructionSetDisplayTitle(instructionSet);
  const count = instructionSet.instructions.length;
  try {
    await ElMessageBox.confirm(
      `删除指令集「${title}」？将删除其中 ${count} 条指令。`,
      '删除指令集',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    );
  } catch {
    return;
  }
  deleteVisualInstructionSet(card, instructionSet.id);
};

const addInstructionToSet = (card: CodeCard, setId: string) => {
  const program = visualProgramOf(card);
  const firstInstruction = program.operations.find((operation) => operation.setId === setId);
  const instruction = defaultVisualAction({
    setId,
    action: 'find',
    target: firstInstruction?.target ?? 'image',
    label: `查找 ${visualTargetLabel(firstInstruction?.target ?? 'image')}`,
    frame: firstInstruction?.frame ?? '',
    box: firstInstruction?.box ?? null,
    pointer: firstInstruction?.pointer ?? {
      start: null,
      end: null,
      durationMs: 0,
    },
  });
  card.body = serializeVisualProgram(defaultVisualProgram([...program.operations, instruction]));
  scheduleCodeCardSave(card);
  selectedVisualInstructionSetKey.value = visualInstructionSetKey(card.id, setId);
  selectedVisualInstructionKey.value = visualInstructionKey(card.id, instruction.id);
};

const visualPointText = (point: VisualPoint | null) => (
  point ? `${point.x},${point.y}` : '未设置'
);

const visualInstructionFallbackTitle = (action: VisualMacroAction) => (
  `${visualActionLabel(action.action)} ${visualTargetLabel(action.target)}`
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

const sortedCodeCards = computed(() => sortCodeCards(codeCards.value));

const reorderCodeCards = (oldIndex: number, newIndex: number) => {
  const cards = sortedCodeCards.value;
  if (oldIndex < 0 || newIndex < 0 || oldIndex >= cards.length || newIndex >= cards.length) return;
  const nextCards = [...cards];
  const [moved] = nextCards.splice(oldIndex, 1);
  if (!moved) return;
  nextCards.splice(newIndex, 0, moved);
  const orderById = new Map(nextCards.map((card, index) => [card.id, index]));
  codeCards.value = codeCards.value.map((card) => ({
    ...card,
    order_index: orderById.get(card.id) ?? card.order_index,
  }));
  nextCards.forEach((card, index) => {
    if (card.order_index !== index) {
      const nextCard = codeCards.value.find((item) => item.id === card.id);
      if (nextCard) scheduleCodeCardSave(nextCard);
    }
  });
};

useSortableList({
  listRef: codeCardListRef,
  getDeps: () => [
    sortedCodeCards.value.map((card) => card.id).join(','),
    sortedCodeCards.value.length,
  ] as const,
  isEnabled: () => sortedCodeCards.value.length > 1,
  handle: '.code-card-order-handle',
  ghostClass: 'code-card-ghost',
  onReorder: reorderCodeCards,
});

const setVisualInstructionSetListRef = (element: Element | ComponentPublicInstance | null, cardId: string) => {
  if (!(element instanceof HTMLElement)) {
    visualInstructionSetListRefs.delete(cardId);
    return;
  }
  visualInstructionSetListRefs.set(cardId, element);
};

const reorderVisualInstructionSets = (card: CodeCard, oldIndex: number, newIndex: number) => {
  const instructionSets = visualInstructionSetsOf(card);
  if (oldIndex < 0 || newIndex < 0 || oldIndex >= instructionSets.length || newIndex >= instructionSets.length) return;
  const nextSets = [...instructionSets];
  const [moved] = nextSets.splice(oldIndex, 1);
  if (!moved) return;
  nextSets.splice(newIndex, 0, moved);
  const operations = nextSets.flatMap((instructionSet) => instructionSet.instructions);
  card.body = serializeVisualProgram(defaultVisualProgram(operations));
  scheduleCodeCardSave(card);
};

const moveVisualInstructionSetAcrossCards = (
  sourceCard: CodeCard,
  targetCard: CodeCard,
  sourceIndex: number,
  targetIndex: number,
) => {
  const sourceSets = visualInstructionSetsOf(sourceCard);
  const movedSet = sourceSets[sourceIndex];
  if (!movedSet) return;

  if (sourceCard.id === targetCard.id) {
    reorderVisualInstructionSets(sourceCard, sourceIndex, targetIndex);
    return;
  }

  const movedIds = new Set(movedSet.instructions.map((instruction) => instruction.id));
  const selectedInstructionId = selectedVisualInstructionContext.value?.instruction.id || '';
  const nextSourceOperations = visualInstructionsOf(sourceCard).filter((instruction) => !movedIds.has(instruction.id));
  const targetSets = visualInstructionSetsOf(targetCard);
  const nextTargetSets = [...targetSets];
  nextTargetSets.splice(Math.max(0, Math.min(targetIndex, nextTargetSets.length)), 0, movedSet);

  sourceCard.body = serializeVisualProgram(defaultVisualProgram(nextSourceOperations));
  targetCard.body = serializeVisualProgram(defaultVisualProgram(nextTargetSets.flatMap((instructionSet) => instructionSet.instructions)));
  scheduleCodeCardSave(sourceCard);
  scheduleCodeCardSave(targetCard);

  if (selectedVisualInstructionSetKey.value === visualInstructionSetKey(sourceCard.id, movedSet.id)) {
    selectedVisualInstructionSetKey.value = visualInstructionSetKey(targetCard.id, movedSet.id);
  }
  if (selectedInstructionId && movedIds.has(selectedInstructionId)) {
    selectedVisualInstructionKey.value = visualInstructionKey(targetCard.id, selectedInstructionId);
  }
};

const destroyVisualInstructionSetSortables = () => {
  visualInstructionSetSortables.forEach((sortable) => sortable.destroy());
  visualInstructionSetSortables.clear();
};

const initVisualInstructionSetSortables = () => {
  destroyVisualInstructionSetSortables();
  sortedCodeCards.value.forEach((card) => {
    if (!isCodeCardExpanded(card.id)) return;
    const element = visualInstructionSetListRefs.get(card.id);
    if (!element) return;
    const sortable = Sortable.create(element, {
      group: 'fanxiu-visual-instruction-sets',
      handle: '.sortable-order-handle',
      animation: 150,
      ghostClass: 'visual-instruction-set-ghost',
      onEnd: ({ from, to, oldIndex, newIndex }) => {
        if (oldIndex == null || newIndex == null) return;
        const sourceCardId = (from as HTMLElement).dataset.cardId || '';
        const targetCardId = (to as HTMLElement).dataset.cardId || '';
        if (!sourceCardId || !targetCardId) return;
        if (sourceCardId === targetCardId && oldIndex === newIndex) return;
        const sourceCard = codeCards.value.find((item) => item.id === sourceCardId);
        const targetCard = codeCards.value.find((item) => item.id === targetCardId);
        if (!sourceCard || !targetCard) return;
        moveVisualInstructionSetAcrossCards(sourceCard, targetCard, oldIndex, newIndex);
      },
    });
    visualInstructionSetSortables.set(card.id, sortable);
  });
};

const nextCodeCardOrder = () => {
  return sortedCodeCards.value.length ? Math.max(...sortedCodeCards.value.map((card) => card.order_index)) + 1 : 0;
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
        title: card.title || `脚本${index + 1}`,
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
    pruneVisualMacroUiState();
    const restoredInstruction = selectedVisualInstructionContext.value;
    if (restoredInstruction && screenshotPanelOpen.value) {
      await selectVisualInstructionFrame(restoredInstruction.card, restoredInstruction.instruction);
    }
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
      scope: 'action',
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

const addCodeCard = async () => {
  const index = sortedCodeCards.value.length + 1;
  try {
    const card = await createFanxiuPseudoCodeCard({
      scope: 'action',
      title: `脚本${index}`,
      body: serializeVisualProgram(defaultVisualProgram()),
      enabled: true,
      order_index: nextCodeCardOrder(),
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
    await ElMessageBox.confirm(`删除 ${card.title.trim() || '未命名脚本'}？`, '删除脚本', {
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

const codeCardTitle = (card: CodeCard) => {
  return card.title.trim() || '未命名脚本';
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
  if (actionLabel === '执行') {
    pseudoExecutionLog.value = response.log || '执行完成';
    pseudoExecutionResult.value = response.result || '';
    return;
  }
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

const runVisualScript = async (card: CodeCard) => {
  if (!selectedEntryId.value) {
    ElMessage.warning('先选择设备');
    return;
  }
  await flushCodeCardSaves();
  visualScriptRunningCardId.value = card.id;
  pseudoOutputTab.value = 'log';
  pseudoExecutionLog.value = `执行中：${codeCardTitle(card)}`;
  try {
    const response = await runFanxiuVisualScript({
      entry_id: selectedEntryId.value,
      card_id: card.id,
      timeout: 0,
      tick_interval: 1,
      title: targetTitle.value.trim(),
      title_match: titleMatch.value,
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: fixedFrameWidth.value,
      fixed_height: fixedFrameHeight.value,
      frame_width: naturalWidth.value || undefined,
      frame_height: naturalHeight.value || undefined,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    });
    applyPseudoCodeRunResponse('执行', response);
    pseudoOutputTab.value = 'log';
    if (response.status === 'stopped') {
      ElMessage.info('脚本已停止');
    } else {
      ElMessage.success('脚本执行完成');
    }
  } catch (error) {
    const message = getErrorMessage(error);
    pseudoExecutionLog.value = `执行失败：${message}`;
    ElMessage.error(message);
  } finally {
    visualScriptRunningCardId.value = '';
  }
};

const stopVisualScript = async (card: CodeCard) => {
  if (!selectedEntryId.value || visualScriptRunningCardId.value !== card.id) return;
  pseudoOutputTab.value = 'log';
  pseudoExecutionLog.value = `${pseudoExecutionLog.value || ''}\n停止中：${codeCardTitle(card)}`.trim();
  try {
    const response = await stopFanxiuVisualScript({
      entry_id: selectedEntryId.value,
      card_id: card.id,
    });
    if (!response.stopped) {
      visualScriptRunningCardId.value = '';
      ElMessage.info('当前没有运行中的脚本');
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const streamUrl = computed(() => {
  if (windowViewMode.value === 'off') return '';
  if (!selectedEntryId.value || !streamToken.value) return '';
  const params = new URLSearchParams({
    token: streamToken.value,
    title: targetTitle.value.trim(),
    title_match: titleMatch.value,
    fps: String(fps.value),
    quality: String(quality.value),
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    fixed_width: String(fixedFrameWidth.value),
    fixed_height: String(fixedFrameHeight.value),
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
  if (queryEntryId) return queryEntryId;
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
  return 'mumu';
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

const loadServiceStatus = async (silent = false, options: { force?: boolean } = {}) => {
  const entryId = selectedEntryId.value;
  if (!entryId || windowViewMode.value === 'off') {
    serviceStatus.value = null;
    runtimeLoading.value = false;
    serviceStatusLastLoadedAt = 0;
    return;
  }
  if (serviceStatusRequestInFlight) return;
  if (
    silent
    && !options.force
    && serviceStatus.value
    && Date.now() - serviceStatusLastLoadedAt < SERVICE_STATUS_SILENT_POLL_INTERVAL_MS
  ) {
    return;
  }
  serviceStatusRequestInFlight = true;
  runtimeLoading.value = !silent;
  try {
    serviceStatus.value = await getFanxiuGameWindow2ServiceStatus();
    serviceStatusLastLoadedAt = Date.now();
  } catch (error) {
    if (!silent) ElMessage.error(getErrorMessage(error));
  } finally {
    serviceStatusRequestInFlight = false;
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
  serviceStatusLastLoadedAt = 0;
  screenshotImages.value = [];
  screenshotLoaded.value = false;
  clearScreenshotSelection();
  clearMatchResults();
  persistEntrySelection(selectedEntryId.value);
  applyWindowConfig();
  if (windowViewMode.value !== 'off') {
    await refreshStreamToken();
    if (windowViewMode.value === 'control') void loadServiceStatus(true, { force: true });
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
    serviceStatus.value = null;
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
  options: { active?: boolean; draft?: boolean; role?: VisualShapeRole } = {},
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
  const scanRole = options.role === 'scan';
  const activeColor = scanRole ? '#f97316' : '#ff4d4f';
  const inactiveColor = scanRole ? '#fb923c' : '#f97316';
  ctx.strokeStyle = options.draft ? '#e6a23c' : (options.active ? activeColor : inactiveColor);
  ctx.fillStyle = scanRole
    ? (options.active ? 'rgba(249, 115, 22, 0.1)' : 'rgba(251, 146, 60, 0.07)')
    : (options.active ? 'rgba(255, 77, 79, 0.12)' : 'rgba(249, 115, 22, 0.08)');
  if (options.draft) ctx.setLineDash([6, 4]);
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
  if (options.active && !options.draft) {
    const handleSize = 8;
    ctx.fillStyle = '#fff';
    ctx.strokeStyle = activeColor;
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
  point: VisualPointerPoint,
  displayWidth: number,
  displayHeight: number,
) => {
  if (!screenshotNaturalWidth.value || !screenshotNaturalHeight.value) return;
  const p = screenshotDisplayPoint(point, displayWidth, displayHeight);
  const displayRadius = screenshotDisplayRadius(point.r, displayWidth, displayHeight);
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
  start: VisualPointerPoint,
  end: VisualPointerPoint,
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
  drawScreenshotClickPoint(ctx, start, displayWidth, displayHeight);
  drawScreenshotClickPoint(ctx, end, displayWidth, displayHeight);
};

const drawSelectedVisualInstructionInput = (
  ctx: CanvasRenderingContext2D,
  displayWidth: number,
  displayHeight: number,
) => {
  const instruction = selectedVisualInstruction.value;
  if (!instruction || instruction.frame !== selectedScreenshotFilename.value) return;
  if (instruction.target === 'image' && instruction.box) {
    drawScreenshotBox(
      ctx,
      { id: instruction.id, name: '图片区域', ...instruction.box },
      displayWidth,
      displayHeight,
      { active: activeVisualShapeRole.value === 'target', role: 'target' },
    );
  }
  if (visualInstructionUsesTargetConfig(instruction) && instruction.scan === 'range') {
    drawScreenshotBox(
      ctx,
      { id: `${instruction.id}:scan`, name: '搜索区域', ...visualScanBoxOrDefault(instruction) },
      displayWidth,
      displayHeight,
      { active: activeVisualShapeRole.value === 'scan', role: 'scan' },
    );
  }
  if (screenshotDraftBox.value) {
    drawScreenshotBox(ctx, normalizeScreenshotBox(screenshotDraftBox.value), displayWidth, displayHeight, { draft: true });
  }
  if (!instruction.pointer.start) return;
  if (instruction.action === 'drag' && instruction.pointer.end) {
    drawScreenshotDragPoints(ctx, instruction.pointer.start, instruction.pointer.end, displayWidth, displayHeight);
    return;
  }
  if (visualActionUsesPointer(instruction.action)) {
    drawScreenshotClickPoint(ctx, instruction.pointer.start, displayWidth, displayHeight);
  }
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

const captureCurrentLiveFrameDataUrl = () => {
  const image = streamImageRef.value;
  if (!image || !image.naturalWidth || !image.naturalHeight) return '';
  const targetWidth = fixedFrameWidth.value || image.naturalWidth;
  const targetHeight = fixedFrameHeight.value || image.naturalHeight;
  const canvas = document.createElement('canvas');
  canvas.width = targetWidth;
  canvas.height = targetHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', Math.max(0.1, Math.min(1, Number(quality.value || 82) / 100)));
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
    serviceStatus.value = null;
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

const connectWindow = async (options: { allowStartService?: boolean } = {}) => {
  if (!selectedEntryId.value) return;
  if (windowViewMode.value === 'off') {
    streamEnabled.value = false;
    controlEnabled.value = false;
    serviceStatus.value = null;
    streamToken.value = '';
    streamTokenExpiresAt.value = 0;
    if (streamImageRef.value) streamImageRef.value.src = '';
    return;
  }
  connectionLoading.value = true;
  try {
    if (!serviceStatus.value && options.allowStartService) {
      await loadServiceStatus(true, { force: true });
    }
    if (!serviceActive.value && options.allowStartService) {
      const result = await startFanxiuGameWindow2Service();
      serviceStatus.value = result.service;
    }
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
      title_match: titleMatch.value,
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: fixedFrameWidth.value,
      fixed_height: fixedFrameHeight.value,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
      current_frame_data_url: captureCurrentLiveFrameDataUrl(),
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
    title_match: titleMatch.value,
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    fixed_width: fixedFrameWidth.value,
    fixed_height: fixedFrameHeight.value,
    quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
  });
  if (screenshotPanelOpen.value) {
    void loadScreenshotList(result.filename);
  }
  return result;
};

const scaleVisualPointerPointToFrame = (
  point: VisualPointerPoint | null,
  scaleX: number,
  scaleY: number,
): VisualPointerPoint | null => {
  if (!point) return null;
  return {
    ...point,
    x: Math.round(point.x * scaleX),
    y: Math.round(point.y * scaleY),
  };
};

const scaleVisualBoxToFrame = (
  box: VisualBox | null,
  scaleX: number,
  scaleY: number,
): VisualBox | null => {
  if (!box) return null;
  return {
    x: Math.round(box.x * scaleX),
    y: Math.round(box.y * scaleY),
    w: Math.max(1, Math.round(box.w * scaleX)),
    h: Math.max(1, Math.round(box.h * scaleY)),
  };
};

const rebindSelectedVisualInstructionFrame = async () => {
  const context = selectedVisualEditInstructionContext.value;
  if (!context || !selectedEntryId.value) return;
  const sourceFilename = context.instruction.frame || selectedScreenshotFilename.value;
  const { width: sourceWidth, height: sourceHeight } = selectedScreenshotSourceSize(sourceFilename);
  saveFrameLoading.value = true;
  try {
    const result = await captureVisualMacroFrame();
    const scaleX = sourceWidth > 0 ? result.width / sourceWidth : 1;
    const scaleY = sourceHeight > 0 ? result.height / sourceHeight : 1;
    updateVisualInstruction(context.card, context.instruction.id, {
      frame: result.filename,
      pointer: {
        ...context.instruction.pointer,
        start: scaleVisualPointerPointToFrame(context.instruction.pointer.start, scaleX, scaleY),
        end: scaleVisualPointerPointToFrame(context.instruction.pointer.end, scaleX, scaleY),
      },
      box: scaleVisualBoxToFrame(context.instruction.box, scaleX, scaleY),
      scanBox: scaleVisualBoxToFrame(context.instruction.scanBox, scaleX, scaleY),
    });
    await loadScreenshotList(result.filename);
    ElMessage.success(`已重绑 ${result.filename}`);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    saveFrameLoading.value = false;
  }
};

const buildDefaultImageBox = (point: VisualPoint, size = 50): VisualBox => {
  const width = Math.max(4, Math.round(size));
  const height = Math.max(4, Math.round(size));
  const maxWidth = Math.max(1, screenshotNaturalWidth.value || naturalWidth.value || width);
  const maxHeight = Math.max(1, screenshotNaturalHeight.value || naturalHeight.value || height);
  const x = Math.round(clamp(point.x - width / 2, 0, Math.max(0, maxWidth - width)));
  const y = Math.round(clamp(point.y - height / 2, 0, Math.max(0, maxHeight - height)));
  return {
    x,
    y,
    w: Math.min(width, maxWidth),
    h: Math.min(height, maxHeight),
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
  const setId = createVisualId();
  const nextAction = action === 'click' ? 'waitClick' : action;
  const nextTarget: VisualTargetKind = action === 'drag' ? 'coordinate' : 'image';
  const operationAction = defaultVisualAction({
    setId,
    action: nextAction,
    target: nextTarget,
    label: `${visualActionLabel(nextAction)} ${visualTargetLabel(nextTarget)}`,
    frame,
    pointer: {
      start: point ? { ...point, r: visualMacroDefaultPointRadius.value } : null,
      end: endPoint ? { ...endPoint, r: visualMacroDefaultPointRadius.value } : null,
      durationMs: Math.round(Math.max(0, durationMs)),
    },
    box: point ? buildDefaultImageBox(point) : null,
    timeout: action === 'drag' ? Math.round(clamp(durationMs / 1000, 0, 120)) : 8,
  });
  card.body = serializeVisualProgram(defaultVisualProgram([...program.operations, operationAction]));
  await saveCodeCardNow(card);
  await selectVisualInstructionFrame(card, operationAction);
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
    ElMessage.success('已追加指令集');
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

const selectedVisualInstructionMatchBox = (instruction = selectedVisualInstruction.value): OverlayBox | null => {
  if (!instruction || instruction.target !== 'image') return null;
  const box = instruction.box ?? visualBoxOrDefault(instruction);
  return {
    id: instruction.id,
    name: '图片区域',
    ...box,
  };
};

const formatFrameSize = (width: number, height: number) => `${Math.round(width)}x${Math.round(height)}`;

const selectedScreenshotSourceSize = (filename: string) => {
  const item = screenshotImages.value.find((screenshot) => screenshot.filename === filename);
  const width = item?.width || (filename === selectedScreenshotFilename.value ? screenshotNaturalWidth.value : 0);
  const height = item?.height || (filename === selectedScreenshotFilename.value ? screenshotNaturalHeight.value : 0);
  return { width, height };
};

const describeMatchGeometryIssue = (filename: string, box: OverlayBox) => {
  const { width: sourceWidth, height: sourceHeight } = selectedScreenshotSourceSize(filename);
  if (sourceWidth > 0 && sourceHeight > 0) {
    if (box.x < 0 || box.y < 0 || box.x + box.w > sourceWidth || box.y + box.h > sourceHeight) {
      return `标注越界 ${formatFrameSize(sourceWidth, sourceHeight)}`;
    }
    if (naturalWidth.value > 0 && naturalHeight.value > 0) {
      const sourceRatio = sourceWidth / sourceHeight;
      const currentRatio = naturalWidth.value / naturalHeight.value;
      if (Math.abs(sourceRatio - currentRatio) > 0.05) {
        return `画面比例不一致 ${formatFrameSize(sourceWidth, sourceHeight)}→${formatFrameSize(naturalWidth.value, naturalHeight.value)}`;
      }
    }
  }
  return '';
};

const matchFrameAspectMismatchText = (response: FanxiuGameWindow2MatchResponse) => {
  if (!response.source_width || !response.source_height || !response.width || !response.height) return false;
  const sourceRatio = response.source_width / response.source_height;
  const currentRatio = response.width / response.height;
  if (Math.abs(sourceRatio - currentRatio) <= 0.05) return '';
  return `画面比例不一致 ${formatFrameSize(response.source_width, response.source_height)}→${formatFrameSize(response.width, response.height)}`;
};

const stopVisualSimilarityProbe = () => {
  visualSimilarityProbeSeq += 1;
  if (visualSimilarityProbeTimer) {
    window.clearInterval(visualSimilarityProbeTimer);
    visualSimilarityProbeTimer = null;
  }
  visualSimilarityProbeActive.value = false;
  visualSimilarityProbeLoading.value = false;
};

const runVisualSimilarityProbeOnce = async () => {
  const instruction = selectedVisualInstruction.value;
  const instructionKey = selectedVisualInstructionKey.value;
  const screenshotFilename = selectedScreenshotFilename.value;
  const box = selectedVisualInstructionMatchBox(instruction);
  const templateFilename = instruction?.frame || screenshotFilename;
  if (!selectedEntryId.value || !instruction || !templateFilename || !box) {
    visualSimilarityProbeText.value = '';
    return;
  }
  const geometryIssue = describeMatchGeometryIssue(templateFilename, box);
  if (geometryIssue) {
    visualSimilarityProbeText.value = geometryIssue;
    stopVisualSimilarityProbe();
    return;
  }
  if (visualSimilarityProbeLoading.value) return;
  const requestSeq = ++visualSimilarityProbeSeq;
  visualSimilarityProbeLoading.value = true;
  try {
    const response = await matchFanxiuGameWindow2Screenshot({
      entry_id: selectedEntryId.value,
      filename: templateFilename,
      box: toMatchBoxPayload(box),
      pixel_tolerance: selectedVisualInstruction.value?.pixelTolerance ?? visualMacroDefaultPixelTolerance.value,
      title: targetTitle.value.trim(),
      title_match: titleMatch.value,
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: fixedFrameWidth.value,
      fixed_height: fixedFrameHeight.value,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
      current_frame_data_url: captureCurrentLiveFrameDataUrl(),
    });
    if (
      requestSeq !== visualSimilarityProbeSeq
      || instructionKey !== selectedVisualInstructionKey.value
      || templateFilename !== (selectedVisualInstruction.value?.frame || selectedScreenshotFilename.value)
    ) {
      return;
    }
    const matchGeometryIssue = matchFrameAspectMismatchText(response);
    if (matchGeometryIssue) {
      visualSimilarityProbeText.value = matchGeometryIssue;
      stopVisualSimilarityProbe();
      return;
    }
    const similarity = instruction.scan === 'fixed'
      ? response.fixed_similarity ?? response.similarity
      : response.template_similarity ?? response.template_crop_similarity ?? response.similarity;
    visualSimilarityProbeText.value = `${Math.round(Number(similarity) || 0)}%`;
  } catch (error) {
    visualSimilarityProbeText.value = getErrorMessage(error);
    stopVisualSimilarityProbe();
  } finally {
    visualSimilarityProbeLoading.value = false;
  }
};

const toggleVisualSimilarityProbe = () => {
  if (visualSimilarityProbeActive.value) {
    stopVisualSimilarityProbe();
    return;
  }
  visualSimilarityProbeActive.value = true;
  void runVisualSimilarityProbeOnce();
  visualSimilarityProbeTimer = window.setInterval(() => {
    void runVisualSimilarityProbeOnce();
  }, 1200);
};

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
  const geometryIssue = describeMatchGeometryIssue(selectedScreenshotFilename.value, box);
  if (geometryIssue) {
    ElMessage.warning(geometryIssue);
    return;
  }
  matchingBoxId.value = box.id;
  try {
    await flushScreenshotAutosave();
    const response = await matchFanxiuGameWindow2Screenshot({
      entry_id: selectedEntryId.value,
      filename: selectedScreenshotFilename.value,
      box: toMatchBoxPayload(box),
      pixel_tolerance: selectedVisualInstruction.value?.pixelTolerance ?? visualMacroDefaultPixelTolerance.value,
      title: targetTitle.value.trim(),
      title_match: titleMatch.value,
      mode: 'screen',
      area: captureArea.value,
      crop: cropText.value.trim(),
      trim_border: trimBorderText.value.trim(),
      rotate: rotateDegrees.value,
      fixed_width: fixedFrameWidth.value,
      fixed_height: fixedFrameHeight.value,
      quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    });
    const matchGeometryIssue = matchFrameAspectMismatchText(response);
    if (matchGeometryIssue) {
      ElMessage.warning(matchGeometryIssue);
    }
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
    const instructionFrame = selectedVisualInstruction.value?.frame || '';
    const targetFilename = preferFilename
      || (selectedVisualInstructionKey.value && screenshotImages.value.some((item) => item.filename === instructionFrame) ? instructionFrame : '')
      || (selectedVisualInstructionKey.value && screenshotImages.value.some((item) => item.filename === selectedScreenshotFilename.value) ? selectedScreenshotFilename.value : '')
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

const visualBoxToOverlayBox = (instruction: VisualMacroAction, role: VisualShapeRole): OverlayBox | null => {
  if (role === 'scan') {
    if (!visualInstructionUsesTargetConfig(instruction) || instruction.scan !== 'range') return null;
    return {
      id: `${instruction.id}:scan`,
      name: '搜索区域',
      ...visualScanBoxOrDefault(instruction),
    };
  }
  if (!instruction.box || instruction.target !== 'image') return null;
  return {
    id: instruction.id,
    name: '图片区域',
    ...instruction.box,
  };
};

const selectedVisualInstructionEditableBox = () => {
  const instruction = selectedVisualInstruction.value;
  if (!instruction || instruction.frame !== selectedScreenshotFilename.value) return null;
  return visualBoxToOverlayBox(instruction, activeVisualShapeRole.value);
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
  const instructionBox = selectedVisualInstructionEditableBox();
  if (instructionBox && x >= instructionBox.x && x <= instructionBox.x + instructionBox.w && y >= instructionBox.y && y <= instructionBox.y + instructionBox.h) {
    return 0;
  }
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

  const instructionBox = selectedVisualInstructionEditableBox();
  const boxesToCheck = instructionBox ? [instructionBox] : screenshotBoxes.value;
  for (let index = boxesToCheck.length - 1; index >= 0; index -= 1) {
    const box = boxesToCheck[index];
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
  title_match: titleMatch.value,
  mode: 'screen' as const,
  area: captureArea.value,
  crop: cropText.value.trim(),
  trim_border: trimBorderText.value.trim(),
  rotate: rotateDegrees.value,
  fixed_width: fixedFrameWidth.value,
  fixed_height: fixedFrameHeight.value,
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

const closeVisualInstructionSetContextMenu = () => {
  if (!visualInstructionSetContextMenu.value.visible) return;
  visualInstructionSetContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    cardId: '',
    setId: '',
  };
};

const closeScreenshotContextMenus = () => {
  closeScreenshotBoxContextMenu();
  closeScreenshotBoxListContextMenu();
  closeVisualInstructionSetContextMenu();
};

const openVisualInstructionSetContextMenu = (event: MouseEvent, card: CodeCard, instructionSet: VisualInstructionSet) => {
  event.preventDefault();
  closeScreenshotBoxContextMenu();
  closeScreenshotBoxListContextMenu();
  visualInstructionSetContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    cardId: card.id,
    setId: instructionSet.id,
  };
  void selectVisualInstructionSetFrame(card, instructionSet);
};

const confirmDeleteVisualInstructionSetFromContext = async () => {
  const { cardId, setId } = visualInstructionSetContextMenu.value;
  closeVisualInstructionSetContextMenu();
  const card = codeCards.value.find((item) => item.id === cardId);
  const instructionSet = card ? visualInstructionSetsOf(card).find((item) => item.id === setId) : null;
  if (!card || !instructionSet) return;
  await confirmDeleteVisualInstructionSet(card, instructionSet);
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
  const instruction = selectedVisualInstruction.value;
  if (!instruction || instruction.frame !== selectedScreenshotFilename.value || !visualInstructionUsesShape(instruction)) return;
  if (activeVisualShapeRole.value === 'target' && instruction.target !== 'image') return;
  if (activeVisualShapeRole.value === 'scan' && instruction.scan !== 'range') return;
  const point = getScreenshotFramePoint(event);
  if (!point) return;
  event.preventDefault();
  event.stopPropagation();

  const resizeHit = findScreenshotResizeHandleAt(point.x, point.y);
  const box = selectedVisualInstructionEditableBox();
  if (resizeHit && box) {
    screenshotResizeState.value = {
      pointerId: event.pointerId,
      boxId: box.id,
      handle: resizeHit.handle,
      original: box,
    };
    screenshotOverlayCanvasRef.value?.setPointerCapture(event.pointerId);
    updateScreenshotCanvasCursor(event);
    return;
  }

  if (box && point.x >= box.x && point.x <= box.x + box.w && point.y >= box.y && point.y <= box.y + box.h) {
    return;
  }

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
    const original = resizeState.original;
    let nextBox: VisualBox;
    if (resizeState.handle === 'top-left') {
      const right = original.x + original.w;
      const bottom = original.y + original.h;
      const nextX = clamp(point.x, 0, right - 4);
      const nextY = clamp(point.y, 0, bottom - 4);
      nextBox = {
        x: Math.round(nextX),
        y: Math.round(nextY),
        w: Math.round(right - nextX),
        h: Math.round(bottom - nextY),
      };
    } else {
      const nextRight = clamp(point.x, original.x + 4, screenshotNaturalWidth.value || Number.MAX_SAFE_INTEGER);
      const nextBottom = clamp(point.y, original.y + 4, screenshotNaturalHeight.value || Number.MAX_SAFE_INTEGER);
      nextBox = {
        x: Math.round(original.x),
        y: Math.round(original.y),
        w: Math.round(nextRight - original.x),
        h: Math.round(nextBottom - original.y),
      };
    }
    if (selectedVisualInstruction.value && resizeState.boxId.startsWith(selectedVisualInstruction.value.id)) {
      if (activeVisualShapeRole.value === 'scan') {
        updateSelectedVisualInstructionScanBox(nextBox);
      } else {
      updateSelectedVisualInstructionBox(nextBox, 'manual');
      }
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
    x: normalized.x,
    y: normalized.y,
    w: normalized.w,
    h: normalized.h,
  };
  if (activeVisualShapeRole.value === 'scan') {
    updateSelectedVisualInstructionScanBox(nextBox);
  } else {
    updateSelectedVisualInstructionBox(nextBox, 'manual');
  }
  drawScreenshotOverlay();
};

const finishScreenshotResize = (event: PointerEvent) => {
  const resizeState = screenshotResizeState.value;
  if (!resizeState || resizeState.pointerId !== event.pointerId) return false;
  screenshotOverlayCanvasRef.value?.releasePointerCapture(event.pointerId);
  screenshotResizeState.value = null;
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
    if (windowViewMode.value !== 'control' && !serviceStatus.value) return;
    void loadServiceStatus(true);
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
watch(
  [screenshotPanelOpen, expandedCodeCardIds, selectedVisualInstructionSetKey, selectedVisualInstructionKey, pseudoOutputTab],
  persistVisualMacroUiState,
  { deep: true },
);
watch(selectedVisualInstructionKey, () => {
  stopVisualSimilarityProbe();
  visualSimilarityProbeSeq += 1;
  visualSimilarityProbeText.value = '';
  drawScreenshotOverlay();
});
watch(
  () => [
    expandedCodeCardIds.value.join(','),
    sortedCodeCards.value.map((card) => `${card.id}:${visualInstructionSetsOf(card).map((instructionSet) => instructionSet.id).join(',')}`).join('|'),
  ],
  async () => {
    await nextTick();
    initVisualInstructionSetSortables();
  },
  { immediate: true },
);
watch(visualMacroDefaultThreshold, (value) => {
  setVisualMacroDefaultThreshold(value);
});
watch(visualMacroDefaultPointRadius, (value) => {
  setVisualMacroDefaultPointRadius(value);
});
watch(visualMacroDefaultPixelTolerance, (value) => {
  setVisualMacroDefaultPixelTolerance(value);
});

onMounted(async () => {
  loadVisualMacroDefaults();
  applyVisualMacroUiState();
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
      await refreshStreamToken();
      if (windowViewMode.value === 'control') void loadServiceStatus(true, { force: true });
    }
    if (screenshotPanelOpen.value) void loadScreenshotList();
  }
  startPolling();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  stopPolling();
  stopVisualSimilarityProbe();
  destroyVisualInstructionSetSortables();
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
  flex: none;
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
.code-card-run,
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

.code-card-ghost {
  opacity: 0.6;
  background: #dbeafe;
}

.code-card-head {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr) 24px 24px 68px 24px;
  align-items: center;
  gap: 6px;
}

.code-card-summary-row {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr) 24px 24px;
  align-items: center;
  gap: 6px;
}

.code-card-title-input {
  height: 24px;
}

.code-card-title-input :deep(.el-input__wrapper) {
  height: 24px;
  min-height: 24px;
  padding: 0;
  background: transparent;
  border: none;
  box-shadow: none;
}

.code-card-title-input :deep(.el-input__inner) {
  height: 24px;
  color: #1f2937;
  font-size: 13px;
  line-height: 24px;
}

.code-card-title-input :deep(.el-input__wrapper.is-focus) {
  padding: 0 6px;
  background: #ffffff;
  box-shadow: 0 0 0 1px #93c5fd inset;
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

.code-card :deep(.sortable-order-handle) {
  background: #e0f2fe;
  color: #0369a1;
  border: 1px solid #bae6fd;
}

.code-card :deep(.sortable-order-handle:hover:not(:disabled)) {
  background: #bae6fd;
  color: #075985;
}

.code-card-collapse {
  color: #64748b;
  font-size: 14px;
}

.code-card-collapse:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.code-card-run {
  color: #2563eb;
}

.code-card-run:hover {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.code-card-run.is-running {
  color: #dc2626;
}

.code-card-run.is-running:hover {
  background: #fef2f2;
  border-color: #fecaca;
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
  margin-top: 6px;
  padding-left: 28px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.visual-operation {
  padding: 2px 0;
  cursor: pointer;
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

.visual-instruction-set {
  padding: 2px 0;
  cursor: pointer;
}

.visual-instruction-set :deep(.sortable-order-handle) {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fde68a;
}

.visual-instruction-set :deep(.sortable-order-handle:hover:not(:disabled)) {
  background: #fde68a;
  color: #78350f;
}

.visual-instruction-set + .visual-instruction-set {
  margin-top: 2px;
}

.visual-action-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.visual-operation-index {
  min-width: 30px;
  padding: 0 4px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #475569;
  background: #eef2f7;
  border-radius: 4px;
  font-size: 12px;
}

.visual-summary-token {
  min-width: 56px;
  padding: 0 8px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  color: #334155;
  background: #ffffff;
  border: 1px solid #d8e1ee;
  border-radius: 4px;
  font-size: 13px;
}

.visual-summary-text {
  min-width: 0;
  padding: 0 7px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  color: #334155;
  background: #ffffff;
  border: 1px solid #d8e1ee;
  border-radius: 4px;
  font-size: 13px;
}

.visual-summary-text.is-unique-title {
  color: #1d4ed8;
  border-color: #93c5fd;
  background: #eff6ff;
}

.visual-action-kind-select {
  width: 96px;
}

.visual-instruction-set-ghost {
  opacity: 0.6;
  background: #dbeafe;
  border-radius: 4px;
}

.visual-target-kind-select {
  width: 86px;
}

.visual-call-config-row {
  flex-wrap: wrap;
}

.visual-call-config-row .visual-target-kind-select {
  width: 92px;
}

.visual-reference-select {
  width: 170px;
}

.visual-reference-note {
  flex-basis: 100%;
  padding-left: 0;
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.visual-reference-token {
  color: #1d4ed8;
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
  grid-template-columns: max-content 420px;
  gap: 14px;
  align-items: start;
}

.screenshot-preview-column {
  min-width: 0;
}

.screenshot-detail-head {
  margin-bottom: 6px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.screenshot-detail-title {
  color: #111827;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.screenshot-geometry-warning {
  margin-bottom: 6px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #b45309;
  font-size: 12px;
  line-height: 1.4;
}

.screenshot-rebind-frame {
  height: 22px;
  padding: 0 8px;
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 4px;
  cursor: pointer;
}

.screenshot-rebind-frame:disabled {
  color: #94a3b8;
  cursor: not-allowed;
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

.screenshot-config-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.screenshot-panel-title {
  margin-bottom: 6px;
  color: #1f2937;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.screenshot-panel-title-row {
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.screenshot-panel-title-row .screenshot-panel-title {
  margin-bottom: 0;
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

.pointer-config-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pointer-config-row {
  display: grid;
  grid-template-columns: 34px repeat(3, minmax(0, 72px));
  align-items: center;
  gap: 6px;
}

.visual-box-config {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.visual-image-box-mode-select {
  width: 92px;
}

.visual-box-config .screenshot-box-metric :deep(.el-input-number) {
  width: 64px;
}

.screenshot-config-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.screenshot-config-line {
  min-width: 0;
  display: grid;
  grid-template-columns: max-content max-content;
  align-items: center;
  gap: 8px;
  color: #64748b;
  font-size: 12px;
}

.screenshot-config-line > span {
  white-space: nowrap;
}

.visual-primary-label {
  color: #dc2626;
}

.screenshot-wide-number-input {
  width: 78px;
}

.visual-match-config {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
}

.visual-threshold-line {
  display: grid;
}

.visual-probe-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.visual-threshold-probe {
  height: 24px;
  padding: 0 7px;
  color: #2563eb;
  background: #ffffff;
  border: 1px solid #d8e1ee;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  white-space: nowrap;
}

.visual-threshold-probe:hover:not(:disabled),
.visual-threshold-probe.is-active {
  background: #eff6ff;
  border-color: #93c5fd;
}

.visual-threshold-probe:disabled {
  cursor: default;
  opacity: 0.65;
}

.visual-threshold-probe-result {
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.screenshot-duration-input {
  width: 112px;
}

.instruction-sequence {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.instruction-sequence-row {
  min-width: 0;
  height: 26px;
  padding: 0 6px;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) 20px;
  align-items: center;
  gap: 6px;
  color: #475569;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.instruction-sequence-row > span:first-child {
  min-width: 22px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #6d28d9;
  background: #ede9fe;
  border: 1px solid #ddd6fe;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.instruction-sequence-title {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 22px;
  padding: 0 6px;
  color: #334155;
  background: #ffffff;
  border: 1px solid #d8e1ee;
  border-radius: 4px;
  font-size: 12px;
}

.instruction-sequence-title:placeholder-shown {
  color: #94a3b8;
}

.instruction-sequence-title:focus {
  outline: none;
  border-color: #93c5fd;
  box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.22);
}

.instruction-sequence-title.is-unique-title {
  color: #1d4ed8;
  border-color: #93c5fd;
  background: #eff6ff;
}

.instruction-sequence-title:disabled {
  color: #64748b;
  background: #f8fafc;
}

.instruction-reference-title {
  min-width: 0;
  height: 22px;
  padding: 0 6px;
  display: inline-flex;
  align-items: center;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #93c5fd;
  border-radius: 4px;
  font-size: 12px;
}

.instruction-set-title-input {
  width: 260px;
  max-width: 100%;
}

.instruction-sequence-add,
.instruction-sequence-delete {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.instruction-sequence-add {
  color: #2563eb;
}

.instruction-sequence-delete {
  justify-self: end;
  flex: 0 0 20px;
  color: #dc2626;
}

.instruction-sequence-add:hover {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.instruction-sequence-delete:hover {
  background: #fef2f2;
  border-color: #fecaca;
}

.instruction-sequence-row:hover,
.instruction-sequence-row.is-active {
  color: #1d4ed8;
  background: #eff6ff;
  border-color: #bfdbfe;
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

.screenshot-box-metric > span {
  flex: none;
  white-space: nowrap;
}

.screenshot-box-metric :deep(.el-input-number) {
  width: 100%;
}

.screenshot-advanced-metrics .screenshot-box-metric :deep(.el-input-number) {
  width: 96px;
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

.screenshot-box-menu button.is-danger {
  color: #dc2626;
}

.screenshot-box-menu button.is-danger:hover:not(:disabled) {
  color: #b91c1c;
  background: #fef2f2;
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
