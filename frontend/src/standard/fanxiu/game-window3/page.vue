<template>
  <div class="game-window-page">
    <section class="stage-pane">
      <div class="topbar">
        <div class="topbar-content">
          <h2>游戏窗口3</h2>
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
                <div v-if="selectedWindowKey === 'mumu'" class="control-field">
                  <span class="control-label">通道</span>
                  <el-select v-model="mumuChannel" class="channel-select" size="small" @change="restartStream">
                    <el-option label="桌面" value="desktop" />
                    <el-option label="ADB" value="adb" />
                  </el-select>
                </div>
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
                  <span class="actual-fps-text">实际 {{ actualFpsText }}</span>
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
                <span class="connection-status" :class="{ 'is-ready': connectionReady, 'is-loading': connectionButtonLoading }">
                  {{ connectionButtonText }}
                </span>
                <button type="button" class="capture-runtime-link" @click="router.push('/cluster/runtime')">
                  {{ captureRuntimeText }}
                </button>
                <el-button
                  size="small"
                  :type="gameMacroRecording ? 'primary' : 'default'"
                  :icon="gameMacroRecording ? VideoPause : VideoPlay"
                  :disabled="!selectedEntryId"
                  @click="toggleGameMacroRecording"
                >
                  录制宏
                </el-button>
                <el-button
                  size="small"
                  :icon="Setting"
                  title="录制宏配置"
                  aria-label="录制宏配置"
                  @click="gameMacroConfigVisible = true"
                />
              </div>
            </div>
            <div class="control-row behavior-row">
              <div class="control-group behavior-controls">
                <span class="control-label">步进器</span>
                <el-select
                  v-model="selectedStepperFunctionId"
                  class="behavior-function-select"
                  size="small"
                  placeholder="函数"
                  :disabled="stepperRunning || stepperStepping"
                >
                  <el-option
                    v-for="fn in stepperTaskFunctionDefinitions"
                    :key="fn.id"
                    :label="fn.label"
                    :value="fn.id"
                  />
                </el-select>
                <el-select
                  v-if="selectedStepperPresetDefinitions.length"
                  v-model="selectedStepperTaskId"
                  class="behavior-preset-select"
                  size="small"
                  placeholder="参数预设"
                  :disabled="stepperRunning || stepperStepping"
                >
                  <el-option
                    v-for="preset in selectedStepperPresetDefinitions"
                    :key="preset.id"
                    :label="preset.label"
                    :value="preset.id"
                  />
                </el-select>
                <span class="behavior-row-break" aria-hidden="true"></span>
                <el-button
                  size="small"
                  plain
                  :loading="stepperStepping"
                  :disabled="!selectedEntryId || stepperRunning || stepperStepping"
                  @click="runStepperSingleTick"
                >
                  单步
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :loading="stepperRunning"
                  :disabled="!selectedEntryId || stepperRunning || stepperStepping"
                  @click="runStepperToTarget"
                >
                  运行
                </el-button>
                <el-button
                  size="small"
                  :disabled="!stepperRunning"
                  @click="stopStepperRun"
                >
                  停止
                </el-button>
                <button type="button" class="shape-inline-help" title="查看步进器说明" aria-label="查看步进器说明" @click="showStepperHelp">
                  ?
                </button>
                <el-button size="small" plain @click="stepperLogDialogVisible = true">
                  日志
                </el-button>
                <span v-if="stepperRunStatus" class="behavior-run-status">{{ stepperRunStatus }}</span>
                <span
                  v-if="stepperLastDailyFindSummaryText"
                  class="daily-find-summary"
                  :title="stepperLastDailyFindDetailText"
                >
                  {{ stepperLastDailyFindSummaryText }}
                </span>
                <span v-if="gameMacroStatusText" class="behavior-run-status">{{ gameMacroStatusText }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="workspace">
        <div class="viewer-pane">
          <div class="live-workspace">
            <div
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
                    v-if="streamEnabled && liveImageUrl"
                    ref="streamImageRef"
                    class="stream-image"
                    :src="liveImageUrl"
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

            <aside class="annotation-panel" :style="annotationPanelStyle">
              <div class="annotation-panel-head">
                <span>文件树</span>
                <div class="annotation-panel-actions">
                  <el-button size="small" :icon="Plus" title="新建目录" aria-label="新建目录" @click="addAssetFolder" />
                  <el-button
                    size="small"
                    :icon="Picture"
                    title="保存帧"
                    aria-label="保存帧"
                    :loading="saveFrameLoading"
                    :disabled="!selectedEntryId"
                    @click="saveCurrentFrame"
                  />
                  <el-button
                    size="small"
                    :type="burstCaptureRunning ? 'primary' : 'default'"
                    :icon="burstCaptureRunning ? VideoPause : VideoPlay"
                    title="连拍"
                    aria-label="连拍"
                    :disabled="!selectedEntryId"
                    @click="toggleBurstCapture"
                  />
                  <el-button size="small" plain @click="openBurstDialog">
                    连拍
                  </el-button>
                  <el-button size="small" :icon="Delete" title="删除选中节点" aria-label="删除选中节点" :disabled="!selectedAssetNode" @click="deleteSelectedAsset" />
                </div>
              </div>

              <div class="asset-tree-scroll">
                <el-tree
                  class="asset-tree"
                  :data="assetTree"
                  :props="assetTreeProps"
                  node-key="id"
                  :default-expanded-keys="expandedAssetNodeIds"
                  highlight-current
                  draggable
                  :expand-on-click-node="false"
                  :current-node-key="selectedAssetId"
                  :allow-drop="allowAssetDrop"
                  @node-click="selectAssetNode"
                  @node-expand="node => setAssetNodeExpanded(node.id, true)"
                  @node-collapse="node => setAssetNodeExpanded(node.id, false)"
                  @node-contextmenu="openAssetContextMenu"
                >
                  <template #default="{ data }">
                    <span class="asset-tree-node" :class="{ 'is-image': data.type === 'image' }" @dblclick.stop="renameAssetNode(data)">
                      <el-icon v-if="data.type === 'folder'"><Folder /></el-icon>
                      <span v-else class="asset-node-id">{{ assetImageIdMark(data) }}</span>
                      <span>{{ data.title }}</span>
                    </span>
                  </template>
                </el-tree>
              </div>

              <div
                v-if="assetContextMenu.visible"
                class="asset-context-menu"
                :style="{ left: `${assetContextMenu.x}px`, top: `${assetContextMenu.y}px` }"
                @click.stop
                @contextmenu.prevent
              >
                <button
                  v-if="assetContextMenuNode?.type === 'image' && assetContextMenuNode.filename"
                  type="button"
                  @click="resetAssetFrameFromContextMenu"
                >
                  重置帧
                </button>
                <button type="button" class="is-danger" @click="deleteAssetFromContextMenu">
                  删除
                </button>
              </div>

            </aside>
          </div>

          <section class="annotation-workbench">
            <div class="annotation-workbench-head">
              <div class="annotation-title-tools">
                <span>{{ selectedImageTitleText }}</span>
                <el-checkbox v-if="selectedImageNode" v-model="globalOcclusionMaskEnabled" size="small">
                  遮挡标记
                </el-checkbox>
              </div>
              <div class="annotation-panel-actions">
                <el-button size="small" :icon="Plus" :disabled="!selectedImageNode" title="新建 shape" aria-label="新建 shape" @click="addAnnotationShape" />
                <el-button size="small" :icon="Delete" :disabled="!selectedShapeCopyCount" title="删除 shape" aria-label="删除 shape" @click="deleteSelectedShape" />
              </div>
            </div>

            <div v-if="selectedImageNode" class="annotation-editor">
              <div class="annotation-main-row">
                <div
                  ref="screenshotViewportRef"
                  class="screenshot-preview annotation-preview"
                  :class="screenshotViewportClasses"
                  :style="annotationCanvasStyle"
                  @wheel="handleScreenshotWheel"
                  @mousedown.capture="handleScreenshotViewportMouseDown"
                >
                  <div class="screenshot-workspace" :style="annotationCanvasStyle">
                    <div
                      ref="annotationCanvasRef"
                      class="screenshot-image-wrap annotation-image-wrap"
                      :style="annotationContentStyle"
                      @pointerdown="startShapeDraft"
                    >
                      <img
                        v-if="selectedImagePreviewUrl"
                        class="screenshot-image annotation-image"
                        :src="selectedImagePreviewUrl"
                        :style="annotationCanvasStyle"
                        :alt="selectedImageNode.title"
                        draggable="false"
                      />
                      <div v-else class="empty-image-surface">
                        <span>{{ selectedImageNode.filename || selectedImagePreviewLoading ? '加载中' : '空图' }}</span>
                      </div>
                      <div
                        v-if="selectedImagePreviewUrl"
                        v-for="shape in occlusionOverlayShapes"
                        :key="'occlusion-' + shape.id"
                        class="annotation-occlusion-mask"
                        :style="shapeBoxStyle(shape)"
                      />
                      <div
                        v-if="selectedImagePreviewUrl"
                        v-for="shape in annotationShapes"
                        :key="shape.id"
                        class="annotation-shape"
                        :class="{ 'is-active': isShapeSelected(shape.id) }"
                        :style="shapeBoxStyle(shape)"
                        @pointerdown.stop="startShapeMove($event, shape.id)"
                        @contextmenu.prevent.stop="openShapeContextMenu($event, shape.id)"
                      >
                        <button
                          type="button"
                          class="shape-corner-handle is-top-left"
                          title="拖拽调整左上角"
                          aria-label="拖拽调整左上角"
                          @pointerdown.stop="startShapeResize($event, shape.id, 'top-left')"
                        />
                        <button
                          type="button"
                          class="shape-corner-handle is-bottom-right"
                          title="拖拽调整右下角"
                          aria-label="拖拽调整右下角"
                          @pointerdown.stop="startShapeResize($event, shape.id, 'bottom-right')"
                        />
                      </div>
                      <div
                        v-if="shapeDraftBox"
                        class="annotation-shape is-draft"
                        :style="shapeBoxStyle(shapeDraftBox)"
                      />
                    </div>
                  </div>
                </div>

                <div class="shape-tree-scroll" :style="annotationPanelStyle">
                  <el-tree
                    class="shape-tree"
                    :data="selectedImageShapes"
                    :props="shapeTreeProps"
                    node-key="id"
                    :default-expanded-keys="expandedShapeNodeIds"
                    highlight-current
                    draggable
                    :current-node-key="selectedShapeId"
                    @node-click="handleShapeTreeNodeClick"
                    @node-expand="node => setShapeNodeExpanded(node.id, true)"
                    @node-collapse="node => setShapeNodeExpanded(node.id, false)"
                    @node-contextmenu="openShapeTreeContextMenu"
                    @contextmenu.prevent="openShapeTreeBlankContextMenu"
                  >
                    <template #default="{ data }">
                      <span class="shape-tree-node" :class="{ 'is-group': data.kind === 'group', 'is-selected': isShapeSelected(data.id) }">
                        {{ data.title || 'shape' }}
                      </span>
                    </template>
                  </el-tree>
                </div>

                <div
                  v-if="shapeContextMenu.visible"
                  class="asset-context-menu shape-context-menu"
                  :style="{ left: `${shapeContextMenu.x}px`, top: `${shapeContextMenu.y}px` }"
                  @click.stop
                  @contextmenu.prevent
                >
                  <button type="button" :disabled="!selectedShapeCopyCount" @click="copySelectedShapes">
                    复制
                  </button>
                  <button type="button" :disabled="!copiedShapes.length || !selectedImageNode" @click="pasteCopiedShapes">
                    粘贴
                  </button>
                  <button type="button" class="is-danger" :disabled="!selectedShapeCopyCount" @click="deleteShapeFromContextMenu">
                    删除
                  </button>
                </div>
              </div>

    <div v-if="selectedShape" class="shape-fields">
                <el-input v-model="selectedShape.title" size="small" placeholder="标题" />
                <div v-if="selectedShape.kind !== 'group'" class="shape-detect-row">
                  <el-checkbox v-model="selectedShape.floating">
                    浮动
                  </el-checkbox>
                  <button
                    type="button"
                    class="shape-condition-toggle"
                    :class="'is-' + selectedShapeSceneIdentityRole"
                    :title="shapeMatchRoleTitle('scene', selectedShapeSceneIdentityRole)"
                    :aria-label="shapeMatchRoleTitle('scene', selectedShapeSceneIdentityRole)"
                    @click="cycleSelectedShapeSceneIdentityRole"
                  >
                    {{ shapeMatchRoleLabel(selectedShapeSceneIdentityRole) }}
                  </button>
                  <span>
                    场景标识
                  </span>
                  <div class="shape-jump-field">
                    <span>场景跳转</span>
                    <button type="button" class="shape-inline-help" title="查看场景跳转说明" aria-label="查看场景跳转说明" @click="showSceneJumpHelp">
                      ?
                    </button>
                    <el-input
                      v-model="selectedShape.sceneJumpTarget"
                      size="small"
                      placeholder="17(3),18,-1"
                      @blur="normalizeSelectedShapeSceneJumpTarget"
                    />
                  </div>
                  <div class="shape-jump-field">
                    <span>内容方向</span>
                    <el-select v-model="selectedShape.contentDirection" class="shape-direction-select" size="small">
                      <el-option label="无" value="none" />
                      <el-option label="↑" value="up" />
                      <el-option label="↓" value="down" />
                      <el-option label="←" value="left" />
                      <el-option label="→" value="right" />
                    </el-select>
                  </div>
                  <span class="shape-row-break" aria-hidden="true" />
                  <div class="shape-jump-field shape-pixel-tolerance-field">
                    <button
                      type="button"
                      class="shape-condition-toggle"
                      :class="'is-' + selectedShapeImageMatchRole"
                      :title="shapeMatchRoleTitle('image', selectedShapeImageMatchRole)"
                      :aria-label="shapeMatchRoleTitle('image', selectedShapeImageMatchRole)"
                      @click="cycleSelectedShapeMatchRole('image')"
                    >
                      {{ shapeMatchRoleLabel(selectedShapeImageMatchRole) }}
                    </button>
                    <span>图像</span>
                    <span>像素容差</span>
                    <el-input-number
                      v-model="selectedShape.pixelTolerance"
                      class="shape-pixel-tolerance-input"
                      size="small"
                      :min="0"
                      :max="255"
                      :step="1"
                      :controls="false"
                    />
                  </div>
                  <div class="shape-action-group">
                    <el-checkbox v-model="selectedShape.maskEnabled" title="启用抠图" aria-label="启用抠图" />
                    <el-button size="small" :disabled="!selectedShape" @click="openShapeMaskDialog">
                      抠图
                    </el-button>
                  </div>
                  <div class="shape-action-group">
                    <el-checkbox v-model="selectedShape.toleranceEnabled" title="启用容差" aria-label="启用容差" />
                    <el-button size="small" :disabled="!selectedShape" @click="openShapeToleranceDialog">
                      容差
                    </el-button>
                  </div>
                  <div class="shape-action-group">
                    <el-checkbox v-model="selectedShape.discriminatorEnabled" title="启用区分" aria-label="启用区分" />
                    <el-button size="small" :disabled="!selectedShape" @click="openShapeDiscriminatorDialog">
                      区分
                    </el-button>
                  </div>
                  <span class="shape-row-break" aria-hidden="true" />
                  <div class="shape-action-group shape-ocr-config">
                    <button
                      type="button"
                      class="shape-condition-toggle"
                      :class="'is-' + selectedShapeOcrMatchRole"
                      :title="shapeMatchRoleTitle('ocr', selectedShapeOcrMatchRole)"
                      :aria-label="shapeMatchRoleTitle('ocr', selectedShapeOcrMatchRole)"
                      @click="cycleSelectedShapeMatchRole('ocr')"
                    >
                      {{ shapeMatchRoleLabel(selectedShapeOcrMatchRole) }}
                    </button>
                    <span>
                      OCR
                    </span>
                    <el-input
                      v-model="selectedShape.ocrText"
                      class="shape-ocr-text-input"
                      size="small"
                      placeholder="文本"
                    />
                    <el-select v-model="selectedShape.ocrMatchMode" class="shape-ocr-mode-select" size="small">
                      <el-option label="包含" value="contains" />
                      <el-option label="等于" value="exact" />
                      <el-option label="通配符" value="wildcard" />
                      <el-option label="正则" value="regex" />
                    </el-select>
                  </div>
                </div>
                <div v-if="selectedShape.kind !== 'group'" class="shape-detect-row">
                  <div class="shape-action-group shape-detect-group">
                    <el-button
                      size="small"
                      :loading="shapeDetectingId === selectedShape.id"
                      :disabled="!canDetectSelectedShape"
                      @click="detectSelectedShape"
                    >
                      检测
                    </el-button>
                    <span v-if="selectedShapeDetectResult" class="shape-detect-result">
                      {{ selectedShapeDetectResult }}
                    </span>
                  </div>
                </div>
                <el-input v-model="selectedShape.description" type="textarea" :rows="4" placeholder="说明" />
              </div>
            </div>

            <div v-else class="annotation-empty">选择一个图片节点后编辑标注</div>
          </section>




        </div>
      </div>






    </section>
    <el-dialog
      v-model="burstDialogVisible"
      title="连拍缓存"
      width="860px"
      append-to-body
      @opened="loadBurstFrames"
      @closed="releaseBurstPreviewUrls"
    >
      <div class="burst-toolbar">
        <span class="burst-summary">
          {{ burstCaptureRunning ? '连拍中' : '已停止' }}，已存 {{ burstSavedCount }}，跳过 {{ burstSkippedCount }}，缓存 {{ burstTotal }}
        </span>
        <div class="burst-actions">
          <el-button size="small" :type="burstCaptureRunning ? 'primary' : 'default'" @click="toggleBurstCapture">
            {{ burstCaptureRunning ? '停止连拍' : '开始连拍' }}
          </el-button>
          <el-button size="small" type="primary" plain :disabled="!selectedBurstFilenames.length" @click="importSelectedBurstFrames">
            保存选中
          </el-button>
          <el-button size="small" :loading="burstLoading" @click="loadBurstFrames">刷新</el-button>
          <el-button size="small" type="danger" plain :disabled="!burstTotal" @click="clearBurstFrames">清空</el-button>
        </div>
      </div>
      <div v-loading="burstLoading" class="burst-grid">
        <div
          v-for="item in burstItems"
          :key="item.filename"
          class="burst-card"
          :class="{ 'is-selected': selectedBurstFilenames.includes(item.filename) }"
          @click="toggleBurstFrameSelection(item.filename)"
        >
          <el-checkbox
            class="burst-card-check"
            :model-value="selectedBurstFilenames.includes(item.filename)"
            @click.stop
            @change="toggleBurstFrameSelection(item.filename)"
          />
          <img v-if="burstPreviewUrls[item.filename]" :src="burstPreviewUrls[item.filename]" :alt="item.filename" />
          <div v-else class="burst-card-empty">加载中</div>
          <div class="burst-card-meta">
            <span>{{ item.filename }}</span>
          </div>
        </div>
        <div v-if="!burstItems.length" class="burst-empty">暂无连拍缓存</div>
      </div>
      <div class="burst-pager">
        <span>第 {{ burstPage }} / {{ burstPageCount }} 页</span>
        <el-pagination
          background
          layout="prev, pager, next"
          :current-page="burstPage"
          :page-size="burstPageSize"
          :total="burstTotal"
          @current-change="handleBurstPageChange"
        />
      </div>
    </el-dialog>
    <el-dialog
      v-model="gameMacroConfigVisible"
      title="录制宏配置"
      width="420px"
      append-to-body
    >
      <div class="game-macro-config">
        <label class="game-macro-config-row">
          <span>默认尺寸</span>
          <el-input-number
            v-model="gameMacroConfig.defaultShapeSize"
            size="small"
            :min="8"
            :max="240"
            :step="2"
            :controls="false"
          />
        </label>
        <label class="game-macro-config-row">
          <span>首个 shape 标记场景</span>
          <el-checkbox v-model="gameMacroConfig.markFirstShapeAsSceneIdentity" />
        </label>
        <label class="game-macro-config-row">
          <span>拖拽耗时模式</span>
          <el-select v-model="gameMacroConfig.dragDurationMode" size="small">
            <el-option label="真实耗时" value="real" />
            <el-option label="固定耗时" value="fixed" />
          </el-select>
        </label>
        <label v-if="gameMacroConfig.dragDurationMode === 'fixed'" class="game-macro-config-row">
          <span>固定耗时 ms</span>
          <el-input-number
            v-model="gameMacroConfig.defaultDragDurationMs"
            size="small"
            :min="50"
            :max="3000"
            :step="50"
            :controls="false"
          />
        </label>
        <label class="game-macro-config-row">
          <span>标注模式</span>
          <el-select v-model="gameMacroConfig.annotationMode" size="small">
            <el-option label="工程保底" value="simple" />
            <el-option label="AI辅助" value="ai" />
          </el-select>
        </label>
      </div>
    </el-dialog>
    <el-dialog
      v-model="shapeMaskDialogVisible"
      class="shape-mask-dialog"
      width="780px"
      append-to-body
      @closed="stopShapeMaskSampling"
    >
      <template #header>
        <div class="shape-dialog-head">
          <span>方框抠图</span>
          <button type="button" class="shape-help-button" title="查看说明" aria-label="查看抠图说明" @click="showShapeMaskHelp">
            ?
          </button>
        </div>
      </template>
      <div class="shape-mask-tool">
        <div class="shape-mask-previews">
          <div class="shape-mask-preview">
            <div class="shape-mask-label">当前直播</div>
            <img v-if="shapeMaskLivePreviewUrl" :src="shapeMaskLivePreviewUrl" alt="当前直播" />
            <div v-else class="shape-mask-empty">等待采样</div>
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">抠图结果</div>
            <img v-if="shapeMaskResultPreviewUrl" :src="shapeMaskResultPreviewUrl" alt="抠图结果" />
            <div v-else class="shape-mask-empty">等待采样</div>
          </div>
        </div>
        <div class="shape-mask-controls">
          <span>采样 {{ shapeMaskFrameCount }} 帧</span>
          <div class="shape-mask-slider">
            <span>阈值 {{ shapeMaskThreshold }}</span>
            <el-slider v-model="shapeMaskThreshold" :min="0" :max="120" :step="1" @input="refreshShapeMaskPreview" />
          </div>
          <el-button size="small" type="primary" plain :disabled="shapeMaskRunning" @click="startShapeMaskSampling">
            开始
          </el-button>
          <el-button size="small" :disabled="!shapeMaskRunning" @click="pauseShapeMaskSampling">
            暂停
          </el-button>
          <el-button size="small" :disabled="!shapeMaskAlphaDataUrl" @click="cleanShapeMaskAlpha">
            净化
          </el-button>
          <el-button size="small" @click="resetShapeMaskSampling">重置</el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="shapeMaskDialogVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!shapeMaskAlphaDataUrl" @click="saveShapeMaskAndClose">
          保存
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="shapeToleranceDialogVisible"
      class="shape-mask-dialog"
      width="780px"
      append-to-body
      @closed="stopShapeToleranceSampling"
    >
      <template #header>
        <div class="shape-dialog-head">
          <span>方框容差</span>
          <button type="button" class="shape-help-button" title="查看说明" aria-label="查看容差说明" @click="showShapeToleranceHelp">
            ?
          </button>
        </div>
      </template>
      <div class="shape-mask-tool">
        <div class="shape-mask-previews">
          <div class="shape-mask-preview">
            <div class="shape-mask-label">最小值</div>
            <img v-if="shapeToleranceMinPreviewUrl" :src="shapeToleranceMinPreviewUrl" alt="最小值" />
            <div v-else class="shape-mask-empty">等待采样</div>
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">最大值</div>
            <img v-if="shapeToleranceMaxPreviewUrl" :src="shapeToleranceMaxPreviewUrl" alt="最大值" />
            <div v-else class="shape-mask-empty">等待采样</div>
          </div>
        </div>
        <div class="shape-mask-controls">
          <span>采样 {{ shapeToleranceFrameCount }} 帧</span>
          <el-button size="small" type="primary" plain :disabled="shapeToleranceRunning" @click="startShapeToleranceSampling">
            开始
          </el-button>
          <el-button size="small" :disabled="!shapeToleranceRunning" @click="pauseShapeToleranceSampling">
            暂停
          </el-button>
          <el-button size="small" @click="resetShapeToleranceSampling">重置</el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="shapeToleranceDialogVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!shapeToleranceMinPreviewUrl || !shapeToleranceMaxPreviewUrl" @click="saveShapeToleranceAndClose">
          保存
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="shapeDiscriminatorDialogVisible"
      class="shape-mask-dialog"
      width="920px"
      append-to-body
      @closed="stopShapeDiscriminatorSampling"
    >
      <template #header>
        <div class="shape-dialog-head">
          <span>方框区分</span>
          <button type="button" class="shape-help-button" title="查看说明" aria-label="查看区分说明" @click="showShapeDiscriminatorHelp">
            ?
          </button>
        </div>
      </template>
      <div class="shape-mask-tool">
        <div class="shape-mask-controls">
          <div class="shape-jump-field">
            <span>组名</span>
            <el-input v-model="shapeDiscriminatorGroupTitle" size="small" placeholder="区分组" />
          </div>
          <el-checkbox v-model="shapeDiscriminatorSyncBox">同步框选</el-checkbox>
        </div>
        <div class="shape-mask-controls">
          <div class="shape-jump-field">
            <span>添加状态</span>
            <el-input-number
              v-model="shapeDiscriminatorNewImageId"
              size="small"
              :min="0"
              :controls="false"
              placeholder="图片ID"
            />
          </div>
          <el-select
            v-model="shapeDiscriminatorNewShapeId"
            class="shape-discriminator-shape-select"
            size="small"
            placeholder="选择 shape"
            :disabled="!shapeDiscriminatorNewImageId || !shapeDiscriminatorCandidateShapes.length"
          >
            <el-option
              v-for="candidate in shapeDiscriminatorCandidateShapes"
              :key="candidate.shape.id"
              :label="candidate.label"
              :value="candidate.shape.id"
            />
          </el-select>
          <el-button size="small" @click="addShapeDiscriminatorMember">添加</el-button>
          <el-button size="small" @click="resetShapeDiscriminator">刷新</el-button>
          <el-button size="small" type="primary" plain :disabled="shapeDiscriminatorRunning || !shapeDiscriminatorReady" @click="startShapeDiscriminatorSampling">
            开始
          </el-button>
          <el-button size="small" :disabled="!shapeDiscriminatorRunning" @click="pauseShapeDiscriminatorSampling">
            暂停
          </el-button>
          <span v-if="shapeDiscriminatorResultText" class="shape-detect-result">
            {{ shapeDiscriminatorResultText }}
          </span>
        </div>
        <div class="shape-discriminator-members">
          <div
            v-for="member in shapeDiscriminatorMembers"
            :key="member.shapeId"
            class="shape-discriminator-member"
          >
            <span>#{{ member.imageId }}</span>
            <el-input v-model="member.label" size="small" placeholder="状态名" />
            <button
              type="button"
              class="shape-discriminator-remove"
              title="删除状态"
              aria-label="删除状态"
              @click="removeShapeDiscriminatorMember(member.shapeId)"
            >
              -
            </button>
          </div>
        </div>
        <div class="shape-mask-previews is-three">
          <div class="shape-mask-preview">
            <div class="shape-mask-label">当前状态</div>
            <img v-if="shapeDiscriminatorSourcePreviewUrl" :src="shapeDiscriminatorSourcePreviewUrl" alt="当前图" />
            <div v-else class="shape-mask-empty">等待配置</div>
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">成员状态</div>
            <img v-if="shapeDiscriminatorTargetPreviewUrl" :src="shapeDiscriminatorTargetPreviewUrl" alt="对照图" />
            <div v-else class="shape-mask-empty">等待配置</div>
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">差异权重</div>
            <img v-if="shapeDiscriminatorWeightPreviewUrl" :src="shapeDiscriminatorWeightPreviewUrl" alt="差异权重" />
            <div v-else class="shape-mask-empty">等待配置</div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="shapeDiscriminatorDialogVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!shapeDiscriminatorReady" @click="saveShapeDiscriminatorAndClose">
          保存
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="stepperLogDialogVisible"
      class="stepper-log-dialog"
      title="步进器日志"
      width="720px"
      append-to-body
      top="10vh"
    >
      <div v-if="stepperLogs.length" class="stepper-log-list">
        <div
          v-for="entry in pagedStepperLogs"
          :key="entry.id"
          class="stepper-log-row"
          :class="`is-${entry.kind}`"
        >
          <span class="stepper-log-time">{{ entry.time }}</span>
          <span class="stepper-log-kind">{{ stepperLogKindLabel(entry.kind) }}</span>
          <span class="stepper-log-message">{{ entry.message }}</span>
        </div>
      </div>
      <div v-else class="stepper-log-empty">暂无日志</div>
      <div v-if="stepperLogs.length" class="stepper-log-pager">
        <span>{{ stepperLogPageStart }}-{{ stepperLogPageEnd }} / {{ stepperLogs.length }}</span>
        <el-pagination
          v-model:current-page="stepperLogPage"
          size="small"
          layout="prev, pager, next"
          :page-size="STEPPER_LOG_PAGE_SIZE"
          :total="stepperLogs.length"
        />
      </div>
      <template #footer>
        <el-button :disabled="!stepperLogs.length" @click="clearStepperLogs">清空</el-button>
        <el-button type="primary" @click="stepperLogDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ArrowLeft,
  ArrowRight,
  Delete,
  Folder,
  Picture,
  Plus,
  Setting,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue';
import Sortable from 'sortablejs';
import {
  annotateFanxiuGameWindow3MacroShape,
  appendFanxiuGameWindow3StepperLog,
  clearFanxiuGameWindow2BurstFrames,
  clearFanxiuGameWindow3StepperLogs,
  clickFanxiuGameWindow2,
  compileFanxiuPseudoCode,
  createFanxiuPseudoCodeCard,
  createFanxiuGameWindow2StreamToken,
  deleteFanxiuPseudoCodeCard,
  deleteFanxiuGameWindow2Screenshot,
  dragFanxiuGameWindow2,
  ensureFanxiuCaptureRuntime,
  getFanxiuGameWindow2BurstFrameImage,
  getFanxiuCaptureRuntimeStatus,
  getFanxiuGameWindow3AssetTree,
  getFanxiuGameWindow3StepperLogs,
  getFanxiuGameWindow2MatchImage,
  getFanxiuGameWindow2Screenshot,
  getFanxiuGameWindow2PreLabel,
  importFanxiuGameWindow2BurstFrames,
  keyeventFanxiuGameWindow2,
  listFanxiuGameWindow2BurstFrames,
  listFanxiuPseudoCodeCards,
  listFanxiuGameWindow2Screenshots,
  matchFanxiuGameWindow2Screenshot,
  recognizeFanxiuGameWindow3OcrFrame,
  runFanxiuVisualScript,
  saveFanxiuGameWindow2BurstFrame,
  saveFanxiuGameWindow2Frame,
  saveFanxiuGameWindow2PreLabel,
  saveFanxiuGameWindow3AssetTree,
  screencapFanxiuGameWindow2,
  startFanxiuPseudoCode,
  stopFanxiuVisualScript,
  textFanxiuGameWindow2,
  updateFanxiuPseudoCodeCard,
  releaseFanxiuCaptureRuntime,
  type FanxiuCaptureRuntimeStatus,
  type FanxiuGameWindow2MatchBox,
  type FanxiuGameWindow2BurstFrameItem,
  type FanxiuGameWindow2MatchPayload,
  type FanxiuGameWindow2MatchResponse,
  type FanxiuGameWindow2ScreenshotItem,
  type FanxiuGameWindow2PreLabelBox,
  type FanxiuGameWindow2PreLabelPayload,
  type FanxiuGameWindow3MacroAnnotateResponse,
  type FanxiuGameWindow3OcrFrameLine,
  type FanxiuGameWindow3StepperLogEntry,
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
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { taskStore, type Device } from '@/store/taskStore';
import { useSortableList } from '@/utils/useSortableList';
import {
  decideDailyFindResult,
  extractDailyProgress,
  extractDailyStatusText,
  getDailyStatusCode,
  type DailyFindDecision,
  type DailyFindProgress,
  type DailyFindResult,
  type DailyFindStatusCode,
} from './dailyFindDecision';
import {
  defaultDailyTaskPresets,
  type DailyTaskMatchMode,
  type DailyTaskPreset,
} from './dailyTaskPresets';

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

type WindowSceneKey = 'star-cloud-phone' | 'sunlogin' | 'mumu';
type CaptureArea = 'outer' | 'client';
type RotateDegrees = '0' | '90' | '180' | '270';
type WindowViewMode = 'live' | 'control' | 'off';
type WindowTitleMatch = 'contains' | 'exact';
type MumuChannel = 'desktop' | 'adb';
type RuntimeChannelUse = 'frontend' | 'stepper';
type RuntimeChannelPolicyChannel = 'selected' | MumuChannel;

interface RuntimeChannelPolicy {
  mumu: RuntimeChannelPolicyChannel;
}

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
  mumuChannel?: MumuChannel;
}

interface WindowSceneConfig {
  trimBorderText: string;
  rotateDegrees: RotateDegrees;
  fps: number;
  quality: number;
  autoDismissPopup: boolean;
  mumuChannel: MumuChannel;
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

type MatchResultEntryKind = 'fixed';

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

type GameMacroAnnotationMode = 'simple' | 'ai';
type GameMacroDragDurationMode = 'real' | 'fixed';
type ShapeMatchRole = 'off' | 'optional' | 'required';
type ShapeOcrMatchMode = 'contains' | 'exact' | 'wildcard' | 'regex';

type GameMacroConfig = {
  version: number;
  defaultShapeSize: number;
  markFirstShapeAsSceneIdentity: boolean;
  dragDurationMode: GameMacroDragDurationMode;
  defaultDragDurationMs: number;
  annotationMode: GameMacroAnnotationMode;
};

type GameMacroPendingJump = {
  imageId: string;
  shapeId: string;
};

type GameMacroShapeAnnotation = {
  box: FanxiuGameWindow2MatchBox;
  label?: string;
  confidence?: number;
  usedAi?: boolean;
};

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
const GAME_MACRO_CONFIG_STORAGE_KEY = 'fanxiu.gameWindow3.gameMacro.config.v1';
const GAME_MACRO_CONFIG_VERSION = 2;
const GAME_MACRO_DEFAULT_DRAG_DURATION_MS = 1500;
const FALLBACK_FRAME_WIDTH = 900;
const FALLBACK_FRAME_HEIGHT = 1600;
const SCREENSHOT_MIN_ZOOM_PERCENT = 20;
const SCREENSHOT_MAX_ZOOM_PERCENT = 500;
const SCREENSHOT_ZOOM_STEP = 10;
const MIN_CONTENT_VISIBLE_AREA_RATIO = 0.2;
const MIN_CONTENT_VISIBLE_AXIS_RATIO = Math.sqrt(MIN_CONTENT_VISIBLE_AREA_RATIO);
const GAME_WINDOW_SERVICE_KEY = 'fanxiu-game-window';
const VISUAL_ACTION_MARKER_START = '<!-- codeyun-visual-action-v1';
const VISUAL_ACTION_MARKER_END = '-->';
const RUNTIME_CHANNEL_POLICIES: Record<RuntimeChannelUse, RuntimeChannelPolicy> = {
  frontend: { mumu: 'selected' },
  stepper: { mumu: 'adb' },
};
const windowViewModes: Array<{ value: WindowViewMode; label: string }> = [
  { value: 'live', label: '直播' },
  { value: 'control', label: '交互' },
  { value: 'off', label: '关闭' },
];
const windowScenes: WindowScene[] = [
  {
    key: 'star-cloud-phone',
    label: '星星云手机',
    defaults: {
      targetTitle: '云手机',
      titleMatch: 'contains',
      cropText: '0,0,0,0',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 12,
      quality: 82,
      autoDismissPopup: false,
      displayScale: 100,
      fixedWidth: 0,
      fixedHeight: 0,
      mumuChannel: 'desktop',
    },
  },
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
      mumuChannel: 'desktop',
    },
  },
  {
    key: 'mumu',
    label: 'MuMu模拟器',
    defaults: {
      targetTitle: 'Powered by MuMu模拟器',
      titleMatch: 'contains',
      cropText: '0,60,0,0',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 12,
      quality: 82,
      autoDismissPopup: false,
      displayScale: 60,
      fixedWidth: 900,
      fixedHeight: 1600,
      mumuChannel: 'adb',
    },
  },
];

const devices = computed(() => taskStore.devices);
const selectedEntryId = ref('');
const selectedWindowKey = ref<WindowSceneKey>('star-cloud-phone');
const runtimeStatus = ref<RuntimeStatusResponse | null>(null);
const runtimeLoading = ref(false);
const captureRuntimeStatus = ref<FanxiuCaptureRuntimeStatus | null>(null);
const connectionLoading = ref(false);

const trimBorderText = ref('0,0,0,0');
const rotateDegrees = ref<RotateDegrees>('0');
const displayScale = ref(100);
const fps = ref(12);
const quality = ref(82);
const mumuChannel = ref<MumuChannel>('desktop');
const autoDismissPopup = ref(false);
const streamEnabled = ref(true);
const streamNonce = ref(Date.now());
const streamError = ref('');
const streamToken = ref('');
const streamTokenExpiresAt = ref(0);
const adbFrameUrl = ref('');
const streamTokenLoading = ref(false);
const actualFps = ref(0);
const layerVisible = ref(true);
const windowViewMode = ref<WindowViewMode>('live');
const controlEnabled = ref(false);
const saveFrameLoading = ref(false);
const burstCaptureRunning = ref(false);
const burstCaptureSaving = ref(false);
const burstDialogVisible = ref(false);
const burstLoading = ref(false);
const burstSavedCount = ref(0);
const burstSkippedCount = ref(0);
const burstPage = ref(1);
const burstPageSize = 24;
const burstTotal = ref(0);
const burstItems = ref<FanxiuGameWindow2BurstFrameItem[]>([]);
const burstPreviewUrls = ref<Record<string, string>>({});
const selectedBurstFilenames = ref<string[]>([]);
const gameMacroRecording = ref(false);
const gameMacroConfigVisible = ref(false);
const gameMacroCapturePending = ref(false);
const gameMacroStatusText = ref('');
const gameMacroPendingJump = ref<GameMacroPendingJump | null>(null);
const gameMacroConfig = ref<GameMacroConfig>({
  version: GAME_MACRO_CONFIG_VERSION,
  defaultShapeSize: 50,
  markFirstShapeAsSceneIdentity: true,
  dragDurationMode: 'real',
  defaultDragDurationMs: GAME_MACRO_DEFAULT_DRAG_DURATION_MS,
  annotationMode: 'simple',
});
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
let adbFrameTimer: number | null = null;
let actualFpsResetTimer: number | null = null;
let actualFpsSamplerTimer: number | null = null;
const liveFrameTimestamps: number[] = [];
let lastLiveFrameSample = '';
let burstCaptureTimer: number | null = null;
let screenshotSaveTimer: number | null = null;
let visualSimilarityProbeTimer: number | null = null;
let visualSimilarityProbeSeq = 0;
let tokenRequestSeq = 0;
let lastInputErrorAt = 0;
let isApplyingWindowConfig = false;
let isApplyingVisualMacroUiState = false;
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
        similarity: result.fixed_pixel_similarity ?? result.fixed_exact_pixel_similarity ?? result.fixed_similarity ?? result.similarity,
        result,
      },
    ];
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
  const width = naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || FALLBACK_FRAME_WIDTH;
  const height = naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || FALLBACK_FRAME_HEIGHT;
  const stageWidth = Math.max(1, Math.round(width * displayScale.value / 100));
  const stageHeight = Math.max(1, Math.round(height * displayScale.value / 100));
  return {
    width: `${stageWidth}px`,
    height: `${stageHeight}px`,
  };
});
const annotationPanelStyle = computed(() => ({
  height: liveCanvasStyle.value.height,
  maxHeight: liveCanvasStyle.value.height,
}));
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
  if (shouldCaptureWithAdb('frontend')) return '正在获取 ADB 画面';
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

const normalizeGameMacroConfig = (value: unknown): GameMacroConfig => {
  const item = value && typeof value === 'object'
    ? value as Partial<GameMacroConfig> & { recordRealDragDuration?: unknown }
    : {};
  const rawVersion = Math.round(Number(item.version));
  const version = rawVersion === GAME_MACRO_CONFIG_VERSION ? GAME_MACRO_CONFIG_VERSION : 1;
  const defaultShapeSize = Math.round(Number(item.defaultShapeSize));
  const rawDefaultDragDurationMs = Math.round(Number(item.defaultDragDurationMs));
  const defaultDragDurationMs = !Number.isFinite(rawDefaultDragDurationMs)
    ? GAME_MACRO_DEFAULT_DRAG_DURATION_MS
    : version < GAME_MACRO_CONFIG_VERSION && rawDefaultDragDurationMs === 300
      ? GAME_MACRO_DEFAULT_DRAG_DURATION_MS
      : clamp(rawDefaultDragDurationMs, 50, 3000);
  const annotationMode = item.annotationMode === 'ai' ? 'ai' : 'simple';
  const dragDurationMode = item.dragDurationMode === 'fixed' || item.dragDurationMode === 'real'
    ? item.dragDurationMode
    : (item.recordRealDragDuration === false ? 'fixed' : 'real');
  return {
    version: GAME_MACRO_CONFIG_VERSION,
    defaultShapeSize: Number.isFinite(defaultShapeSize) ? clamp(defaultShapeSize, 8, 240) : 50,
    markFirstShapeAsSceneIdentity: item.markFirstShapeAsSceneIdentity !== false,
    dragDurationMode,
    defaultDragDurationMs,
    annotationMode,
  };
};

const loadGameMacroConfig = () => {
  if (typeof window === 'undefined') return;
  try {
    gameMacroConfig.value = normalizeGameMacroConfig(JSON.parse(window.localStorage.getItem(GAME_MACRO_CONFIG_STORAGE_KEY) || '{}'));
  } catch {
    gameMacroConfig.value = normalizeGameMacroConfig({});
  }
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
    adb_screencap: shouldCaptureWithAdb('frontend') ? 'true' : 'false',
    popup_check_interval: '3',
    nonce: String(streamNonce.value),
  });
  return `/api/fanxiu/game-window2/stream?${params.toString()}`;
});
const liveImageUrl = computed(() => streamUrl.value);
const actualFpsText = computed(() => (streamEnabled.value && windowViewMode.value !== 'off'
  ? `${actualFps.value.toFixed(1)}`
  : '-'
));
const burstPageCount = computed(() => Math.max(1, Math.ceil(burstTotal.value / burstPageSize)));
const connectionReady = computed(() => Boolean(
  selectedEntryId.value
  && serviceActive.value
  && streamEnabled.value
  && liveImageUrl.value
  && naturalWidth.value
  && naturalHeight.value
  && !streamError.value
));
const connectionButtonLoading = computed(() => connectionLoading.value || streamTokenLoading.value);
const connectionButtonText = computed(() => {
  if (windowViewMode.value === 'off') return '已关闭';
  if (connectionReady.value) return '运行中';
  if (connectionButtonLoading.value || (streamEnabled.value && (streamToken.value || shouldCaptureWithAdb('frontend')) && !streamError.value)) return '连接中';
  return '连接';
});
const captureRuntimeText = computed(() => {
  const status = captureRuntimeStatus.value;
  if (!status) return '抓包未同步';
  const stateLabel = ({
    stopped: '抓包已停',
    waiting_game: '等待游戏',
    recovering: '抓包恢复中',
    running: '抓包中',
  } as Record<string, string>)[status.state] ?? status.state;
  return status.last_error ? `${stateLabel} · ${status.last_error}` : stateLabel;
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
  const nextMumuChannel = raw.mumuChannel === 'desktop' || raw.mumuChannel === 'adb'
    ? raw.mumuChannel
    : (fallback.mumuChannel ?? 'desktop');
  return {
    trimBorderText: raw.trimBorderText || fallback.trimBorderText,
    rotateDegrees: rotate === '0' || rotate === '90' || rotate === '180' || rotate === '270'
      ? rotate
      : fallback.rotateDegrees,
    fps: Number.isFinite(nextFps) ? Math.min(Math.max(Math.round(nextFps), 1), 30) : fallback.fps,
    quality: Number.isFinite(nextQuality) ? Math.min(Math.max(Math.round(nextQuality), 1), 100) : fallback.quality,
    autoDismissPopup: typeof raw.autoDismissPopup === 'boolean' ? raw.autoDismissPopup : fallback.autoDismissPopup,
    mumuChannel: nextMumuChannel,
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
  mumuChannel: mumuChannel.value,
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
  mumuChannel.value = config.mumuChannel;
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

const ensureCaptureRuntime = async () => {
  try {
    captureRuntimeStatus.value = await ensureFanxiuCaptureRuntime('game-window3');
  } catch (error) {
    ElMessage.warning(getErrorMessage(error));
  }
};

const refreshCaptureRuntimeStatus = async () => {
  try {
    captureRuntimeStatus.value = await getFanxiuCaptureRuntimeStatus();
  } catch {
    // 抓包状态只是辅助状态，静默等待下一轮同步。
  }
};

const releaseCaptureRuntime = () => {
  releaseFanxiuCaptureRuntime('game-window3')
    .then((status) => {
      captureRuntimeStatus.value = status;
    })
    .catch(() => undefined);
};

const handleEntryChange = async () => {
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  streamToken.value = '';
  streamTokenExpiresAt.value = 0;
  stopAdbFramePolling();
  revokeAdbFrameUrl();
  screenshotImages.value = [];
  screenshotLoaded.value = false;
  clearScreenshotSelection();
  clearMatchResults();
  persistEntrySelection(selectedEntryId.value);
  applyWindowConfig();
  if (selectedEntryId.value) await loadEntryAssetTree(selectedEntryId.value);
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
  stopAdbFramePolling();
  revokeAdbFrameUrl();
  clearMatchResults();
  persistWindowSelection();
  applyWindowConfig();
  await connectWindow();
};

const handleWindowViewModeChange = async () => {
  controlClickState.value = null;
  if (windowViewMode.value === 'off') {
    await restartStream();
    return;
  }
  await connectWindow();
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

const drawLiveMatchBox = (
  ctx: CanvasRenderingContext2D,
  box: FanxiuGameWindow2MatchBox,
  displayWidth: number,
  displayHeight: number,
  index: number,
) => {
  if (!naturalWidth.value || !naturalHeight.value) return;
  const scaleX = displayWidth / naturalWidth.value;
  const scaleY = displayHeight / naturalHeight.value;
  const x = box.x * scaleX;
  const y = box.y * scaleY;
  const w = box.w * scaleX;
  const h = box.h * scaleY;
  const color = index === 0 ? '#22c55e' : '#f97316';
  ctx.save();
  ctx.lineWidth = index === 0 ? 2.2 : 1.5;
  ctx.strokeStyle = color;
  ctx.fillStyle = index === 0 ? 'rgba(34, 197, 94, 0.12)' : 'rgba(249, 115, 22, 0.08)';
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
  const label = box.name || `${index + 1}`;
  ctx.font = '12px sans-serif';
  const labelWidth = Math.max(22, ctx.measureText(label).width + 10);
  ctx.fillStyle = color;
  ctx.fillRect(x, Math.max(0, y - 18), labelWidth, 16);
  ctx.fillStyle = '#fff';
  ctx.fillText(label, x + 5, Math.max(12, y - 6));
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
  if (streamEnabled.value && windowViewMode.value !== 'off') {
    shapeDetectLiveBoxes.value.forEach((box, index) => {
      drawLiveMatchBox(ctx, box, width, height, index);
    });
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
};

const resetActualFps = () => {
  liveFrameTimestamps.length = 0;
  actualFps.value = 0;
  lastLiveFrameSample = '';
  if (actualFpsResetTimer) {
    window.clearTimeout(actualFpsResetTimer);
    actualFpsResetTimer = null;
  }
};

const recordLiveFrameArrival = () => {
  const now = performance.now();
  liveFrameTimestamps.push(now);
  const windowMs = 3000;
  while (liveFrameTimestamps.length && now - liveFrameTimestamps[0] > windowMs) {
    liveFrameTimestamps.shift();
  }
  if (liveFrameTimestamps.length >= 2) {
    const elapsedSeconds = (liveFrameTimestamps[liveFrameTimestamps.length - 1] - liveFrameTimestamps[0]) / 1000;
    actualFps.value = elapsedSeconds > 0 ? (liveFrameTimestamps.length - 1) / elapsedSeconds : 0;
  } else {
    actualFps.value = 0;
  }
  if (actualFpsResetTimer) window.clearTimeout(actualFpsResetTimer);
  actualFpsResetTimer = window.setTimeout(() => {
    resetActualFps();
  }, 2000);
};

const sampleLiveFrameSignature = () => {
  const image = streamImageRef.value;
  if (!image || !image.naturalWidth || !image.naturalHeight) return '';
  const canvas = document.createElement('canvas');
  const width = 12;
  const height = 12;
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return '';
  try {
    context.drawImage(image, 0, 0, width, height);
    const data = context.getImageData(0, 0, width, height).data;
    let hash = 2166136261;
    for (let index = 0; index < data.length; index += 4) {
      hash ^= data[index];
      hash = Math.imul(hash, 16777619);
      hash ^= data[index + 1];
      hash = Math.imul(hash, 16777619);
      hash ^= data[index + 2];
      hash = Math.imul(hash, 16777619);
    }
    return String(hash >>> 0);
  } catch {
    return '';
  }
};

const pollActualFps = () => {
  if (!streamEnabled.value || windowViewMode.value === 'off') return;
  const signature = sampleLiveFrameSignature();
  if (!signature) return;
  if (lastLiveFrameSample && signature !== lastLiveFrameSample) {
    recordLiveFrameArrival();
  }
  lastLiveFrameSample = signature;
};

const stopActualFpsSampler = () => {
  if (actualFpsSamplerTimer) {
    window.clearInterval(actualFpsSamplerTimer);
    actualFpsSamplerTimer = null;
  }
};

const startActualFpsSampler = () => {
  stopActualFpsSampler();
  actualFpsSamplerTimer = window.setInterval(pollActualFps, 80);
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
  return canvas.toDataURL('image/png');
};

const resolveMumuChannelForUse = (use: RuntimeChannelUse = 'frontend'): MumuChannel => {
  const policy = RUNTIME_CHANNEL_POLICIES[use] ?? RUNTIME_CHANNEL_POLICIES.frontend;
  return policy.mumu === 'selected' ? mumuChannel.value : policy.mumu;
};

const resolveRuntimeInputBackend = (use: RuntimeChannelUse = 'frontend'): MumuChannel => (
  selectedWindowKey.value === 'mumu' ? resolveMumuChannelForUse(use) : 'desktop'
);

const shouldCaptureWithAdb = (use: RuntimeChannelUse = 'frontend') => (
  selectedWindowKey.value === 'mumu' && resolveMumuChannelForUse(use) === 'adb'
);

const captureCurrentFrameDataUrl = async (use: RuntimeChannelUse = 'frontend') => {
  const useAdb = shouldCaptureWithAdb(use);
  if (useAdb && selectedEntryId.value) {
    try {
      const blob = await screencapFanxiuGameWindow2(selectedEntryId.value);
      return await blobToDataUrl(blob);
    } catch {
      // Fall back to the visible live frame if ADB screencap is temporarily unavailable.
    }
  }
  return captureCurrentLiveFrameDataUrl();
};

const revokeAdbFrameUrl = () => {
  if (!adbFrameUrl.value) return;
  URL.revokeObjectURL(adbFrameUrl.value);
  adbFrameUrl.value = '';
};

const stopAdbFramePolling = () => {
  if (adbFrameTimer) {
    window.clearInterval(adbFrameTimer);
    adbFrameTimer = null;
  }
};

const setWindowViewModeOff = async () => {
  windowViewMode.value = 'off';
  stopAdbFramePolling();
  stopBurstCapture();
  stopActualFpsSampler();
  resetActualFps();
  gameMacroRecording.value = false;
  gameMacroCapturePending.value = false;
  streamEnabled.value = false;
  controlEnabled.value = false;
  runtimeStatus.value = null;
  streamToken.value = '';
  streamTokenExpiresAt.value = 0;
  revokeAdbFrameUrl();
  if (streamImageRef.value) streamImageRef.value.src = '';
  await nextTick(syncCanvas);
};

const handleStreamError = () => {
  if (windowViewMode.value === 'off') return;
  const message = '未获取到画面，检查设备入口、画面流服务和窗口场景。';
  streamError.value = message;
  void setWindowViewModeOff();
  ElMessage.error(message);
};

const restartStream = async () => {
  streamError.value = '';
  shapeDetectLiveBoxes.value = [];
  stopAdbFramePolling();
  stopActualFpsSampler();
  resetActualFps();
  if (windowViewMode.value === 'off') {
    await setWindowViewModeOff();
    return;
  }
  streamEnabled.value = true;
  controlEnabled.value = windowViewMode.value === 'control';
  revokeAdbFrameUrl();
  await ensureStreamToken();
  streamNonce.value = Date.now();
  startActualFpsSampler();
  void nextTick(syncCanvas);
};

const connectWindow = async () => {
  if (!selectedEntryId.value) return;
  if (windowViewMode.value === 'off') {
    await setWindowViewModeOff();
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
    const currentFrameDataUrl = await captureCurrentFrameDataUrl();
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
      current_frame_data_url: currentFrameDataUrl,
    });
    let imageDataUrl = currentFrameDataUrl;
    try {
      const blob = await getFanxiuGameWindow2Screenshot(selectedEntryId.value, result.filename);
      imageDataUrl = await blobToDataUrl(blob);
    } catch {
      // 保存帧已经成功；预览读取失败时退回当前直播帧。
    }
    const node = createAssetImageNode(result.filename, {
      filename: result.filename,
      width: result.width,
      height: result.height,
    });
    assetImagePreviewUrls.value = {
      ...assetImagePreviewUrls.value,
      [node.id]: imageDataUrl,
    };
    addSavedFrameToAssetTree(node);
    ElMessage.success(`已保存到文件树：${result.filename}`);
  } catch (error) {
    streamError.value = getErrorMessage(error);
    await setWindowViewModeOff();
    ElMessage.error(getErrorMessage(error));
  } finally {
    saveFrameLoading.value = false;
  }
};

const burstFramePayload = async () => ({
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
  current_frame_data_url: await captureCurrentFrameDataUrl(),
});

const releaseBurstPreviewUrls = () => {
  Object.values(burstPreviewUrls.value).forEach((url) => URL.revokeObjectURL(url));
  burstPreviewUrls.value = {};
};

const loadBurstFrames = async () => {
  if (!selectedEntryId.value) return;
  burstLoading.value = true;
  try {
    const response = await listFanxiuGameWindow2BurstFrames(selectedEntryId.value, burstPage.value, burstPageSize);
    burstTotal.value = response.total;
    burstItems.value = response.items;
    const names = new Set(response.items.map((item) => item.filename));
    selectedBurstFilenames.value = selectedBurstFilenames.value.filter((name) => names.has(name));
    releaseBurstPreviewUrls();
    const previews: Record<string, string> = {};
    await Promise.all(response.items.map(async (item) => {
      const blob = await getFanxiuGameWindow2BurstFrameImage(selectedEntryId.value, item.filename);
      previews[item.filename] = URL.createObjectURL(blob);
    }));
    burstPreviewUrls.value = previews;
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    burstLoading.value = false;
  }
};

const toggleBurstFrameSelection = (filename: string) => {
  selectedBurstFilenames.value = selectedBurstFilenames.value.includes(filename)
    ? selectedBurstFilenames.value.filter((item) => item !== filename)
    : [...selectedBurstFilenames.value, filename];
};

const importSelectedBurstFrames = async () => {
  if (!selectedEntryId.value || !selectedBurstFilenames.value.length) return;
  try {
    const response = await importFanxiuGameWindow2BurstFrames(selectedEntryId.value, selectedBurstFilenames.value);
    await loadScreenshotList();
    const importedNodes = response.imported.map((item) => createAssetImageNode(item.filename, {
      filename: item.filename,
      width: item.width,
      height: item.height,
    }));
    for (const node of importedNodes) addSavedFrameToAssetTree(node);
    selectedBurstFilenames.value = [];
    ElMessage.success(`已保存 ${response.imported_count} 张到文件树`);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const saveBurstFrameOnce = async () => {
  if (!selectedEntryId.value || burstCaptureSaving.value) return;
  burstCaptureSaving.value = true;
  try {
    const result = await saveFanxiuGameWindow2BurstFrame(await burstFramePayload());
    if (result.saved) {
      burstSavedCount.value += 1;
      if (burstDialogVisible.value) await loadBurstFrames();
    } else if (result.skipped) {
      burstSkippedCount.value += 1;
    }
  } catch (error) {
    stopBurstCapture();
    ElMessage.error(getErrorMessage(error));
  } finally {
    burstCaptureSaving.value = false;
  }
};

const stopBurstCapture = () => {
  if (burstCaptureTimer) {
    window.clearInterval(burstCaptureTimer);
    burstCaptureTimer = null;
  }
  burstCaptureRunning.value = false;
};

const startBurstCapture = () => {
  if (!selectedEntryId.value || burstCaptureRunning.value) return;
  burstCaptureRunning.value = true;
  void saveBurstFrameOnce();
  const interval = Math.max(200, Math.round(1000 / Math.max(1, Number(fps.value) || 1)));
  burstCaptureTimer = window.setInterval(() => {
    void saveBurstFrameOnce();
  }, interval);
};

const toggleBurstCapture = () => {
  if (burstCaptureRunning.value) {
    stopBurstCapture();
    return;
  }
  startBurstCapture();
};

const openBurstDialog = () => {
  burstDialogVisible.value = true;
  void loadBurstFrames();
};

const handleBurstPageChange = (page: number) => {
  burstPage.value = page;
  void loadBurstFrames();
};

const clearBurstFrames = async () => {
  if (!selectedEntryId.value) return;
  try {
    await ElMessageBox.confirm('清空全部连拍缓存？', '清空连拍', { type: 'warning' });
    const result = await clearFanxiuGameWindow2BurstFrames(selectedEntryId.value);
    burstSavedCount.value = 0;
    burstSkippedCount.value = 0;
    burstPage.value = 1;
    burstTotal.value = 0;
    burstItems.value = [];
    selectedBurstFilenames.value = [];
    releaseBurstPreviewUrls();
    ElMessage.success(`已清空 ${result.cleared} 张`);
  } catch (error) {
    if (error === 'cancel' || error === 'close') return;
    ElMessage.error(getErrorMessage(error));
  }
};

const resetAssetFrame = async (node: GameWindow3AssetNode) => {
  if (!selectedEntryId.value || node.type !== 'image' || !node.filename) return;
  const currentFrameDataUrl = await captureCurrentFrameDataUrl();
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
    current_frame_data_url: currentFrameDataUrl,
    overwrite_filename: node.filename,
  });
  node.filename = result.filename;
  node.width = result.width;
  node.height = result.height;
  delete node.imageDataUrl;
  assetImagePreviewUrls.value = {
    ...assetImagePreviewUrls.value,
    [node.id]: currentFrameDataUrl,
  };
  if (selectedAssetId.value === node.id) {
    await nextTick();
    void ensureSelectedImagePreview();
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

const recordGameMacroInput = async (
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null = null,
  durationMs = 0,
) => {
  if (!gameMacroRecording.value || !selectedEntryId.value) return;
  if (gameMacroCapturePending.value) return;
  gameMacroCapturePending.value = true;
  gameMacroStatusText.value = '录制宏：捕捉画面';
  try {
    const currentFrameDataUrl = await captureCurrentFrameDataUrl();
    const frame = await findOrCreateGameMacroFrame(currentFrameDataUrl);
    setGameMacroPendingJumpTarget(frame.image);
    const fallbackBox = buildGameMacroFallbackBox(frame.image, action, point, endPoint);
    const shape = createRecordedGameShape(frame.image, action, point, endPoint, durationMs, { box: fallbackBox });
    const frameText = frame.created ? `新帧 ${frame.image.title}` : `${assetImageIdMark(frame.image)} ${frame.image.title}`;
    gameMacroStatusText.value = `录制宏：${frameText}，已生成 ${shape.title}`;
    if (gameMacroConfig.value.annotationMode === 'ai') {
      void refineRecordedGameShapeWithAi(
        frame.image,
        shape,
        action,
        point,
        endPoint,
        durationMs,
        currentFrameDataUrl,
        fallbackBox,
      );
    }
  } catch (error) {
    gameMacroStatusText.value = '录制宏：记录失败';
    ElMessage.error(getErrorMessage(error));
  } finally {
    gameMacroCapturePending.value = false;
  }
};

const toggleGameMacroRecording = async () => {
  if (!selectedEntryId.value) {
    ElMessage.warning('先选择设备并连接画面');
    return;
  }
  gameMacroRecording.value = !gameMacroRecording.value;
  if (!gameMacroRecording.value) {
    gameMacroCapturePending.value = false;
    gameMacroPendingJump.value = null;
    gameMacroStatusText.value = '录制宏：已停止';
    return;
  }
  windowViewMode.value = 'control';
  controlEnabled.value = true;
  if (!streamEnabled.value || !serviceActive.value) {
    await connectWindow();
  } else {
    await restartStream();
  }
  gameMacroStatusText.value = '录制宏：记录下一次操作';
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
      current_frame_data_url: await captureCurrentFrameDataUrl(),
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
    const similarity = response.fixed_pixel_similarity
      ?? response.fixed_exact_pixel_similarity
      ?? response.fixed_similarity
      ?? response.similarity;
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
    const fixedText = `原位${response.fixed_pixel_similarity ?? response.fixed_exact_pixel_similarity ?? response.fixed_similarity ?? response.similarity}%`;
    ElMessage.success(fixedText);
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

const buildRemoteInputPayloadBase = (use: RuntimeChannelUse = 'frontend') => ({
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
  input_backend: resolveRuntimeInputBackend(use),
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
    await recordGameMacroInput('drag', startPoint, endPoint, durationMs);
    await recordVisualMacroInput('drag', startPoint, endPoint, durationMs);
    void sendRemoteDrag(startPoint, endPoint, durationMs);
    return;
  }
  const clickPoint = normalizeControlPoint(point);
  await recordGameMacroInput('click', clickPoint);
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
      if (screenshotPanelOpen.value && (selectedScreenshotImage.value || selectedImageNode.value)) {
        void setScreenshotZoomPercent(screenshotZoomPercent.value + SCREENSHOT_ZOOM_STEP);
      } else {
        void setLiveContentZoomPercent(liveContentZoomPercent.value + 5);
      }
      return;
    }
    if (event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract') {
      event.preventDefault();
      if (screenshotPanelOpen.value && (selectedScreenshotImage.value || selectedImageNode.value)) {
        void setScreenshotZoomPercent(screenshotZoomPercent.value - SCREENSHOT_ZOOM_STEP);
      } else {
        void setLiveContentZoomPercent(liveContentZoomPercent.value - 5);
      }
      return;
    }
    if (event.key === '0' || event.code === 'Numpad0') {
      event.preventDefault();
      if (screenshotPanelOpen.value && (selectedScreenshotImage.value || selectedImageNode.value)) {
        void resetScreenshotContentView();
      } else {
        void resetLiveContentView();
      }
      return;
    }
  }
  if (!screenshotPanelOpen.value || (!selectedScreenshotImage.value && !selectedImageNode.value)) return;
  if (event.key === 'Delete' || event.key === 'Backspace') {
    if (selectedImageNode.value) {
      deleteSelectedShape();
    } else {
      deleteSelectedScreenshotBox();
    }
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
    void refreshCaptureRuntimeStatus();
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
  [trimBorderText, rotateDegrees, fps, autoDismissPopup, mumuChannel],
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
watch(gameMacroConfig, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_MACRO_CONFIG_STORAGE_KEY, JSON.stringify(normalizeGameMacroConfig(value)));
}, { deep: true });

onMounted(async () => {
  loadGameMacroConfig();
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('keyup', handleKeyup);
  window.addEventListener('blur', handleWindowBlur);
  window.addEventListener('resize', handleWindowResize);
  window.addEventListener('click', closeAssetContextMenu);
  window.addEventListener('click', closeShapeContextMenu);
  resizeObserver = new ResizeObserver(syncCanvas);
  if (imageWrapRef.value) resizeObserver.observe(imageWrapRef.value);

  await taskStore.fetchDevices();
  selectedEntryId.value = chooseDefaultEntryId();
  selectedWindowKey.value = chooseDefaultWindowKey();
  applyWindowConfig();
  if (selectedEntryId.value) {
    persistEntrySelection(selectedEntryId.value);
    persistWindowSelection();
    await loadEntryAssetTree(selectedEntryId.value);
    if (windowViewMode.value !== 'off') {
      await connectWindow();
    }
  }
  void ensureCaptureRuntime();
  startPolling();
  void loadStepperLogs();
  void ensureSelectedImagePreview();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  if (assetTreeSaveTimer) {
    window.clearTimeout(assetTreeSaveTimer);
    assetTreeSaveTimer = null;
  }
  stopPolling();
  stopAdbFramePolling();
  stopActualFpsSampler();
  stopBurstCapture();
  resetActualFps();
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('keyup', handleKeyup);
  window.removeEventListener('blur', handleWindowBlur);
  window.removeEventListener('resize', handleWindowResize);
  window.removeEventListener('click', closeAssetContextMenu);
  window.removeEventListener('click', closeShapeContextMenu);
  stopLivePan();
  stopScreenshotPan();
  stopShapeMaskSampling();
  stopShapeToleranceSampling();
  stopShapeDiscriminatorSampling();
  cancelShapeDraft();
  finishShapeDrag();
  resizeObserver?.disconnect();
  revokeAdbFrameUrl();
  releaseBurstPreviewUrls();
  if (streamImageRef.value) streamImageRef.value.src = '';
  revokeScreenshotImageUrl();
  clearMatchResults();
  releaseCaptureRuntime();
});

type GameWindow3AssetNode = {
  id: string;
  type: 'folder' | 'image';
  title: string;
  children?: GameWindow3AssetNode[];
  filename?: string;
  imageDataUrl?: string;
  width?: number;
  height?: number;
  shapes?: GameWindow3Shape[];
};

type GameWindow3Shape = {
  id: string;
  kind?: 'shape' | 'group';
  title: string;
  description: string;
  floating?: boolean;
  isSceneIdentity?: boolean;
  sceneIdentityRole?: ShapeMatchRole;
  sceneJumpTarget?: string;
  contentDirection?: 'none' | 'up' | 'down' | 'left' | 'right';
  imageMatchRole?: ShapeMatchRole;
  pixelTolerance?: number;
  ocrMatchRole?: ShapeMatchRole;
  ocrEnabled?: boolean;
  ocrText?: string;
  ocrMatchMode?: ShapeOcrMatchMode;
  maskEnabled?: boolean;
  alphaMask?: ShapeAlphaMask | null;
  toleranceEnabled?: boolean;
  toleranceRange?: ShapeToleranceRange | null;
  discriminatorEnabled?: boolean;
  discriminator?: ShapeDiscriminator | null;
  discriminatorGroupId?: string | null;
  discriminatorValue?: string;
  x: number;
  y: number;
  w: number;
  h: number;
  children?: GameWindow3Shape[];
};

type ShapeAlphaMask = {
  width: number;
  height: number;
  dataUrl: string;
};

type ShapeToleranceRange = {
  width: number;
  height: number;
  minDataUrl: string;
  maxDataUrl: string;
};

type ShapeDiscriminator = {
  targetImageId: number | null;
};

type SceneJumpEntry = {
  label: string;
  count: number;
};

type StepperTask =
  | {
      id: string;
      type: 'go_scene';
      targetText: string;
      targets: GameWindow3AssetNode[];
    }
  | {
      id: string;
      type: 'drag_shape_to_shape';
      sourceText: string;
      sourceShapeTitle: string;
      targetShapeTitle: string;
      sourceImages: GameWindow3AssetNode[];
    }
  | {
      id: string;
      type: 'daily_find';
      query: string;
      matchMode: ShapeOcrMatchMode;
      completedFallbackPattern: string;
      completedFallbackExcludePattern: string;
      completedFallbackMinTotal: number;
      notFoundStatus: number;
      timeoutSeconds: number;
      dragCount: number;
      requireProgress: boolean;
      openOnReady: boolean;
      legacySource: string;
      note: string;
      startedAt: number;
      attempts: number;
    };

type StepperTaskDefinition =
  | {
      id: string;
      label: string;
      type: 'go_scene';
      targetText: string;
    }
  | {
      id: string;
      label: string;
      type: 'drag_shape_to_shape';
      sourceText: string;
      sourceShapeTitle: string;
      targetShapeTitle: string;
    }
  | {
      id: string;
      label: string;
      type: 'daily_find';
      query: string;
      matchMode: ShapeOcrMatchMode;
      completedFallbackPattern?: string;
      completedFallbackExcludePattern?: string;
      completedFallbackMinTotal?: number;
      notFoundStatus: number;
      timeoutSeconds: number;
      dragCount: number;
      requireProgress?: boolean;
      openOnReady?: boolean;
      legacySource?: string;
      note?: string;
    };

type StepperTaskFunctionId = string;

type StepperTaskFunctionDefinition = {
  id: StepperTaskFunctionId;
  label: string;
  task?: StepperTaskDefinition;
  presets: StepperTaskDefinition[];
};

type DailyFindTaskDefinition = Extract<StepperTaskDefinition, { type: 'daily_find' }>;

type StepperLastAction = {
  fromImageId: string;
  shapeId: string;
  shapeTitle: string;
  isIndependentExit: boolean;
  expectedTargets: GameWindow3AssetNode[];
};

type StepperSceneCandidate = {
  image: GameWindow3AssetNode;
  score: number;
  full: number;
  min: number;
  average: number;
  count: number;
};

type StepperRankedSceneCandidate = StepperSceneCandidate & {
  layerIndex: number;
  layerPriority: number;
};

type StepperActionEdge = {
  from: GameWindow3AssetNode;
  shape: GameWindow3Shape;
  jumpEntries: SceneJumpEntry[];
  targets: Array<{ image: GameWindow3AssetNode; count: number }>;
  isIndependentExit: boolean;
  isNoJump: boolean;
  hasExperience: boolean;
};

type StepperLogKind = 'start' | 'wait' | 'action' | 'success' | 'stop' | 'error' | 'detail';

type StepperLogEntry = FanxiuGameWindow3StepperLogEntry & {
  id: string;
  time: string;
  kind: StepperLogKind | string;
  message: string;
};

type StepperTickOptions = {
  verbose?: boolean;
  waitOnIdle?: boolean;
};

type DiscriminatorGroupMember = {
  imageId: number;
  shapeId: string;
  label: string;
};

type DiscriminatorGroup = {
  id: string;
  title: string;
  syncBox: boolean;
  members: DiscriminatorGroupMember[];
};

type ShapeDragState = {
  pointerId: number;
  shapeId: string;
  mode: 'move' | 'top-left' | 'bottom-right';
  startClientX: number;
  startClientY: number;
  startBox: Pick<GameWindow3Shape, 'x' | 'y' | 'w' | 'h'>;
};

type ShapeDraftState = {
  pointerId: number;
  startX: number;
  startY: number;
  rect: DOMRect;
};

const GAME_WINDOW3_STORAGE_KEY = 'fanxiu.gameWindow3.assetTree.v1';
const GAME_WINDOW3_DISCRIMINATOR_GROUPS_KEY = 'fanxiu.gameWindow3.discriminatorGroups.v1';
const GAME_WINDOW3_UI_STATE_STORAGE_KEY = 'fanxiu.gameWindow3.uiState.v1';
const GAME_WINDOW3_OCCLUSION_MASK_ENABLED_KEY = 'fanxiu.gameWindow3.occlusionMaskEnabled.v1';
const GAME_WINDOW3_STEPPER_TASKS_STORAGE_KEY = 'fanxiu.gameWindow3.stepperTasks.v1';
const GAME_WINDOW3_STEPPER_SELECTED_FUNCTION_STORAGE_KEY = 'fanxiu.gameWindow3.selectedStepperFunction.v1';
const GAME_WINDOW3_STEPPER_SELECTED_TASK_STORAGE_KEY = 'fanxiu.gameWindow3.selectedStepperTask.v1';
const STEPPER_SCENE_MATCH_THRESHOLD = 80;
const STEPPER_LOG_PAGE_SIZE = 20;
const annotationCanvasRef = ref<HTMLElement | null>(null);
const selectedAssetId = ref<string | null>(null);
const selectedShapeId = ref<string | null>(null);
const selectedShapeIds = ref<string[]>([]);
const shapeSelectionAnchorId = ref<string | null>(null);
const globalOcclusionMaskEnabled = ref(false);
const copiedShapes = ref<GameWindow3Shape[]>([]);
const expandedAssetNodeIds = ref<string[]>([]);
const expandedShapeNodeIds = ref<string[]>([]);
const assetImagePreviewUrls = ref<Record<string, string>>({});
const assetImagePreviewLoadingIds = ref<Record<string, boolean>>({});
const dailyTaskPresetToDefinition = (
  preset: DailyTaskPreset,
  openOnReady = false,
): DailyFindTaskDefinition => ({
  id: openOnReady ? preset.id.replace('daily-find-', 'daily-enter-') : preset.id,
  label: preset.label,
  type: 'daily_find',
  query: preset.query,
  matchMode: preset.matchMode,
  completedFallbackPattern: preset.completedFallbackPattern,
  completedFallbackExcludePattern: preset.completedFallbackExcludePattern,
  completedFallbackMinTotal: preset.completedFallbackMinTotal,
  notFoundStatus: preset.notFoundStatus,
  timeoutSeconds: preset.timeoutSeconds,
  dragCount: preset.dragCount,
  requireProgress: preset.requireProgress,
  openOnReady,
  legacySource: preset.legacySource,
  note: preset.note,
});

const defaultDailyFindTaskDefinitions = (): DailyFindTaskDefinition[] => (
  defaultDailyTaskPresets().map((preset) => dailyTaskPresetToDefinition(preset))
);

const defaultDailyEnterTaskDefinitions = (): DailyFindTaskDefinition[] => (
  defaultDailyTaskPresets().map((preset) => dailyTaskPresetToDefinition(preset, true))
);

const defaultStepperTaskDefinitions = (): StepperTaskDefinition[] => [
  {
    id: 'go-world',
    label: '到世界',
    type: 'go_scene',
    targetText: '世界',
  },
  {
    id: 'hide-floating-window',
    label: '隐藏浮动窗',
    type: 'drag_shape_to_shape',
    sourceText: '#58',
    sourceShapeTitle: '图标',
    targetShapeTitle: '隐藏区',
  },
  ...defaultDailyFindTaskDefinitions(),
  ...defaultDailyEnterTaskDefinitions(),
];

const stepperTaskFunctionIdOf = (task: StepperTaskDefinition): StepperTaskFunctionId => (
  task.type === 'go_scene' ? task.type : (task.type === 'daily_find' ? (task.openOnReady ? 'daily_enter' : 'daily_find') : task.id)
);

const stepperTaskFunctionLabelOf = (task: StepperTaskDefinition) => (
  task.type === 'go_scene' ? '到场景' : (task.type === 'daily_find' ? (task.openOnReady ? '日常进入' : '日常定位') : task.label)
);

const normalizeStepperTaskDefinition = (item: unknown): StepperTaskDefinition | null => {
  if (!item || typeof item !== 'object') return null;
  const raw = item as Partial<StepperTaskDefinition>;
  const id = typeof raw.id === 'string' ? raw.id.trim() : '';
  const label = typeof raw.label === 'string' ? raw.label.trim() : '';
  if (!id || !label) return null;
  if (raw.type === 'go_scene') {
    const targetText = typeof raw.targetText === 'string' ? raw.targetText.trim() : '';
    if (!targetText) return null;
    return { id, label, type: 'go_scene', targetText };
  }
  if (raw.type === 'drag_shape_to_shape') {
    const sourceText = typeof raw.sourceText === 'string' ? raw.sourceText.trim() : '';
    const sourceShapeTitle = typeof raw.sourceShapeTitle === 'string' ? raw.sourceShapeTitle.trim() : '';
    const targetShapeTitle = typeof raw.targetShapeTitle === 'string' ? raw.targetShapeTitle.trim() : '';
    if (!sourceText || !sourceShapeTitle || !targetShapeTitle) return null;
    return { id, label, type: 'drag_shape_to_shape', sourceText, sourceShapeTitle, targetShapeTitle };
  }
  if (raw.type === 'daily_find') {
    const query = typeof raw.query === 'string' ? raw.query.trim() : '';
    if (!query) return null;
    const notFoundStatus = Number(raw.notFoundStatus);
    const timeoutSeconds = Number(raw.timeoutSeconds);
    const dragCount = Number(raw.dragCount);
    const completedFallbackMinTotal = Number(raw.completedFallbackMinTotal);
    return {
      id,
      label,
      type: 'daily_find',
      query,
      matchMode: raw.matchMode === 'exact' || raw.matchMode === 'wildcard' || raw.matchMode === 'regex' ? raw.matchMode as DailyTaskMatchMode : 'contains',
      completedFallbackPattern: typeof raw.completedFallbackPattern === 'string' ? raw.completedFallbackPattern.trim() : '',
      completedFallbackExcludePattern: typeof raw.completedFallbackExcludePattern === 'string' ? raw.completedFallbackExcludePattern.trim() : '',
      completedFallbackMinTotal: Number.isFinite(completedFallbackMinTotal) ? Math.max(0, Math.round(completedFallbackMinTotal)) : 0,
      notFoundStatus: Number.isFinite(notFoundStatus) ? Math.round(notFoundStatus) : -1,
      timeoutSeconds: Number.isFinite(timeoutSeconds) ? clamp(Math.round(timeoutSeconds), 1, 600) : 90,
      dragCount: Number.isFinite(dragCount) ? clamp(Math.round(dragCount), 0, 200) : 20,
      requireProgress: raw.requireProgress !== false,
      openOnReady: raw.openOnReady === true || id.startsWith('daily-enter-'),
      legacySource: typeof raw.legacySource === 'string' ? raw.legacySource.trim() : '',
      note: typeof raw.note === 'string' ? raw.note.trim() : '',
    };
  }
  return null;
};

const normalizeStepperTaskDefinitions = (items: unknown): StepperTaskDefinition[] => {
  const defaults = defaultStepperTaskDefinitions();
  const defaultsById = new Map(defaults.map((item) => [item.id, item]));
  const normalized = Array.isArray(items)
    ? items.map(normalizeStepperTaskDefinition).filter((item): item is StepperTaskDefinition => Boolean(item))
    : [];
  const merged = new Map<string, StepperTaskDefinition>();
  for (const item of defaults) merged.set(item.id, item);
  for (const item of normalized) {
    const defaultItem = defaultsById.get(item.id);
    if (item.type === 'daily_find' && defaultItem?.type === 'daily_find') {
      merged.set(item.id, {
        ...item,
        query: defaultItem.query,
        matchMode: defaultItem.matchMode,
        completedFallbackPattern: defaultItem.completedFallbackPattern,
        completedFallbackExcludePattern: defaultItem.completedFallbackExcludePattern,
        completedFallbackMinTotal: defaultItem.completedFallbackMinTotal,
        notFoundStatus: defaultItem.notFoundStatus,
        timeoutSeconds: defaultItem.timeoutSeconds,
        dragCount: defaultItem.dragCount,
        requireProgress: defaultItem.requireProgress,
        legacySource: item.legacySource || defaultItem.legacySource || '',
        note: item.note || defaultItem.note || '',
        openOnReady: item.openOnReady ?? defaultItem.openOnReady,
      });
    } else {
      merged.set(item.id, item);
    }
  }
  return Array.from(merged.values());
};

const loadStepperTaskDefinitions = () => {
  if (typeof window === 'undefined') return defaultStepperTaskDefinitions();
  try {
    return normalizeStepperTaskDefinitions(JSON.parse(window.localStorage.getItem(GAME_WINDOW3_STEPPER_TASKS_STORAGE_KEY) || '[]'));
  } catch {
    return defaultStepperTaskDefinitions();
  }
};

const loadSelectedStepperTaskId = (tasks: StepperTaskDefinition[]) => {
  if (typeof window === 'undefined') return tasks[0]?.id ?? '';
  const saved = window.localStorage.getItem(GAME_WINDOW3_STEPPER_SELECTED_TASK_STORAGE_KEY) || '';
  return tasks.some((task) => task.id === saved) ? saved : (tasks[0]?.id ?? '');
};

const loadSelectedStepperFunctionId = (tasks: StepperTaskDefinition[], selectedTaskId: string): StepperTaskFunctionId => {
  const selectedTask = tasks.find((task) => task.id === selectedTaskId);
  const fallback = selectedTask ? stepperTaskFunctionIdOf(selectedTask) : (tasks[0] ? stepperTaskFunctionIdOf(tasks[0]) : 'go_scene');
  if (typeof window === 'undefined') return fallback;
  const saved = window.localStorage.getItem(GAME_WINDOW3_STEPPER_SELECTED_FUNCTION_STORAGE_KEY);
  return saved && tasks.some((task) => stepperTaskFunctionIdOf(task) === saved) ? saved : fallback;
};

const stepperTaskDefinitions = ref<StepperTaskDefinition[]>(loadStepperTaskDefinitions());
const selectedStepperTaskId = ref(loadSelectedStepperTaskId(stepperTaskDefinitions.value));
const selectedStepperFunctionId = ref<StepperTaskFunctionId>(loadSelectedStepperFunctionId(stepperTaskDefinitions.value, selectedStepperTaskId.value));
const stepperTaskFunctionDefinitions = computed<StepperTaskFunctionDefinition[]>(() => {
  const grouped = new Map<StepperTaskFunctionId, StepperTaskFunctionDefinition>();
  for (const task of stepperTaskDefinitions.value) {
    const id = stepperTaskFunctionIdOf(task);
    const current = grouped.get(id) ?? {
      id,
      label: stepperTaskFunctionLabelOf(task),
      task,
      presets: [],
    };
    if (task.type === 'go_scene' || task.type === 'daily_find') {
      current.presets.push(task);
      current.task = current.presets[0];
    } else {
      current.task = task;
      current.presets = [];
    }
    grouped.set(id, current);
  }
  return Array.from(grouped.values());
});
const selectedStepperFunctionDefinition = computed(() => (
  stepperTaskFunctionDefinitions.value.find((fn) => fn.id === selectedStepperFunctionId.value) ?? stepperTaskFunctionDefinitions.value[0] ?? null
));
const selectedStepperTaskDefinition = computed(() => {
  const fn = selectedStepperFunctionDefinition.value;
  if (!fn) return null;
  if (!fn.presets.length) return fn.task ?? null;
  return fn.presets.find((preset) => preset.id === selectedStepperTaskId.value) ?? fn.presets[0] ?? null;
});
const selectedStepperPresetDefinitions = computed(() => (
  selectedStepperFunctionDefinition.value?.presets ?? []
));
const stepperRunning = ref(false);
const stepperStepping = ref(false);
const stepperStopRequested = ref(false);
const stepperRunStatus = ref('');
const stepperLogDialogVisible = ref(false);
const stepperLogs = ref<StepperLogEntry[]>([]);
const stepperLogPage = ref(1);
const stepperTaskStack = ref<StepperTask[]>([]);
const stepperLastAction = ref<StepperLastAction | null>(null);
const stepperLastDailyFindResult = ref<StepperDailyFindResult | null>(null);
const stepperLastDailyFindSummaryText = computed(() => {
  const result = stepperLastDailyFindResult.value;
  if (!result) return '';
  const summary = result.summary;
  const decisionText = result.decision === 'ready'
    ? '待执行'
    : result.decision === 'completed'
      ? '已完成'
      : result.decision === 'ongoing'
        ? '进行中'
        : result.decision === 'retry'
          ? '需复核'
          : '未找到';
  const progressText = summary?.progress ? ` ${summary.progress.current}/${summary.progress.total}` : '';
  const statusText = summary?.status ? ` ${summary.status}` : '';
  const pointText = summary ? ` @${summary.clickPoint.x},${summary.clickPoint.y}` : '';
  return `日常 ${decisionText} S${result.statusCode}${statusText}${progressText}${pointText}`;
});
const stepperLastDailyFindDetailText = computed(() => {
  const result = stepperLastDailyFindResult.value;
  if (!result) return '';
  const summary = result.summary;
  const parts = [
    `查询：${result.query}`,
    result.matchedText ? `命中：${result.matchedText}` : '',
    `判断：${result.decision}`,
    `状态码：${result.statusCode}`,
    `原因：${result.reason}`,
    summary?.anchorText ? `锚点：${summary.anchorText}` : '',
    summary?.candidateText && summary.candidateText !== summary?.anchorText ? `候选行：${summary.candidateText}` : '',
    summary?.status ? `状态：${summary.status}` : '',
    summary?.statusText && summary.statusText !== summary?.status ? `状态字段：${summary.statusText}` : '',
    summary?.progress ? `进度：${summary.progress.current}/${summary.progress.total}` : '',
    summary?.progressText ? `进度字段：${summary.progressText}` : '',
    summary ? `点击点：${summary.clickPoint.x},${summary.clickPoint.y}` : '',
    summary ? `来源：${summary.source}` : '',
  ].filter(Boolean);
  return parts.join('\n');
});
const stepperTriedActionEdges = ref<Set<string>>(new Set());
const stepperTickCount = ref(0);
const shapeDragState = ref<ShapeDragState | null>(null);
const shapeDraftState = ref<ShapeDraftState | null>(null);
const shapeDraftBox = ref<GameWindow3Shape | null>(null);
const shapeDetectingId = ref<string | null>(null);
const shapeDetectResults = ref<Record<string, string>>({});
const shapeDetectLiveBoxes = ref<FanxiuGameWindow2MatchBox[]>([]);
const shapeDetectSeq = ref(0);
const shapeMaskDialogVisible = ref(false);
const shapeMaskFrameCount = ref(0);
const shapeMaskThreshold = ref(36);
const shapeMaskLivePreviewUrl = ref('');
const shapeMaskResultPreviewUrl = ref('');
const shapeMaskAlphaDataUrl = ref('');
const shapeMaskRunning = ref(false);
const shapeMaskSamplingFrame = ref<number | null>(null);
const shapeMaskStats = ref<{
  width: number;
  height: number;
  min: Uint8ClampedArray;
  max: Uint8ClampedArray;
  baseAlpha: Uint8ClampedArray | null;
  reference: ImageData | null;
} | null>(null);
const shapeToleranceDialogVisible = ref(false);
const shapeToleranceFrameCount = ref(0);
const shapeToleranceMinPreviewUrl = ref('');
const shapeToleranceMaxPreviewUrl = ref('');
const shapeToleranceRunning = ref(false);
const shapeToleranceSamplingFrame = ref<number | null>(null);
const shapeToleranceStats = ref<{
  width: number;
  height: number;
  min: Uint8ClampedArray;
  max: Uint8ClampedArray;
} | null>(null);
const shapeDiscriminatorDialogVisible = ref(false);
const shapeDiscriminatorGroupId = ref<string | null>(null);
const shapeDiscriminatorGroupTitle = ref('');
const shapeDiscriminatorSyncBox = ref(true);
const shapeDiscriminatorMembers = ref<DiscriminatorGroupMember[]>([]);
const shapeDiscriminatorNewImageId = ref<number | null>(null);
const shapeDiscriminatorNewShapeId = ref('');
const shapeDiscriminatorSourcePreviewUrl = ref('');
const shapeDiscriminatorTargetPreviewUrl = ref('');
const shapeDiscriminatorWeightPreviewUrl = ref('');
const shapeDiscriminatorResultText = ref('');
const shapeDiscriminatorRunning = ref(false);
const shapeDiscriminatorSamplingFrame = ref<number | null>(null);
const shapeDiscriminatorReady = ref(false);
const shapeDiscriminatorState = ref<{
  width: number;
  height: number;
  variants: Array<{
    imageId: number;
    label: string;
    shapeId: string;
    reference: ImageData;
    alpha: Uint8ClampedArray | null;
  }>;
  weights: Float32Array;
  activePixels: number;
} | null>(null);
const assetContextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  nodeId: '',
});
const shapeContextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  shapeId: '',
});
const assetTreeProps = {
  children: 'children',
  label: 'title',
};
const shapeTreeProps = {
  children: 'children',
  label: 'title',
};

type GameWindow3UiState = {
  selectedAssetId?: string | null;
  selectedShapeId?: string | null;
  expandedAssetNodeIds?: string[];
  expandedShapeNodeIds?: string[];
};

const createAssetId = (prefix: string) => prefix + '-' + Date.now() + '-' + Math.random().toString(16).slice(2);

const createAssetImageNode = (
  title: string,
  options: Partial<Pick<GameWindow3AssetNode, 'filename' | 'imageDataUrl' | 'width' | 'height'>> = {},
): GameWindow3AssetNode => ({
  id: createAssetId('image'),
  type: 'image',
  title,
  ...options,
  shapes: [],
});

const createDefaultAssetTree = (): GameWindow3AssetNode[] => ([
  {
    id: createAssetId('folder'),
    type: 'folder',
    title: '默认分组',
    children: [createAssetImageNode('空图')],
  },
]);

const normalizeShapeContentDirection = (value: unknown): GameWindow3Shape['contentDirection'] => (
  value === 'up' || value === 'down' || value === 'left' || value === 'right' ? value : 'none'
);

const normalizeShapePixelTolerance = (value: unknown) => {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? clamp(Math.round(numberValue), 0, 255) : 5;
};

const normalizeShapeMatchRole = (value: unknown, fallback: ShapeMatchRole = 'off'): ShapeMatchRole => (
  value === 'optional' || value === 'required' || value === 'off' ? value : fallback
);

const normalizeShapeOcrMatchMode = (value: unknown): ShapeOcrMatchMode => (
  value === 'exact' || value === 'wildcard' || value === 'regex' ? value : 'contains'
);

const SHAPE_MATCH_ROLE_ORDER: ShapeMatchRole[] = ['off', 'required', 'optional'];
const shapeMatchRoleLabel = (role: ShapeMatchRole) => ({
  off: '关',
  optional: '定',
  required: '必',
}[role]);
const shapeMatchRoleTitle = (kind: 'image' | 'ocr' | 'scene', role: ShapeMatchRole) => {
  const name = kind === 'image' ? '图像' : (kind === 'ocr' ? 'OCR' : '场景标识');
  return {
    off: `${name}：不要求`,
    optional: `${name}：命中即定`,
    required: `${name}：必须命中`,
  }[role];
};
const nextShapeMatchRole = (role: ShapeMatchRole) => {
  const current = normalizeShapeMatchRole(role);
  const index = SHAPE_MATCH_ROLE_ORDER.indexOf(current);
  return SHAPE_MATCH_ROLE_ORDER[(index + 1) % SHAPE_MATCH_ROLE_ORDER.length];
};

const parseSceneJumpEntry = (value: string): SceneJumpEntry | null => {
  const text = value.trim();
  if (!text || text === '?') return null;
  if (text === '-1') return { label: '-1', count: 0 };
  const match = text.match(/^(.+?)\((\d+)\)$/);
  const label = (match ? match[1] : text).trim();
  if (!label || label === '?') return null;
  const count = match ? Math.max(0, Math.floor(Number(match[2]) || 0)) : 0;
  return { label, count };
};

const parseSceneJumpEntries = (value: string | number | null | undefined): SceneJumpEntry[] => {
  const items = String(value ?? '')
    .replace(/，/g, ',')
    .split(',')
    .map(parseSceneJumpEntry)
    .filter((item): item is SceneJumpEntry => Boolean(item));
  if (items.some((item) => item.label === '-1')) return [{ label: '-1', count: 0 }];
  const merged = new Map<string, SceneJumpEntry>();
  for (const item of items) {
    const current = merged.get(item.label);
    merged.set(item.label, {
      label: item.label,
      count: Math.max(current?.count ?? 0, item.count),
    });
  }
  return Array.from(merged.values()).sort((a, b) => (b.count - a.count) || a.label.localeCompare(b.label));
};

const serializeSceneJumpEntries = (entries: SceneJumpEntry[]) => (
  entries
    .map((item) => item.count > 0 ? `${item.label}(${item.count})` : item.label)
    .join(',')
);

const normalizeSceneJumpTargetText = (value: string | number | null | undefined) => {
  return serializeSceneJumpEntries(parseSceneJumpEntries(value));
};

const DAILY_TASK_BLOCK_TEMPLATE_DESCRIPTION = '日常滚动窗口内的单个任务块模板。它本身是普通 shape，同时作为字段子树的父节点；运行时由任务块模板整体浮动，子字段只按父 shape 相对位置读取状态、次数和活跃度。';

const isDailyTaskBlockTemplateShape = (shape: GameWindow3Shape) => (
  shape.id === 'shape-daily-task-block-template' || shape.title === '任务块模板'
);

const normalizeShapes = (
  shapes: GameWindow3Shape[] = [],
  parentIsDailyTaskBlockTemplate = false,
): GameWindow3Shape[] => shapes.flatMap((shape) => {
  if (shape.id === 'scene-identity') {
    return normalizeShapes(shape.children ?? [], parentIsDailyTaskBlockTemplate);
  }
  const isDailyTaskBlockTemplate = isDailyTaskBlockTemplateShape(shape);
  const isDailyTaskBlockField = parentIsDailyTaskBlockTemplate;
  const normalizedSceneIdentityRole = normalizeShapeMatchRole(shape.sceneIdentityRole, shape.isSceneIdentity ? 'required' : 'off');
  const normalizedImageMatchRole = normalizeShapeMatchRole(
    shape.imageMatchRole,
    shape.floating ? 'required' : (normalizedSceneIdentityRole !== 'off' ? normalizedSceneIdentityRole : 'off'),
  );
  const normalizedOcrMatchRole = normalizeShapeMatchRole(shape.ocrMatchRole, shape.ocrEnabled ? 'required' : 'off');
  return [{
    ...shape,
    kind: isDailyTaskBlockTemplate ? 'shape' : (shape.kind === 'group' ? 'group' : 'shape'),
    title: typeof shape.title === 'string' ? shape.title : '',
    description: isDailyTaskBlockTemplate
      ? DAILY_TASK_BLOCK_TEMPLATE_DESCRIPTION
      : (typeof shape.description === 'string' ? shape.description : ''),
    floating: isDailyTaskBlockField ? false : Boolean(shape.floating),
    isSceneIdentity: normalizedSceneIdentityRole !== 'off',
    sceneIdentityRole: normalizedSceneIdentityRole,
    sceneJumpTarget: typeof shape.sceneJumpTarget === 'string'
      ? normalizeSceneJumpTargetText(shape.sceneJumpTarget)
      : (typeof shape.sceneJumpTarget === 'number' ? String(shape.sceneJumpTarget) : ''),
    contentDirection: normalizeShapeContentDirection(shape.contentDirection),
    imageMatchRole: normalizedImageMatchRole,
    pixelTolerance: normalizeShapePixelTolerance(shape.pixelTolerance),
    ocrMatchRole: normalizedOcrMatchRole,
    ocrEnabled: Boolean(shape.ocrEnabled),
    ocrText: typeof shape.ocrText === 'string' ? shape.ocrText : '',
    ocrMatchMode: normalizeShapeOcrMatchMode(shape.ocrMatchMode),
    maskEnabled: Boolean(shape.maskEnabled),
    alphaMask: shape.alphaMask && typeof shape.alphaMask === 'object'
      ? {
          width: Number(shape.alphaMask.width) || 0,
          height: Number(shape.alphaMask.height) || 0,
          dataUrl: typeof shape.alphaMask.dataUrl === 'string' ? shape.alphaMask.dataUrl : '',
        }
      : null,
    toleranceEnabled: Boolean(shape.toleranceEnabled),
    toleranceRange: shape.toleranceRange && typeof shape.toleranceRange === 'object'
      ? {
          width: Number(shape.toleranceRange.width) || 0,
          height: Number(shape.toleranceRange.height) || 0,
          minDataUrl: typeof shape.toleranceRange.minDataUrl === 'string' ? shape.toleranceRange.minDataUrl : '',
          maxDataUrl: typeof shape.toleranceRange.maxDataUrl === 'string' ? shape.toleranceRange.maxDataUrl : '',
        }
      : null,
    discriminatorEnabled: Boolean(shape.discriminatorEnabled),
    discriminator: shape.discriminator && typeof shape.discriminator === 'object'
      ? {
          targetImageId: typeof shape.discriminator.targetImageId === 'number' ? shape.discriminator.targetImageId : null,
        }
      : null,
    discriminatorGroupId: typeof shape.discriminatorGroupId === 'string' ? shape.discriminatorGroupId : null,
    discriminatorValue: typeof shape.discriminatorValue === 'string' ? shape.discriminatorValue : '',
    x: typeof shape.x === 'number' ? shape.x : 0,
    y: typeof shape.y === 'number' ? shape.y : 0,
    w: typeof shape.w === 'number' ? shape.w : 0.1,
    h: typeof shape.h === 'number' ? shape.h : 0.1,
    children: normalizeShapes(shape.children ?? [], isDailyTaskBlockTemplate),
  }];
});

const normalizeAssetTree = (nodes: GameWindow3AssetNode[]): GameWindow3AssetNode[] => nodes.map((node) => {
  if (node.type === 'folder') {
    return {
      ...node,
      children: normalizeAssetTree(node.children ?? []),
    };
  }
  const normalizedNode = { ...node } as GameWindow3AssetNode & { occlusionMaskEnabled?: boolean };
  delete normalizedNode.occlusionMaskEnabled;
  return {
    ...normalizedNode,
    filename: typeof node.filename === 'string' ? node.filename : undefined,
    imageDataUrl: !node.filename && typeof node.imageDataUrl === 'string' ? node.imageDataUrl : undefined,
    width: typeof node.width === 'number' ? node.width : undefined,
    height: typeof node.height === 'number' ? node.height : undefined,
    shapes: normalizeShapes(node.shapes ?? []),
    children: normalizeAssetTree(node.children ?? []),
  };
});

const loadGlobalOcclusionMaskEnabled = () => {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(GAME_WINDOW3_OCCLUSION_MASK_ENABLED_KEY) === 'true';
};

const loadAssetTree = (): GameWindow3AssetNode[] => {
  if (typeof window === 'undefined') return createDefaultAssetTree();
  const raw = window.localStorage.getItem(GAME_WINDOW3_STORAGE_KEY);
  if (!raw) return createDefaultAssetTree();
  try {
    const parsed = JSON.parse(raw) as GameWindow3AssetNode[];
    return Array.isArray(parsed) && parsed.length ? normalizeAssetTree(parsed) : createDefaultAssetTree();
  } catch {
    return createDefaultAssetTree();
  }
};

const sendRemoteKeyevent = async (key: string) => {
  if (!selectedEntryId.value) return;
  try {
    await keyeventFanxiuGameWindow2({
      entry_id: selectedEntryId.value,
      key,
    });
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

const sendRemoteText = async (text: string) => {
  if (!selectedEntryId.value || !text) return;
  try {
    await textFanxiuGameWindow2({
      entry_id: selectedEntryId.value,
      text,
    });
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  }
};

globalOcclusionMaskEnabled.value = loadGlobalOcclusionMaskEnabled();

const assetTree = ref<GameWindow3AssetNode[]>(loadAssetTree());
const assetTreeBackendHydrating = ref(false);
const assetTreeBackendUpdatedAt = ref(0);
let assetTreeSaveTimer: ReturnType<typeof setTimeout> | null = null;

const cloneAssetTree = (nodes: GameWindow3AssetNode[]) => (
  JSON.parse(JSON.stringify(nodes)) as GameWindow3AssetNode[]
);

const assetNodeMergeKey = (node: GameWindow3AssetNode) => (
  node.type === 'image'
    ? `image:${node.filename || node.title}`
    : `folder:${node.title}`
);

const collectAssetNodeMergeKeys = (nodes: GameWindow3AssetNode[], keys = new Set<string>()) => {
  for (const node of nodes) {
    keys.add(assetNodeMergeKey(node));
    collectAssetNodeMergeKeys(node.children ?? [], keys);
  }
  return keys;
};

const findMergeTargetFolder = (nodes: GameWindow3AssetNode[], title: string) => {
  for (const node of nodes) {
    if (node.type === 'folder' && node.title === title) return node;
    const found = findMergeTargetFolder(node.children ?? [], title);
    if (found) return found;
  }
  return null;
};

const findAssetNodeByMergeKey = (nodes: GameWindow3AssetNode[], key: string): GameWindow3AssetNode | null => {
  for (const node of nodes) {
    if (assetNodeMergeKey(node) === key) return node;
    const found = findAssetNodeByMergeKey(node.children ?? [], key);
    if (found) return found;
  }
  return null;
};

const mergeDuplicateAssetNode = (target: GameWindow3AssetNode, source: GameWindow3AssetNode) => {
  if (!target.filename && source.filename) target.filename = source.filename;
  if (!target.imageDataUrl && source.imageDataUrl) target.imageDataUrl = source.imageDataUrl;
  if (!target.width && source.width) target.width = source.width;
  if (!target.height && source.height) target.height = source.height;
  if (target.type === 'folder' && source.type === 'folder') {
    target.children = mergeAssetTreeNodes(target.children ?? [], source.children ?? []);
  }
  if (target.type === 'image' && source.type === 'image') {
    const shapeIds = new Set((target.shapes ?? []).map((shape) => shape.id));
    const extraShapes = (source.shapes ?? []).filter((shape) => !shapeIds.has(shape.id));
    if (extraShapes.length) {
      target.shapes = [
        ...(target.shapes ?? []),
        ...(JSON.parse(JSON.stringify(extraShapes)) as GameWindow3Shape[]),
      ];
    }
  }
};

const removeAssetNodeByReference = (
  nodes: GameWindow3AssetNode[],
  target: GameWindow3AssetNode,
): boolean => {
  const index = nodes.indexOf(target);
  if (index >= 0) {
    nodes.splice(index, 1);
    return true;
  }
  return nodes.some((node) => removeAssetNodeByReference(node.children ?? [], target));
};

const compactDuplicateAssetNodes = (nodes: GameWindow3AssetNode[]) => {
  const merged = cloneAssetTree(nodes);
  const seen = new Map<string, GameWindow3AssetNode>();
  const visit = (items: GameWindow3AssetNode[]) => {
    for (const node of [...items]) {
      const key = assetNodeMergeKey(node);
      const previous = seen.get(key);
      if (previous && previous !== node) {
        mergeDuplicateAssetNode(node, previous);
        removeAssetNodeByReference(merged, previous);
        seen.set(key, node);
      } else {
        seen.set(key, node);
      }
      visit(node.children ?? []);
    }
  };
  visit(merged);
  return merged;
};

const mergeAssetTreeNodes = (baseNodes: GameWindow3AssetNode[], extraNodes: GameWindow3AssetNode[]) => {
  const merged = cloneAssetTree(baseNodes);
  const knownKeys = collectAssetNodeMergeKeys(merged);
  const appendMissing = (target: GameWindow3AssetNode[], incoming: GameWindow3AssetNode[]) => {
    for (const node of incoming) {
      const key = assetNodeMergeKey(node);
      const existingNode = findAssetNodeByMergeKey(merged, key);
      if (existingNode) mergeDuplicateAssetNode(existingNode, node);
      if (node.type === 'folder') {
        const targetFolder = findMergeTargetFolder(merged, node.title);
        if (targetFolder) {
          targetFolder.children = targetFolder.children ?? [];
          appendMissing(targetFolder.children, node.children ?? []);
          continue;
        }
      }
      if (knownKeys.has(key)) continue;
      const cloned = cloneAssetTree([node])[0];
      target.push(cloned);
      collectAssetNodeMergeKeys([cloned], knownKeys);
    }
  };
  appendMissing(merged, extraNodes);
  return compactDuplicateAssetNodes(merged);
};

const mergeEntryAssetTrees = (localTree: GameWindow3AssetNode[], backendTree: GameWindow3AssetNode[]) => {
  const mergedTree = mergeAssetTreeNodes(backendTree, localTree);
  return compactDuplicateAssetNodes(mergedTree);
};

const selectFirstAvailableAsset = () => {
  if (selectedAssetId.value && findAssetNode(assetTree.value, selectedAssetId.value)) return;
  selectedAssetId.value = findFirstImageNode(assetTree.value)?.id ?? null;
};

const flushAssetTreeToBackend = async (entryId: string, tree: GameWindow3AssetNode[]) => {
  try {
    const response = await saveFanxiuGameWindow3AssetTree(entryId, tree);
    assetTreeBackendUpdatedAt.value = Math.max(assetTreeBackendUpdatedAt.value, Number(response.updated_at) || 0);
  } catch {
    // 文件树仍保留在本地缓存；后端短暂失败时不打断标注操作。
  }
};

const scheduleAssetTreeBackendSave = () => {
  if (assetTreeBackendHydrating.value || !selectedEntryId.value) return;
  const entryId = selectedEntryId.value;
  const tree = JSON.parse(JSON.stringify(assetTree.value)) as GameWindow3AssetNode[];
  if (assetTreeSaveTimer) window.clearTimeout(assetTreeSaveTimer);
  assetTreeSaveTimer = window.setTimeout(() => {
    assetTreeSaveTimer = null;
    void flushAssetTreeToBackend(entryId, tree);
  }, 400);
};

const loadEntryAssetTree = async (entryId: string) => {
  if (!entryId) return;
  assetTreeBackendHydrating.value = true;
  try {
    const localTree = loadAssetTree();
    const response = await getFanxiuGameWindow3AssetTree(entryId);
    if (response.exists && Array.isArray(response.tree) && response.tree.length) {
      const backendTree = normalizeAssetTree(response.tree as GameWindow3AssetNode[]);
      const mergedTree = mergeEntryAssetTrees(localTree, backendTree);
      assetTree.value = mergedTree;
      if (JSON.stringify(mergedTree) !== JSON.stringify(backendTree)) {
        void flushAssetTreeToBackend(entryId, mergedTree);
      }
      assetTreeBackendUpdatedAt.value = Math.max(assetTreeBackendUpdatedAt.value, Number(response.updated_at) || 0);
    } else {
      assetTree.value = localTree;
      void flushAssetTreeToBackend(entryId, localTree);
    }
    selectFirstAvailableAsset();
    void nextTick(syncCanvas);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    assetTreeBackendHydrating.value = false;
  }
};

const refreshEntryAssetTreeIfChanged = async () => {
  if (!selectedEntryId.value || assetTreeBackendHydrating.value) return;
  const entryId = selectedEntryId.value;
  const selectedNode = selectedAssetNode.value;
  const selectedKey = selectedNode ? assetNodeMergeKey(selectedNode) : '';
  try {
    const response = await getFanxiuGameWindow3AssetTree(entryId);
    const backendUpdatedAt = Number(response.updated_at) || 0;
    if (!response.exists || !Array.isArray(response.tree) || backendUpdatedAt <= assetTreeBackendUpdatedAt.value) return;
    assetTreeBackendHydrating.value = true;
    const backendTree = normalizeAssetTree(response.tree as GameWindow3AssetNode[]);
    const mergedTree = mergeEntryAssetTrees(assetTree.value, backendTree);
    assetTree.value = mergedTree;
    assetTreeBackendUpdatedAt.value = backendUpdatedAt;
    if (selectedAssetId.value && findAssetNode(mergedTree, selectedAssetId.value)) return;
    const restoredNode = selectedKey ? findAssetNodeByMergeKey(mergedTree, selectedKey) : null;
    selectedAssetId.value = restoredNode?.id ?? findFirstImageNode(mergedTree)?.id ?? null;
  } catch {
    // 切换节点时的后台刷新不应打断用户选择。
  } finally {
    assetTreeBackendHydrating.value = false;
  }
};

const normalizeDiscriminatorGroups = (groups: DiscriminatorGroup[] = []): DiscriminatorGroup[] => groups.map((group) => ({
  id: typeof group.id === 'string' ? group.id : createAssetId('disc-group'),
  title: typeof group.title === 'string' ? group.title : '区分组',
  syncBox: group.syncBox !== false,
  members: Array.isArray(group.members)
    ? group.members
        .map((member) => ({
          imageId: Number(member.imageId),
          shapeId: typeof member.shapeId === 'string' ? member.shapeId : '',
          label: typeof member.label === 'string' ? member.label : '',
        }))
        .filter((member) => Number.isFinite(member.imageId) && member.shapeId)
    : [],
}));

const loadDiscriminatorGroups = (): DiscriminatorGroup[] => {
  if (typeof window === 'undefined') return [];
  const raw = window.localStorage.getItem(GAME_WINDOW3_DISCRIMINATOR_GROUPS_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as DiscriminatorGroup[];
    return Array.isArray(parsed) ? normalizeDiscriminatorGroups(parsed) : [];
  } catch {
    return [];
  }
};

const discriminatorGroups = ref<DiscriminatorGroup[]>(loadDiscriminatorGroups());

const normalizeStringIdArray = (value: unknown) => (
  Array.isArray(value)
    ? [...new Set(value.filter((id): id is string => typeof id === 'string' && Boolean(id)))]
    : []
);

const loadGameWindow3UiState = (): GameWindow3UiState => {
  if (typeof window === 'undefined') return {};
  const raw = window.localStorage.getItem(GAME_WINDOW3_UI_STATE_STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as GameWindow3UiState;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
};

const persistGameWindow3UiState = () => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_WINDOW3_UI_STATE_STORAGE_KEY, JSON.stringify({
    selectedAssetId: selectedAssetId.value,
    selectedShapeId: selectedShapeId.value,
    expandedAssetNodeIds: expandedAssetNodeIds.value,
    expandedShapeNodeIds: expandedShapeNodeIds.value,
  }));
};

const findAssetNode = (nodes: GameWindow3AssetNode[], id: string | null): GameWindow3AssetNode | null => {
  if (!id) return null;
  for (const node of nodes) {
    if (node.id === id) return node;
    const found = findAssetNode(node.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const findFirstImageNode = (nodes: GameWindow3AssetNode[]): GameWindow3AssetNode | null => {
  for (const node of nodes) {
    if (node.type === 'image') return node;
    const found = findFirstImageNode(node.children ?? []);
    if (found) return found;
  }
  return null;
};

const flattenAssetImages = (nodes: GameWindow3AssetNode[]): GameWindow3AssetNode[] => nodes.flatMap((node) => [
  ...(node.type === 'image' ? [node] : []),
  ...flattenAssetImages(node.children ?? []),
]);

const findAssetParentFolder = (
  nodes: GameWindow3AssetNode[],
  id: string | null,
  parent: GameWindow3AssetNode | null = null,
): GameWindow3AssetNode | null => {
  if (!id) return null;
  for (const node of nodes) {
    if (node.id === id) return parent;
    const found = findAssetParentFolder(node.children ?? [], id, node.type === 'folder' ? node : parent);
    if (found) return found;
  }
  return null;
};

const assetImageIdMark = (node: GameWindow3AssetNode) => {
  const source = node.filename || node.id;
  const filenameNumber = node.filename?.match(/(\d+)(?=\.[^.]+$|$)/)?.[1];
  if (filenameNumber) return '#' + String(Number(filenameNumber));
  const idTail = source.match(/([a-zA-Z0-9]{2,})$/)?.[1] || source;
  return '#' + idTail.slice(-6);
};

const assetNumericImageId = (node: GameWindow3AssetNode) => {
  if (node.type !== 'image') return null;
  const filenameNumber = node.filename?.match(/(\d+)(?=\.[^.]+$|$)/)?.[1];
  return filenameNumber ? Number(filenameNumber) : null;
};

const findAssetImageByNumericId = (nodes: GameWindow3AssetNode[], id: number | null): GameWindow3AssetNode | null => {
  if (id === null) return null;
  for (const node of nodes) {
    if (assetNumericImageId(node) === id) return node;
    const found = findAssetImageByNumericId(node.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const firstSceneJumpNumericTarget = (value: string | number | null | undefined) => {
  const firstToken = parseSceneJumpEntries(value)[0]?.label ?? '';
  const numeric = Number(firstToken.replace(/^#/, ''));
  return Number.isFinite(numeric) && firstToken ? numeric : null;
};

const normalizeSelectedShapeSceneJumpTarget = () => {
  const shape = selectedShape.value;
  if (!shape) return;
  shape.sceneJumpTarget = normalizeSceneJumpTargetText(shape.sceneJumpTarget);
};

const findImageNodeByShapeId = (nodes: GameWindow3AssetNode[], shapeId: string): GameWindow3AssetNode | null => {
  for (const node of nodes) {
    if (node.type === 'image' && findShapeById(node.shapes ?? [], shapeId)) return node;
    const found = findImageNodeByShapeId(node.children ?? [], shapeId);
    if (found) return found;
  }
  return null;
};

const findShapeGlobal = (shapeId: string) => {
  const image = findImageNodeByShapeId(assetTree.value, shapeId);
  const shape = image ? findShapeById(image.shapes ?? [], shapeId) : null;
  return image && shape ? { image, shape } : null;
};

const findAssetParentChildren = (
  nodes: GameWindow3AssetNode[],
  id: string | null,
): GameWindow3AssetNode[] | null => {
  if (!id) return null;
  if (nodes.some((node) => node.id === id)) return nodes;
  for (const node of nodes) {
    const found = findAssetParentChildren(node.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const selectedAssetNode = computed(() => findAssetNode(assetTree.value, selectedAssetId.value));
const assetContextMenuNode = computed(() => findAssetNode(assetTree.value, assetContextMenu.value.nodeId));
const selectedImageNode = computed(() => {
  const node = selectedAssetNode.value;
  return node?.type === 'image' ? node : null;
});
const selectedImageTitleText = computed(() => (
  selectedImageNode.value
    ? `${assetImageIdMark(selectedImageNode.value)} ${selectedImageNode.value.title}`
    : '未选择图片'
));
const selectedImagePreviewUrl = computed(() => {
  const image = selectedImageNode.value;
  if (!image) return '';
  return assetImagePreviewUrls.value[image.id] || image.imageDataUrl || '';
});
const selectedImagePreviewLoading = computed(() => {
  const image = selectedImageNode.value;
  return Boolean(image && image.filename && !selectedImagePreviewUrl.value && assetImagePreviewLoadingIds.value[image.id]);
});
const selectedImageShapes = computed(() => selectedImageNode.value?.shapes ?? []);
const isDrawableShape = (shape: GameWindow3Shape) => shape.kind !== 'group';
const flattenShapes = (shapes: GameWindow3Shape[]): GameWindow3Shape[] => shapes.flatMap((shape) => [
  shape,
  ...flattenShapes(shape.children ?? []),
]);
const isOcclusionAssetGroup = (node: GameWindow3AssetNode) => (
  node.type === 'folder' && node.title.trim() === '遮挡标记'
);
const collectOcclusionAssetImages = (
  nodes: GameWindow3AssetNode[],
  inOcclusionGroup = false,
): GameWindow3AssetNode[] => {
  const images: GameWindow3AssetNode[] = [];
  for (const node of nodes) {
    const nextInOcclusionGroup = inOcclusionGroup || isOcclusionAssetGroup(node);
    if (node.type === 'image' && nextInOcclusionGroup) images.push(node);
    images.push(...collectOcclusionAssetImages(node.children ?? [], nextInOcclusionGroup));
  }
  return images;
};
const findShapeById = (shapes: GameWindow3Shape[], id: string | null): GameWindow3Shape | null => {
  if (!id) return null;
  for (const shape of shapes) {
    if (shape.id === id) return shape;
    const found = findShapeById(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const filterExistingAssetNodeIds = (ids: string[]) => ids.filter((id) => Boolean(findAssetNode(assetTree.value, id)));
const filterExistingShapeNodeIds = (ids: string[]) => {
  const image = selectedImageNode.value;
  if (!image) return [];
  return ids.filter((id) => Boolean(findShapeById(image.shapes ?? [], id)));
};

const collectAssetFolderIds = (nodes: GameWindow3AssetNode[]): string[] => nodes.flatMap((node) => [
  ...(node.type === 'folder' ? [node.id] : []),
  ...collectAssetFolderIds(node.children ?? []),
]);

const collectExpandableShapeIds = (shapes: GameWindow3Shape[]): string[] => shapes.flatMap((shape) => [
  ...((shape.children ?? []).length ? [shape.id] : []),
  ...collectExpandableShapeIds(shape.children ?? []),
]);

const restoreGameWindow3UiState = () => {
  const state = loadGameWindow3UiState();
  const savedAssetId = typeof state.selectedAssetId === 'string' ? state.selectedAssetId : null;
  const savedAsset = savedAssetId ? findAssetNode(assetTree.value, savedAssetId) : null;
  selectedAssetId.value = savedAsset?.id ?? findFirstImageNode(assetTree.value)?.id ?? null;
  expandedAssetNodeIds.value = Array.isArray(state.expandedAssetNodeIds)
    ? filterExistingAssetNodeIds(normalizeStringIdArray(state.expandedAssetNodeIds))
    : collectAssetFolderIds(assetTree.value);

  const image = selectedImageNode.value;
  const savedShapeId = typeof state.selectedShapeId === 'string' ? state.selectedShapeId : null;
  selectedShapeId.value = image && savedShapeId && findShapeById(image.shapes ?? [], savedShapeId)
    ? savedShapeId
    : (image ? flattenShapes(image.shapes ?? [])[0]?.id ?? null : null);
  expandedShapeNodeIds.value = Array.isArray(state.expandedShapeNodeIds)
    ? filterExistingShapeNodeIds(normalizeStringIdArray(state.expandedShapeNodeIds))
    : collectExpandableShapeIds(image?.shapes ?? []);
};

const setExpandedNodeId = (ids: string[], id: string, expanded: boolean) => {
  if (!id) return ids;
  if (expanded) return ids.includes(id) ? ids : [...ids, id];
  return ids.filter((item) => item !== id);
};

const setAssetNodeExpanded = (id: string, expanded: boolean) => {
  expandedAssetNodeIds.value = setExpandedNodeId(expandedAssetNodeIds.value, id, expanded);
};

const setShapeNodeExpanded = (id: string, expanded: boolean) => {
  expandedShapeNodeIds.value = setExpandedNodeId(expandedShapeNodeIds.value, id, expanded);
};

const findShapeParentChildren = (shapes: GameWindow3Shape[], id: string | null): GameWindow3Shape[] | null => {
  if (!id) return null;
  if (shapes.some((shape) => shape.id === id)) return shapes;
  for (const shape of shapes) {
    const found = findShapeParentChildren(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const findShapeParentShape = (shapes: GameWindow3Shape[], id: string | null): GameWindow3Shape | null => {
  if (!id) return null;
  for (const shape of shapes) {
    if ((shape.children ?? []).some((child) => child.id === id)) return shape;
    const found = findShapeParentShape(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const isShapeDescendantOf = (shape: GameWindow3Shape, ancestorId: string): boolean => (
  (shape.children ?? []).some((child) => child.id === ancestorId || isShapeDescendantOf(child, ancestorId))
);

const cloneShapeTreeWithNewIds = (shape: GameWindow3Shape): GameWindow3Shape => ({
  ...JSON.parse(JSON.stringify(shape)) as GameWindow3Shape,
  id: createAssetId('shape'),
  children: (shape.children ?? []).map(cloneShapeTreeWithNewIds),
});
const annotationShapes = computed(() => flattenShapes(selectedImageShapes.value).filter(isDrawableShape));
const occlusionMaskShapes = computed(() => (
  collectOcclusionAssetImages(assetTree.value)
    .flatMap((image) => flattenShapes(image.shapes ?? []))
    .filter(isDrawableShape)
));
const occlusionOverlayShapes = computed(() => (
  globalOcclusionMaskEnabled.value ? occlusionMaskShapes.value : []
));
const selectedShape = computed(() => findShapeById(selectedImageShapes.value, selectedShapeId.value));
const selectedShapeCopyCount = computed(() => {
  if (selectedShapeIds.value.length) return selectedShapeIds.value.length;
  return selectedShape.value ? 1 : 0;
});
const selectedShapeDetectResult = computed(() => (
  selectedShapeId.value ? shapeDetectResults.value[selectedShapeId.value] || '' : ''
));
const selectedShapeImageMatchRole = computed(() => normalizeShapeMatchRole(selectedShape.value?.imageMatchRole));
const selectedShapeOcrMatchRole = computed(() => normalizeShapeMatchRole(selectedShape.value?.ocrMatchRole, selectedShape.value?.ocrEnabled ? 'required' : 'off'));
const selectedShapeSceneIdentityRole = computed(() => normalizeShapeMatchRole(selectedShape.value?.sceneIdentityRole, selectedShape.value?.isSceneIdentity ? 'required' : 'off'));
const cycleSelectedShapeMatchRole = (kind: 'image' | 'ocr') => {
  const shape = selectedShape.value;
  if (!shape || !isDrawableShape(shape)) return;
  if (kind === 'image') {
    shape.imageMatchRole = nextShapeMatchRole(normalizeShapeMatchRole(shape.imageMatchRole));
    return;
  }
  shape.ocrMatchRole = nextShapeMatchRole(normalizeShapeMatchRole(shape.ocrMatchRole, shape.ocrEnabled ? 'required' : 'off'));
  shape.ocrEnabled = shape.ocrMatchRole !== 'off';
};
const cycleSelectedShapeSceneIdentityRole = () => {
  const shape = selectedShape.value;
  if (!shape || !isDrawableShape(shape)) return;
  shape.sceneIdentityRole = nextShapeMatchRole(normalizeShapeMatchRole(shape.sceneIdentityRole, shape.isSceneIdentity ? 'required' : 'off'));
  shape.isSceneIdentity = shape.sceneIdentityRole !== 'off';
  if (shape.sceneIdentityRole !== 'off' && !shapePrimaryMatchKind(shape)) {
    shape.imageMatchRole = shape.sceneIdentityRole;
  }
};
const canDetectSelectedShape = computed(() => Boolean(
  selectedEntryId.value
  && selectedImageNode.value?.filename
  && selectedShape.value
  && isDrawableShape(selectedShape.value)
  && shapePrimaryMatchKind(selectedShape.value)
  && !shapeDetectingId.value
));
const shapeToMatchBox = (shape: GameWindow3Shape, image: GameWindow3AssetNode): FanxiuGameWindow2MatchBox => {
  const width = image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  return {
    name: shape.title || 'shape',
    x: Math.round(shape.x * width),
    y: Math.round(shape.y * height),
    w: Math.round(shape.w * width),
    h: Math.round(shape.h * height),
  };
};

const shapePixelTolerance = (shape: GameWindow3Shape) => normalizeShapePixelTolerance(shape.pixelTolerance);
const shapeImageMatchRole = (shape: GameWindow3Shape) => normalizeShapeMatchRole(shape.imageMatchRole);
const shapeOcrMatchRole = (shape: GameWindow3Shape) => normalizeShapeMatchRole(shape.ocrMatchRole, shape.ocrEnabled ? 'required' : 'off');
type ShapeMatchKind = 'image' | 'ocr';
const shapeMatchRoleForKind = (shape: GameWindow3Shape, kind: ShapeMatchKind) => (
  kind === 'image' ? shapeImageMatchRole(shape) : shapeOcrMatchRole(shape)
);
const shapeActiveMatchKinds = (shape: GameWindow3Shape): ShapeMatchKind[] => [
  ...(shapeImageMatchRole(shape) !== 'off' ? ['image' as const] : []),
  ...(shapeOcrMatchRole(shape) !== 'off' && shape.ocrText?.trim() ? ['ocr' as const] : []),
];
const shapeCanFloat = (shape: GameWindow3Shape) => Boolean(shapeActiveMatchKinds(shape).length);
const shapePrimaryMatchKind = (shape: GameWindow3Shape): ShapeMatchKind | null => shapeActiveMatchKinds(shape)[0] ?? null;
const shapeHasRequiredMatch = (shape: GameWindow3Shape) => (
  shapeImageMatchRole(shape) === 'required'
  || (shapeOcrMatchRole(shape) === 'required' && Boolean(shape.ocrText?.trim()))
);
const shapeSceneIdentityRole = (shape: GameWindow3Shape) => normalizeShapeMatchRole(shape.sceneIdentityRole, shape.isSceneIdentity ? 'required' : 'off');
const isSceneIdentityShape = (shape: GameWindow3Shape) => shapeSceneIdentityRole(shape) !== 'off';

const shapeBoxIou = (a: GameWindow3Shape, b: GameWindow3Shape) => {
  const left = Math.max(a.x, b.x);
  const top = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.w, b.x + b.w);
  const bottom = Math.min(a.y + a.h, b.y + b.h);
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top);
  const union = Math.max(0, a.w * a.h) + Math.max(0, b.w * b.h) - intersection;
  return union > 0 ? intersection / union : 0;
};

const shapeBoxCenterDistance = (a: GameWindow3Shape, b: GameWindow3Shape) => {
  const dx = (a.x + a.w / 2) - (b.x + b.w / 2);
  const dy = (a.y + a.h / 2) - (b.y + b.h / 2);
  return Math.sqrt(dx * dx + dy * dy);
};

const shapeDiscriminatorCandidateShapes = computed(() => {
  const image = findAssetImageByNumericId(assetTree.value, shapeDiscriminatorNewImageId.value);
  const source = selectedShape.value;
  if (!image || !source) return [];
  return flattenShapes(image.shapes ?? [])
    .filter(isDrawableShape)
    .map((shape) => {
      const iou = shapeBoxIou(source, shape);
      const distance = shapeBoxCenterDistance(source, shape);
      return {
        shape,
        iou,
        distance,
        label: shape.title || 'shape',
      };
    })
    .sort((a, b) => (b.iou - a.iou) || (a.distance - b.distance));
});

const buildOcclusionAlphaMaskDataUrl = (
  image: GameWindow3AssetNode,
  targetShape: GameWindow3Shape,
  box: FanxiuGameWindow2MatchBox,
) => {
  if (!globalOcclusionMaskEnabled.value || box.w <= 0 || box.h <= 0) return '';
  const occlusionShapes = occlusionMaskShapes.value
    .filter((shape) => isDrawableShape(shape) && shape.id !== targetShape.id);
  if (!occlusionShapes.length) return '';
  const imageWidth = image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const imageHeight = image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(box.w));
  canvas.height = Math.max(1, Math.round(box.h));
  const context = canvas.getContext('2d');
  if (!context) return '';
  context.fillStyle = '#fff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#000';
  for (const shape of occlusionShapes) {
    const left = shape.x * imageWidth - box.x;
    const top = shape.y * imageHeight - box.y;
    const width = shape.w * imageWidth;
    const height = shape.h * imageHeight;
    if (left >= canvas.width || top >= canvas.height || left + width <= 0 || top + height <= 0) continue;
    context.fillRect(left, top, width, height);
  }
  return canvas.toDataURL('image/png');
};
const annotationCanvasStyle = computed(() => {
  const width = selectedImageNode.value?.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || FALLBACK_FRAME_WIDTH;
  const height = selectedImageNode.value?.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || FALLBACK_FRAME_HEIGHT;
  const scale = displayScale.value / 100;
  const stageWidth = Math.max(1, Math.round(width * scale));
  const stageHeight = Math.max(1, Math.round(height * scale));
  return {
    width: `${stageWidth}px`,
    height: `${stageHeight}px`,
    aspectRatio: `${Math.max(width, 1)} / ${Math.max(height, 1)}`,
  };
});
const annotationContentStyle = computed(() => ({
  ...annotationCanvasStyle.value,
  transform: `translate(${screenshotPanX.value}px, ${screenshotPanY.value}px) scale(${screenshotZoomPercent.value / 100})`,
}));

restoreGameWindow3UiState();

watch(assetTree, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_WINDOW3_STORAGE_KEY, JSON.stringify(value));
  expandedAssetNodeIds.value = filterExistingAssetNodeIds(expandedAssetNodeIds.value);
  scheduleAssetTreeBackendSave();
}, { deep: true });

watch(discriminatorGroups, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_WINDOW3_DISCRIMINATOR_GROUPS_KEY, JSON.stringify(value));
}, { deep: true });

watch(globalOcclusionMaskEnabled, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_WINDOW3_OCCLUSION_MASK_ENABLED_KEY, value ? 'true' : 'false');
});
watch(stepperTaskDefinitions, (value) => {
  if (typeof window === 'undefined') return;
  const normalized = normalizeStepperTaskDefinitions(value);
  window.localStorage.setItem(GAME_WINDOW3_STEPPER_TASKS_STORAGE_KEY, JSON.stringify(normalized));
  if (!normalized.some((task) => stepperTaskFunctionIdOf(task) === selectedStepperFunctionId.value)) {
    selectedStepperFunctionId.value = normalized[0] ? stepperTaskFunctionIdOf(normalized[0]) : 'go_scene';
  }
  if (!normalized.some((task) => task.id === selectedStepperTaskId.value && stepperTaskFunctionIdOf(task) === selectedStepperFunctionId.value)) {
    selectedStepperTaskId.value = normalized.find((task) => stepperTaskFunctionIdOf(task) === selectedStepperFunctionId.value)?.id ?? normalized[0]?.id ?? '';
  }
}, { deep: true });
watch(selectedStepperFunctionId, (value) => {
  if (typeof window !== 'undefined') window.localStorage.setItem(GAME_WINDOW3_STEPPER_SELECTED_FUNCTION_STORAGE_KEY, value);
  const fn = selectedStepperFunctionDefinition.value;
  if (!fn) {
    selectedStepperTaskId.value = '';
    return;
  }
  selectedStepperTaskId.value = fn.presets.length
    ? (fn.presets.some((preset) => preset.id === selectedStepperTaskId.value) ? selectedStepperTaskId.value : fn.presets[0].id)
    : (fn.task?.id ?? '');
});
watch(selectedStepperTaskId, (value) => {
  if (typeof window === 'undefined') return;
  if (value) {
    window.localStorage.setItem(GAME_WINDOW3_STEPPER_SELECTED_TASK_STORAGE_KEY, value);
    const task = stepperTaskDefinitions.value.find((item) => item.id === value);
    if (task && stepperTaskFunctionIdOf(task) !== selectedStepperFunctionId.value) selectedStepperFunctionId.value = stepperTaskFunctionIdOf(task);
  } else {
    window.localStorage.removeItem(GAME_WINDOW3_STEPPER_SELECTED_TASK_STORAGE_KEY);
  }
});

watch(selectedImageNode, (node) => {
  const firstShape = node ? flattenShapes(node.shapes ?? [])[0] ?? null : null;
  selectedShapeId.value = node && selectedShapeId.value && findShapeById(node.shapes ?? [], selectedShapeId.value)
    ? selectedShapeId.value
    : firstShape?.id ?? null;
  selectedShapeIds.value = [];
  shapeSelectionAnchorId.value = selectedShapeId.value;
  expandedShapeNodeIds.value = filterExistingShapeNodeIds(expandedShapeNodeIds.value);
  shapeDetectResults.value = {};
  shapeDetectLiveBoxes.value = [];
  drawOverlay();
  resetScreenshotViewState();
  void ensureSelectedImagePreview();
});

watch(selectedShapeId, (id) => {
  shapeDetectLiveBoxes.value = [];
  drawOverlay();
  if (!id || !shapeDetectResults.value[id]) return;
  const next = { ...shapeDetectResults.value };
  delete next[id];
  shapeDetectResults.value = next;
});

watch(
  [selectedAssetId, selectedShapeId, expandedAssetNodeIds, expandedShapeNodeIds],
  persistGameWindow3UiState,
  { deep: true },
);

watch(stepperLogs, () => {
  if (stepperLogPage.value > stepperLogPageCount.value) goStepperLogLastPage();
}, { deep: true });

watch(stepperLogDialogVisible, (visible) => {
  if (visible) goStepperLogLastPage();
});

watch(shapeDiscriminatorNewImageId, () => {
  const firstCandidate = shapeDiscriminatorCandidateShapes.value[0];
  shapeDiscriminatorNewShapeId.value = firstCandidate?.shape.id ?? '';
});

watch(shapeDiscriminatorNewShapeId, (shapeId) => {
  if (!shapeId && shapeDiscriminatorNewImageId.value) {
    const firstCandidate = shapeDiscriminatorCandidateShapes.value[0];
    shapeDiscriminatorNewShapeId.value = firstCandidate?.shape.id ?? '';
  }
});

const getAssetInsertContext = () => {
  const siblings = findAssetParentChildren(assetTree.value, selectedAssetId.value) ?? assetTree.value;
  const selectedIndex = selectedAssetId.value ? siblings.findIndex((node) => node.id === selectedAssetId.value) : -1;
  return {
    siblings,
    insertIndex: selectedIndex >= 0 ? selectedIndex + 1 : siblings.length,
  };
};

const insertAssetNodeAfterSelection = (node: GameWindow3AssetNode) => {
  const { siblings, insertIndex } = getAssetInsertContext();
  siblings.splice(insertIndex, 0, node);
  selectedAssetId.value = node.id;
};

const addAssetFolder = () => {
  const { siblings } = getAssetInsertContext();
  const folderCount = siblings.filter((node) => node.type === 'folder').length + 1;
  const node: GameWindow3AssetNode = {
    id: createAssetId('folder'),
    type: 'folder',
    title: '分组' + folderCount,
    children: [],
  };
  insertAssetNodeAfterSelection(node);
};

const addAssetImage = () => {
  const { siblings } = getAssetInsertContext();
  const imageCount = siblings.filter((node) => node.type === 'image').length + 1;
  const node = createAssetImageNode('图片' + imageCount);
  insertAssetNodeAfterSelection(node);
};

const blobToDataUrl = (blob: Blob) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(blob);
});

const getAssetImageDataUrl = async (image: GameWindow3AssetNode) => {
  if (assetImagePreviewUrls.value[image.id]) return assetImagePreviewUrls.value[image.id];
  if (image.imageDataUrl) return image.imageDataUrl;
  if (!selectedEntryId.value || !image.filename) return '';
  assetImagePreviewLoadingIds.value = {
    ...assetImagePreviewLoadingIds.value,
    [image.id]: true,
  };
  try {
    const blob = await getFanxiuGameWindow2Screenshot(selectedEntryId.value, image.filename);
    const dataUrl = await blobToDataUrl(blob);
    assetImagePreviewUrls.value = {
      ...assetImagePreviewUrls.value,
      [image.id]: dataUrl,
    };
    return dataUrl;
  } finally {
    const next = { ...assetImagePreviewLoadingIds.value };
    delete next[image.id];
    assetImagePreviewLoadingIds.value = next;
  }
};

const ensureSelectedImagePreview = async () => {
  const image = selectedImageNode.value;
  if (!image || selectedImagePreviewUrl.value) return;
  try {
    await getAssetImageDataUrl(image);
  } catch {
    // Preview loading is opportunistic; matching can still use the saved filename.
  }
};

const insertSavedFrameNode = (node: GameWindow3AssetNode) => {
  const selectedNode = selectedAssetNode.value;
  if (selectedNode?.type === 'folder') {
    selectedNode.children = selectedNode.children ?? [];
    selectedNode.children.push(node);
    expandedAssetNodeIds.value = setExpandedNodeId(expandedAssetNodeIds.value, selectedNode.id, true);
    selectedAssetId.value = node.id;
    return;
  }
  insertAssetNodeAfterSelection(node);
};

const addSavedFrameToAssetTree = (node: GameWindow3AssetNode) => {
  insertSavedFrameNode(node);
};

const saveFrameDataUrlToAssetTree = async (currentFrameDataUrl: string) => {
  if (!selectedEntryId.value) return null;
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
    current_frame_data_url: currentFrameDataUrl,
  });
  const node = createAssetImageNode(result.filename, {
    filename: result.filename,
    width: result.width,
    height: result.height,
  });
  assetImagePreviewUrls.value = {
    ...assetImagePreviewUrls.value,
    [node.id]: currentFrameDataUrl,
  };
  addSavedFrameToAssetTree(node);
  return node;
};

const gameMacroFrameTargetText = (image: GameWindow3AssetNode) => {
  const numeric = assetNumericImageId(image);
  return numeric !== null ? String(numeric) : image.title.trim();
};

const setGameMacroPendingJumpTarget = (targetImage: GameWindow3AssetNode) => {
  const pending = gameMacroPendingJump.value;
  if (!pending) return;
  const sourceImage = findAssetNode(assetTree.value, pending.imageId);
  const shape = sourceImage?.type === 'image' ? findShapeById(sourceImage.shapes ?? [], pending.shapeId) : null;
  if (!shape) {
    gameMacroPendingJump.value = null;
    return;
  }
  const label = gameMacroFrameTargetText(targetImage);
  if (label) shape.sceneJumpTarget = serializeSceneJumpEntries([{ label, count: 1 }]);
  gameMacroPendingJump.value = null;
};

const findOrCreateGameMacroFrame = async (currentFrameDataUrl: string) => {
  const candidates = flattenAssetImages(assetTree.value)
    .filter((image) => image.filename && isStepperSceneAsset(image));
  if (candidates.length) {
    const result = await matchStepperLayer(candidates, currentFrameDataUrl, 4);
    const best = result.candidates.find((candidate) => candidate.score >= STEPPER_SCENE_MATCH_THRESHOLD);
    if (best) return { image: best.image, created: false, score: best.score };
  }
  const image = await saveFrameDataUrlToAssetTree(currentFrameDataUrl);
  if (!image) throw new Error('保存录制帧失败');
  return { image, created: true, score: 0 };
};

const boxToShapeRect = (
  image: GameWindow3AssetNode,
  box: { x: number; y: number; w: number; h: number },
) => {
  const width = image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  return {
    x: clamp(box.x / width, 0, 1),
    y: clamp(box.y / height, 0, 1),
    w: clamp(box.w / width, 0.001, 1),
    h: clamp(box.h / height, 0.001, 1),
  };
};

const pointShapeBox = (point: VisualPoint, image: GameWindow3AssetNode) => {
  const size = gameMacroConfig.value.defaultShapeSize;
  const width = image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || size;
  const height = image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || size;
  const w = Math.min(size, width);
  const h = Math.min(size, height);
  return {
    x: Math.round(clamp(point.x - w / 2, 0, Math.max(0, width - w))),
    y: Math.round(clamp(point.y - h / 2, 0, Math.max(0, height - h))),
    w,
    h,
  };
};

const dragShapeBox = (start: VisualPoint, end: VisualPoint, image: GameWindow3AssetNode) => {
  const size = gameMacroConfig.value.defaultShapeSize;
  const width = image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || size;
  const height = image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || size;
  const minX = Math.min(start.x, end.x);
  const maxX = Math.max(start.x, end.x);
  const minY = Math.min(start.y, end.y);
  const maxY = Math.max(start.y, end.y);
  const x = Math.round(clamp(minX - size / 2, 0, Math.max(0, width - 1)));
  const y = Math.round(clamp(minY - size / 2, 0, Math.max(0, height - 1)));
  const right = Math.round(clamp(maxX + size / 2, x + 1, width));
  const bottom = Math.round(clamp(maxY + size / 2, y + 1, height));
  return { x, y, w: right - x, h: bottom - y };
};

const dragDirectionOf = (start: VisualPoint, end: VisualPoint): GameWindow3Shape['contentDirection'] => {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left';
  return dy >= 0 ? 'down' : 'up';
};

const gameMacroDragDurationMs = (durationMs: number) => (
  gameMacroConfig.value.dragDurationMode === 'fixed'
    ? gameMacroConfig.value.defaultDragDurationMs
    : Math.round(durationMs)
);

const buildGameMacroFallbackBox = (
  image: GameWindow3AssetNode,
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null,
): FanxiuGameWindow2MatchBox => {
  const box = action === 'drag' && endPoint ? dragShapeBox(point, endPoint, image) : pointShapeBox(point, image);
  return { name: action === 'drag' ? '拖拽' : '点击', ...box };
};

const gameMacroFrameSize = (image: GameWindow3AssetNode) => ({
  width: image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1,
  height: image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1,
});

const applyGameMacroShapeAnnotation = (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  annotation: GameMacroShapeAnnotation,
  action: 'click' | 'drag',
  durationMs: number,
) => {
  const rect = boxToShapeRect(image, annotation.box);
  shape.x = rect.x;
  shape.y = rect.y;
  shape.w = rect.w;
  shape.h = rect.h;
  const label = (annotation.label || '').trim();
  if (label) shape.title = label;
  const descriptions: string[] = [];
  if (action === 'drag') {
    const mode = gameMacroConfig.value.dragDurationMode;
    descriptions.push(`duration=${gameMacroDragDurationMs(durationMs)}ms`);
    descriptions.push(`duration_mode=${mode}`);
  }
  if (annotation.usedAi) {
    descriptions.push(`ai_confidence=${Math.round((annotation.confidence ?? 0) * 100)}%`);
  }
  shape.description = descriptions.join('\n');
};

const createRecordedGameShape = (
  image: GameWindow3AssetNode,
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null,
  durationMs: number,
  annotation?: GameMacroShapeAnnotation,
) => {
  image.shapes ??= [];
  const existingShapes = flattenShapes(image.shapes).filter(isDrawableShape);
  const shouldMarkScene = gameMacroConfig.value.markFirstShapeAsSceneIdentity
    && !existingShapes.some(isSceneIdentityShape);
  const box = annotation?.box ?? buildGameMacroFallbackBox(image, action, point, endPoint);
  const rect = boxToShapeRect(image, box);
  const shape: GameWindow3Shape = {
    id: createAssetId('shape'),
    kind: 'shape',
    title: (annotation?.label || '').trim() || (action === 'drag' ? '拖拽' : '点击'),
    description: '',
    floating: false,
    isSceneIdentity: shouldMarkScene,
    sceneIdentityRole: shouldMarkScene ? 'required' : 'off',
    sceneJumpTarget: '',
    contentDirection: action === 'drag' && endPoint ? dragDirectionOf(point, endPoint) : 'none',
    imageMatchRole: shouldMarkScene ? 'required' : 'off',
    pixelTolerance: 5,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    maskEnabled: false,
    alphaMask: null,
    toleranceEnabled: false,
    toleranceRange: null,
    discriminatorEnabled: false,
    discriminator: null,
    discriminatorGroupId: null,
    discriminatorValue: '',
    ...rect,
    children: [],
  };
  applyGameMacroShapeAnnotation(image, shape, annotation ?? { box }, action, durationMs);
  image.shapes.push(shape);
  selectedAssetId.value = image.id;
  selectedShapeId.value = shape.id;
  gameMacroPendingJump.value = { imageId: image.id, shapeId: shape.id };
  return shape;
};

const refineRecordedGameShapeWithAi = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null,
  durationMs: number,
  currentFrameDataUrl: string,
  fallbackBox: FanxiuGameWindow2MatchBox,
) => {
  const size = gameMacroFrameSize(image);
  const direction = action === 'drag' && endPoint ? dragDirectionOf(point, endPoint) : 'none';
  let response: FanxiuGameWindow3MacroAnnotateResponse | null = null;
  try {
    gameMacroStatusText.value = `录制宏：${shape.title} 已生成，AI 标注中`;
    response = await annotateFanxiuGameWindow3MacroShape({
      image_data_url: currentFrameDataUrl,
      action,
      start: point,
      end: endPoint,
      fallback_box: fallbackBox,
      frame_width: size.width,
      frame_height: size.height,
      duration_ms: action === 'drag' ? gameMacroDragDurationMs(durationMs) : Math.round(durationMs),
      direction,
    });
  } catch (error) {
    gameMacroStatusText.value = `录制宏：AI 标注失败，保留工程框（${getErrorMessage(error)}）`;
    return;
  }
  if (!response.ok || !response.used_ai) {
    gameMacroStatusText.value = response.reason
      ? `录制宏：AI 标注不可用，保留工程框（${response.reason}）`
      : '录制宏：AI 标注不可用，保留工程框';
    return;
  }
  applyGameMacroShapeAnnotation(
    image,
    shape,
    {
      box: response.box,
      label: response.label,
      confidence: response.confidence,
      usedAi: true,
    },
    action,
    durationMs,
  );
  selectedAssetId.value = image.id;
  selectedShapeId.value = shape.id;
  gameMacroStatusText.value = `录制宏：AI 已修正 ${shape.title}（${Math.round(response.confidence * 100)}%）`;
};

const deleteSelectedAsset = async () => {
  const node = selectedAssetNode.value;
  const parent = findAssetParentChildren(assetTree.value, selectedAssetId.value);
  if (!node || !parent) return;
  await ElMessageBox.confirm('删除“' + node.title + '”？', '删除节点', { type: 'warning' });
  const index = parent.findIndex((item) => item.id === node.id);
  if (index >= 0) parent.splice(index, 1);
  selectedAssetId.value = findFirstImageNode(assetTree.value)?.id ?? null;
};

const selectAssetNode = (node: GameWindow3AssetNode) => {
  closeAssetContextMenu();
  selectedAssetId.value = node.id;
  void refreshEntryAssetTreeIfChanged();
};

const closeAssetContextMenu = () => {
  if (!assetContextMenu.value.visible) return;
  assetContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    nodeId: '',
  };
};

const openAssetContextMenu = (event: MouseEvent, node: GameWindow3AssetNode) => {
  event.preventDefault();
  selectedAssetId.value = node.id;
  assetContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    nodeId: node.id,
  };
};

const allowAssetDrop = (
  _draggingNode: { data?: GameWindow3AssetNode },
  _dropNode: { data?: GameWindow3AssetNode },
  _type: 'prev' | 'inner' | 'next',
) => true;

const deleteAssetFromContextMenu = async () => {
  selectedAssetId.value = assetContextMenu.value.nodeId || selectedAssetId.value;
  closeAssetContextMenu();
  await deleteSelectedAsset();
};

const selectAssetRenameInputText = async () => {
  await nextTick();
  const input = document.querySelector<HTMLInputElement>('.el-message-box__input input');
  input?.focus();
  input?.select();
};

const renameAssetNode = async (node: GameWindow3AssetNode) => {
  selectedAssetId.value = node.id;
  closeAssetContextMenu();
  const nodeKindText = node.type === 'folder' ? '目录' : '图片';
  try {
    const prompt = ElMessageBox.prompt(nodeKindText + '名称', '重命名' + nodeKindText, {
      inputValue: node.title,
      inputPattern: /\S+/,
      inputErrorMessage: '请输入' + nodeKindText + '名称',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    });
    void selectAssetRenameInputText();
    const result = await prompt;
    const nextTitle = String(result.value ?? '').trim();
    if (nextTitle) node.title = nextTitle;
  } catch {
    // User cancelled.
  }
};

const resetAssetFrameFromContextMenu = async () => {
  selectedAssetId.value = assetContextMenu.value.nodeId || selectedAssetId.value;
  const node = selectedAssetNode.value;
  closeAssetContextMenu();
  if (!node || node.type !== 'image' || !node.filename) return;
  try {
    await ElMessageBox.confirm(`用当前画面覆盖 ${node.filename}？`, '重置帧', { type: 'warning' });
    await resetAssetFrame(node);
    ElMessage.success(`已重置 ${node.filename}`);
  } catch (error) {
    if (error === 'cancel' || error === 'close') return;
    ElMessage.error(getErrorMessage(error));
  }
};

const addAnnotationShape = () => {
  const image = selectedImageNode.value;
  if (!image) return;
  image.shapes ??= [];
  const shape: GameWindow3Shape = {
    id: createAssetId('shape'),
    kind: 'shape',
    title: 'shape ' + (flattenShapes(image.shapes).filter(isDrawableShape).length + 1),
    description: '',
    floating: false,
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    contentDirection: 'none',
    imageMatchRole: 'off',
    pixelTolerance: 5,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    maskEnabled: false,
    alphaMask: null,
    toleranceEnabled: false,
    toleranceRange: null,
    discriminatorEnabled: false,
    discriminator: null,
    discriminatorGroupId: null,
    discriminatorValue: '',
    x: 0.16,
    y: 0.16,
    w: 0.36,
    h: 0.18,
    children: [],
  };
  image.shapes.push(shape);
  selectedShapeId.value = shape.id;
  selectedShapeIds.value = [];
  shapeSelectionAnchorId.value = shape.id;
};

const isShapeSelected = (id: string) => selectedShapeId.value === id || selectedShapeIds.value.includes(id);

const shapeTreeLinearIds = () => flattenShapes(selectedImageShapes.value).map((shape) => shape.id);

const selectShapeRange = (fromId: string, toId: string) => {
  const ids = shapeTreeLinearIds();
  const fromIndex = ids.indexOf(fromId);
  const toIndex = ids.indexOf(toId);
  if (fromIndex < 0 || toIndex < 0) {
    selectedShapeIds.value = [toId];
    return;
  }
  const start = Math.min(fromIndex, toIndex);
  const end = Math.max(fromIndex, toIndex);
  selectedShapeIds.value = ids.slice(start, end + 1);
};

const selectedShapeRoots = () => {
  const ids = selectedShapeIds.value.length
    ? selectedShapeIds.value
    : (selectedShapeId.value ? [selectedShapeId.value] : []);
  const selected = ids
    .map((id) => findShapeById(selectedImageShapes.value, id))
    .filter((shape): shape is GameWindow3Shape => Boolean(shape));
  return selected.filter((shape) => !selected.some((other) => other.id !== shape.id && isShapeDescendantOf(other, shape.id)));
};

const removeShapesByIds = (shapes: GameWindow3Shape[], ids: Set<string>) => {
  for (let index = shapes.length - 1; index >= 0; index -= 1) {
    const shape = shapes[index];
    if (ids.has(shape.id)) {
      shapes.splice(index, 1);
    } else if (shape.children?.length) {
      removeShapesByIds(shape.children, ids);
    }
  }
};

const deleteSelectedShape = () => {
  const image = selectedImageNode.value;
  if (!image?.shapes) return;
  const targets = selectedShapeRoots();
  if (!targets.length) return;
  removeShapesByIds(image.shapes, new Set(targets.map((shape) => shape.id)));
  selectedShapeIds.value = [];
  selectedShapeId.value = flattenShapes(image.shapes)[0]?.id ?? null;
  shapeSelectionAnchorId.value = selectedShapeId.value;
};

const selectShape = (id: string | null, event?: MouseEvent) => {
  if (id && event?.shiftKey) {
    const anchorId = shapeSelectionAnchorId.value || selectedShapeId.value || id;
    selectShapeRange(anchorId, id);
    selectedShapeId.value = id;
    return;
  }
  selectedShapeId.value = id;
  selectedShapeIds.value = [];
  shapeSelectionAnchorId.value = id;
};

const handleShapeTreeNodeClick = (
  data: GameWindow3Shape,
  _node: unknown,
  _component: unknown,
  event?: MouseEvent,
) => {
  selectShape(data.id, event);
};

const copySelectedShapes = () => {
  const targets = selectedShapeRoots();
  if (!targets.length) return;
  copiedShapes.value = targets.map((shape) => JSON.parse(JSON.stringify(shape)) as GameWindow3Shape);
  closeShapeContextMenu();
  ElMessage.success(`已复制 ${copiedShapes.value.length} 个 shape`);
};

const pasteCopiedShapes = () => {
  const image = selectedImageNode.value;
  if (!image || !copiedShapes.value.length) return;
  image.shapes ??= [];
  const pasted = copiedShapes.value.map(cloneShapeTreeWithNewIds);
  image.shapes.push(...pasted);
  selectedShapeIds.value = [];
  selectedShapeId.value = pasted[0]?.id ?? selectedShapeId.value;
  shapeSelectionAnchorId.value = selectedShapeId.value;
  closeShapeContextMenu();
  ElMessage.success(`已粘贴 ${pasted.length} 个 shape`);
};

const closeShapeContextMenu = () => {
  shapeContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    shapeId: '',
  };
};

const openShapeContextMenu = (event: MouseEvent, shapeId: string) => {
  event.preventDefault();
  event.stopPropagation();
  if (!selectedShapeIds.value.includes(shapeId)) {
    selectedShapeIds.value = [];
  }
  selectedShapeId.value = shapeId;
  shapeSelectionAnchorId.value = shapeId;
  shapeContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    shapeId,
  };
};

const openShapeTreeContextMenu = (event: MouseEvent, data: GameWindow3Shape) => {
  openShapeContextMenu(event, data.id);
};

const openShapeTreeBlankContextMenu = (event: MouseEvent) => {
  const target = event.target as HTMLElement | null;
  if (target?.closest('.el-tree-node')) return;
  shapeContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    shapeId: '',
  };
};

const deleteShapeFromContextMenu = () => {
  selectedShapeId.value = shapeContextMenu.value.shapeId || selectedShapeId.value;
  closeShapeContextMenu();
  deleteSelectedShape();
};

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

const stepperImageLabel = (image: GameWindow3AssetNode) => `${assetImageIdMark(image)} ${image.title}`;

const stepperScoreText = (score: number) => `${Math.round(score)}%`;

const stepperCandidateSummary = (candidates: StepperSceneCandidate[], limit = 5) => (
  candidates.slice(0, limit)
    .map((candidate) => `${stepperImageLabel(candidate.image)} ${stepperScoreText(candidate.score)}`)
    .join('，') || '无'
);

const stepperActionSummary = (edge: StepperActionEdge) => (
  `${edge.hasExperience ? '经验' : '探索'} ${edge.shape.title || 'shape'}`
);

const findShapeByTitle = (image: GameWindow3AssetNode, title: string) => (
  flattenShapes(image.shapes ?? []).find((shape) => isDrawableShape(shape) && shape.title.trim() === title.trim()) ?? null
);

const boxCenterPoint = (box: FanxiuGameWindow2MatchBox) => ({
  x: Math.round(box.x + box.w / 2),
  y: Math.round(box.y + box.h / 2),
});

const runtimeShapeMatchScoreOf = (response: FanxiuGameWindow2MatchResponse) => Number(
  (() => {
    const localPixelSimilarity = Number(response.fixed_pixel_similarity ?? response.fixed_exact_pixel_similarity ?? NaN);
    if (Number.isFinite(localPixelSimilarity)) return localPixelSimilarity;
    return 0;
  })(),
);

const matchCandidateScoreOf = (candidate: NonNullable<FanxiuGameWindow2MatchResponse['matches']>[number]) => Number(
  candidate.crop_similarity ?? candidate.similarity ?? 0,
);

type RuntimeShapeMatchResult = {
  kind: ShapeMatchKind;
  response: FanxiuGameWindow2MatchResponse;
  score: number;
};

type RuntimeShapeMatchDetails = {
  results: RuntimeShapeMatchResult[];
  response: FanxiuGameWindow2MatchResponse;
};

const bestRuntimeShapeMatchOf = (shape: GameWindow3Shape, response: FanxiuGameWindow2MatchResponse) => {
  if (shape.floating && response.matches?.length) {
    const best = [...response.matches].sort((a, b) => matchCandidateScoreOf(b) - matchCandidateScoreOf(a))[0];
    return {
      score: matchCandidateScoreOf(best),
      box: best.box,
    };
  }
  return {
    score: runtimeShapeMatchScoreOf(response),
    box: response.current_box ?? response.fixed_box ?? response.box,
  };
};

const shapeDetectResultTextOf = (shape: GameWindow3Shape, response: FanxiuGameWindow2MatchResponse) => {
  if (response.match_strategy === 'ocr') {
    const bestText = response.matches?.[0]?.ocr_text || response.ocr_text || '';
    const suffix = bestText ? `「${bestText}」` : '';
    if (!shape.floating) return `OCR ${runtimeShapeMatchScoreOf(response)}%${suffix}`;
    const matches = [...(response.matches ?? [])].sort((a, b) => matchCandidateScoreOf(b) - matchCandidateScoreOf(a));
    const accepted = matches.filter((item, index) => index === 0 || matchCandidateScoreOf(item) >= STEPPER_SCENE_MATCH_THRESHOLD);
    if (!accepted.length) return `OCR 最佳 ${runtimeShapeMatchScoreOf(response)}%${suffix}`;
    return accepted
      .map((item, index) => `${index === 0 ? '最佳' : `#${index + 1}`} ${Math.round(matchCandidateScoreOf(item))}%「${item.ocr_text || ''}」`)
      .join('，');
  }
  const bestScore = runtimeShapeMatchScoreOf(response);
  if (!shape.floating) return `原位 ${bestScore}%`;
  const matches = [...(response.matches ?? [])].sort((a, b) => matchCandidateScoreOf(b) - matchCandidateScoreOf(a));
  const accepted = matches.filter((item, index) => index === 0 || matchCandidateScoreOf(item) >= STEPPER_SCENE_MATCH_THRESHOLD);
  if (!accepted.length) return `浮动 最佳 ${bestScore}%`;
  return accepted
    .map((item, index) => `${index === 0 ? '最佳' : `#${index + 1}`} ${Math.round(matchCandidateScoreOf(item))}%`)
    .join('，');
};

const ocrTextOfResponse = (response: FanxiuGameWindow2MatchResponse) => (
  response.matches?.[0]?.ocr_text || response.ocr_text || ''
);

const shapeDetectResultTextOfDetails = (shape: GameWindow3Shape, details: RuntimeShapeMatchDetails) => {
  const parts = details.results.map((result) => {
    const role = shapeMatchRoleLabel(shapeMatchRoleForKind(shape, result.kind));
    if (result.kind === 'ocr') {
      const text = ocrTextOfResponse(result.response);
      return `OCR${role} ${Math.round(result.score)}%${text ? `「${text}」` : ''}`;
    }
    const prefix = shape.floating ? '图像浮动' : '图像原位';
    return `${prefix}${role} ${Math.round(result.score)}%`;
  });
  return parts.join('，');
};

const shapeDetectLiveBoxesOf = (shape: GameWindow3Shape, response: FanxiuGameWindow2MatchResponse) => {
  if (!shape.floating) return [];
  const matches = [...(response.matches ?? [])].sort((a, b) => matchCandidateScoreOf(b) - matchCandidateScoreOf(a));
  return matches
    .filter((item, index) => index === 0 || matchCandidateScoreOf(item) >= STEPPER_SCENE_MATCH_THRESHOLD)
    .slice(0, 12)
    .map((item) => item.box);
};

const buildRuntimeShapeMatchPayload = (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  currentFrameDataUrl: string,
  matchKind: ShapeMatchKind | null = shapePrimaryMatchKind(shape),
): FanxiuGameWindow2MatchPayload | null => {
  if (!selectedEntryId.value || !image.filename || !isDrawableShape(shape)) return null;
  if (!matchKind) return null;
  const box = shapeToMatchBox(shape, image);
  if (box.w <= 0 || box.h <= 0) return null;
  const occlusionAlphaMaskDataUrl = buildOcclusionAlphaMaskDataUrl(image, shape, box);
  const parentShape = shape.floating ? findShapeParentShape(image.shapes ?? [], shape.id) : null;
  const scanBox = parentShape && isDrawableShape(parentShape) ? shapeToMatchBox(parentShape, image) : undefined;
  return {
    entry_id: selectedEntryId.value,
    filename: image.filename,
    box,
    scan: Boolean(shape.floating),
    scan_box: scanBox,
    pixel_tolerance: shapePixelTolerance(shape),
    alpha_mask_data_url: occlusionAlphaMaskDataUrl || (shape.maskEnabled ? shape.alphaMask?.dataUrl : undefined),
    tolerance_min_data_url: shape.toleranceEnabled ? shape.toleranceRange?.minDataUrl : undefined,
    tolerance_max_data_url: shape.toleranceEnabled ? shape.toleranceRange?.maxDataUrl : undefined,
    ocr_enabled: matchKind === 'ocr',
    ocr_text: shape.ocrText?.trim() || undefined,
    ocr_match_mode: normalizeShapeOcrMatchMode(shape.ocrMatchMode),
    title: targetTitle.value.trim(),
    title_match: titleMatch.value,
    mode: 'screen',
    area: captureArea.value,
    crop: cropText.value.trim(),
    trim_border: trimBorderText.value.trim(),
    rotate: rotateDegrees.value,
    fixed_width: fixedFrameWidth.value,
    fixed_height: fixedFrameHeight.value,
    fps: Number(fps.value) || selectedWindowScene.value.defaults.fps,
    quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
    auto_dismiss_popup: autoDismissPopup.value,
    current_frame_data_url: currentFrameDataUrl,
  };
};

const resolveRuntimeShapeBox = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  currentFrameDataUrl?: string,
  channelUse: RuntimeChannelUse = 'frontend',
) => {
  const fallbackBox = shapeToMatchBox(shape, image);
  const needsRuntimeMatch = Boolean((shape.floating && shapeCanFloat(shape)) || shapeHasRequiredMatch(shape));
  if (!needsRuntimeMatch) {
    return {
      box: fallbackBox,
      score: 100,
      floating: false,
    };
  }
  const frameDataUrl = currentFrameDataUrl || await captureCurrentFrameDataUrl(channelUse);
  const response = await matchRuntimeShape(image, shape, frameDataUrl);
  if (!response) {
    throw new Error(`无法匹配 shape：${shape.title || 'shape'}`);
  }
  const best = bestRuntimeShapeMatchOf(shape, response);
  if (best.score < STEPPER_SCENE_MATCH_THRESHOLD) {
    throw new Error(`shape「${shape.title || 'shape'}」匹配不足：${stepperScoreText(best.score)}`);
  }
  return {
    box: best.box,
    score: best.score,
    floating: Boolean(shape.floating && shapeCanFloat(shape)),
  };
};

const matchRuntimeShapeByKind = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  currentFrameDataUrl: string,
  matchKind: ShapeMatchKind,
) => {
  const payload = buildRuntimeShapeMatchPayload(image, shape, currentFrameDataUrl, matchKind);
  return payload ? matchFanxiuGameWindow2Screenshot(payload) : null;
};

const matchRuntimeShape = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  currentFrameDataUrl: string,
) => {
  const details = await matchRuntimeShapeDetails(image, shape, currentFrameDataUrl);
  return details?.response ?? null;
};

const matchRuntimeShapeDetails = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  currentFrameDataUrl: string,
): Promise<RuntimeShapeMatchDetails | null> => {
  const results: RuntimeShapeMatchResult[] = [];
  for (const kind of shapeActiveMatchKinds(shape)) {
    const response = await matchRuntimeShapeByKind(image, shape, currentFrameDataUrl, kind);
    if (!response) continue;
    results.push({ kind, response, score: bestRuntimeShapeMatchOf(shape, response).score });
  }
  if (!results.length) return null;
  const required = results.filter((result) => shapeMatchRoleForKind(shape, result.kind) === 'required');
  const failedRequired = required.find((result) => result.score < STEPPER_SCENE_MATCH_THRESHOLD);
  const selected = failedRequired ?? [...results].sort((a, b) => b.score - a.score)[0];
  return {
    results,
    response: selected.response,
  };
};

const detectSelectedShape = async () => {
  const image = selectedImageNode.value;
  const shape = selectedShape.value;
  if (!selectedEntryId.value || !image || !shape || !isDrawableShape(shape)) return;
  if (!image.filename) {
    ElMessage.warning('当前图片没有原始帧文件，无法检测');
    return;
  }
  const box = shapeToMatchBox(shape, image);
  if (box.w <= 0 || box.h <= 0) {
    ElMessage.warning('请先框选有效区域');
    return;
  }
  const requestSeq = shapeDetectSeq.value + 1;
  shapeDetectSeq.value = requestSeq;
  const requestImageId = image.id;
  const requestShapeId = shape.id;
  shapeDetectResults.value = {
    ...shapeDetectResults.value,
    [shape.id]: '',
  };
  shapeDetectLiveBoxes.value = [];
  drawOverlay();
  shapeDetectingId.value = shape.id;
  try {
    const details = await matchRuntimeShapeDetails(image, shape, await captureCurrentFrameDataUrl());
    if (!details) {
      ElMessage.warning('请先框选有效区域');
      return;
    }
    if (
      requestSeq !== shapeDetectSeq.value
      || selectedImageNode.value?.id !== requestImageId
      || selectedShapeId.value !== requestShapeId
    ) {
      return;
    }
    const response = details.response;
    const geometryIssue = matchFrameAspectMismatchText(response);
    if (geometryIssue) ElMessage.warning(geometryIssue);
    const resultText = shapeDetectResultTextOfDetails(shape, details);
    shapeDetectLiveBoxes.value = shapeDetectLiveBoxesOf(shape, response);
    drawOverlay();
    shapeDetectResults.value = {
      ...shapeDetectResults.value,
      [requestShapeId]: resultText,
    };
    ElMessage.success(resultText);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    shapeDetectingId.value = null;
  }
};

const stepperSceneIdentityShapes = (image: GameWindow3AssetNode) => (
  flattenShapes(image.shapes ?? []).filter((shape) => (
    isDrawableShape(shape)
    && isSceneIdentityShape(shape)
    && !isBlankExitShape(shape)
    && !isIndependentExitShape(shape)
  ))
);

const stepperFallbackIdentityShapes = (image: GameWindow3AssetNode) => (
  flattenShapes(image.shapes ?? []).filter((shape) => (
    isDrawableShape(shape)
    && isSceneIdentityShape(shape)
    && !isBlankExitShape(shape)
  ))
);

const stepperSceneMatchShapes = (image: GameWindow3AssetNode) => {
  const primary = stepperSceneIdentityShapes(image);
  return primary.length ? primary : stepperFallbackIdentityShapes(image);
};

const isStepperSceneAsset = (image: GameWindow3AssetNode) => (
  findAssetParentFolder(assetTree.value, image.id)?.title.trim() !== '遮挡标记'
);

const isIndependentExitShape = (shape: GameWindow3Shape) => normalizeSceneJumpTargetText(shape.sceneJumpTarget) === '-1';

const isNoJumpShape = (shape: GameWindow3Shape) => normalizeSceneJumpTargetText(shape.sceneJumpTarget) === '0';

const isIndependentSceneImage = (image: GameWindow3AssetNode) => (
  flattenShapes(image.shapes ?? []).some((shape) => isDrawableShape(shape) && isIndependentExitShape(shape))
);

const matchStepperShape = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape,
  currentFrameDataUrl: string,
) => {
  const response = await matchRuntimeShape(image, shape, currentFrameDataUrl);
  return response ? bestRuntimeShapeMatchOf(shape, response).score : 0;
};

const matchStepperSceneCandidate = async (
  image: GameWindow3AssetNode,
  currentFrameDataUrl: string,
): Promise<StepperSceneCandidate> => {
  const identityShapes = stepperSceneMatchShapes(image);
  const results: Array<{ shape: GameWindow3Shape; role: ShapeMatchRole; score: number }> = [];
  for (const shape of identityShapes) {
    if (stepperStopRequested.value) break;
    try {
      results.push({ shape, role: shapeSceneIdentityRole(shape), score: await matchStepperShape(image, shape, currentFrameDataUrl) });
    } catch {
      results.push({ shape, role: shapeSceneIdentityRole(shape), score: 0 });
    }
  }
  const scores = results.map((result) => result.score);
  const required = results.filter((result) => result.role === 'required');
  const failedRequired = required.filter((result) => result.score < STEPPER_SCENE_MATCH_THRESHOLD);
  const min = scores.length ? Math.min(...scores) : 0;
  const average = scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : 0;
  const requiredMin = required.length ? Math.min(...required.map((result) => result.score)) : 0;
  const optionalMax = results
    .filter((result) => result.role === 'optional')
    .reduce((best, result) => Math.max(best, result.score), 0);
  const identityScore = failedRequired.length
    ? Math.min(...failedRequired.map((result) => result.score))
    : (required.length ? requiredMin * 0.75 + average * 0.25 : optionalMax);
  return {
    image,
    score: scores.length ? identityScore : 0,
    full: 0,
    min,
    average,
    count: scores.length,
  };
};

const matchStepperLayer = async (
  images: GameWindow3AssetNode[],
  currentFrameDataUrl: string,
  concurrency = 4,
) => {
  const queue = [...images];
  let best: StepperSceneCandidate | null = null;
  const candidates: StepperSceneCandidate[] = [];
  const workers = Array.from({ length: Math.min(concurrency, queue.length) }, async () => {
    while (queue.length && !stepperStopRequested.value) {
      const image = queue.shift();
      if (!image) break;
      try {
        const candidate = await matchStepperSceneCandidate(image, currentFrameDataUrl);
        candidates.push(candidate);
        if (!best || candidate.score > best.score) best = candidate;
      } catch {
        // A failed candidate should not stop the whole tick.
      }
    }
  });
  await Promise.all(workers);
  candidates.sort((a, b) => b.score - a.score);
  return { best, candidates };
};

const uniqueImages = (images: GameWindow3AssetNode[]) => (
  Array.from(new Map(images.map((image) => [image.id, image])).values())
);

const resolveStepperSceneTargets = (token: string) => {
  const images = flattenAssetImages(assetTree.value);
  const numeric = Number(token.replace(/^#/, ''));
  if (Number.isFinite(numeric) && token.replace(/^#/, '').trim()) {
    return images.filter((image) => assetNumericImageId(image) === numeric);
  }
  const normalizedToken = token.trim();
  const titleMatches = images.filter((image) => image.title.trim() === normalizedToken);
  if (titleMatches.length) return titleMatches;
  return images.filter((image) => findAssetParentFolder(assetTree.value, image.id)?.title.trim() === normalizedToken);
};

const resolveStepperJumpTargets = (entries: SceneJumpEntry[]) => (
  uniqueImages(entries.flatMap((entry) => resolveStepperSceneTargets(entry.label)))
);

const buildStepperCandidateLayers = (lastAction: StepperLastAction | null) => {
  const allImages = flattenAssetImages(assetTree.value)
    .filter((image) => image.filename && isStepperSceneAsset(image));
  const seen = new Set<string>();
  const take = (images: GameWindow3AssetNode[]) => {
    const result: GameWindow3AssetNode[] = [];
    for (const image of images) {
      if (seen.has(image.id)) continue;
      seen.add(image.id);
      result.push(image);
    }
    return result;
  };
  const history = take(lastAction?.expectedTargets ?? []);
  const independent = take(allImages.filter(isIndependentSceneImage));
  const rest = take(allImages);
  return [history, independent, rest].filter((layer) => layer.length);
};

const isBlankExitShape = (shape: GameWindow3Shape) => shape.title.trim() === '空白';

const buildStepperActionEdges = () => {
  const edges: StepperActionEdge[] = [];
  for (const image of flattenAssetImages(assetTree.value)) {
    if (!isStepperSceneAsset(image)) continue;
    for (const shape of flattenShapes(image.shapes ?? []).filter(isDrawableShape)) {
      if (isSceneIdentityShape(shape)) continue;
      const isNoJump = isNoJumpShape(shape);
      const isIndependentExit = isIndependentExitShape(shape);
      const jumpEntries = isIndependentExit || isNoJump ? [] : parseSceneJumpEntries(shape.sceneJumpTarget);
      const targets = jumpEntries.flatMap((entry) => (
        resolveStepperSceneTargets(entry.label).map((target) => ({ image: target, count: entry.count }))
      ));
      const uniqueTargets = Array.from(
        new Map(targets.map((target) => [target.image.id, target])).values(),
      ).sort((a, b) => b.count - a.count);
      edges.push({
        from: image,
        shape,
        jumpEntries,
        targets: uniqueTargets,
        isIndependentExit,
        isNoJump,
        hasExperience: uniqueTargets.length > 0,
      });
    }
  }
  return edges;
};

const findStepperPath = (
  current: GameWindow3AssetNode,
  targets: GameWindow3AssetNode[],
  triedEdges: Set<string> = new Set(),
): StepperActionEdge[] => {
  const targetIds = new Set(targets.map((target) => target.id));
  if (targetIds.has(current.id)) return [];
  const edges = buildStepperActionEdges();
  const queue: Array<{ image: GameWindow3AssetNode; path: StepperActionEdge[] }> = [{ image: current, path: [] }];
  const visited = new Set<string>([current.id]);
  while (queue.length) {
    const item = queue.shift();
    if (!item) break;
    for (const edge of edges.filter((candidate) => candidate.from.id === item.image.id)) {
      if (triedEdges.has(`${edge.from.id}:${edge.shape.id}`)) continue;
      for (const target of edge.targets.map((item) => item.image)) {
        if (visited.has(target.id)) continue;
        const path = [...item.path, edge];
        if (targetIds.has(target.id)) return path;
        visited.add(target.id);
        queue.push({ image: target, path });
      }
    }
  }
  return [];
};

const stepperActionPriority = (edge: StepperActionEdge, targetIds: Set<string>) => {
  const title = edge.shape.title.trim();
  if (edge.targets.some((target) => targetIds.has(target.image.id))) {
    return 100 + Math.max(...edge.targets.map((target) => target.count));
  }
  if (edge.hasExperience) return 60 + Math.max(...edge.targets.map((target) => target.count));
  if (edge.isIndependentExit) return 50;
  if (title === '空白') return 45;
  if (/[关返回退离开跳过空白取消确定完成进入]/.test(title)) return 35;
  return 0;
};

const findStepperNextAction = (
  current: GameWindow3AssetNode,
  targets: GameWindow3AssetNode[],
  triedEdges: Set<string>,
) => {
  const path = findStepperPath(current, targets, triedEdges);
  if (path.length) return path[0];
  const targetIds = new Set(targets.map((target) => target.id));
  return buildStepperActionEdges()
    .filter((edge) => edge.from.id === current.id)
    .filter((edge) => !triedEdges.has(`${edge.from.id}:${edge.shape.id}`))
    .sort((a, b) => stepperActionPriority(b, targetIds) - stepperActionPriority(a, targetIds))[0] ?? null;
};

const isStepperCandidateActionable = (
  candidate: StepperSceneCandidate,
  task: Extract<StepperTask, { type: 'go_scene' }>,
  triedEdges: Set<string>,
) => (
  task.targets.some((target) => target.id === candidate.image.id)
  || Boolean(findStepperNextAction(candidate.image, task.targets, triedEdges))
);

const identifyStepperScene = async (
  lastAction: StepperLastAction | null,
  task: Extract<StepperTask, { type: 'go_scene' }>,
  triedEdges: Set<string>,
  options: StepperTickOptions = {},
): Promise<StepperSceneCandidate | null> => {
  const detailLog = (message: string) => {
    if (options.verbose) appendStepperLog('detail', message);
  };
  detailLog(`开始识别：目标 ${task.targetText}，上次动作 ${lastAction?.shapeTitle || '无'}`);
  const currentFrameDataUrl = await captureCurrentFrameDataUrl('stepper');
  detailLog('已截取当前帧，开始按候选层匹配');
  const acceptCandidate = (candidate: StepperSceneCandidate) => (
    candidate.score >= STEPPER_SCENE_MATCH_THRESHOLD && isStepperCandidateActionable(candidate, task, triedEdges)
  );
  const layers = buildStepperCandidateLayers(lastAction);
  detailLog(`候选层：${layers.map((layer, index) => `第 ${index + 1} 层 ${layer.length} 个`).join('，') || '无'}`);
  const rankedCandidates: StepperRankedSceneCandidate[] = [];
  for (const [index, layer] of layers.entries()) {
    if (stepperStopRequested.value) return null;
    detailLog(`匹配第 ${index + 1} 层：${layer.map(stepperImageLabel).join('，') || '无候选'}`);
    const result = await matchStepperLayer(layer, currentFrameDataUrl, 4);
    detailLog(`第 ${index + 1} 层结果：${stepperCandidateSummary(result.candidates)}`);
    rankedCandidates.push(...result.candidates.map((candidate) => ({
      ...candidate,
      layerIndex: index,
      layerPriority: index,
    })));
  }
  const accepted = rankedCandidates
    .filter(acceptCandidate)
    .sort((a, b) => (a.layerPriority - b.layerPriority) || (b.score - a.score));
  if (accepted.length) {
    const candidate = accepted[0];
    detailLog(`可执行命中：第 ${candidate.layerIndex + 1} 层 ${stepperImageLabel(candidate.image)} ${stepperScoreText(candidate.score)}`);
    return candidate;
  }
  const visible = rankedCandidates
    .filter((candidate) => candidate.score >= STEPPER_SCENE_MATCH_THRESHOLD)
    .sort((a, b) => (a.layerPriority - b.layerPriority) || (b.score - a.score));
  if (visible.length) {
    detailLog(`已识别但无可执行路径：${visible.slice(0, 5).map((candidate) => `第 ${candidate.layerIndex + 1} 层 ${stepperImageLabel(candidate.image)} ${stepperScoreText(candidate.score)}`).join('，')}`);
  }
  detailLog('没有识别到可执行场景');
  return null;
};

const clickStepperShape = async (image: GameWindow3AssetNode, shape: GameWindow3Shape) => {
  if (!selectedEntryId.value) return null;
  const width = image.width || naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = image.height || naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  const resolved = await resolveRuntimeShapeBox(image, shape, undefined, 'stepper');
  const center = boxCenterPoint(resolved.box);
  await clickFanxiuGameWindow2({
    ...buildRemoteInputPayloadBase('stepper'),
    frame_width: naturalWidth.value || width,
    frame_height: naturalHeight.value || height,
    x: center.x,
    y: center.y,
  });
  return {
    point: center,
    score: resolved.score,
    floating: resolved.floating,
  };
};

const incrementSceneJumpExperience = (shape: GameWindow3Shape, target: GameWindow3AssetNode) => {
  if (isIndependentExitShape(shape) || isNoJumpShape(shape)) return;
  const targetId = assetNumericImageId(target);
  const label = targetId !== null ? String(targetId) : target.title.trim();
  if (!label) return;
  const entries = parseSceneJumpEntries(shape.sceneJumpTarget).filter((entry) => entry.label !== '-1' && entry.label !== '0');
  const found = entries.find((entry) => entry.label === label || resolveStepperSceneTargets(entry.label).some((image) => image.id === target.id));
  if (found) {
    found.count += 1;
  } else {
    entries.push({ label, count: 1 });
  }
  shape.sceneJumpTarget = serializeSceneJumpEntries(entries.sort((a, b) => (b.count - a.count) || a.label.localeCompare(b.label)));
};

const applyStepperObservedTransition = (candidate: StepperSceneCandidate) => {
  const action = stepperLastAction.value;
  if (!action || action.isIndependentExit) return;
  const from = findAssetNode(assetTree.value, action.fromImageId);
  const shape = from?.type === 'image' ? findShapeById(from.shapes ?? [], action.shapeId) : null;
  if (!shape) return;
  incrementSceneJumpExperience(shape, candidate.image);
};

const stepperLogKindLabel = (kind: string) => ({
  start: '开始',
  detail: '细节',
  wait: '等待',
  action: '动作',
  success: '完成',
  stop: '停止',
  error: '异常',
}[kind as StepperLogKind] ?? kind);

const stepperLogPageCount = computed(() => Math.max(1, Math.ceil(stepperLogs.value.length / STEPPER_LOG_PAGE_SIZE)));
const pagedStepperLogs = computed(() => {
  const page = clamp(stepperLogPage.value, 1, stepperLogPageCount.value);
  const start = (page - 1) * STEPPER_LOG_PAGE_SIZE;
  return stepperLogs.value.slice(start, start + STEPPER_LOG_PAGE_SIZE);
});
const stepperLogPageStart = computed(() => (
  stepperLogs.value.length ? (clamp(stepperLogPage.value, 1, stepperLogPageCount.value) - 1) * STEPPER_LOG_PAGE_SIZE + 1 : 0
));
const stepperLogPageEnd = computed(() => Math.min(stepperLogs.value.length, stepperLogPageStart.value + STEPPER_LOG_PAGE_SIZE - 1));
const goStepperLogLastPage = () => {
  stepperLogPage.value = stepperLogPageCount.value;
};

const appendStepperLog = (kind: StepperLogKind, message: string) => {
  const entry: StepperLogEntry = {
    id: createAssetId('stepper-log'),
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    kind,
    message,
    ts: new Date().toISOString(),
  };
  stepperLogs.value.push(entry);
  if (stepperLogs.value.length > 200) {
    stepperLogs.value = stepperLogs.value.slice(-200);
  }
  goStepperLogLastPage();
  appendFanxiuGameWindow3StepperLog(entry).catch(() => {
    // 页面日志不因为持久化失败而中断步进器运行。
  });
};

const setStepperRunStatus = (message: string, kind?: StepperLogKind) => {
  stepperRunStatus.value = message;
  if (kind) appendStepperLog(kind, message);
};

const loadStepperLogs = async () => {
  try {
    const response = await getFanxiuGameWindow3StepperLogs(500);
    stepperLogs.value = response.entries;
    goStepperLogLastPage();
  } catch {
    // 日志读取失败不影响页面主体功能。
  }
};

const clearStepperLogs = async () => {
  const response = await clearFanxiuGameWindow3StepperLogs();
  stepperLogs.value = response.entries;
  goStepperLogLastPage();
};

const stopStepperRun = () => {
  stepperStopRequested.value = true;
  setStepperRunStatus('正在停止', 'stop');
};

const resetStepperTaskState = (task: StepperTask, label: string) => {
  stepperTaskStack.value = [task];
  stepperLastAction.value = null;
  stepperLastDailyFindResult.value = null;
  shapeDetectLiveBoxes.value = [];
  drawOverlay();
  stepperTriedActionEdges.value = new Set();
  stepperTickCount.value = 0;
  setStepperRunStatus(`开始任务：${label}`, 'start');
};

const ensureStepperTask = (forceReset = false) => {
  const taskDefinition = selectedStepperTaskDefinition.value;
  if (!taskDefinition) {
    ElMessage.warning('没有可运行的步进器任务');
    return false;
  }
  const currentTask = stepperTaskStack.value.at(-1);
  if (!forceReset && currentTask && currentTask.type === taskDefinition.type) {
    if (taskDefinition.type === 'go_scene' && currentTask.type === 'go_scene' && currentTask.targetText === taskDefinition.targetText) return true;
    if (
      taskDefinition.type === 'daily_find'
      && currentTask.type === 'daily_find'
      && currentTask.query === taskDefinition.query
      && currentTask.matchMode === taskDefinition.matchMode
      && currentTask.completedFallbackPattern === (taskDefinition.completedFallbackPattern ?? '')
      && currentTask.completedFallbackExcludePattern === (taskDefinition.completedFallbackExcludePattern ?? '')
      && currentTask.completedFallbackMinTotal === (taskDefinition.completedFallbackMinTotal ?? 0)
      && currentTask.requireProgress === (taskDefinition.requireProgress !== false)
      && currentTask.openOnReady === (taskDefinition.openOnReady === true)
    ) return true;
    if (
      taskDefinition.type === 'drag_shape_to_shape'
      && currentTask.type === 'drag_shape_to_shape'
      && currentTask.sourceText === taskDefinition.sourceText
      && currentTask.sourceShapeTitle === taskDefinition.sourceShapeTitle
      && currentTask.targetShapeTitle === taskDefinition.targetShapeTitle
    ) return true;
  }
  if (taskDefinition.type === 'go_scene') {
    const targets = resolveStepperSceneTargets(taskDefinition.targetText);
    if (!targets.length) {
      ElMessage.warning(`没有找到目标场景：${taskDefinition.targetText}`);
      return false;
    }
    resetStepperTaskState({
      id: createAssetId('stepper-task'),
      type: 'go_scene',
      targetText: taskDefinition.targetText,
      targets,
    }, taskDefinition.label);
    return true;
  }
  if (taskDefinition.type === 'daily_find') {
    resetStepperTaskState({
      id: createAssetId('stepper-task'),
      type: 'daily_find',
      query: taskDefinition.query,
      matchMode: taskDefinition.matchMode,
      completedFallbackPattern: taskDefinition.completedFallbackPattern ?? '',
      completedFallbackExcludePattern: taskDefinition.completedFallbackExcludePattern ?? '',
      completedFallbackMinTotal: taskDefinition.completedFallbackMinTotal ?? 0,
      notFoundStatus: taskDefinition.notFoundStatus,
      timeoutSeconds: taskDefinition.timeoutSeconds,
      dragCount: taskDefinition.dragCount,
      requireProgress: taskDefinition.requireProgress !== false,
      openOnReady: taskDefinition.openOnReady === true,
      legacySource: taskDefinition.legacySource ?? '',
      note: taskDefinition.note ?? '',
      startedAt: Date.now(),
      attempts: 0,
    }, taskDefinition.label);
    return true;
  }
  const sourceImages = resolveStepperSceneTargets(taskDefinition.sourceText);
  if (!sourceImages.length) {
    ElMessage.warning(`没有找到参考截图：${taskDefinition.sourceText}`);
    return false;
  }
  resetStepperTaskState({
    id: createAssetId('stepper-task'),
    type: 'drag_shape_to_shape',
    sourceText: taskDefinition.sourceText,
    sourceShapeTitle: taskDefinition.sourceShapeTitle,
    targetShapeTitle: taskDefinition.targetShapeTitle,
    sourceImages,
  }, taskDefinition.label);
  return true;
};

const dragStepperFramePoints = async (
  startPoint: { x: number; y: number },
  endPoint: { x: number; y: number },
  durationMs = 450,
) => {
  if (!selectedEntryId.value) return;
  const frameWidth = naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth;
  const frameHeight = naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight;
  const normalize = (point: { x: number; y: number }) => ({
    x: Math.round(clamp(point.x, 0, Math.max(0, frameWidth - 1))),
    y: Math.round(clamp(point.y, 0, Math.max(0, frameHeight - 1))),
  });
  const start = normalize(startPoint);
  const end = normalize(endPoint);
  await dragFanxiuGameWindow2({
    ...buildRemoteInputPayloadBase('stepper'),
    frame_width: frameWidth,
    frame_height: frameHeight,
    start_x: start.x,
    start_y: start.y,
    end_x: end.x,
    end_y: end.y,
    duration_ms: durationMs,
  });
};

const normalizeStepperSearchText = (text: string) => text.replace(/\s+/g, '').trim();

const escapeStepperRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const stepperWildcardToRegExp = (pattern: string) => new RegExp(
  `^${normalizeStepperSearchText(pattern).split('').map((char) => {
    if (char === '*') return '.*';
    if (char === '?') return '.';
    return escapeStepperRegExp(char);
  }).join('')}$`,
  'i',
);

const stepperTextMatches = (text: string, query: string, mode: ShapeOcrMatchMode) => {
  const normalizedText = normalizeStepperSearchText(text);
  const normalizedQuery = normalizeStepperSearchText(query);
  if (!normalizedText || !normalizedQuery) return false;
  if (mode === 'exact') return normalizedText === normalizedQuery;
  if (mode === 'wildcard') return stepperWildcardToRegExp(normalizedQuery).test(normalizedText);
  if (mode === 'regex') {
    try {
      return new RegExp(normalizedQuery, 'i').test(normalizedText);
    } catch {
      return false;
    }
  }
  return normalizedText.includes(normalizedQuery);
};

const stepperOcrLineCenter = (line: FanxiuGameWindow3OcrFrameLine) => ({
  x: Math.round(line.x + line.w / 2),
  y: Math.round(line.y + line.h / 2),
});

type StepperFrameRect = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
};

const stepperOcrLineRect = (line: FanxiuGameWindow3OcrFrameLine): StepperFrameRect => ({
  left: line.x,
  top: line.y,
  right: line.x + line.w,
  bottom: line.y + line.h,
  width: line.w,
  height: line.h,
});

type StepperDailyTaskSummary = {
  text: string;
  anchorText: string;
  candidateText: string;
  status: string;
  statusCode: DailyFindStatusCode;
  progress: DailyFindProgress | null;
  blockBox: StepperFrameRect | null;
  anchorBox: StepperFrameRect;
  statusBox: StepperFrameRect | null;
  progressBox: StepperFrameRect | null;
  statusText: string;
  progressText: string;
  activityText: string;
  clickPoint: { x: number; y: number };
  source: 'template' | 'heuristic';
};

type StepperDailyFindDecision = DailyFindDecision;

type StepperDailyFindResult = DailyFindResult<StepperDailyTaskSummary>;

type StepperDailyAnchorCandidate = {
  anchor: FanxiuGameWindow3OcrFrameLine;
  text: string;
};

const shapeTitleEquals = (shape: GameWindow3Shape, title: string) => shape.title.trim() === title;

const findNestedShapeByTitle = (shapes: GameWindow3Shape[], title: string): GameWindow3Shape | null => {
  for (const shape of shapes) {
    if (shapeTitleEquals(shape, title)) return shape;
    const found = findNestedShapeByTitle(shape.children ?? [], title);
    if (found) return found;
  }
  return null;
};

const stepperShapeFrameRect = (image: GameWindow3AssetNode, shape: GameWindow3Shape): StepperFrameRect => {
  const width = image.width || naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || FALLBACK_FRAME_WIDTH;
  const height = image.height || naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || FALLBACK_FRAME_HEIGHT;
  const left = shape.x * width;
  const top = shape.y * height;
  const rectWidth = shape.w * width;
  const rectHeight = shape.h * height;
  return {
    left,
    top,
    right: left + rectWidth,
    bottom: top + rectHeight,
    width: rectWidth,
    height: rectHeight,
  };
};

const translateStepperRect = (rect: StepperFrameRect, dx: number, dy: number): StepperFrameRect => ({
  left: rect.left + dx,
  top: rect.top + dy,
  right: rect.right + dx,
  bottom: rect.bottom + dy,
  width: rect.width,
  height: rect.height,
});

const expandStepperRect = (
  rect: StepperFrameRect,
  dx: number,
  dy: number,
): StepperFrameRect => ({
  left: rect.left - dx,
  top: rect.top - dy,
  right: rect.right + dx,
  bottom: rect.bottom + dy,
  width: rect.width + dx * 2,
  height: rect.height + dy * 2,
});

const stepperLineInRect = (line: FanxiuGameWindow3OcrFrameLine, rect: StepperFrameRect) => {
  const center = stepperOcrLineCenter(line);
  return center.x >= rect.left && center.x <= rect.right && center.y >= rect.top && center.y <= rect.bottom;
};

const collectStepperTextInRect = (lines: FanxiuGameWindow3OcrFrameLine[], rect: StepperFrameRect) => (
  lines
    .filter((line) => stepperLineInRect(line, rect))
    .sort((a, b) => (a.y - b.y) || (a.x - b.x))
    .map((line) => line.text)
    .join('')
);

const findStepperDailyTemplate = () => {
  for (const image of flattenAssetImages(assetTree.value)) {
    const shapes = image.shapes ?? [];
    const listShape = findNestedShapeByTitle(shapes, '滚动窗口');
    const templateShape = findNestedShapeByTitle(shapes, '任务块模板');
    const titleShape = templateShape ? findNestedShapeByTitle(templateShape.children ?? [], '标题') : null;
    if (listShape && templateShape && titleShape) return { image, listShape, templateShape, titleShape };
  }
  return null;
};

const findStepperDailySceneAnchor = () => {
  const template = findStepperDailyTemplate();
  if (!template) return null;
  const shape = findNestedShapeByTitle(template.image.shapes ?? [], '日常');
  return shape ? { image: template.image, shape } : null;
};

const getStepperDailySceneEvidence = (lines: FanxiuGameWindow3OcrFrameLine[]) => {
  const anchor = findStepperDailySceneAnchor();
  if (!anchor) return { ok: true, text: '', box: null as StepperFrameRect | null };
  const box = expandStepperRect(stepperShapeFrameRect(anchor.image, anchor.shape), 18, 12);
  const text = collectStepperTextInRect(lines, box);
  return {
    ok: /日\s*常|周\s*常/.test(text),
    text,
    box,
  };
};

const stepperDailyFindListBox = () => {
  const template = findStepperDailyTemplate();
  if (template) {
    const rect = stepperShapeFrameRect(template.image, template.listShape);
    return {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      anchorLeft: rect.left,
      anchorRight: rect.right,
      width: template.image.width || naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || FALLBACK_FRAME_WIDTH,
      height: template.image.height || naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || FALLBACK_FRAME_HEIGHT,
    };
  }
  const frameWidth = naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || FALLBACK_FRAME_WIDTH;
  const frameHeight = naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || FALLBACK_FRAME_HEIGHT;
  return {
    left: frameWidth * 0.08,
    right: frameWidth * 0.95,
    top: frameHeight * 0.245,
    bottom: frameHeight * 0.80,
    anchorLeft: frameWidth * 0.40,
    anchorRight: frameWidth * 0.95,
    width: frameWidth,
    height: frameHeight,
  };
};

const isStepperDailyFindLineInList = (line: FanxiuGameWindow3OcrFrameLine) => {
  const box = stepperDailyFindListBox();
  const center = stepperOcrLineCenter(line);
  return center.x >= box.left && center.x <= box.right && center.y >= box.top && center.y <= box.bottom;
};

const isStepperDailyFindAnchorLine = (line: FanxiuGameWindow3OcrFrameLine) => {
  const box = stepperDailyFindListBox();
  const center = stepperOcrLineCenter(line);
  return center.x >= box.anchorLeft && center.x <= box.anchorRight && center.y >= box.top && center.y <= box.bottom;
};

const buildStepperDailyAnchorCandidates = (
  lines: FanxiuGameWindow3OcrFrameLine[],
): StepperDailyAnchorCandidate[] => {
  const anchorLines = lines.filter((line) => isStepperDailyFindAnchorLine(line));
  return anchorLines.map((anchor) => {
    const center = stepperOcrLineCenter(anchor);
    const yTolerance = Math.max(24, anchor.h * 1.6);
    const rowText = lines
      .filter((line) => isStepperDailyFindLineInList(line))
      .filter((line) => Math.abs(stepperOcrLineCenter(line).y - center.y) <= yTolerance)
      .sort((a, b) => a.x - b.x)
      .map((line) => line.text)
      .join('');
    return {
      anchor,
      text: rowText || anchor.text,
    };
  });
};

const safeStepperRegExp = (pattern: string) => {
  try {
    return pattern.trim() ? new RegExp(normalizeStepperSearchText(pattern), 'i') : null;
  } catch {
    return null;
  }
};

const findStepperDailyCompletedFallback = (
  task: Extract<StepperTask, { type: 'daily_find' }>,
  lines: FanxiuGameWindow3OcrFrameLine[],
): StepperDailyAnchorCandidate | null => {
  const pattern = safeStepperRegExp(task.completedFallbackPattern);
  if (!pattern) return null;
  const excludePattern = safeStepperRegExp(task.completedFallbackExcludePattern);
  const minTotal = Math.max(0, task.completedFallbackMinTotal);
  return buildStepperDailyAnchorCandidates(lines).find((candidate) => {
    const text = normalizeStepperSearchText(candidate.text);
    if (excludePattern?.test(text)) return false;
    if (!pattern.test(text)) return false;
    const progress = extractStepperDailyProgress(text);
    const status = extractDailyStatusText(text);
    const progressDone = Boolean(progress && progress.current >= progress.total && progress.total >= minTotal);
    const statusDone = getDailyStatusCode(status, progress) === 2;
    return progressDone || statusDone;
  }) ?? null;
};

const extractStepperDailyProgress = extractDailyProgress;

const stepperRectCenter = (rect: StepperFrameRect) => ({
  x: Math.round(rect.left + rect.width / 2),
  y: Math.round(rect.top + rect.height / 2),
});

const stepperRectToMatchBox = (name: string, rect: StepperFrameRect): FanxiuGameWindow2MatchBox => ({
  name,
  x: Math.round(rect.left),
  y: Math.round(rect.top),
  w: Math.max(1, Math.round(rect.width)),
  h: Math.max(1, Math.round(rect.height)),
});

const showStepperDailyFindBoxes = (summary: StepperDailyTaskSummary) => {
  const boxes: FanxiuGameWindow2MatchBox[] = [
    summary.blockBox ? stepperRectToMatchBox('任务块', summary.blockBox) : null,
    stepperRectToMatchBox('标题', summary.anchorBox),
    summary.statusBox ? stepperRectToMatchBox('状态', summary.statusBox) : null,
    summary.progressBox ? stepperRectToMatchBox('次数', summary.progressBox) : null,
  ].filter((box): box is FanxiuGameWindow2MatchBox => Boolean(box));
  shapeDetectLiveBoxes.value = boxes;
  drawOverlay();
};

const decideStepperDailyFindResult = decideDailyFindResult<StepperDailyTaskSummary>;

const summarizeStepperDailyTaskByTemplate = (
  anchor: FanxiuGameWindow3OcrFrameLine,
  lines: FanxiuGameWindow3OcrFrameLine[],
  candidateText = anchor.text,
): StepperDailyTaskSummary | null => {
  const template = findStepperDailyTemplate();
  if (!template) return null;
  const { image, templateShape, titleShape } = template;
  const titleRect = stepperShapeFrameRect(image, titleShape);
  const anchorCenter = stepperOcrLineCenter(anchor);
  const titleCenter = {
    x: titleRect.left + titleRect.width / 2,
    y: titleRect.top + titleRect.height / 2,
  };
  const dx = anchorCenter.x - titleCenter.x;
  const dy = anchorCenter.y - titleCenter.y;
  const blockBox = translateStepperRect(stepperShapeFrameRect(image, templateShape), dx, dy);
  const childRect = (title: string) => {
    const child = findNestedShapeByTitle(templateShape.children ?? [], title);
    return child ? translateStepperRect(stepperShapeFrameRect(image, child), dx, dy) : null;
  };
  const statusBox = childRect('任务状态');
  const progressBox = childRect('次数');
  const activityBox = childRect('活跃度');
  const statusText = collectStepperTextInRect(lines, statusBox ?? blockBox);
  const progressText = collectStepperTextInRect(lines, progressBox ?? blockBox);
  const activityText = activityBox ? collectStepperTextInRect(lines, activityBox) : '';
  const blockText = collectStepperTextInRect(lines, blockBox);
  const status = extractDailyStatusText(statusText) || extractDailyStatusText(blockText);
  const progress = extractStepperDailyProgress(progressText) ?? extractStepperDailyProgress(blockText);
  return {
    text: blockText || [anchor.text, progressText, statusText].filter(Boolean).join(''),
    anchorText: anchor.text,
    candidateText,
    status,
    statusCode: getDailyStatusCode(status, progress),
    progress,
    blockBox,
    anchorBox: stepperOcrLineRect(anchor),
    statusBox,
    progressBox,
    statusText,
    progressText,
    activityText,
    clickPoint: stepperRectCenter(statusBox ?? blockBox),
    source: 'template',
  };
};

const summarizeStepperDailyTaskHeuristic = (
  anchor: FanxiuGameWindow3OcrFrameLine,
  lines: FanxiuGameWindow3OcrFrameLine[],
  candidateText = anchor.text,
): StepperDailyTaskSummary => {
  const center = stepperOcrLineCenter(anchor);
  const rowTop = center.y - Math.max(60, anchor.h * 2.5);
  const rowBottom = center.y + Math.max(95, anchor.h * 4);
  const rowLines = lines
    .filter((line) => isStepperDailyFindLineInList(line))
    .filter((line) => {
      const lineCenter = stepperOcrLineCenter(line);
      return lineCenter.y >= rowTop && lineCenter.y <= rowBottom;
    })
    .sort((a, b) => (a.y - b.y) || (a.x - b.x));
  const text = rowLines.map((line) => line.text).join('');
  const status = extractDailyStatusText(text);
  const progress = extractStepperDailyProgress(text);
  return {
    text,
    anchorText: anchor.text,
    candidateText,
    status,
    statusCode: getDailyStatusCode(status, progress),
    progress,
    blockBox: {
      left: stepperDailyFindListBox().left,
      right: stepperDailyFindListBox().right,
      top: rowTop,
      bottom: rowBottom,
      width: stepperDailyFindListBox().right - stepperDailyFindListBox().left,
      height: rowBottom - rowTop,
    },
    anchorBox: stepperOcrLineRect(anchor),
    statusBox: null,
    progressBox: null,
    statusText: status,
    progressText: text,
    activityText: '',
    clickPoint: {
      x: Math.round(stepperDailyFindListBox().right - stepperDailyFindListBox().width * 0.12),
      y: center.y + 20,
    },
    source: 'heuristic',
  };
};

const summarizeStepperDailyTask = (
  anchor: FanxiuGameWindow3OcrFrameLine,
  lines: FanxiuGameWindow3OcrFrameLine[],
  candidateText = anchor.text,
): StepperDailyTaskSummary => {
  return summarizeStepperDailyTaskByTemplate(anchor, lines, candidateText) ?? summarizeStepperDailyTaskHeuristic(anchor, lines, candidateText);
};

const shouldStepperDailyFindRecenter = (line: FanxiuGameWindow3OcrFrameLine, summary?: StepperDailyTaskSummary) => {
  const box = stepperDailyFindListBox();
  const bottom = summary?.blockBox?.bottom ?? (line.y + line.h);
  return (box.bottom - bottom) < Math.max(90, box.height * 0.08);
};

const STEPPER_DAILY_DRAG_PERCENT = 0.5;
const STEPPER_DAILY_RECENTER_DRAG_PERCENT = 0.18;

const dragStepperDailyTaskList = async (percent = STEPPER_DAILY_DRAG_PERCENT) => {
  const box = stepperDailyFindListBox();
  const x = box.width * 0.52;
  await dragStepperFramePoints(
    { x, y: box.top + (box.bottom - box.top) * 0.72 },
    { x, y: box.top + (box.bottom - box.top) * (0.72 - percent) },
    520,
  );
};

const clickStepperDailyFindResult = async (result: StepperDailyFindResult) => {
  const point = result.summary?.clickPoint;
  if (!point || !selectedEntryId.value || !naturalWidth.value || !naturalHeight.value) return false;
  const normalized = normalizeControlPoint(point);
  await clickFanxiuGameWindow2({
    ...buildRemoteInputPayloadBase('stepper'),
    x: normalized.x,
    y: normalized.y,
  });
  return true;
};

const runStepperDailyFindTick = async (
  task: Extract<StepperTask, { type: 'daily_find' }>,
  options: StepperTickOptions = {},
) => {
  const detailLog = (message: string) => {
    if (options.verbose) appendStepperLog('detail', message);
  };
  const tickNo = stepperTickCount.value + 1;
  stepperTickCount.value = tickNo;
  task.attempts += 1;
  const elapsedSeconds = Math.round((Date.now() - task.startedAt) / 1000);
  if (elapsedSeconds > task.timeoutSeconds) {
    const result = decideStepperDailyFindResult(task, '', null);
    stepperLastDailyFindResult.value = result;
    shapeDetectLiveBoxes.value = [];
    drawOverlay();
    setStepperRunStatus(`日常定位「${task.query}」超时，返回状态 ${task.notFoundStatus}：${result.reason}`, 'wait');
    stepperTaskStack.value.pop();
    return 'done';
  }

  detailLog(`Tick ${tickNo} 开始：OCR 查找「${task.query}」`);
  if (task.legacySource) detailLog(`旧版来源：${task.legacySource}`);
  if (task.note) detailLog(`预设说明：${task.note}`);
  const currentFrameDataUrl = await captureCurrentFrameDataUrl('stepper');
  const response = await recognizeFanxiuGameWindow3OcrFrame(currentFrameDataUrl);
  const lines = response.lines ?? [];
  detailLog(`OCR 文本：${lines.slice(0, 16).map((line) => `「${line.text}」`).join('，') || '无'}`);
  const sceneEvidence = getStepperDailySceneEvidence(lines);
  if (!sceneEvidence.ok) {
    stepperLastDailyFindResult.value = null;
    shapeDetectLiveBoxes.value = sceneEvidence.box ? [stepperRectToMatchBox('日常场景标识', sceneEvidence.box)] : [];
    drawOverlay();
    const evidenceText = sceneEvidence.text ? `，场景框 OCR「${sceneEvidence.text}」` : '，场景框未识别到日常标题';
    setStepperRunStatus(`日常定位「${task.query}」停止：当前不像日常页${evidenceText}。请先运行到场景「日常」或保存/修正日常场景标识。`, 'wait');
    detailLog(`日常场景确认失败${evidenceText}`);
    stepperTaskStack.value.pop();
    return 'done';
  }
  const matched = buildStepperDailyAnchorCandidates(lines)
    .find((candidate) => stepperTextMatches(candidate.text, task.query, task.matchMode));
  if (matched) {
    const center = stepperOcrLineCenter(matched.anchor);
    const summary = summarizeStepperDailyTask(matched.anchor, lines, matched.text);
    if (shouldStepperDailyFindRecenter(matched.anchor, summary) && task.attempts <= task.dragCount) {
      setStepperRunStatus(`Tick ${tickNo}：命中「${matched.text}」但靠近底部，小幅上滑后复核`, 'action');
      await dragStepperDailyTaskList(STEPPER_DAILY_RECENTER_DRAG_PERCENT);
      if (options.waitOnIdle) await sleep(1000);
      return 'continue';
    }
    const progressText = summary.progress ? `，进度 ${summary.progress.current}/${summary.progress.total}` : '';
    const statusText = summary.status ? `，状态「${summary.status}」` : '';
    const sourceText = summary.source === 'template' ? '模板' : '启发式';
    const result = decideStepperDailyFindResult(task, matched.text, summary);
    stepperLastDailyFindResult.value = result;
    showStepperDailyFindBoxes(summary);
    const decisionText = result.decision === 'ready'
      ? '待执行'
      : result.decision === 'completed'
        ? '已完成'
        : result.decision === 'ongoing'
          ? '进行中'
          : result.decision === 'retry'
            ? '需复核'
            : '未找到';
    detailLog(`任务块文本（${sourceText}）：${summary.text || matched.text}`);
    detailLog(`任务块锚点：${summary.anchorText}，候选行：${summary.candidateText}`);
    detailLog(`任务块字段：状态字段=${summary.statusText || '无'}，进度字段=${summary.progressText || '无'}`);
    detailLog(`任务块点击点：(${summary.clickPoint.x},${summary.clickPoint.y})，活跃度文本：${summary.activityText || '无'}`);
    if (task.openOnReady && result.decision === 'ready') {
      await clickStepperDailyFindResult(result);
      setStepperRunStatus(`日常进入「${matched.text}」${statusText}${progressText}，点击 (${summary.clickPoint.x},${summary.clickPoint.y})`, 'action');
      if (options.waitOnIdle) await sleep(1000);
      stepperTaskStack.value.pop();
      return 'done';
    }
    const blockedEnterText = task.openOnReady ? '，未进入' : '';
    setStepperRunStatus(`日常定位命中「${matched.text}」@(${center.x},${center.y})${statusText}${progressText}，${decisionText}${blockedEnterText}，状态码 ${result.statusCode}：${result.reason}`, 'success');
    ElMessage.success(stepperRunStatus.value);
    stepperTaskStack.value.pop();
    return 'done';
  }

  const completedFallback = findStepperDailyCompletedFallback(task, lines);
  if (completedFallback) {
    const summary = summarizeStepperDailyTask(completedFallback.anchor, lines, completedFallback.text);
    const result = decideStepperDailyFindResult(task, completedFallback.text, summary);
    stepperLastDailyFindResult.value = {
      ...result,
      statusCode: 2,
      decision: 'completed',
      reason: `完成行兜底确认：${result.reason}`,
    };
    showStepperDailyFindBoxes(summary);
    detailLog(`完成行兜底命中：${completedFallback.text}`);
    detailLog(`任务块字段：状态字段=${summary.statusText || '无'}，进度字段=${summary.progressText || '无'}`);
    setStepperRunStatus(
      `日常定位「${task.query}」未命中入口，但完成行兜底确认「${completedFallback.text}」，状态码 2`,
      'success',
    );
    ElMessage.success(stepperRunStatus.value);
    stepperTaskStack.value.pop();
    return 'done';
  }

  if (task.attempts <= task.dragCount) {
    setStepperRunStatus(
      `Tick ${tickNo}：未找到「${task.query}」，滚动任务清单 ${task.attempts}/${task.dragCount}`,
      'action',
    );
    await dragStepperDailyTaskList();
    if (options.waitOnIdle) await sleep(1000);
    return 'continue';
  }

  const result = decideStepperDailyFindResult(task, '', null);
  stepperLastDailyFindResult.value = result;
  shapeDetectLiveBoxes.value = [];
  drawOverlay();
  setStepperRunStatus(
    `Tick ${tickNo}：未找到「${task.query}」，返回状态 ${task.notFoundStatus}：${result.reason}${options.waitOnIdle ? '，等待 1 秒' : ''}`,
    'wait',
  );
  stepperTaskStack.value.pop();
  if (options.waitOnIdle) await sleep(1000);
  return 'done';
};

const runStepperDragShapeToShapeTick = async (
  task: Extract<StepperTask, { type: 'drag_shape_to_shape' }>,
  options: StepperTickOptions = {},
) => {
  const detailLog = (message: string) => {
    if (options.verbose) appendStepperLog('detail', message);
  };
  const tickNo = stepperTickCount.value + 1;
  stepperTickCount.value = tickNo;
  detailLog(`Tick ${tickNo} 开始：检测 ${task.sourceText} 的「${task.sourceShapeTitle}」`);
  const currentFrameDataUrl = await captureCurrentFrameDataUrl('stepper');
  let best: {
    image: GameWindow3AssetNode;
    sourceShape: GameWindow3Shape;
    targetShape: GameWindow3Shape;
    score: number;
    box: FanxiuGameWindow2MatchBox;
  } | null = null;
  const scores: string[] = [];
  for (const image of task.sourceImages) {
    if (stepperStopRequested.value) return 'continue';
    const sourceShape = findShapeByTitle(image, task.sourceShapeTitle);
    const targetShape = findShapeByTitle(image, task.targetShapeTitle);
    if (!sourceShape || !targetShape) {
      detailLog(`${stepperImageLabel(image)} 缺少「${!sourceShape ? task.sourceShapeTitle : task.targetShapeTitle}」shape`);
      continue;
    }
    try {
      const response = await matchRuntimeShape(image, sourceShape, currentFrameDataUrl);
      if (!response) continue;
      const match = bestRuntimeShapeMatchOf(sourceShape, response);
      scores.push(`${stepperImageLabel(image)} ${stepperScoreText(match.score)}`);
      if (!best || match.score > best.score) {
        best = {
          image,
          sourceShape,
          targetShape,
          score: match.score,
          box: match.box,
        };
      }
    } catch (error) {
      detailLog(`${stepperImageLabel(image)} 检测失败：${getErrorMessage(error)}`);
    }
  }
  detailLog(`检测结果：${scores.join('，') || '无'}`);
  if (!best || best.score < STEPPER_SCENE_MATCH_THRESHOLD) {
    setStepperRunStatus(
      `Tick ${tickNo}：未检测到${task.sourceText}「${task.sourceShapeTitle}」${best ? `，最佳 ${stepperScoreText(best.score)}` : ''}${options.waitOnIdle ? '，等待 1 秒' : ''}`,
      'wait',
    );
    if (options.waitOnIdle) await sleep(1000);
    return 'continue';
  }
  const start = boxCenterPoint(best.box);
  let targetMatch: Awaited<ReturnType<typeof resolveRuntimeShapeBox>>;
  try {
    targetMatch = await resolveRuntimeShapeBox(best.image, best.targetShape, currentFrameDataUrl);
  } catch (error) {
    setStepperRunStatus(
      `Tick ${tickNo}：未检测到目标「${task.targetShapeTitle}」${options.waitOnIdle ? '，等待 1 秒' : ''}`,
      'wait',
    );
    detailLog(`目标检测失败：${getErrorMessage(error)}`);
    if (options.waitOnIdle) await sleep(1000);
    return 'continue';
  }
  const end = boxCenterPoint(targetMatch.box);
  detailLog(`拖拽：${task.sourceShapeTitle} (${start.x},${start.y}) -> ${task.targetShapeTitle} (${end.x},${end.y})`);
  if (targetMatch.floating) {
    detailLog(`目标浮动重检：${stepperScoreText(targetMatch.score)}`);
  }
  selectedAssetId.value = best.image.id;
  selectedShapeId.value = best.sourceShape.id;
  setStepperRunStatus(
    `Tick ${tickNo}：拖拽 ${task.sourceShapeTitle} -> ${task.targetShapeTitle}，识别 ${stepperScoreText(best.score)}`,
    'action',
  );
  await dragStepperFramePoints(start, end);
  stepperTaskStack.value.pop();
  setStepperRunStatus('已隐藏浮动窗', 'success');
  return 'done';
};

const runStepperTick = async (options: StepperTickOptions = {}) => {
  const detailLog = (message: string) => {
    if (options.verbose) appendStepperLog('detail', message);
  };
  const task = stepperTaskStack.value.at(-1);
  if (!task) return 'done';
  if (task.type === 'drag_shape_to_shape') return runStepperDragShapeToShapeTick(task, options);
  if (task.type === 'daily_find') return runStepperDailyFindTick(task, options);
  const tickNo = stepperTickCount.value + 1;
  stepperTickCount.value = tickNo;
  detailLog(`Tick ${tickNo} 开始：目标 ${task.targetText}`);
  const current = await identifyStepperScene(stepperLastAction.value, task, stepperTriedActionEdges.value, options);
  if (!current) {
    setStepperRunStatus(`Tick ${tickNo}：过渡中${options.waitOnIdle ? '，等待 1 秒' : ''}`, 'wait');
    if (options.waitOnIdle) await sleep(1000);
    return 'continue';
  }
  detailLog(`当前场景：${stepperImageLabel(current.image)}，识别分 ${stepperScoreText(current.score)}，标识 ${current.count} 个`);
  applyStepperObservedTransition(current);
  stepperLastAction.value = null;
  if (task.targets.some((target) => target.id === current.image.id)) {
    setStepperRunStatus(`已到达 ${stepperImageLabel(current.image)}`, 'success');
    ElMessage.success(stepperRunStatus.value);
    selectedAssetId.value = current.image.id;
    stepperTaskStack.value.pop();
    return 'done';
  }
  const next = findStepperNextAction(current.image, task.targets, stepperTriedActionEdges.value);
  if (!next) {
    detailLog(`决策结果：${stepperImageLabel(current.image)} 没有未尝试的可推进动作`);
    setStepperRunStatus(`Tick ${tickNo}：没有可推进动作${options.waitOnIdle ? '，等待 1 秒' : ''}`, 'wait');
    if (options.waitOnIdle) await sleep(1000);
    return 'continue';
  }
  const candidateActions = buildStepperActionEdges()
    .filter((edge) => edge.from.id === current.image.id)
    .filter((edge) => !stepperTriedActionEdges.value.has(`${edge.from.id}:${edge.shape.id}`))
    .map(stepperActionSummary);
  detailLog(`可选动作：${candidateActions.join('，') || '无'}`);
  detailLog(`决策动作：${stepperActionSummary(next)}，预期 ${next.targets.map((target) => stepperImageLabel(target.image)).join('，') || '未知'}`);
  stepperTriedActionEdges.value.add(`${next.from.id}:${next.shape.id}`);
  setStepperRunStatus(`Tick ${tickNo}：${stepperImageLabel(current.image)} -> ${next.hasExperience ? '点击' : '探索'} ${next.shape.title || 'shape'}`, 'action');
  selectedAssetId.value = current.image.id;
  selectedShapeId.value = next.shape.id;
  stepperLastAction.value = {
    fromImageId: current.image.id,
    shapeId: next.shape.id,
    shapeTitle: next.shape.title,
    isIndependentExit: next.isIndependentExit,
    expectedTargets: next.targets.map((target) => target.image),
  };
  detailLog(`点击坐标：${next.shape.title || 'shape'} ${next.shape.floating ? '浮动重检后中心' : '原位中心'}`);
  const clickResult = await clickStepperShape(current.image, next.shape);
  if (clickResult?.floating) {
    detailLog(`浮动重检：${stepperScoreText(clickResult.score)}，点击 (${clickResult.point.x},${clickResult.point.y})`);
  }
  detailLog('点击完成，等待画面过渡 1 秒');
  await sleep(1000);
  return 'continue';
};

const runStepperSingleTick = async () => {
  if (!selectedEntryId.value || stepperRunning.value || stepperStepping.value) return;
  if (!ensureStepperTask(false)) return;
  stepperStepping.value = true;
  stepperStopRequested.value = false;
  try {
    await runStepperTick({ verbose: true });
  } catch (error) {
    setStepperRunStatus(getErrorMessage(error), 'error');
    ElMessage.error(stepperRunStatus.value);
  } finally {
    stepperStepping.value = false;
    stepperStopRequested.value = false;
  }
};

const runStepperToTarget = async () => {
  if (!selectedEntryId.value || stepperRunning.value || stepperStepping.value) return;
  if (!ensureStepperTask(true)) return;
  stepperRunning.value = true;
  stepperStopRequested.value = false;
  try {
    for (let step = 0; step < 24; step += 1) {
      if (stepperStopRequested.value) break;
      const result = await runStepperTick({ waitOnIdle: true });
      if (result === 'done') return;
    }
    if (stepperStopRequested.value) {
      setStepperRunStatus('已停止', 'stop');
      return;
    }
    setStepperRunStatus('超过最大步数，未到达目标', 'error');
    ElMessage.warning(stepperRunStatus.value);
  } catch (error) {
    setStepperRunStatus(getErrorMessage(error), 'error');
    ElMessage.error(stepperRunStatus.value);
  } finally {
    stepperRunning.value = false;
    stepperStopRequested.value = false;
  }
};

const getSelectedShapePixelSize = () => {
  const image = selectedImageNode.value;
  const shape = selectedShape.value;
  if (!image || !shape) return null;
  const width = image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  return {
    width: Math.max(1, Math.round(shape.w * width)),
    height: Math.max(1, Math.round(shape.h * height)),
  };
};

const loadMaskImage = (src: string) => new Promise<HTMLImageElement>((resolve, reject) => {
  const image = new Image();
  image.onload = () => resolve(image);
  image.onerror = () => reject(new Error('图片加载失败'));
  image.src = src;
});

const cropImageDataUrlByShape = async (imageDataUrl: string, shape: GameWindow3Shape | null, width: number, height: number) => {
  if (!shape) return null;
  const image = await loadMaskImage(imageDataUrl);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return null;
  context.drawImage(
    image,
    shape.x * image.naturalWidth,
    shape.y * image.naturalHeight,
    shape.w * image.naturalWidth,
    shape.h * image.naturalHeight,
    0,
    0,
    width,
    height,
  );
  return context.getImageData(0, 0, width, height);
};

const cropImageDataUrlByBox = async (imageDataUrl: string, box: FanxiuGameWindow2MatchBox, width: number, height: number) => {
  const image = await loadMaskImage(imageDataUrl);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return null;
  context.drawImage(
    image,
    box.x,
    box.y,
    box.w,
    box.h,
    0,
    0,
    width,
    height,
  );
  return context.getImageData(0, 0, width, height);
};

const loadShapeAlphaMask = async (shape: GameWindow3Shape, width: number, height: number) => {
  if (!shape.maskEnabled || !shape.alphaMask?.dataUrl) return null;
  const image = await loadMaskImage(shape.alphaMask.dataUrl);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return null;
  context.drawImage(image, 0, 0, width, height);
  const data = context.getImageData(0, 0, width, height).data;
  const alpha = new Uint8ClampedArray(width * height);
  for (let index = 0; index < alpha.length; index += 1) {
    const offset = index * 4;
    alpha[index] = Math.min(data[offset], data[offset + 1], data[offset + 2], data[offset + 3]);
  }
  return alpha;
};

const applyAlphaToPreview = (imageData: ImageData, alpha: Uint8ClampedArray | null) => {
  if (!alpha) return imageData;
  const preview = new ImageData(new Uint8ClampedArray(imageData.data), imageData.width, imageData.height);
  const total = preview.width * preview.height;
  for (let index = 0; index < total; index += 1) {
    preview.data[index * 4 + 3] = alpha[index] ?? 255;
  }
  return preview;
};

const alphaToMaskImageData = (alpha: Uint8ClampedArray, width: number, height: number) => {
  const image = new ImageData(width, height);
  const total = width * height;
  for (let index = 0; index < total; index += 1) {
    const value = alpha[index] ?? 255;
    const offset = index * 4;
    image.data[offset] = value;
    image.data[offset + 1] = value;
    image.data[offset + 2] = value;
    image.data[offset + 3] = 255;
  }
  return image;
};

const cropImageDataUrlToShape = async (imageDataUrl: string, width: number, height: number) => {
  return cropImageDataUrlByShape(imageDataUrl, selectedShape.value, width, height);
};

const captureLiveShapeImageData = async (width: number, height: number) => {
  const shape = selectedShape.value;
  const selectedImage = selectedImageNode.value;
  if (!shape) return null;
  const currentFrameDataUrl = captureCurrentLiveFrameDataUrl();
  if (!currentFrameDataUrl) return null;
  if (shape.floating && selectedImage?.filename) {
    const response = await matchRuntimeShape(selectedImage, shape, currentFrameDataUrl);
    if (!response) return null;
    const best = bestRuntimeShapeMatchOf(shape, response);
    return cropImageDataUrlByBox(currentFrameDataUrl, best.box, width, height);
  }
  return cropImageDataUrlByShape(currentFrameDataUrl, shape, width, height);
};

const imageDataToDataUrl = (imageData: ImageData) => {
  const canvas = document.createElement('canvas');
  canvas.width = imageData.width;
  canvas.height = imageData.height;
  const context = canvas.getContext('2d');
  if (!context) return '';
  context.putImageData(imageData, 0, 0);
  return canvas.toDataURL('image/png');
};

const refreshShapeMaskPreview = () => {
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return;
  const total = stats.width * stats.height;
  const maskImage = new ImageData(stats.width, stats.height);
  const resultImage = new ImageData(new Uint8ClampedArray(stats.reference.data), stats.width, stats.height);
  for (let index = 0; index < total; index += 1) {
    const volatility = stats.max[index] - stats.min[index];
    const dynamicAlpha = volatility <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((volatility - shapeMaskThreshold.value) * 5));
    const alpha = Math.min(stats.baseAlpha?.[index] ?? 255, dynamicAlpha);
    const offset = index * 4;
    maskImage.data[offset] = alpha;
    maskImage.data[offset + 1] = alpha;
    maskImage.data[offset + 2] = alpha;
    maskImage.data[offset + 3] = 255;
    resultImage.data[offset + 3] = alpha;
  }
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(maskImage);
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(resultImage);
};

const currentShapeMaskAlpha = () => {
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return null;
  const total = stats.width * stats.height;
  const alpha = new Uint8ClampedArray(total);
  for (let index = 0; index < total; index += 1) {
    const volatility = stats.max[index] - stats.min[index];
    const dynamicAlpha = volatility <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((volatility - shapeMaskThreshold.value) * 5));
    alpha[index] = Math.min(stats.baseAlpha?.[index] ?? 255, dynamicAlpha);
  }
  return alpha;
};

const referencePixelSaturation = (reference: ImageData, index: number) => {
  const offset = index * 4;
  const r = reference.data[offset];
  const g = reference.data[offset + 1];
  const b = reference.data[offset + 2];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  return max - min;
};

const cleanAlphaLargestComponent = (
  alpha: Uint8ClampedArray,
  reference: ImageData,
  width: number,
  height: number,
) => {
  const total = width * height;
  const solid = new Uint8Array(total);
  for (let index = 0; index < total; index += 1) {
    solid[index] = alpha[index] >= 150 && referencePixelSaturation(reference, index) >= 24 ? 1 : 0;
  }
  const visited = new Uint8Array(total);
  let best: number[] = [];
  const queue: number[] = [];
  for (let start = 0; start < total; start += 1) {
    if (!solid[start] || visited[start]) continue;
    const component: number[] = [];
    visited[start] = 1;
    queue.length = 0;
    queue.push(start);
    for (let head = 0; head < queue.length; head += 1) {
      const index = queue[head];
      component.push(index);
      const x = index % width;
      const y = Math.floor(index / width);
      const neighbors = [
        x > 0 ? index - 1 : -1,
        x < width - 1 ? index + 1 : -1,
        y > 0 ? index - width : -1,
        y < height - 1 ? index + width : -1,
      ];
      for (const next of neighbors) {
        if (next >= 0 && solid[next] && !visited[next]) {
          visited[next] = 1;
          queue.push(next);
        }
      }
    }
    if (component.length > best.length) best = component;
  }
  const keep = new Uint8Array(total);
  for (const index of best) keep[index] = 1;
  const cleaned = new Uint8ClampedArray(total);
  for (let index = 0; index < total; index += 1) {
    if (!keep[index]) continue;
    cleaned[index] = 255;
    const x = index % width;
    const y = Math.floor(index / width);
    const neighbors = [
      x > 0 ? index - 1 : -1,
      x < width - 1 ? index + 1 : -1,
      y > 0 ? index - width : -1,
      y < height - 1 ? index + width : -1,
    ];
    for (const next of neighbors) {
      if (next >= 0 && alpha[next] >= 220) cleaned[next] = Math.max(cleaned[next], 220);
    }
  }
  return cleaned;
};

const cleanShapeMaskAlpha = () => {
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return;
  const alpha = currentShapeMaskAlpha();
  if (!alpha) return;
  const cleaned = cleanAlphaLargestComponent(alpha, stats.reference, stats.width, stats.height);
  stats.baseAlpha = cleaned;
  stats.min.fill(255);
  stats.max.fill(0);
  shapeMaskFrameCount.value = 0;
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(alphaToMaskImageData(cleaned, stats.width, stats.height));
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(stats.reference, cleaned));
};

const updateShapeMaskStats = (frame: ImageData) => {
  const stats = shapeMaskStats.value;
  if (!stats) return;
  const total = stats.width * stats.height;
  for (let index = 0; index < total; index += 1) {
    const offset = index * 4;
    const gray = Math.round(
      frame.data[offset] * 0.299
      + frame.data[offset + 1] * 0.587
      + frame.data[offset + 2] * 0.114,
    );
    stats.min[index] = Math.min(stats.min[index], gray);
    stats.max[index] = Math.max(stats.max[index], gray);
  }
  shapeMaskFrameCount.value += 1;
  if (shapeMaskFrameCount.value % 5 === 1) {
    shapeMaskLivePreviewUrl.value = imageDataToDataUrl(frame);
  }
  refreshShapeMaskPreview();
};

const scheduleShapeMaskSampling = () => {
  shapeMaskSamplingFrame.value = window.requestAnimationFrame(async () => {
    if (!shapeMaskDialogVisible.value || !shapeMaskStats.value || !shapeMaskRunning.value) return;
    const frame = await captureLiveShapeImageData(shapeMaskStats.value.width, shapeMaskStats.value.height);
    if (!shapeMaskDialogVisible.value || !shapeMaskStats.value || !shapeMaskRunning.value) return;
    if (frame) updateShapeMaskStats(frame);
    scheduleShapeMaskSampling();
  });
};

const pauseShapeMaskSampling = () => {
  shapeMaskRunning.value = false;
  if (shapeMaskSamplingFrame.value !== null) {
    window.cancelAnimationFrame(shapeMaskSamplingFrame.value);
    shapeMaskSamplingFrame.value = null;
  }
};

const stopShapeMaskSampling = pauseShapeMaskSampling;

const initializeShapeMaskSampling = async (useExistingMask: boolean) => {
  pauseShapeMaskSampling();
  const shape = selectedShape.value;
  const image = selectedImageNode.value;
  const size = getSelectedShapePixelSize();
  if (!shape || !image || !size) return;
  const imageDataUrl = await getAssetImageDataUrl(image);
  if (!imageDataUrl) return;
  const reference = await cropImageDataUrlToShape(imageDataUrl, size.width, size.height);
  if (!reference) return;
  const total = size.width * size.height;
  const baseAlpha = useExistingMask ? await loadShapeAlphaMask(shape, size.width, size.height) : null;
  shapeMaskFrameCount.value = 0;
  shapeMaskLivePreviewUrl.value = '';
  shapeMaskAlphaDataUrl.value = baseAlpha ? imageDataToDataUrl(alphaToMaskImageData(baseAlpha, size.width, size.height)) : '';
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(reference, baseAlpha));
  shapeMaskStats.value = {
    width: size.width,
    height: size.height,
    min: new Uint8ClampedArray(total).fill(255),
    max: new Uint8ClampedArray(total),
    baseAlpha,
    reference,
  };
};

const resetShapeMaskSampling = async () => {
  await initializeShapeMaskSampling(false);
};

const startShapeMaskSampling = async () => {
  if (!shapeMaskStats.value) await initializeShapeMaskSampling(true);
  if (!shapeMaskStats.value || shapeMaskRunning.value) return;
  shapeMaskRunning.value = true;
  scheduleShapeMaskSampling();
};

const openShapeMaskDialog = async () => {
  if (!selectedShape.value || selectedShape.value.kind === 'group') return;
  shapeMaskDialogVisible.value = true;
  await nextTick();
  await initializeShapeMaskSampling(true);
};

const saveShapeMaskAndClose = () => {
  const shape = selectedShape.value;
  const stats = shapeMaskStats.value;
  if (!shape || !stats || !shapeMaskAlphaDataUrl.value) return;
  shape.alphaMask = {
    width: stats.width,
    height: stats.height,
    dataUrl: shapeMaskAlphaDataUrl.value,
  };
  shape.maskEnabled = true;
  shapeMaskDialogVisible.value = false;
};

const toleranceStatsToImageData = (data: Uint8ClampedArray, width: number, height: number) => {
  const image = new ImageData(width, height);
  const total = width * height;
  for (let index = 0; index < total; index += 1) {
    const sourceOffset = index * 3;
    const targetOffset = index * 4;
    image.data[targetOffset] = data[sourceOffset];
    image.data[targetOffset + 1] = data[sourceOffset + 1];
    image.data[targetOffset + 2] = data[sourceOffset + 2];
    image.data[targetOffset + 3] = 255;
  }
  return image;
};

const refreshShapeTolerancePreview = () => {
  const stats = shapeToleranceStats.value;
  if (!stats) return;
  shapeToleranceMinPreviewUrl.value = imageDataToDataUrl(toleranceStatsToImageData(stats.min, stats.width, stats.height));
  shapeToleranceMaxPreviewUrl.value = imageDataToDataUrl(toleranceStatsToImageData(stats.max, stats.width, stats.height));
};

const updateShapeToleranceStats = (frame: ImageData) => {
  const stats = shapeToleranceStats.value;
  if (!stats) return;
  const total = stats.width * stats.height;
  for (let index = 0; index < total; index += 1) {
    const sourceOffset = index * 4;
    const targetOffset = index * 3;
    stats.min[targetOffset] = Math.min(stats.min[targetOffset], frame.data[sourceOffset]);
    stats.min[targetOffset + 1] = Math.min(stats.min[targetOffset + 1], frame.data[sourceOffset + 1]);
    stats.min[targetOffset + 2] = Math.min(stats.min[targetOffset + 2], frame.data[sourceOffset + 2]);
    stats.max[targetOffset] = Math.max(stats.max[targetOffset], frame.data[sourceOffset]);
    stats.max[targetOffset + 1] = Math.max(stats.max[targetOffset + 1], frame.data[sourceOffset + 1]);
    stats.max[targetOffset + 2] = Math.max(stats.max[targetOffset + 2], frame.data[sourceOffset + 2]);
  }
  shapeToleranceFrameCount.value += 1;
  if (shapeToleranceFrameCount.value % 5 === 1) refreshShapeTolerancePreview();
};

const scheduleShapeToleranceSampling = () => {
  shapeToleranceSamplingFrame.value = window.requestAnimationFrame(async () => {
    if (!shapeToleranceDialogVisible.value || !shapeToleranceStats.value || !shapeToleranceRunning.value) return;
    const frame = await captureLiveShapeImageData(shapeToleranceStats.value.width, shapeToleranceStats.value.height);
    if (!shapeToleranceDialogVisible.value || !shapeToleranceStats.value || !shapeToleranceRunning.value) return;
    if (frame) updateShapeToleranceStats(frame);
    scheduleShapeToleranceSampling();
  });
};

const pauseShapeToleranceSampling = () => {
  shapeToleranceRunning.value = false;
  if (shapeToleranceSamplingFrame.value !== null) {
    window.cancelAnimationFrame(shapeToleranceSamplingFrame.value);
    shapeToleranceSamplingFrame.value = null;
  }
};

const stopShapeToleranceSampling = pauseShapeToleranceSampling;

const resetShapeToleranceSampling = async () => {
  pauseShapeToleranceSampling();
  const image = selectedImageNode.value;
  const size = getSelectedShapePixelSize();
  if (!image || !size) return;
  const imageDataUrl = await getAssetImageDataUrl(image);
  if (!imageDataUrl) return;
  const reference = await cropImageDataUrlToShape(imageDataUrl, size.width, size.height);
  if (!reference) return;
  const total = size.width * size.height;
  const min = new Uint8ClampedArray(total * 3);
  const max = new Uint8ClampedArray(total * 3);
  for (let index = 0; index < total; index += 1) {
    const sourceOffset = index * 4;
    const targetOffset = index * 3;
    min[targetOffset] = reference.data[sourceOffset];
    min[targetOffset + 1] = reference.data[sourceOffset + 1];
    min[targetOffset + 2] = reference.data[sourceOffset + 2];
    max[targetOffset] = reference.data[sourceOffset];
    max[targetOffset + 1] = reference.data[sourceOffset + 1];
    max[targetOffset + 2] = reference.data[sourceOffset + 2];
  }
  shapeToleranceFrameCount.value = 0;
  shapeToleranceStats.value = {
    width: size.width,
    height: size.height,
    min,
    max,
  };
  refreshShapeTolerancePreview();
};

const startShapeToleranceSampling = async () => {
  if (!shapeToleranceStats.value) await resetShapeToleranceSampling();
  if (!shapeToleranceStats.value || shapeToleranceRunning.value) return;
  shapeToleranceRunning.value = true;
  scheduleShapeToleranceSampling();
};

const openShapeToleranceDialog = async () => {
  if (!selectedShape.value || selectedShape.value.kind === 'group') return;
  shapeToleranceDialogVisible.value = true;
  await nextTick();
  await resetShapeToleranceSampling();
};

const saveShapeToleranceAndClose = () => {
  const shape = selectedShape.value;
  const stats = shapeToleranceStats.value;
  if (!shape || !stats || !shapeToleranceMinPreviewUrl.value || !shapeToleranceMaxPreviewUrl.value) return;
  shape.toleranceRange = {
    width: stats.width,
    height: stats.height,
    minDataUrl: shapeToleranceMinPreviewUrl.value,
    maxDataUrl: shapeToleranceMaxPreviewUrl.value,
  };
  shape.toleranceEnabled = true;
  shapeToleranceDialogVisible.value = false;
};

const buildDiscriminatorWeightPreview = (weights: Float32Array, width: number, height: number) => {
  const image = new ImageData(width, height);
  const total = width * height;
  for (let index = 0; index < total; index += 1) {
    const value = Math.round(Math.min(1, weights[index]) * 255);
    const offset = index * 4;
    image.data[offset] = value;
    image.data[offset + 1] = value;
    image.data[offset + 2] = value;
    image.data[offset + 3] = 255;
  }
  return imageDataToDataUrl(image);
};

const computeDiscriminatorVariantError = (
  sample: ImageData,
  reference: ImageData,
  weights: Float32Array,
  alpha: Uint8ClampedArray | null,
) => {
  let weightedDiff = 0;
  let totalWeight = 0;
  const total = sample.width * sample.height;
  for (let index = 0; index < total; index += 1) {
    const alphaWeight = alpha ? (alpha[index] ?? 0) / 255 : 1;
    if (alphaWeight <= 0.02) continue;
    const weight = weights[index] * alphaWeight;
    if (weight <= 0) continue;
    const offset = index * 4;
    const diff = Math.max(
      Math.abs(sample.data[offset] - reference.data[offset]),
      Math.abs(sample.data[offset + 1] - reference.data[offset + 1]),
      Math.abs(sample.data[offset + 2] - reference.data[offset + 2]),
    );
    weightedDiff += diff * weight;
    totalWeight += weight;
  }
  return totalWeight > 0 ? weightedDiff / totalWeight : Number.POSITIVE_INFINITY;
};

const currentDiscriminatorGroup = () => (
  discriminatorGroups.value.find((group) => group.id === shapeDiscriminatorGroupId.value) ?? null
);

const ensureDiscriminatorCurrentMember = () => {
  const shape = selectedShape.value;
  const image = selectedImageNode.value;
  if (!shape || !image) return;
  const imageId = assetNumericImageId(image);
  if (imageId === null) return;
  if (!shapeDiscriminatorMembers.value.some((member) => member.shapeId === shape.id)) {
    shapeDiscriminatorMembers.value.unshift({
      imageId,
      shapeId: shape.id,
      label: shape.discriminatorValue || shape.title || image.title || `#${imageId}`,
    });
  }
};

const createLinkedShapeForImage = (image: GameWindow3AssetNode, imageId: number, label: string) => {
  const source = selectedShape.value;
  image.shapes ??= [];
  const existing = image.shapes.find((shape) => shape.discriminatorGroupId === shapeDiscriminatorGroupId.value);
  if (existing) return existing;
  const shape: GameWindow3Shape = {
    id: createAssetId('shape'),
    kind: 'shape',
    title: label || source?.title || image.title,
    description: '',
    floating: Boolean(source?.floating),
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    contentDirection: 'none',
    imageMatchRole: source?.imageMatchRole ?? 'off',
    pixelTolerance: 5,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    maskEnabled: false,
    alphaMask: null,
    toleranceEnabled: false,
    toleranceRange: null,
    discriminatorEnabled: true,
    discriminator: null,
    discriminatorGroupId: shapeDiscriminatorGroupId.value,
    discriminatorValue: label || image.title || `#${imageId}`,
    x: source?.x ?? 0,
    y: source?.y ?? 0,
    w: source?.w ?? 0.1,
    h: source?.h ?? 0.1,
    children: [],
  };
  image.shapes.push(shape);
  return shape;
};

const selectShapeDiscriminatorCandidate = (shape: GameWindow3Shape, label: string) => {
  shape.discriminatorEnabled = true;
  shape.discriminatorGroupId = shapeDiscriminatorGroupId.value;
  shape.discriminatorValue = label;
  if (shapeDiscriminatorSyncBox.value) {
    const source = selectedShape.value;
    if (source) {
      shape.x = source.x;
      shape.y = source.y;
      shape.w = source.w;
      shape.h = source.h;
    }
  }
  return shape;
};

const addShapeDiscriminatorMember = () => {
  const imageId = shapeDiscriminatorNewImageId.value;
  if (imageId === null) return;
  const image = findAssetImageByNumericId(assetTree.value, imageId);
  if (!image) {
    ElMessage.warning(`没有找到 #${imageId}`);
    return;
  }
  ensureDiscriminatorCurrentMember();
  const candidateShape = shapeDiscriminatorNewShapeId.value
    ? findShapeById(image.shapes ?? [], shapeDiscriminatorNewShapeId.value)
    : null;
  const label = candidateShape?.discriminatorValue
    || candidateShape?.title
    || image.title
    || `#${imageId}`;
  const shape = candidateShape
    ? selectShapeDiscriminatorCandidate(candidateShape, label)
    : createLinkedShapeForImage(image, imageId, label);
  if (!shapeDiscriminatorMembers.value.some((member) => member.shapeId === shape.id)) {
    shapeDiscriminatorMembers.value.push({ imageId, shapeId: shape.id, label });
  }
  shape.discriminatorValue = label;
  shapeDiscriminatorNewImageId.value = null;
  shapeDiscriminatorNewShapeId.value = '';
  void resetShapeDiscriminator();
};

const removeShapeDiscriminatorMember = (shapeId: string) => {
  shapeDiscriminatorMembers.value = shapeDiscriminatorMembers.value.filter((member) => member.shapeId !== shapeId);
  void resetShapeDiscriminator();
};

const resetShapeDiscriminator = async () => {
  pauseShapeDiscriminatorSampling();
  ensureDiscriminatorCurrentMember();
  const size = getSelectedShapePixelSize();
  shapeDiscriminatorReady.value = false;
  shapeDiscriminatorResultText.value = '';
  shapeDiscriminatorSourcePreviewUrl.value = '';
  shapeDiscriminatorTargetPreviewUrl.value = '';
  shapeDiscriminatorWeightPreviewUrl.value = '';
  shapeDiscriminatorState.value = null;
  if (!size || shapeDiscriminatorMembers.value.length < 2) return;
  const variants: NonNullable<typeof shapeDiscriminatorState.value>['variants'] = [];
  for (const member of shapeDiscriminatorMembers.value) {
    const found = findShapeGlobal(member.shapeId);
    if (!found) continue;
    const imageDataUrl = await getAssetImageDataUrl(found.image);
    if (!imageDataUrl) continue;
    const reference = await cropImageDataUrlByShape(imageDataUrl, found.shape, size.width, size.height);
    if (!reference) continue;
    const alpha = await loadShapeAlphaMask(found.shape, size.width, size.height);
    variants.push({
      imageId: member.imageId,
      label: member.label || found.shape.title || `#${member.imageId}`,
      shapeId: member.shapeId,
      reference: applyAlphaToPreview(reference, alpha),
      alpha,
    });
  }
  if (variants.length < 2) return;
  const total = size.width * size.height;
  const weights = new Float32Array(total);
  const rawDiffs = new Float32Array(total);
  let maxDiff = 0;
  for (let index = 0; index < total; index += 1) {
    const offset = index * 4;
    let minR = 255;
    let minG = 255;
    let minB = 255;
    let maxR = 0;
    let maxG = 0;
    let maxB = 0;
    let visibleCount = 0;
    for (const variant of variants) {
      const alpha = variant.alpha ? (variant.alpha[index] ?? 0) : 255;
      if (alpha <= 5) continue;
      visibleCount += 1;
      minR = Math.min(minR, variant.reference.data[offset]);
      minG = Math.min(minG, variant.reference.data[offset + 1]);
      minB = Math.min(minB, variant.reference.data[offset + 2]);
      maxR = Math.max(maxR, variant.reference.data[offset]);
      maxG = Math.max(maxG, variant.reference.data[offset + 1]);
      maxB = Math.max(maxB, variant.reference.data[offset + 2]);
    }
    const diff = visibleCount >= 2
      ? Math.max(maxR - minR, maxG - minG, maxB - minB)
      : (visibleCount === 1 ? 24 : 0);
    rawDiffs[index] = diff;
    maxDiff = Math.max(maxDiff, diff);
  }
  const sortedDiffs = Array.from(rawDiffs).sort((a, b) => a - b);
  const percentileIndex = Math.max(0, Math.floor(sortedDiffs.length * 0.88));
  const activeThreshold = Math.max(12, sortedDiffs[percentileIndex] ?? 0);
  let activePixels = 0;
  for (let index = 0; index < total; index += 1) {
    if (rawDiffs[index] < activeThreshold || maxDiff <= 0) {
      weights[index] = 0;
      continue;
    }
    weights[index] = rawDiffs[index] / maxDiff;
    activePixels += 1;
  }
  shapeDiscriminatorState.value = {
    width: size.width,
    height: size.height,
    variants,
    weights,
    activePixels,
  };
  shapeDiscriminatorSourcePreviewUrl.value = imageDataToDataUrl(variants[0].reference);
  shapeDiscriminatorTargetPreviewUrl.value = imageDataToDataUrl(variants[1].reference);
  shapeDiscriminatorWeightPreviewUrl.value = buildDiscriminatorWeightPreview(weights, size.width, size.height);
  shapeDiscriminatorReady.value = true;
};

const updateShapeDiscriminatorResult = (frame: ImageData) => {
  const state = shapeDiscriminatorState.value;
  if (!state) return;
  const results = state.variants
    .map((variant) => ({
      ...variant,
      error: computeDiscriminatorVariantError(frame, variant.reference, state.weights, variant.alpha),
    }))
    .sort((a, b) => a.error - b.error);
  const best = results[0];
  const second = results[1];
  const gap = second ? second.error - best.error : 0;
  const enoughSignal = state.activePixels >= 3;
  const confident = enoughSignal && gap >= 4;
  const prefix = confident ? `更像 #${best.imageId}` : '不确定';
  const signalText = enoughSignal ? '' : '，差异像素太少';
  const rankText = results.map((item) => `#${item.imageId} ${item.error.toFixed(1)}`).join('，');
  shapeDiscriminatorResultText.value = `${prefix}，${rankText}，差距 ${gap.toFixed(1)}${signalText}`;
};

const scheduleShapeDiscriminatorSampling = () => {
  shapeDiscriminatorSamplingFrame.value = window.requestAnimationFrame(() => {
    const state = shapeDiscriminatorState.value;
    if (!shapeDiscriminatorDialogVisible.value || !state || !shapeDiscriminatorRunning.value) return;
    const frame = captureLiveShapeImageData(state.width, state.height);
    if (frame) updateShapeDiscriminatorResult(frame);
    scheduleShapeDiscriminatorSampling();
  });
};

const pauseShapeDiscriminatorSampling = () => {
  shapeDiscriminatorRunning.value = false;
  if (shapeDiscriminatorSamplingFrame.value !== null) {
    window.cancelAnimationFrame(shapeDiscriminatorSamplingFrame.value);
    shapeDiscriminatorSamplingFrame.value = null;
  }
};

const stopShapeDiscriminatorSampling = pauseShapeDiscriminatorSampling;

const startShapeDiscriminatorSampling = async () => {
  if (!shapeDiscriminatorState.value) await resetShapeDiscriminator();
  if (!shapeDiscriminatorState.value || shapeDiscriminatorRunning.value) return;
  shapeDiscriminatorRunning.value = true;
  scheduleShapeDiscriminatorSampling();
};

const openShapeDiscriminatorDialog = async () => {
  const shape = selectedShape.value;
  if (!shape || shape.kind === 'group') return;
  const imageId = assetNumericImageId(selectedImageNode.value as GameWindow3AssetNode);
  let group = shape.discriminatorGroupId
    ? discriminatorGroups.value.find((item) => item.id === shape.discriminatorGroupId) ?? null
    : null;
  if (!group) {
    const legacyTarget = shape.discriminator?.targetImageId ?? firstSceneJumpNumericTarget(shape.sceneJumpTarget);
    group = {
      id: createAssetId('disc-group'),
      title: shape.title ? `${shape.title}区分` : '区分组',
      syncBox: true,
      members: [],
    };
    discriminatorGroups.value.push(group);
    shape.discriminatorGroupId = group.id;
    if (imageId !== null) group.members.push({ imageId, shapeId: shape.id, label: shape.discriminatorValue || shape.title || `#${imageId}` });
    if (legacyTarget !== null) {
      const image = findAssetImageByNumericId(assetTree.value, legacyTarget);
      if (image) {
        const linked = createLinkedShapeForImage(image, legacyTarget, image.title || `#${legacyTarget}`);
        group.members.push({ imageId: legacyTarget, shapeId: linked.id, label: linked.discriminatorValue || image.title || `#${legacyTarget}` });
      }
    }
  }
  shapeDiscriminatorGroupId.value = group.id;
  shapeDiscriminatorGroupTitle.value = group.title;
  shapeDiscriminatorSyncBox.value = group.syncBox !== false;
  shapeDiscriminatorMembers.value = group.members.map((member) => ({ ...member }));
  shapeDiscriminatorNewImageId.value = null;
  shapeDiscriminatorNewShapeId.value = '';
  shapeDiscriminatorDialogVisible.value = true;
  await nextTick();
  await resetShapeDiscriminator();
};

const saveShapeDiscriminatorAndClose = () => {
  const shape = selectedShape.value;
  if (!shape || !shapeDiscriminatorReady.value) return;
  let group = currentDiscriminatorGroup();
  if (!group) {
    group = { id: createAssetId('disc-group'), title: '', syncBox: true, members: [] };
    discriminatorGroups.value.push(group);
    shapeDiscriminatorGroupId.value = group.id;
  }
  ensureDiscriminatorCurrentMember();
  group.title = shapeDiscriminatorGroupTitle.value.trim() || '区分组';
  group.syncBox = shapeDiscriminatorSyncBox.value;
  group.members = shapeDiscriminatorMembers.value.map((member) => ({ ...member }));
  if (group.syncBox) {
    for (const member of group.members) {
      const found = findShapeGlobal(member.shapeId);
      if (!found) continue;
      found.shape.x = shape.x;
      found.shape.y = shape.y;
      found.shape.w = shape.w;
      found.shape.h = shape.h;
    }
  }
  for (const member of group.members) {
    const found = findShapeGlobal(member.shapeId);
    if (!found) continue;
    found.shape.discriminatorGroupId = group.id;
    found.shape.discriminatorValue = member.label;
    found.shape.discriminatorEnabled = true;
  }
  shape.discriminatorEnabled = true;
  shape.discriminatorGroupId = group.id;
  shapeDiscriminatorDialogVisible.value = false;
};

const helpSectionHtml = (title: string, lines: string[]) => (
  `<section class="game-window3-help-section"><h4>${title}</h4>${lines.map((line) => `<p>${line}</p>`).join('')}</section>`
);

const showStructuredHelp = (title: string, sections: Array<{ title: string; lines: string[] }>) => {
  ElMessageBox.alert(
    `<div class="game-window3-help">${sections.map((section) => helpSectionHtml(section.title, section.lines)).join('')}</div>`,
    title,
    {
      confirmButtonText: '知道了',
      dangerouslyUseHTMLString: true,
      customClass: 'game-window3-help-message',
    },
  );
};

const showShapeMaskHelp = () => {
  showStructuredHelp('方框抠图说明', [
    {
      title: '1. 适用场景',
      lines: ['背景持续变化，但前景锚点本身稳定。'],
    },
    {
      title: '2. 计算机制',
      lines: [
        '开始后持续采样直播画面，记录每个像素的波动范围。',
        '波动越大的像素，越容易被判定为动态背景，并在 alpha 通道里变透明。',
      ],
    },
    {
      title: '3. 检测效果',
      lines: [
        '保存后，透明像素会被跳过，不参与相似度计算。',
        '等待越久，动态背景识别通常越精细；随时保存就是固化当前结果。',
      ],
    },
  ]);
};

const showShapeToleranceHelp = () => {
  showStructuredHelp('方框容差说明', [
    {
      title: '1. 适用场景',
      lines: ['同一个目标区域本身存在灯光、特效、轻微抖动。'],
    },
    {
      title: '2. 数据结构',
      lines: ['开始后持续采样直播画面，为每个像素记录 RGB 最小值图和最大值图。'],
    },
    {
      title: '3. 检测机制',
      lines: [
        '当前像素落在最小值和最大值范围内时，误差记为 0。',
        '超出范围时，只计算超出的部分。',
      ],
    },
    {
      title: '4. 组合关系',
      lines: ['可与抠图同时使用：抠图跳过不可靠背景，容差兼容仍要参与匹配的正常波动。'],
    },
  ]);
};

const showShapeDiscriminatorHelp = () => {
  showStructuredHelp('方框区分说明', [
    {
      title: '1. 适用场景',
      lines: ['多张图里的同一 shape 位置很像，但存在极小状态差异。'],
    },
    {
      title: '2. 区分组',
      lines: [
        '一个区分组可以包含两个或多个状态。',
        '各状态应尽量绑定同一位置、同一尺寸的 shape。',
      ],
    },
    {
      title: '3. 差异权重',
      lines: [
        '系统比较各状态在同一 shape 区域里的差异，生成差异权重图。',
        '越亮的像素越能区分状态，检测时权重越高。',
      ],
    },
    {
      title: '4. 抠图关系',
      lines: [
        '区分会引入各状态自己的抠图 alpha。',
        '透明区域只对所属状态跳过，不会把所有状态强行取交集。',
      ],
    },
    {
      title: '5. 实时判断',
      lines: [
        '开始后实时截取直播区域，计算更接近哪个状态，并显示各状态误差和差距。',
        '它判断的是“更像哪个状态”，不是“像不像某一张静态图”。',
      ],
    },
  ]);
};

const showSceneJumpHelp = () => {
  showStructuredHelp('场景跳转说明', [
    {
      title: '1. 用途',
      lines: ['场景跳转记录的是当前 shape 被执行后，历史上实际进入过哪些场景。'],
    },
    {
      title: '2. 格式',
      lines: [
        '多个目标用英文逗号分隔。',
        '目标后可以写出现次数，例如 20(5)、登录弹窗(2)。',
        '没有括号表示 0 次，是人工先验，还没有被运行验证过。',
      ],
    },
    {
      title: '3. 频数',
      lines: [
        '步进器运行中识别到真实结果后，会自动递增对应次数。',
        '保存时会按频数降序排序，让高频路径优先参与下一次匹配。',
      ],
    },
    {
      title: '4. -1',
      lines: [
        '-1 必须单独填写。',
        '它表示这个 shape 是退出、空白、返回原场景类动作，不记录具体目标。',
        '某张图片只要存在 -1 shape，就会被步进器视为独立场景候选。',
      ],
    },
    {
      title: '5. 0',
      lines: [
        '0 必须单独填写。',
        '它表示这个 shape 不产生场景跳转记录，但仍然可以作为动作被执行。',
        '适合会触发局部状态变化、开关、展开等不切换场景的区域。',
      ],
    },
    {
      title: '6. 目录路径',
      lines: [
        '可以写目录名，例如 登录弹窗。',
        '表示该目录下的一组场景都可能进入。',
      ],
    },
    {
      title: '7. 旧写法',
      lines: ['? 不再作为有效配置。未知路径由步进器运行时探索，不需要写进字段。'],
    },
  ]);
};

const showStepperHelp = () => {
  showStructuredHelp('步进器说明', [
    {
      title: '1. Tick',
      lines: [
        '步进器每轮只推进一步，不把流程交给一个可能卡死的长函数。',
        '等待也是一个 tick，默认等待 1 秒后重新感知场景。',
      ],
    },
    {
      title: '2. 任务栈',
      lines: [
        '第一版内置到达目标场景任务。',
        '每轮都会根据当前场景和任务目标重新选择下一步动作。',
      ],
    },
    {
      title: '3. 分层并行',
      lines: [
        '识别候选按三组排序：上一步动作的历史目标、独立场景、其他场景。',
        '这三组只是优先级，不是阻塞条件；每个 tick 会收集所有组的候选再统一决策。',
        '前组候选如果有通向目标的可执行路径会优先尝试；尝试不通后，后续 tick 可以落到后组候选继续探索。',
      ],
    },
    {
      title: '4. 自探索',
      lines: [
        '如果没有明确路径，步进器会按动作优先级尝试可点击 shape。',
        '点击后识别到的新场景会写回场景跳转频数，逐步增强标注数据。',
      ],
    },
    {
      title: '5. 轻量加载',
      lines: [
        '标注元数据常驻，图片像素不在页面初始化时全量解码。',
        '每个 tick 只截取一次当前直播帧，候选匹配按需使用 shape 区域和已有匹配接口。',
      ],
    },
  ]);
};

const shapeBoxStyle = (shape: GameWindow3Shape) => ({
  left: (shape.x * 100) + '%',
  top: (shape.y * 100) + '%',
  width: (shape.w * 100) + '%',
  height: (shape.h * 100) + '%',
});

const buildShapeBox = (startX: number, startY: number, endX: number, endY: number): GameWindow3Shape => ({
  id: 'draft-shape',
  kind: 'shape',
  title: '',
  description: '',
  floating: false,
  isSceneIdentity: false,
  sceneIdentityRole: 'off',
  sceneJumpTarget: '',
  contentDirection: 'none',
  imageMatchRole: 'off',
  pixelTolerance: 5,
  ocrMatchRole: 'off',
  ocrEnabled: false,
  ocrText: '',
  ocrMatchMode: 'contains',
  maskEnabled: false,
  alphaMask: null,
  toleranceEnabled: false,
  toleranceRange: null,
  discriminatorEnabled: false,
  discriminator: null,
  discriminatorGroupId: null,
  discriminatorValue: '',
  x: Math.min(startX, endX),
  y: Math.min(startY, endY),
  w: Math.abs(endX - startX),
  h: Math.abs(endY - startY),
  children: [],
});

const getAnnotationRect = () => {
  const canvas = annotationCanvasRef.value;
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return rect;
};

const getAnnotationPoint = (event: PointerEvent, rect = getAnnotationRect()) => {
  if (!rect) return null;
  return {
    x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
    y: clamp((event.clientY - rect.top) / rect.height, 0, 1),
  };
};

const clampShapeBox = (shape: GameWindow3Shape) => {
  shape.w = Math.min(Math.max(shape.w, 0), 1);
  shape.h = Math.min(Math.max(shape.h, 0), 1);
  shape.x = Math.min(Math.max(shape.x, 0), 1 - shape.w);
  shape.y = Math.min(Math.max(shape.y, 0), 1 - shape.h);
};

const updateShapeDraft = (event: PointerEvent) => {
  const state = shapeDraftState.value;
  const point = getAnnotationPoint(event, state?.rect);
  if (!state || state.pointerId !== event.pointerId || !point) return;
  shapeDraftBox.value = buildShapeBox(state.startX, state.startY, point.x, point.y);
};

const finishShapeDraft = (event: PointerEvent) => {
  const state = shapeDraftState.value;
  if (!state || state.pointerId !== event.pointerId) return;
  updateShapeDraft(event);
  const draft = shapeDraftBox.value;
  const image = selectedImageNode.value;
  shapeDraftState.value = null;
  shapeDraftBox.value = null;
  window.removeEventListener('pointermove', updateShapeDraft);
  window.removeEventListener('pointerup', finishShapeDraft);
  window.removeEventListener('pointercancel', cancelShapeDraft);
  try {
    annotationCanvasRef.value?.releasePointerCapture(event.pointerId);
  } catch {
    // Pointer capture may already be released by the browser.
  }
  if (!draft || !image) {
    return;
  }
  image.shapes ??= [];
  const shape: GameWindow3Shape = {
    ...draft,
    id: createAssetId('shape'),
    kind: 'shape',
    title: 'shape ' + (flattenShapes(image.shapes ?? []).filter(isDrawableShape).length + 1),
    description: '',
    floating: false,
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    contentDirection: 'none',
    imageMatchRole: 'off',
    pixelTolerance: 5,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    maskEnabled: false,
    alphaMask: null,
    toleranceEnabled: false,
    toleranceRange: null,
    discriminatorEnabled: false,
    discriminator: null,
    discriminatorGroupId: null,
    discriminatorValue: '',
    children: [],
  };
  clampShapeBox(shape);
  image.shapes ??= [];
  image.shapes.push(shape);
  selectedShapeId.value = shape.id;
};

const cancelShapeDraft = () => {
  shapeDraftState.value = null;
  shapeDraftBox.value = null;
  window.removeEventListener('pointermove', updateShapeDraft);
  window.removeEventListener('pointerup', finishShapeDraft);
  window.removeEventListener('pointercancel', cancelShapeDraft);
};

const startShapeDraft = (event: PointerEvent) => {
  if (event.button !== 0 || !selectedImageNode.value || shapeDragState.value || screenshotSpacePressed.value || screenshotPanState.value) return;
  const rect = getAnnotationRect();
  const point = getAnnotationPoint(event, rect);
  if (!point) return;
  event.preventDefault();
  event.stopPropagation();
  annotationCanvasRef.value?.setPointerCapture(event.pointerId);
  shapeDraftState.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
    rect: rect as DOMRect,
  };
  shapeDraftBox.value = buildShapeBox(point.x, point.y, point.x, point.y);
  window.addEventListener('pointermove', updateShapeDraft);
  window.addEventListener('pointerup', finishShapeDraft);
  window.addEventListener('pointercancel', cancelShapeDraft);
};

const startShapeDrag = (event: PointerEvent, shapeId: string, mode: ShapeDragState['mode']) => {
  const shape = findShapeById(selectedImageShapes.value, shapeId);
  if (!shape) return;
  selectedShapeId.value = shapeId;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  shapeDragState.value = {
    pointerId: event.pointerId,
    shapeId,
    mode,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startBox: {
      x: shape.x,
      y: shape.y,
      w: shape.w,
      h: shape.h,
    },
  };
  window.addEventListener('pointermove', moveShapeDrag);
  window.addEventListener('pointerup', finishShapeDrag, { once: true });
};

const startShapeMove = (event: PointerEvent, shapeId: string) => startShapeDrag(event, shapeId, 'move');
const startShapeResize = (event: PointerEvent, shapeId: string, mode: Extract<ShapeDragState['mode'], 'top-left' | 'bottom-right'>) => {
  startShapeDrag(event, shapeId, mode);
};

const moveShapeDrag = (event: PointerEvent) => {
  const state = shapeDragState.value;
  const canvas = annotationCanvasRef.value;
  if (!state || state.pointerId !== event.pointerId || !canvas) return;
  const shape = findShapeById(selectedImageShapes.value, state.shapeId);
  if (!shape) return;
  const rect = canvas.getBoundingClientRect();
  const dx = (event.clientX - state.startClientX) / Math.max(rect.width, 1);
  const dy = (event.clientY - state.startClientY) / Math.max(rect.height, 1);
  if (state.mode === 'move') {
    shape.x = state.startBox.x + dx;
    shape.y = state.startBox.y + dy;
    clampShapeBox(shape);
  } else if (state.mode === 'bottom-right') {
    shape.w = state.startBox.w + dx;
    shape.h = state.startBox.h + dy;
    clampShapeBox(shape);
  } else {
    const right = state.startBox.x + state.startBox.w;
    const bottom = state.startBox.y + state.startBox.h;
    const nextX = clamp(state.startBox.x + dx, 0, right);
    const nextY = clamp(state.startBox.y + dy, 0, bottom);
    shape.x = nextX;
    shape.y = nextY;
    shape.w = right - nextX;
    shape.h = bottom - nextY;
  }
};

const finishShapeDrag = () => {
  window.removeEventListener('pointermove', moveShapeDrag);
  shapeDragState.value = null;
};
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

.behavior-controls {
  gap: 8px;
  max-width: 100%;
}

.behavior-function-select {
  width: 92px;
}

.behavior-preset-select {
  width: 168px;
}

.behavior-row-break {
  flex-basis: 100%;
  width: 0;
  height: 0;
}

.behavior-run-status {
  min-width: 0;
  max-width: 520px;
  overflow: hidden;
  color: #606266;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-find-summary {
  max-width: 360px;
  overflow: hidden;
  color: #337ecc;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stepper-log-list {
  height: 50vh;
  overflow: auto;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.stepper-log-row {
  display: grid;
  grid-template-columns: 76px 44px minmax(0, 1fr);
  align-items: start;
  gap: 8px;
  min-height: 30px;
  padding: 7px 10px;
  color: #374151;
  font-size: 12px;
  line-height: 1.35;
  border-bottom: 1px solid #f1f5f9;
}

.stepper-log-row:last-child {
  border-bottom: 0;
}

.stepper-log-row.is-action {
  background: #eff6ff;
}

.stepper-log-row.is-success {
  background: #f0fdf4;
}

.stepper-log-row.is-error {
  background: #fef2f2;
}

.stepper-log-time,
.stepper-log-kind {
  color: #6b7280;
  white-space: nowrap;
}

.stepper-log-message {
  min-width: 0;
  word-break: break-all;
}

.stepper-log-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 8px;
  color: #6b7280;
  font-size: 12px;
}

.stepper-log-empty {
  display: grid;
  min-height: 180px;
  place-items: center;
  color: #9ca3af;
  font-size: 13px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.burst-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
}

.burst-summary {
  color: #4b5563;
  font-size: 12px;
}

.burst-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.burst-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
  min-height: 260px;
  max-height: 50vh;
  overflow: auto;
  padding: 2px;
}

.burst-card {
  position: relative;
  min-width: 0;
  border: 1px solid #e5e7eb;
  background: #fff;
  cursor: pointer;
}

.burst-card.is-selected {
  border-color: #409eff;
  box-shadow: 0 0 0 1px rgba(64, 158, 255, 0.35);
}

.burst-card-check {
  position: absolute;
  top: 4px;
  left: 6px;
  z-index: 1;
}

.burst-card img,
.burst-card-empty {
  display: block;
  width: 100%;
  aspect-ratio: 9 / 16;
  object-fit: contain;
  background: #0b0f14;
}

.burst-card-empty {
  display: grid;
  place-items: center;
  color: #9ca3af;
  font-size: 12px;
}

.burst-card-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.25;
}

.burst-card-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.burst-empty {
  grid-column: 1 / -1;
  display: grid;
  min-height: 220px;
  place-items: center;
  color: #9ca3af;
  font-size: 13px;
}

.burst-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 10px;
  color: #6b7280;
  font-size: 12px;
}

.game-macro-config {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.game-macro-config-row {
  display: grid;
  grid-template-columns: 120px max-content;
  align-items: center;
  gap: 12px;
  color: #4b5563;
  font-size: 12px;
}

.game-macro-config-row .el-input-number {
  width: 72px;
}

.game-macro-config-row .el-select {
  width: 140px;
}

.switch-field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.switch-row {
  width: 100%;
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

.channel-select {
  width: 76px;
}

.mode-select {
  width: 76px;
}

.connection-status {
  min-width: 48px;
  color: #6b7280;
  font-size: 12px;
  white-space: nowrap;
}

.connection-status.is-ready {
  color: #16a34a;
  font-weight: 600;
}

.capture-runtime-link {
  border: 0;
  background: transparent;
  color: #5f6b7a;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
  white-space: nowrap;
}

.capture-runtime-link:hover {
  color: #1677ff;
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

.actual-fps-text {
  color: #6b7280;
  font-size: 12px;
  white-space: nowrap;
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
  width: 100%;
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
  width: 100%;
  height: 100%;
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
    align-items: flex-start;
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

.annotation-panel {
  flex: 1 1 0;
  min-width: 280px;
  border-left: 1px solid #dcdfe6;
  background: #fff;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.annotation-panel-head,
.annotation-workbench-head {
  min-height: 40px;
  padding: 8px 10px;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
  color: #303133;
}

.annotation-panel-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.annotation-title-tools {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.asset-tree-scroll {
  flex: 1 1 auto;
  min-height: 0;
  padding: 6px 8px;
  overflow-x: auto;
  overflow-y: scroll;
}

.asset-tree {
  min-width: max-content;
}

.asset-tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.asset-tree-node.is-image {
  color: #409eff;
}

.asset-node-id {
  flex: 0 0 auto;
  min-width: 28px;
  color: #909399;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.asset-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 96px;
  padding: 4px;
  background: #fff;
  border: 1px solid #dcdfe6;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
}

.asset-context-menu button {
  width: 100%;
  padding: 6px 10px;
  color: #303133;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.asset-context-menu button:hover {
  background: #f5f7fa;
}

.asset-context-menu button.is-danger {
  color: #dc2626;
}

.shape-context-menu {
  min-width: 72px;
}

.annotation-workbench {
  width: 100%;
  margin-top: 12px;
  border-top: 1px solid #dcdfe6;
  background: #fff;
  display: flex;
  flex-direction: column;
}

.annotation-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 10px;
  overflow: auto;
}

.annotation-main-row {
  display: flex;
  align-items: stretch;
  gap: 10px;
  min-width: 0;
  width: 100%;
}

.annotation-canvas {
  position: relative;
  align-self: start;
  flex: 0 0 auto;
  border: 1px solid #dcdfe6;
  background: #f5f7fa;
  overflow: hidden;
  user-select: none;
  touch-action: none;
  cursor: crosshair;
}

.annotation-image {
  pointer-events: none;
}

.annotation-preview {
  align-self: start;
}

.annotation-image-wrap {
  cursor: crosshair;
  touch-action: none;
  user-select: none;
}

.empty-image-surface {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 13px;
  background: #f8fafc;
  pointer-events: none;
}

.annotation-shape {
  position: absolute;
  border: 2px solid #409eff;
  background: transparent;
  box-sizing: border-box;
  cursor: move;
  overflow: visible;
}

.annotation-shape.is-active {
  border-color: #e6a23c;
}

.annotation-shape.is-draft {
  border-color: #e6a23c;
  background: rgba(230, 162, 60, 0.12);
  color: transparent;
  pointer-events: none;
}

.annotation-occlusion-mask {
  position: absolute;
  z-index: 1;
  pointer-events: none;
  background: rgba(245, 108, 108, 0.24);
  border: 1px dashed rgba(220, 38, 38, 0.75);
  box-sizing: border-box;
}

.shape-corner-handle {
  position: absolute;
  width: 9px;
  height: 9px;
  border: 2px solid currentColor;
  border-radius: 50%;
  background: #fff;
  padding: 0;
  box-sizing: border-box;
}

.shape-corner-handle.is-top-left {
  left: -5px;
  top: -5px;
  cursor: nwse-resize;
}

.shape-corner-handle.is-bottom-right {
  right: -5px;
  bottom: -5px;
  cursor: nwse-resize;
}

.shape-tree-scroll {
  flex: 1 1 0;
  min-width: 180px;
  min-height: 0;
  overflow-x: auto;
  overflow-y: scroll;
  border: 1px solid #ebeef5;
}

.shape-tree {
  min-width: max-content;
}

.shape-tree-node.is-group {
  font-weight: 600;
  color: #303133;
}

.shape-tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.shape-tree-node.is-selected {
  color: #409eff;
}

.shape-fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid #ebeef5;
}

.shape-detect-row {
  display: flex;
  align-items: center;
  gap: 10px 14px;
  min-height: 24px;
  flex-wrap: wrap;
  color: var(--el-text-color-regular);
  font-size: 12px;
}

.shape-action-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
}

.shape-condition-toggle {
  display: inline-grid;
  width: 18px;
  height: 18px;
  place-items: center;
  padding: 0;
  border: 1px solid #dcdfe6;
  background: #fff;
  color: #909399;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}

.shape-condition-toggle.is-optional {
  border-color: #409eff;
  color: #409eff;
  background: #ecf5ff;
}

.shape-condition-toggle.is-required {
  border-color: #67c23a;
  color: #fff;
  background: #67c23a;
}

.shape-detect-row :deep(.el-checkbox) {
  height: 24px;
  margin-right: 0;
  --el-checkbox-font-size: 12px;
}

.shape-detect-row :deep(.el-checkbox__label) {
  padding-left: 6px;
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 24px;
}

.shape-detect-row :deep(.el-checkbox__inner) {
  width: 14px;
  height: 14px;
}

.shape-detect-row :deep(.el-button--small) {
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
}

.shape-detect-row :deep(.el-input--small .el-input__wrapper),
.shape-detect-row :deep(.el-input-number--small .el-input__wrapper),
.shape-detect-row :deep(.el-select--small .el-select__wrapper) {
  min-height: 24px;
  height: 24px;
  font-size: 12px;
}

.shape-jump-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 1;
}

.shape-jump-field span {
  color: var(--el-text-color-regular);
  font-size: 12px;
  white-space: nowrap;
}

.shape-inline-help {
  width: 16px;
  height: 16px;
  padding: 0;
  border: 1px solid #cdd6e1;
  border-radius: 50%;
  background: #fff;
  color: #8a96a3;
  font-size: 11px;
  line-height: 14px;
  cursor: pointer;
}

.shape-inline-help:hover {
  color: #409eff;
  border-color: #409eff;
}

.shape-jump-field .el-input-number {
  width: 84px;
}

.shape-jump-field .shape-pixel-tolerance-input {
  width: 48px;
}

.shape-ocr-config {
  gap: 6px;
}

.shape-ocr-text-input {
  width: 160px;
}

.shape-ocr-mode-select {
  width: 72px;
}

.shape-row-break {
  flex-basis: 100%;
  width: 0;
  height: 0;
}

.shape-jump-field .shape-direction-select {
  width: 44px;
}

.shape-jump-field :deep(.el-input__wrapper) {
  height: 24px;
  min-height: 24px;
}

.shape-jump-field :deep(.el-input__inner) {
  height: 22px;
  line-height: 22px;
  font-size: 12px;
}

.shape-jump-field :deep(.el-select__wrapper) {
  min-height: 24px;
  height: 24px;
  font-size: 12px;
}

.shape-jump-field :deep(.el-select__placeholder),
.shape-jump-field :deep(.el-select__selected-item) {
  font-size: 12px;
}

.shape-detect-group {
  min-height: 24px;
}

.shape-detect-result {
  color: #606266;
  font-size: 12px;
}

.shape-dialog-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.shape-help-button {
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid #cdd6e1;
  border-radius: 50%;
  background: #fff;
  color: #8a96a3;
  font-size: 12px;
  line-height: 16px;
  cursor: pointer;
}

.shape-help-button:hover {
  color: #409eff;
  border-color: #409eff;
}

.shape-mask-tool {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.shape-mask-previews {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.shape-mask-previews.is-three {
  grid-template-columns: repeat(3, 1fr);
}

.shape-discriminator-shape-select {
  width: 210px;
}

.shape-discriminator-members {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.shape-discriminator-member {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border: 1px solid #dcdfe6;
  background: #fff;
}

.shape-discriminator-member > span {
  color: #909399;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.shape-discriminator-member .el-input {
  width: 110px;
}

.shape-discriminator-remove {
  width: 20px;
  height: 20px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #dc2626;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.shape-discriminator-remove:hover {
  background: #fef2f2;
  border-color: #fecaca;
}

.shape-mask-preview {
  min-height: 220px;
  border: 1px solid #dcdfe6;
  background:
    linear-gradient(45deg, #f2f3f5 25%, transparent 25%),
    linear-gradient(-45deg, #f2f3f5 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #f2f3f5 75%),
    linear-gradient(-45deg, transparent 75%, #f2f3f5 75%);
  background-color: #fff;
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
}

.shape-mask-label {
  padding: 6px 8px;
  border-bottom: 1px solid #dcdfe6;
  background: #fff;
  font-size: 12px;
  color: #606266;
}

.shape-mask-preview img {
  display: block;
  max-width: 100%;
  max-height: 360px;
  margin: 0 auto;
  object-fit: contain;
}

.shape-mask-empty {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 12px;
}

.shape-mask-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.shape-mask-slider {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 300px;
}

.shape-mask-slider span {
  flex: 0 0 56px;
  white-space: nowrap;
}

.shape-mask-slider .el-slider {
  flex: 1;
  min-width: 0;
}

:global(.game-window3-help-message) {
  width: min(520px, calc(100vw - 32px));
}

:global(.game-window3-help) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: #303133;
  font-size: 14px;
  line-height: 1.7;
}

:global(.game-window3-help-section) {
  margin: 0;
}

:global(.game-window3-help-section h4) {
  margin: 0 0 4px;
  color: #1f2937;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

:global(.game-window3-help-section p) {
  margin: 0;
  color: #606266;
}

.annotation-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 13px;
}

@media (max-width: 1180px) {
  .annotation-panel {
    width: 100%;
    min-width: 0;
    border-left: 0;
    border-top: 1px solid #dcdfe6;
  }

  .annotation-main-row {
    flex-direction: column;
  }

  .shape-tree-scroll {
    width: 100%;
    max-width: none;
    max-height: 220px;
  }
}

</style>
