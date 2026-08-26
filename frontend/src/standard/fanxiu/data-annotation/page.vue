<template>
  <div class="game-window-page">
    <section class="stage-pane">
      <div class="topbar">
        <div class="topbar-content">
          <h2>数据标注</h2>
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
                <el-button
                  size="small"
                  plain
                  @click="router.push({ path: '/fanxiu/data-annotation/runtime', query: { ...route.query, entry_id: selectedEntryId || route.query.entry_id } })"
                >
                  行为树 Runtime
                </el-button>
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
                <div class="annotation-panel-actions">
                  <el-select
                    v-model="assetTreeViewMode"
                    class="asset-view-select"
                    size="small"
                    aria-label="资产树视图"
                  >
                    <el-option
                      v-for="option in assetTreeViewModeOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                  <el-input
                    v-if="assetTreeViewMode !== 'recognitionOps'"
                    v-model="assetFrameSearchText"
                    class="asset-frame-search"
                    size="small"
                    placeholder="编号/名称"
                    clearable
                    :prefix-icon="Search"
                    @keyup.enter="searchAssetFrame"
                  />
                  <el-button
                    v-if="assetTreeViewMode !== 'recognitionOps'"
                    size="small"
                    :icon="Fold"
                    title="全部折叠"
                    aria-label="全部折叠资产树"
                    :disabled="!hasExpandedAssetTreeNodes"
                    @click="collapseAssetTree"
                  />
                  <el-button
                    v-if="assetTreeViewMode !== 'recognitionOps'"
                    size="small"
                    :icon="Aim"
                    title="对齐凡修信息窗当前场景"
                    aria-label="对齐凡修信息窗当前场景"
                    :loading="alignCurrentSceneLoading"
                    :disabled="!selectedEntryId"
                    @click="alignAssetTreeToCurrentScene"
                  />
                  <el-button
                    v-if="assetTreeViewMode === 'business'"
                    size="small"
                    :icon="FolderAdd"
                    title="新建分组"
                    aria-label="新建分组"
                    @click="addAssetFolder"
                  />
                  <el-button
                    v-if="assetTreeViewMode !== 'recognitionOps'"
                    size="small"
                    :icon="Picture"
                    title="保存当前帧"
                    aria-label="保存当前帧"
                    :loading="saveFrameLoading"
                    :disabled="!selectedEntryId"
                    @click="saveCurrentFrame"
                  />
                  <el-button
                    v-if="assetTreeViewMode !== 'recognitionOps'"
                    size="small"
                    :type="burstCaptureRunning ? 'primary' : 'default'"
                    title="连拍管理"
                    aria-label="连拍管理"
                    :disabled="!selectedEntryId"
                    @click="openBurstDialog"
                  >
                    {{ burstCaptureRunning ? '连拍中' : '连拍' }}
                  </el-button>
                  <div v-if="assetTreeViewMode === 'recognitionOps'" class="recognition-ops-toolbar">
                    <span>{{ recognitionOpsMatrixText }}</span>
                    <el-button
                      size="small"
                      plain
                      :loading="recognitionOpsLoading || recognitionOpsRecomputing"
                      :disabled="!selectedEntryId || recognitionOpsRecomputing"
                      :title="recognitionOpsCacheMissing ? '生成当前资产树的识别矩阵' : '按当前资产树签名重算识别矩阵'"
                      @click="loadRecognitionOps(true)"
                    >
                      {{ recognitionOpsRecomputing ? '重算中' : recognitionOpsCacheMissing ? '生成矩阵' : '重算脏节点' }}
                    </el-button>
                  </div>
                </div>
              </div>

              <div v-if="assetTreeViewMode !== 'recognitionOps'" ref="assetTreeScrollRef" class="asset-tree-scroll">
                <el-tree
                  ref="assetTreeRef"
                  class="asset-tree"
                  :data="assetTreeDisplayData"
                  :props="assetTreeProps"
                  node-key="id"
                  :default-expanded-keys="assetTreeDefaultExpandedKeys"
                  :auto-expand-parent="false"
                  highlight-current
                  :draggable="assetTreeViewMode === 'business'"
                  :expand-on-click-node="false"
                  :current-node-key="selectedAssetId"
                  :allow-drop="allowAssetDrop"
                  :filter-node-method="filterAssetTreeNode"
                  @keydown="handleAssetTreeDirectionKey"
                  @node-click="selectAssetNode"
                  @node-drop="handleAssetNodeDrop"
                  @node-expand="node => setAssetNodeExpanded(node.id, true)"
                  @node-collapse="node => setAssetNodeExpanded(node.id, false)"
                  @node-contextmenu="openAssetContextMenu"
                >
                  <template #default="{ data }">
                    <span
                      class="asset-tree-node"
                      :class="assetTreeNodeClasses(data)"
                      :title="assetTreeNodeTitle(data)"
                      @dblclick.stop="renameDisplayAssetNode(data)"
                    >
                      <el-icon v-if="data.type === 'folder'"><Folder /></el-icon>
                      <span v-else class="asset-node-id">{{ assetImageIdMark(data) }}</span>
                      <span
                        class="asset-node-title"
                        :style="data.type === 'image' ? frameLayerStyle(inferredFrameLayer(data)) : undefined"
                      >{{ data.title }}</span>
                    </span>
                  </template>
                </el-tree>
              </div>
                <div v-else class="recognition-ops-panel">
                  <div v-if="recognitionOpsError" class="recognition-ops-error">{{ recognitionOpsError }}</div>
                  <div v-else-if="recognitionOpsLoading && !recognitionOpsReport" class="recognition-ops-empty">计算中</div>
                  <div v-else-if="recognitionOpsCacheMissing && !recognitionOpsReport?.summary.issue_count" class="recognition-ops-empty">
                    全量匹配矩阵未生成，当前无法判断 match 异常
                  </div>
                  <template v-else>
                    <div v-if="recognitionOpsCacheMissing" class="recognition-ops-cache-note">
                      识别矩阵未生成；下方仍显示运行中已留存的问题
                    </div>
                    <div class="recognition-ops-tree-wrap">
                      <el-tree
                        class="recognition-ops-tree"
                        :data="recognitionOpsTreeData"
                        node-key="id"
                        default-expand-all
                        :expand-on-click-node="false"
                        @node-click="handleRecognitionOpsNodeClick"
                      >
                        <template #default="{ data }">
                          <span
                            class="recognition-ops-node"
                            :class="{ 'is-issue': data.type === 'issue', 'is-selected': data.issueId === selectedRecognitionOpsIssueId }"
                            :title="data.label"
                          >
                            {{ data.label }}
                          </span>
                        </template>
                      </el-tree>
                      <div v-if="!recognitionOpsReport?.summary.issue_count" class="recognition-ops-empty">暂无异常</div>
                    </div>
                </template>
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
                <button v-if="assetContextMenuNode" type="button" class="is-danger" @click="deleteAssetFromContextMenu">
                  删除
                </button>
              </div>

            </aside>
          </div>

          <section class="annotation-workbench">
            <div class="annotation-workbench-head">
              <div class="annotation-title-tools">
                <span>{{ selectedRecognitionOpsIssue?.label || selectedImageTitleText }}</span>
                <div v-if="selectedImageNode" class="shape-jump-field scene-parent-field">
                  <span>继承</span>
                  <el-input
                    v-model="selectedSceneParentIds"
                    size="small"
                    placeholder="424, 34"
                    title="逗号分隔多个要继承的场景编号"
                    @blur="normalizeSelectedSceneParentIds"
                  />
                </div>
                <el-checkbox v-if="selectedImageNode" v-model="globalOcclusionMaskEnabled" size="small">
                  遮挡
                </el-checkbox>
                <button
                  v-if="selectedImageNode"
                  type="button"
                  class="image-compare-trigger"
                  :disabled="imageCompareLoading"
                  title="图片对比"
                  aria-label="图片对比"
                  @click="openImageCompareDialog"
                >
                  图片对比
                </button>
                <button
                  v-if="selectedImageUsesJpegFrame"
                  type="button"
                  class="jpeg-frame-reset-hint"
                  title="JPG 有压缩损失，建议用当前画面重置为 PNG 帧"
                  @click="resetSelectedAssetFrameFromHint"
                >
                  JPG，建议重置 PNG
                </button>
              </div>
            </div>

            <div v-if="selectedRecognitionOpsIssue?.incident" class="navigation-incident-detail">
              <div v-if="navigationIncidentLoading" class="annotation-empty">加载复盘证据</div>
              <div v-else-if="navigationIncidentError" class="recognition-ops-error">{{ navigationIncidentError }}</div>
              <template v-else-if="selectedNavigationIncident">
                <div class="navigation-incident-facts">
                  <span>{{ navigationIncidentStatusLabel(selectedNavigationIncident.status) }}</span>
                  <span>目标 #{{ selectedNavigationIncident.target_scene_id ?? '?' }}</span>
                  <span>{{ selectedNavigationIncident.elapsed_seconds ?? 0 }} 秒</span>
                  <span v-if="selectedNavigationIncident.runtime?.task">{{ selectedNavigationIncident.runtime.task }}</span>
                  <span v-if="selectedNavigationIncident.runtime?.cell_id">
                    {{ selectedNavigationIncident.runtime.cell_id }}
                  </span>
                  <span v-if="selectedNavigationIncident.runtime?.kernel_generation !== null && selectedNavigationIncident.runtime?.kernel_generation !== undefined">
                    Kernel {{ selectedNavigationIncident.runtime.kernel_generation }}
                  </span>
                </div>
                <div class="navigation-incident-trigger">
                  {{ navigationIncidentRecordText(selectedNavigationIncident.trigger, 'label') || '导航停滞' }}
                </div>
                <div class="navigation-incident-timeline-wrap">
                  <table class="navigation-incident-timeline">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>识别</th>
                        <th>动作</th>
                        <th>真实落点</th>
                        <th>画面变化</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="item in selectedNavigationIncident.timeline"
                        :key="item.index"
                        :class="{ 'is-selected': item.index === selectedNavigationTimelineIndex }"
                        @click="selectedNavigationTimelineIndex = item.index"
                      >
                        <td>{{ item.index }}</td>
                        <td>{{ navigationIncidentSceneText(item.recognized_scene_id, item.recognized_score) }}</td>
                        <td>{{ item.kind === 'fallback' ? '#424[返回]' : `#${item.source_scene_id ?? '?'}[${item.shape_title || '?'}]` }}</td>
                        <td>{{ navigationIncidentSceneText(item.landing_scene_id, item.landing_score) }}</td>
                        <td>{{ item.frame_similarity === null || item.frame_similarity === undefined ? '--' : `${item.frame_similarity}%` }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-if="selectedNavigationTimelineItem" class="navigation-incident-frame-pair">
                  <figure>
                    <figcaption>动作前真实帧</figcaption>
                    <img
                      v-if="navigationIncidentFrameUrl(selectedNavigationTimelineItem.before_frame)"
                      :src="navigationIncidentFrameUrl(selectedNavigationTimelineItem.before_frame)"
                      alt="动作前真实帧"
                    />
                    <div v-else class="navigation-incident-frame-empty">未保存</div>
                  </figure>
                  <figure>
                    <figcaption>动作后真实帧</figcaption>
                    <img
                      v-if="navigationIncidentFrameUrl(selectedNavigationTimelineItem.after_frame)"
                      :src="navigationIncidentFrameUrl(selectedNavigationTimelineItem.after_frame)"
                      alt="动作后真实帧"
                    />
                    <div v-else class="navigation-incident-frame-empty">未保存</div>
                  </figure>
                  <div class="navigation-incident-step-detail">
                    <span v-if="selectedNavigationTimelineItem.point">
                      点击 ({{ selectedNavigationTimelineItem.point[0] }}, {{ selectedNavigationTimelineItem.point[1] }})；
                    </span>
                    {{ selectedNavigationTimelineItem.reason || '无动作选择说明' }}
                  </div>
                </div>
                <details v-if="selectedNavigationIncident.diagnostic" class="navigation-incident-diagnostic">
                  <summary>识别候选与 OCR 证据</summary>
                  <div v-if="navigationIncidentIdentityCrops.length" class="navigation-incident-crops">
                    <figure v-for="crop in navigationIncidentIdentityCrops" :key="crop.path">
                      <img :src="navigationIncidentFrameUrl(crop.path)" :alt="crop.shape_title || '场景身份裁剪'" />
                      <figcaption>#{{ crop.scene_id ?? '?' }} {{ crop.shape_title || '场景身份' }}</figcaption>
                    </figure>
                  </div>
                  <pre>{{ navigationIncidentDiagnosticText }}</pre>
                </details>
              </template>
            </div>

            <div v-else-if="selectedRecognitionOpsIssue?.ambiguity" class="navigation-incident-detail">
              <div v-if="recognitionAmbiguityLoading" class="annotation-empty">加载并列样本</div>
              <div v-else-if="recognitionAmbiguityError" class="recognition-ops-error">{{ recognitionAmbiguityError }}</div>
              <template v-else-if="selectedRecognitionAmbiguity">
                <div class="navigation-incident-facts">
                  <span>Layer {{ selectedRecognitionAmbiguity.layer }}</span>
                  <span>{{ selectedRecognitionAmbiguity.occurrence_count }} 次</span>
                  <span>{{ selectedRecognitionAmbiguity.distinct_frame_count }} 张不同画面</span>
                  <span>首次 {{ selectedRecognitionAmbiguity.first_seen_at.replace('T', ' ') }}</span>
                  <span>最近 {{ selectedRecognitionAmbiguity.last_seen_at.replace('T', ' ') }}</span>
                  <el-button
                    size="small"
                    plain
                    :loading="recognitionAmbiguityRecomputing"
                    @click="loadRecognitionAmbiguity(selectedRecognitionAmbiguity.signature, true)"
                  >按当前资产重算</el-button>
                </div>
                <div class="navigation-incident-trigger">
                  并列候选 {{ selectedRecognitionAmbiguity.tied_scene_ids.map(id => `#${id}`).join(' / ') }}；
                  临时选择 {{ recognitionAmbiguitySelectionText || '无' }}
                </div>
                <div v-if="selectedRecognitionAmbiguity.sample_frames.length" class="navigation-incident-crops">
                  <figure v-for="frame in selectedRecognitionAmbiguity.sample_frames" :key="frame.sha256">
                    <img :src="recognitionAmbiguityFrameUrl(frame.path)" alt="识别并列现场原帧" />
                    <figcaption>
                      {{ frame.captured_at?.replace('T', ' ') || '现场原帧' }}
                      · {{ frame.fallback_scene_id ? `临时 #${frame.fallback_scene_id}` : '未解决' }}
                    </figcaption>
                  </figure>
                </div>
                <details v-if="selectedRecognitionAmbiguity.recompute" class="navigation-incident-diagnostic">
                  <summary>当前资产重算结果</summary>
                  <pre>{{ JSON.stringify(selectedRecognitionAmbiguity.recompute, null, 2) }}</pre>
                </details>
              </template>
            </div>

            <div v-else-if="selectedImageNode" class="annotation-editor">
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
                        @error="recoverSelectedImagePreview"
                      />
                      <div
                        v-else
                        class="empty-image-surface"
                        :class="{ 'is-missing': selectedImagePreviewMissing }"
                        @click.stop="retrySelectedImagePreview"
                      >
                        <span>{{ selectedImagePlaceholderText }}</span>
                      </div>
                      <div
                        v-for="shape in occlusionOverlayShapes"
                        :key="'occlusion-' + shape.id"
                        class="annotation-occlusion-mask"
                        :style="shapeBoxStyle(shape)"
                      />
                      <div
                        v-for="shape in annotationShapes"
                        :key="shape.id"
                        class="annotation-shape"
                        :class="{
                          'is-active': isShapeSelected(shape.id),
                          'is-locked': isShapeLocked(shape),
                          'is-scene-identity': isSceneIdentityShape(shape),
                        }"
                        :style="shapeBoxStyle(shape)"
                        @pointerdown.stop="startShapeMove($event, shape.id)"
                        @contextmenu.prevent.stop="openShapeContextMenu($event, shape.id)"
                      >
                        <button
                          v-if="!isShapeLocked(shape)"
                          type="button"
                          class="shape-corner-handle is-top-left"
                          title="拖拽调整左上角"
                          aria-label="拖拽调整左上角"
                          @pointerdown.stop="startShapeResize($event, shape.id, 'top-left')"
                        />
                        <button
                          v-if="!isShapeLocked(shape)"
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
                    ref="shapeTreeRef"
                    class="shape-tree"
                    :data="selectedImageShapes"
                    :props="shapeTreeProps"
                    node-key="id"
                    :default-expanded-keys="expandedShapeNodeIds"
                    :auto-expand-parent="false"
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
                      <span
                        class="shape-tree-node"
                        :class="{
                          'is-group': data.kind === 'group',
                          'is-selected': isShapeSelected(data.id),
                          'is-scene-identity': data.kind !== 'group' && isSceneIdentityShape(data),
                          'is-ocr-suggested': data.kind !== 'group' && isShapeOcrSuggested(data),
                        }"
                        :title="shapeOcrSuggestionTitle(data)"
                      >
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
                  <span class="shape-action-group">
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
                    <span>场景标识</span>
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
                    <el-popover placement="bottom-start" :width="310" trigger="click">
                      <template #reference>
                        <button type="button" class="shape-load-config-label">
                          窗口加载方向
                        </button>
                      </template>
                      <div class="shape-load-config">
                        <div class="shape-load-config-row">
                          <span>步进</span>
                          <el-segmented
                            v-model="selectedShapeLoadMode"
                            :options="[
                              { label: '连续', value: 'continuous' },
                              { label: '整页（卡片）', value: 'paged' },
                            ]"
                            size="small"
                          />
                        </div>
                        <div class="shape-load-config-row">
                          <span>边界</span>
                          <el-segmented
                            v-model="selectedShapeLoadBoundary"
                            :options="[
                              { label: '有限', value: 'bounded' },
                              { label: '循环', value: 'cyclic' },
                            ]"
                            size="small"
                          />
                        </div>
                        <div class="shape-load-config-row">
                          <span>初始位置</span>
                          <el-segmented
                            v-model="selectedShapeLoadInitialPosition"
                            :options="[
                              { label: '起始端', value: 'start' },
                              { label: '未知', value: 'unknown' },
                            ]"
                            size="small"
                          />
                        </div>
                      </div>
                    </el-popover>
                    <el-select v-model="selectedShape.loadDirection" class="shape-direction-select" size="small">
                      <el-option label="无" value="none" />
                      <el-option label="↑" value="up" />
                      <el-option label="↓" value="down" />
                      <el-option label="←" value="left" />
                      <el-option label="→" value="right" />
                    </el-select>
                  </div>
                  <el-checkbox v-model="selectedShape.floating">
                    浮动
                  </el-checkbox>
                  <el-checkbox v-model="selectedShape.locked">
                    锁定
                  </el-checkbox>
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
                    <el-button size="small" :disabled="!selectedShape" @click="openShapeMaskDialog('image')">
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
                  <div class="shape-action-group shape-jitter-config">
                    <el-checkbox v-model="selectedShape.jitterEnabled" title="启用抖动校正" aria-label="启用抖动校正">
                      抖动
                    </el-checkbox>
                    <el-input-number
                      v-if="selectedShape.jitterEnabled"
                      v-model="selectedShape.jitterRadius"
                      class="shape-jitter-radius-input"
                      size="small"
                      :min="1"
                      :max="12"
                      :step="1"
                      :controls="false"
                    />
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
                    <el-select
                      v-if="selectedShapeShowsOcrMaskControls"
                      v-model="selectedShape.ocrMaskMode"
                      class="shape-ocr-mask-mode-select"
                      size="small"
                      title="OCR抠图策略"
                    >
                      <el-option label="继承区域" value="inherit-envelope" />
                      <el-option label="OCR专用" value="custom" />
                      <el-option label="不用抠图" value="off" />
                      <el-option label="原始抠图" value="raw-alpha" />
                    </el-select>
                    <el-button
                      v-if="selectedShapeShowsOcrMaskControls && selectedShape.ocrMaskMode === 'custom'"
                      size="small"
                      :disabled="!selectedShape"
                      @click="openShapeMaskDialog('ocr')"
                    >
                      OCR抠图
                    </el-button>
                  </div>
                </div>
                <div v-if="selectedShape.kind !== 'group'" class="shape-detect-row">
                  <div class="shape-action-group shape-detect-group">
                    <el-button
                      size="small"
                      :type="shapeDetectingId === selectedShape.id ? 'primary' : 'default'"
                      :disabled="!canDetectSelectedShape"
                      @click="detectSelectedShape"
                    >
                      {{ shapeDetectingId === selectedShape.id ? (shapeDetectStopRequestedRef ? '停止中' : '停止') : '检测' }}
                    </el-button>
                    <el-checkbox v-model="shapeDetectLoopEnabled" size="small">
                      循环
                    </el-checkbox>
                    <span v-if="selectedShapeDetectResult" class="shape-detect-result">
                      {{ selectedShapeDetectResult }}
                    </span>
                    <el-button
                      v-if="selectedShapeDetectDebug"
                      size="small"
                      plain
                      @click="openShapeDetectDebugDialog"
                    >
                      调试
                    </el-button>
                  </div>
                </div>
                <el-input
                  v-model="selectedShape.description"
                  type="textarea"
                  :autosize="{ minRows: 4 }"
                  placeholder="说明"
                />
              </div>
            </div>

            <div v-else class="annotation-empty">选择一个图片节点后编辑标注</div>

            <section
              v-if="selectedSceneRelationGraphVisible"
              class="scene-relation-graph"
              :class="{ 'is-resizing': sceneRelationGraphResizing }"
              :style="{ height: `${sceneRelationGraphHeight}px`, minHeight: `${sceneRelationGraphHeight}px` }"
            >
              <div class="scene-relation-tabs" role="tablist" aria-label="图结构类型">
                <button
                  v-for="tab in sceneRelationGraphTabs"
                  :key="tab.value"
                  type="button"
                  class="scene-relation-tab"
                  :class="{ 'is-active': activeSceneRelationGraphTab === tab.value }"
                  role="tab"
                  :aria-selected="activeSceneRelationGraphTab === tab.value"
                  @click="activeSceneRelationGraphTab = tab.value"
                >
                  {{ tab.label }}
                </button>
              </div>
              <VueFlow
                :key="selectedSceneGraphKey"
                class="scene-relation-flow"
                :nodes="selectedSceneGraphNodes"
                :edges="selectedSceneGraphEdges"
                :edge-types="sceneRelationGraphEdgeTypes"
                :nodes-draggable="false"
                :nodes-connectable="false"
                :elements-selectable="false"
                :zoom-on-scroll="false"
                :pan-on-scroll="false"
                :pan-on-drag="false"
                :prevent-scrolling="false"
                fit-view-on-init
                :min-zoom="0.45"
                :max-zoom="1.25"
                @node-click="handleSceneGraphNodeClick"
                @edge-click="handleSceneGraphEdgeClick"
              >
                <Controls :show-interactive="false" />
              </VueFlow>
              <div v-if="!selectedSceneGraphEdges.length" class="scene-relation-empty">
                {{ selectedSceneGraphEmptyText }}
              </div>
            </section>
            <div
              v-if="selectedSceneRelationGraphVisible"
              class="scene-relation-resizer"
              :class="{ 'is-resizing': sceneRelationGraphResizing }"
              title="拖拽调整图结构高度"
              @mousedown.prevent="startSceneRelationGraphResizing"
            />
          </section>




        </div>
      </div>






    </section>
    <el-dialog
      v-model="burstDialogVisible"
      title="连拍管理"
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
          <el-button size="small" :type="burstCaptureRunning ? 'primary' : 'default'" :disabled="burstImporting" @click="toggleBurstCapture">
            {{ burstCaptureRunning ? '停止连拍' : '开始连拍' }}
          </el-button>
          <el-button
            size="small"
            type="primary"
            plain
            :loading="burstImporting"
            :disabled="!selectedBurstFilenames.length || burstImporting"
            @click="importSelectedBurstFrames"
          >
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
        <StandardPagination
          :page="burstPage"
          :page-size="burstPageSize"
          :page-count="burstPageCount"
          :show-page-size="false"
          @page-change="handleBurstPageChange"
        />
      </div>
    </el-dialog>
    <el-dialog
      v-model="imageCompareDialogVisible"
      title="图片对比"
      width="min(96vw, 1320px)"
      append-to-body
      @opened="drawImageCompare"
      @closed="closeImageCompareDialog"
    >
      <div v-loading="imageCompareLoading" class="image-compare-dialog">
        <div v-if="imageCompareError" class="image-compare-error">{{ imageCompareError }}</div>
        <template v-else>
          <div class="image-compare-toolbar">
            <span>{{ selectedImageTitleText }}</span>
            <el-button size="small" @click="resetImageCompareView">重置视图</el-button>
            <el-button size="small" @click="clearImageCompareRects">清空矩形</el-button>
          </div>
          <div class="image-compare-grid">
            <div class="image-compare-pane">
              <div class="image-compare-pane-title">截图</div>
              <canvas
                :key="`saved-${imageCompareSavedCanvasSize.width}x${imageCompareSavedCanvasSize.height}`"
                ref="imageCompareSavedCanvasRef"
                class="image-compare-canvas"
                :width="imageCompareSavedCanvasSize.width"
                :height="imageCompareSavedCanvasSize.height"
                :style="imageCompareSavedCanvasStyle"
                @wheel.prevent="event => handleImageCompareWheel(event, 'saved')"
                @pointerdown="event => handleImageComparePointerDown(event, 'saved')"
                @pointermove="event => handleImageComparePointerMove(event, 'saved')"
                @pointerup="handleImageComparePointerUp"
                @pointerleave="handleImageComparePointerLeave"
              />
            </div>
            <div class="image-compare-pane">
              <div class="image-compare-pane-title">直播帧</div>
              <canvas
                :key="`live-${imageCompareLiveCanvasSize.width}x${imageCompareLiveCanvasSize.height}`"
                ref="imageCompareLiveCanvasRef"
                class="image-compare-canvas"
                :width="imageCompareLiveCanvasSize.width"
                :height="imageCompareLiveCanvasSize.height"
                :style="imageCompareLiveCanvasStyle"
                @wheel.prevent="event => handleImageCompareWheel(event, 'live')"
                @pointerdown="event => handleImageComparePointerDown(event, 'live')"
                @pointermove="event => handleImageComparePointerMove(event, 'live')"
                @pointerup="handleImageComparePointerUp"
                @pointerleave="handleImageComparePointerLeave"
              />
            </div>
          </div>
        </template>
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
          <span>{{ shapeMaskTarget === 'ocr' ? 'OCR专用抠图' : '方框抠图' }}</span>
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
            <div v-else class="shape-mask-empty">等待抠图</div>
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">抠图结果</div>
            <img v-if="shapeMaskResultPreviewUrl" :src="shapeMaskResultPreviewUrl" alt="抠图结果" />
            <div v-else class="shape-mask-empty">等待抠图</div>
          </div>
        </div>
        <div class="shape-mask-controls">
          <div class="shape-mask-control-row">
            <el-select v-model="shapeMaskCaptureMode" class="shape-mask-select is-capture" size="small" @change="pauseShapeMaskSampling">
              <el-option label="单帧" value="single" />
              <el-option label="连拍" value="burst" />
            </el-select>
            <el-select v-model="shapeMaskAlgorithm" class="shape-mask-select is-algorithm" size="small" @change="pauseShapeMaskSampling">
              <el-option label="差异抠图" value="difference" />
              <el-option label="连通抠图" value="background" />
              <el-option label="AI抠图" value="ai" />
            </el-select>
            <el-button size="small" @click="resetShapeMaskSampling">重置</el-button>
            <span class="shape-mask-frame-count">已取 {{ shapeMaskFrameCount }} 帧</span>
          </div>
          <div class="shape-mask-control-row">
            <el-button
              size="small"
              :type="shapeMaskRunning ? 'default' : 'primary'"
              :loading="shapeMaskAiRunning"
              plain
              @click="runSelectedShapeMaskMode"
            >
              {{ shapeMaskRunning ? '暂停' : (shapeMaskAlgorithm === 'ai' || shapeMaskCaptureMode === 'single' ? '执行' : '开始') }}
            </el-button>
            <el-button size="small" @click="toggleShapeMaskManualEditor">
              手动编辑
            </el-button>
            <div v-if="shapeMaskAlgorithm === 'difference'" class="shape-mask-slider">
              <span>阈值 {{ shapeMaskThreshold }}</span>
              <el-slider v-model="shapeMaskThreshold" :min="0" :max="120" :step="1" @input="refreshShapeMaskPreview" />
            </div>
            <button
              type="button"
              class="shape-help-button is-inline"
              title="查看抠图说明"
              aria-label="查看抠图说明"
              @click="showShapeMaskCleanHelp"
            >
              ?
            </button>
          </div>
          <div v-if="shapeMaskManualVisible" class="shape-mask-manual">
            <div class="shape-mask-manual-toolbar">
              <el-select v-model="shapeMaskManualTool" class="shape-mask-select is-manual-tool" size="small">
                <el-option label="擦除" value="erase" />
                <el-option label="恢复" value="restore" />
              </el-select>
              <div class="shape-mask-slider is-brush">
                <span>画笔 {{ shapeMaskManualBrushSize }}</span>
                <el-slider v-model="shapeMaskManualBrushSize" :min="1" :max="40" :step="1" />
              </div>
              <el-button size="small" :disabled="!shapeMaskManualUndoStack.length" @click="undoShapeMaskManual">撤销</el-button>
              <el-button size="small" :disabled="!shapeMaskManualRedoStack.length" @click="redoShapeMaskManual">重做</el-button>
            </div>
            <div
              ref="shapeMaskManualCanvasWrapRef"
              class="shape-mask-manual-canvas-wrap"
              :class="{
                'is-pan-ready': screenshotSpacePressed && !shapeMaskManualPanState,
                'is-panning': Boolean(shapeMaskManualPanState),
              }"
              @wheel="handleShapeMaskManualWheel"
            >
              <canvas
                ref="shapeMaskManualCanvasRef"
                class="shape-mask-manual-canvas"
                @pointerdown="handleShapeMaskManualPointerDown"
                @pointermove="handleShapeMaskManualPointerMove"
                @pointerup="handleShapeMaskManualPointerUp"
                @pointercancel="handleShapeMaskManualPointerUp"
                @pointerleave="handleShapeMaskManualPointerUp"
              />
            </div>
          </div>
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
      v-model="shapeDetectDebugDialogVisible"
      class="shape-detect-debug-dialog"
      title="匹配调试"
      width="860px"
      append-to-body
      top="8vh"
    >
      <div v-if="shapeDetectDebugCurrent" class="shape-detect-debug">
        <div class="shape-detect-debug-stats">
          <span>相似度 {{ shapeDetectDebugCurrent.similarity }}%</span>
          <span>容差 {{ shapeDetectDebugCurrent.pixel_tolerance }}</span>
          <span>有效 {{ shapeDetectDebugCurrent.effective_pixel_count }}</span>
          <span>命中 {{ shapeDetectDebugCurrent.matched_pixel_count }}</span>
          <span>错配 {{ shapeDetectDebugCurrent.unmatched_pixel_count }}</span>
          <span>遮罩 {{ Math.round(shapeDetectDebugCurrent.mask_coverage * 100) }}%</span>
        </div>
        <div class="shape-detect-debug-images">
          <div class="shape-mask-preview">
            <div class="shape-mask-label">参考遮罩</div>
            <img
              v-if="shapeDetectDebugCurrent.reference_masked_data_url"
              :src="shapeDetectDebugCurrent.reference_masked_data_url"
              alt="参考遮罩"
            />
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">当前遮罩</div>
            <img
              v-if="shapeDetectDebugCurrent.current_masked_data_url"
              :src="shapeDetectDebugCurrent.current_masked_data_url"
              alt="当前遮罩"
            />
          </div>
          <div class="shape-mask-preview">
            <div class="shape-mask-label">错配热力</div>
            <img
              v-if="shapeDetectDebugCurrent.mismatch_heatmap_data_url"
              :src="shapeDetectDebugCurrent.mismatch_heatmap_data_url"
              alt="错配热力"
            />
          </div>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="shapeDetectDebugDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="runtimeLogDialogVisible"
      class="runtime-log-dialog"
      title="任务调试台日志"
      width="720px"
      append-to-body
      top="10vh"
    >
      <div v-if="runtimeLogs.length" class="runtime-log-list">
        <div
          v-for="entry in pagedRuntimeLogs"
          :key="entry.id"
          class="runtime-log-row"
          :class="`is-${entry.kind}`"
        >
          <span class="runtime-log-time">{{ entry.time }}</span>
          <span class="runtime-log-kind">{{ runtimeLogKindLabel(entry.kind) }}</span>
          <span class="runtime-log-message">{{ entry.message }}</span>
        </div>
      </div>
      <div v-else class="runtime-log-empty">暂无日志</div>
      <div v-if="runtimeLogs.length" class="runtime-log-pager">
        <span>{{ runtimeLogPageStart }}-{{ runtimeLogPageEnd }} / {{ runtimeLogs.length }}</span>
        <StandardPagination
          v-model:page="runtimeLogPage"
          :page-size="RUNTIME_LOG_PAGE_SIZE"
          :total="runtimeLogs.length"
          :show-page-size="false"
        />
      </div>
      <template #footer>
        <el-button :disabled="!runtimeLogs.length" @click="clearRuntimeLogs">清空</el-button>
        <el-button type="primary" @click="runtimeLogDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="runtimeFactsDialogVisible"
      class="runtime-facts-dialog"
      title="任务调试台事实"
      width="780px"
      append-to-body
      top="10vh"
    >
      <div class="runtime-facts-path">{{ runtimeFactsPath || 'world_facts.json' }}</div>
      <pre class="runtime-facts-json">{{ runtimeFactsJson }}</pre>
      <template #footer>
        <el-button :loading="runtimeFactsLoading" @click="loadRuntimeFacts">刷新</el-button>
        <el-button type="primary" @click="runtimeFactsDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="runtimePlanDialogVisible"
      class="runtime-facts-dialog"
      title="任务调试台计划"
      width="780px"
      append-to-body
      top="10vh"
    >
      <div class="runtime-facts-path">{{ runtimePlanPath || 'scheduler_tasks.json' }}</div>
      <pre class="runtime-facts-json">{{ runtimePlanJson }}</pre>
      <template #footer>
        <el-button :loading="runtimePlanLoading" @click="loadRuntimePlan">刷新</el-button>
        <el-button type="primary" @click="runtimePlanDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { Edge, Node } from '@vue-flow/core';
import {
  Aim,
  Fold,
  Folder,
  FolderAdd,
  Picture,
  Search,
  Setting,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue';
import Sortable from 'sortablejs';
import StandardPagination from '@/components/StandardPagination.vue';
import {
  annotateFanxiuDataAnnotationMacroShape,
  clearFanxiuGameWindow2BurstFrames,
  clearFanxiuBehaviorTreeRuntimeLogs,
  clickFanxiuGameWindow2,
  compileFanxiuPseudoCode,
  createFanxiuPseudoCodeCard,
  createFanxiuGameWindow2StreamToken,
  deleteFanxiuPseudoCodeCard,
  deleteFanxiuGameWindow2Screenshot,
  dragFanxiuGameWindow2,
  getFanxiuGameWindow2BurstFrameImage,
  getFanxiuGameWindow2FrameStatus,
  getFanxiuGameWindow2ServiceStatus,
  getFanxiuDataAnnotationImage,
  getFanxiuDataAnnotationAssetTree,
  getFanxiuDataAnnotationNavigationIncident,
  getFanxiuDataAnnotationRecognitionAmbiguity,
  getFanxiuDataAnnotationRecognitionOps,
  getFanxiuBehaviorTreeRuntimeStatus,
  getFanxiuBehaviorTreeRuntimeLogs,
  getFanxiuDataAnnotationWorldFacts,
  getFanxiuInfoWindowStatus,
  getFanxiuDataAnnotationSchedulerPlan,
  getFanxiuDataAnnotationSchedulerTasks,
  getFanxiuGameWindow2MatchImage,
  getFanxiuGameWindow2Screenshot,
  getFanxiuGameWindow2PreLabel,
  importFanxiuGameWindow2BurstFrames,
  keyeventFanxiuGameWindow2,
  listFanxiuGameWindow2BurstFrames,
  listFanxiuPseudoCodeCards,
  listFanxiuGameWindow2Screenshots,
  matchFanxiuGameWindow2Screenshot,
  recognizeFanxiuDataAnnotationOcrFrame,
  removeFanxiuDataAnnotationBackground,
  runFanxiuVisualScript,
  saveFanxiuDataAnnotationFrame,
  saveFanxiuGameWindow2BurstFrame,
  saveFanxiuGameWindow2Frame,
  saveFanxiuGameWindow2PreLabel,
  saveFanxiuDataAnnotationAssetTree,
  runDueFanxiuDataAnnotationSchedulerTasks,
  runNowFanxiuDataAnnotationSchedulerTask,
  screencapFanxiuGameWindow2,
  startFanxiuGameWindow2Service,
  startFanxiuPseudoCode,
  stopFanxiuBehaviorTreeRuntimeCurrentTask,
  stopFanxiuVisualScript,
  textFanxiuGameWindow2,
  updateFanxiuPseudoCodeCard,
  type FanxiuGameWindow2MatchBox,
  type FanxiuGameWindow2BurstFrameItem,
  type FanxiuGameWindow2MatchDebug,
  type FanxiuGameWindow2MatchPayload,
  type FanxiuGameWindow2MatchResponse,
  type FanxiuGameWindow2FrameStatus,
  type FanxiuGameWindow2ServiceStatus,
  type FanxiuGameWindow2ScreenshotItem,
  type FanxiuGameWindow2PreLabelBox,
  type FanxiuGameWindow2PreLabelPayload,
  type FanxiuBehaviorTreeRuntimeStatus,
  type FanxiuDataAnnotationNavigationIncident,
  type FanxiuDataAnnotationRecognitionAmbiguitySummary,
  type FanxiuDataAnnotationSaveFrameResponse,
  type FanxiuDataAnnotationNavigationIncidentTimelineItem,
  type FanxiuDataAnnotationRecognitionOpsIssue,
  type FanxiuDataAnnotationRecognitionOpsResponse,
  type FanxiuDataAnnotationMacroAnnotateResponse,
  type FanxiuDataAnnotationOcrFrameToken,
  type FanxiuDataAnnotationSchedulerTaskItem,
  type FanxiuBehaviorTreeRuntimeLogEntry,
  type FanxiuPseudoCodeCard,
  type FanxiuPseudoCodeCardScope,
  type FanxiuPseudoCodeRunResponse,
} from '@/api/fanxiu';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { taskStore, type Device } from '@/store/taskStore';
import { useResizablePane } from '@/utils/useResizablePane';
import { useSortableList } from '@/utils/useSortableList';
import {
  buildRecognitionOpsTree,
  formatAmbiguitySelectionCounts,
  type RecognitionOpsTreeNode,
} from './recognitionOpsModel';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';

const VueFlow = defineAsyncComponent(() => import('@vue-flow/core').then((mod) => mod.VueFlow));
const Controls = defineAsyncComponent(() => import('@vue-flow/controls').then((mod) => mod.Controls));
const ElkEdge = defineAsyncComponent(() => import('@/components/ElkEdge.vue'));

const SCENE_GRAPH_ARROW_MARKER = 'arrowclosed' as const;
let sceneRelationGraphElkPromise: Promise<{ layout: (graph: unknown) => Promise<any> }> | null = null;
const getSceneRelationGraphElk = async () => {
  if (!sceneRelationGraphElkPromise) {
    sceneRelationGraphElkPromise = import('elkjs/lib/elk.bundled.js')
      .then(({ default: ELK }) => new ELK());
  }
  return sceneRelationGraphElkPromise;
};

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

type WindowSceneKey = 'mumu';
type CaptureArea = 'outer' | 'client';
type RotateDegrees = '0' | '90' | '180' | '270';
type WindowViewMode = 'live' | 'control' | 'off';
type WindowTitleMatch = 'contains' | 'exact';
type MumuChannel = 'desktop' | 'adb';
type RuntimeChannelUse = 'frontend' | 'runtime' | 'save';
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
type FrameLayer = 1 | 2 | 3;
type ShapeOcrMatchMode = 'contains' | 'exact' | 'wildcard' | 'regex';
type ShapeOcrMaskMode = 'inherit-envelope' | 'custom' | 'off' | 'raw-alpha';
type AssetTreeViewMode = 'business' | 'scene' | 'recognitionOps';

type GameMacroConfig = {
  version: number;
  defaultShapeSize: number;
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
const DEFAULT_VISUAL_MACRO_THRESHOLD = 0.8;
const DEFAULT_SHAPE_PIXEL_TOLERANCE = 20;
const GAME_MACRO_CONFIG_STORAGE_KEY = 'fanxiu.dataAnnotation.gameMacro.config.v1';
const SCENE_RELATION_GRAPH_HEIGHT_STORAGE_PREFIX = 'fanxiu.dataAnnotation.sceneRelationGraphHeight.v1';
const SCENE_RELATION_GRAPH_TAB_STORAGE_KEY = 'fanxiu.dataAnnotation.sceneRelationGraphTab.v1';
const GAME_MACRO_CONFIG_VERSION = 2;
const GAME_MACRO_DEFAULT_DRAG_DURATION_MS = 1500;
const FALLBACK_FRAME_WIDTH = 900;
const FALLBACK_FRAME_HEIGHT = 1600;
const SCREENSHOT_MIN_ZOOM_PERCENT = 20;
const SCREENSHOT_MAX_ZOOM_PERCENT = 500;
const SCREENSHOT_ZOOM_STEP = 10;
const MIN_CONTENT_VISIBLE_AREA_RATIO = 0.2;
const MIN_CONTENT_VISIBLE_AXIS_RATIO = Math.sqrt(MIN_CONTENT_VISIBLE_AREA_RATIO);
const VISUAL_ACTION_MARKER_START = '<!-- codeyun-visual-action-v1';
const VISUAL_ACTION_MARKER_END = '-->';
const RUNTIME_CHANNEL_POLICIES: Record<RuntimeChannelUse, RuntimeChannelPolicy> = {
  frontend: { mumu: 'selected' },
  runtime: { mumu: 'adb' },
  save: { mumu: 'adb' },
};
const windowViewModes: Array<{ value: WindowViewMode; label: string }> = [
  { value: 'live', label: '直播' },
  { value: 'control', label: '交互' },
  { value: 'off', label: '关闭' },
];
const windowScenes: WindowScene[] = [
  {
    key: 'mumu',
    label: 'MuMu模拟器',
    defaults: {
      targetTitle: 'MuMu',
      titleMatch: 'contains',
      cropText: '0,60,4,4',
      trimBorderText: '0,0,0,0',
      captureArea: 'client',
      rotateDegrees: '0',
      fps: 2,
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
type SceneRelationGraphTab = 'recognition' | 'jump';
const sceneRelationGraphTabs: Array<{ value: SceneRelationGraphTab; label: string }> = [
  { value: 'recognition', label: '识别结构' },
  { value: 'jump', label: '跳转流转' },
];
const isSceneRelationGraphTab = (value: unknown): value is SceneRelationGraphTab => (
  value === 'recognition' || value === 'jump'
);
const activeSceneRelationGraphTab = ref<SceneRelationGraphTab>('recognition');
const sceneRelationGraphHeightStorageKey = computed(() => (
  `${SCENE_RELATION_GRAPH_HEIGHT_STORAGE_PREFIX}:${selectedEntryId.value || 'default'}`
));
const {
  paneHeight: sceneRelationGraphHeight,
  isResizing: sceneRelationGraphResizing,
  isManualResized: sceneRelationGraphManualResized,
  startResizing: startSceneRelationGraphResizing,
} = useResizablePane({
  initialHeight: 224,
  getResizeBounds: () => {
    const viewportHeight = typeof window === 'undefined' ? 900 : window.innerHeight;
    return {
      min: 128,
      max: Math.max(180, Math.floor(viewportHeight * 0.55)),
    };
  },
});
const clampSceneRelationGraphHeight = (value: number) => {
  const viewportHeight = typeof window === 'undefined' ? 900 : window.innerHeight;
  const max = Math.max(180, Math.floor(viewportHeight * 0.55));
  return Math.max(128, Math.min(max, Math.round(value)));
};
const restoreSceneRelationGraphHeight = () => {
  if (typeof window === 'undefined') return;
  const raw = window.localStorage.getItem(sceneRelationGraphHeightStorageKey.value);
  const parsed = raw ? Number(raw) : NaN;
  sceneRelationGraphHeight.value = Number.isFinite(parsed)
    ? clampSceneRelationGraphHeight(parsed)
    : clampSceneRelationGraphHeight(sceneRelationGraphHeight.value);
};
watch(selectedEntryId, restoreSceneRelationGraphHeight);
watch(sceneRelationGraphHeight, (height) => {
  if (!sceneRelationGraphManualResized.value || typeof window === 'undefined') return;
  window.localStorage.setItem(sceneRelationGraphHeightStorageKey.value, String(clampSceneRelationGraphHeight(height)));
});
if (typeof window !== 'undefined') {
  const savedSceneRelationGraphTab = window.localStorage.getItem(SCENE_RELATION_GRAPH_TAB_STORAGE_KEY);
  if (isSceneRelationGraphTab(savedSceneRelationGraphTab)) {
    activeSceneRelationGraphTab.value = savedSceneRelationGraphTab;
  }
}
watch(activeSceneRelationGraphTab, (tab) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(SCENE_RELATION_GRAPH_TAB_STORAGE_KEY, tab);
});
const selectedWindowKey = ref<WindowSceneKey>('mumu');
const serviceStatus = ref<FanxiuGameWindow2ServiceStatus | null>(null);
const runtimeLoading = ref(false);
const connectionLoading = ref(false);

const trimBorderText = ref('0,0,0,0');
const rotateDegrees = ref<RotateDegrees>('0');
const displayScale = ref(100);
const fps = ref(12);
const quality = ref(82);
const mumuChannel = ref<MumuChannel>('adb');
const autoDismissPopup = ref(false);
const streamEnabled = ref(false);
const streamNonce = ref(Date.now());
const streamError = ref('');
const streamToken = ref('');
const streamTokenExpiresAt = ref(0);
const adbFrameUrl = ref('');
const streamTokenLoading = ref(false);
const actualFps = ref(0);
const frameHeartbeatReady = ref(false);
const layerVisible = ref(true);
const windowViewMode = ref<WindowViewMode>('off');
const controlEnabled = ref(false);
const saveFrameLoading = ref(false);
const burstCaptureRunning = ref(false);
const burstCaptureSaving = ref(false);
const burstImporting = ref(false);
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
const visualMacroDefaultThreshold = ref(DEFAULT_VISUAL_MACRO_THRESHOLD);
const visualMacroDefaultPointRadius = ref(10);
const visualMacroDefaultPixelTolerance = ref(DEFAULT_SHAPE_PIXEL_TOLERANCE);
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
let serviceStatusRequestInFlight = false;
let serviceStatusLastLoadedAt = 0;
let adbFrameTimer: number | null = null;
let frameStatusTimer: number | null = null;
let frameStatusRequestInFlight = false;
let lastFrameSequence = 0;
let lastFrameSequenceObservedAt = 0;
let frameUnhealthyObservedAt = 0;
let streamReconnectTimer: number | null = null;
let streamReconnectAttempt = 0;
let lastLiveFrameDataUrl = '';
let lastLiveFrameCapturedAt = 0;
let burstCaptureTimer: number | null = null;
let burstCaptureToken = 0;
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
const SERVICE_STATUS_SILENT_POLL_INTERVAL_MS = 120_000;
const FRAME_UNHEALTHY_GRACE_MS = 9_000;

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
  transformOrigin: '0 0',
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
  transformOrigin: '0 0',
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
    visualMacroDefaultThreshold.value = DEFAULT_VISUAL_MACRO_THRESHOLD;
    if (persist) window.localStorage.setItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY, String(visualMacroDefaultThreshold.value));
    return;
  }
  const nextValue = Number(value);
  visualMacroDefaultThreshold.value = Number.isFinite(nextValue) ? clamp(nextValue, 0.5, 1) : DEFAULT_VISUAL_MACRO_THRESHOLD;
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
    visualMacroDefaultPixelTolerance.value = DEFAULT_SHAPE_PIXEL_TOLERANCE;
    if (persist) window.localStorage.setItem(VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY, String(visualMacroDefaultPixelTolerance.value));
    return;
  }
  const nextValue = Math.round(Number(value));
  visualMacroDefaultPixelTolerance.value = Number.isFinite(nextValue) ? clamp(nextValue, 0, 255) : DEFAULT_SHAPE_PIXEL_TOLERANCE;
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
  const storedThreshold = window.localStorage.getItem(VISUAL_MACRO_DEFAULT_THRESHOLD_KEY);
  const thresholdValue = Number(storedThreshold);
  if (storedThreshold !== null && Number.isFinite(thresholdValue) && Math.abs(thresholdValue - 0.88) < 0.0001) {
    setVisualMacroDefaultThreshold(DEFAULT_VISUAL_MACRO_THRESHOLD);
  } else {
    setVisualMacroDefaultThreshold(storedThreshold, false);
  }
  setVisualMacroDefaultPointRadius(window.localStorage.getItem(VISUAL_MACRO_DEFAULT_POINT_RADIUS_KEY), false);
  const storedPixelTolerance = window.localStorage.getItem(VISUAL_MACRO_DEFAULT_PIXEL_TOLERANCE_KEY);
  setVisualMacroDefaultPixelTolerance(storedPixelTolerance === '5' ? DEFAULT_SHAPE_PIXEL_TOLERANCE : storedPixelTolerance, false);
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
    auto_dismiss_popup: 'false',
    adb_screencap: 'true',
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
  && streamEnabled.value
  && liveImageUrl.value
  && naturalWidth.value
  && naturalHeight.value
  && frameHeartbeatReady.value
  && !streamError.value
));
const connectionButtonLoading = computed(() => connectionLoading.value || streamTokenLoading.value);
const connectionButtonText = computed(() => {
  if (windowViewMode.value === 'off') return '已关闭';
  if (connectionReady.value) return '运行中';
  if (streamError.value) return '恢复中';
  if (connectionButtonLoading.value || (streamEnabled.value && (streamToken.value || shouldCaptureWithAdb('frontend')) && !streamError.value)) return '连接中';
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

const getHttpStatus = (error: unknown) => {
  if (typeof error !== 'object' || !error || !('response' in error)) return null;
  const maybeError = error as { response?: { status?: number } };
  return typeof maybeError.response?.status === 'number' ? maybeError.response.status : null;
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

const getQueryStringValue = (key: string) => {
  const raw = route.query[key];
  return String(Array.isArray(raw) ? raw[0] || '' : raw || '').trim();
};

const isMfDeviceEntry = (device: { id: string; device_id?: string; name?: string }) => {
  const haystack = `${device.id} ${device.device_id || ''} ${device.name || ''}`.toLowerCase();
  return haystack.includes('codepc_mf') || haystack.includes('codepc-mf') || haystack.includes(' mf');
};

const chooseDefaultEntryId = () => {
  const queryEntryId = getQueryEntryId();
  if (queryEntryId) return queryEntryId;
  const mf = devices.value.find(isMfDeviceEntry);
  if (mf) return mf.id;
  const savedEntryId = window.localStorage.getItem(DEVICE_STORAGE_KEY) || '';
  if (savedEntryId && devices.value.some((device) => device.id === savedEntryId)) return savedEntryId;
  return devices.value[0]?.id || '';
};

const chooseDefaultWindowKey = (): WindowSceneKey => {
  const queryWindowKey = getQueryWindowKey();
  if (queryWindowKey) return queryWindowKey;
  const savedWindowKey = window.localStorage.getItem(WINDOW_STORAGE_KEY) || '';
  if (isWindowSceneKey(savedWindowKey)) return savedWindowKey;
  return 'mumu';
};

const normalizeWindowConfig = (
  raw: Partial<WindowSceneConfig>,
  fallback: WindowSceneDefaults,
  windowKey?: WindowSceneKey,
): WindowSceneConfig => {
  const rotate = raw.rotateDegrees;
  const nextFps = Number(raw.fps ?? fallback.fps);
  const nextQuality = Number(raw.quality ?? fallback.quality);
  const rawMumuChannel = raw.mumuChannel === 'desktop' || raw.mumuChannel === 'adb'
    ? raw.mumuChannel
    : (fallback.mumuChannel ?? 'desktop');
  const nextMumuChannel = windowKey === 'mumu' ? 'adb' : rawMumuChannel;
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
  if (!rawText) return normalizeWindowConfig({}, scene.defaults, windowKey);
  try {
    const raw = JSON.parse(rawText) as Partial<WindowSceneConfig>;
    return normalizeWindowConfig(raw, scene.defaults, windowKey);
  } catch {
    return normalizeWindowConfig({}, scene.defaults, windowKey);
  }
};

const currentWindowConfig = (): WindowSceneConfig => ({
  trimBorderText: trimBorderText.value,
  rotateDegrees: rotateDegrees.value,
  fps: Number(fps.value) || selectedWindowScene.value.defaults.fps,
  quality: Number(quality.value) || selectedWindowScene.value.defaults.quality,
  autoDismissPopup: autoDismissPopup.value,
  mumuChannel: selectedWindowKey.value === 'mumu' ? 'adb' : mumuChannel.value,
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
  const nextEntryId = selectedEntryId.value;
  if (assetTreeDirty && assetTreeLoadedEntryId) {
    const persisted = await saveAssetTreeNow();
    if (!persisted) {
      selectedEntryId.value = assetTreeLoadedEntryId;
      return;
    }
  }
  selectedEntryId.value = nextEntryId;
  releaseAssetImagePreviewUrls();
  controlEnabled.value = false;
  controlClickState.value = null;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
  streamError.value = '';
  streamToken.value = '';
  streamTokenExpiresAt.value = 0;
  serviceStatusLastLoadedAt = 0;
  clearLastLiveFrameCache();
  stopAdbFramePolling();
  revokeAdbFrameUrl();
  screenshotImages.value = [];
  screenshotLoaded.value = false;
  clearScreenshotSelection();
  clearMatchResults();
  persistEntrySelection(selectedEntryId.value);
  applyWindowConfig();
  if (selectedEntryId.value) await loadEntryAssetTree(selectedEntryId.value);
  void refreshRuntimeTaskStatus();
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
  clearLastLiveFrameCache();
  stopAdbFramePolling();
  revokeAdbFrameUrl();
  clearMatchResults();
  persistWindowSelection();
  applyWindowConfig();
  await connectWindow({ allowStartService: windowViewMode.value === 'control' });
};

const handleWindowViewModeChange = async () => {
  controlClickState.value = null;
  if (windowViewMode.value === 'off') {
    await restartStream();
    return;
  }
  await connectWindow({ allowStartService: windowViewMode.value === 'control' });
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
  actualFps.value = 0;
  frameHeartbeatReady.value = false;
};

const resetFrameStatusTracking = () => {
  resetActualFps();
  lastFrameSequence = 0;
  lastFrameSequenceObservedAt = 0;
  frameUnhealthyObservedAt = 0;
};

const clearLastLiveFrameCache = () => {
  lastLiveFrameDataUrl = '';
  lastLiveFrameCapturedAt = 0;
};

const cacheLiveFrameDataUrl = (dataUrl: string) => {
  if (!dataUrl) return;
  lastLiveFrameDataUrl = dataUrl;
  lastLiveFrameCapturedAt = Date.now();
};

const recentLiveFrameDataUrl = (maxAgeMs = 8000) => {
  if (!lastLiveFrameDataUrl || !lastLiveFrameCapturedAt) return '';
  if (Date.now() - lastLiveFrameCapturedAt > maxAgeMs) return '';
  return lastLiveFrameDataUrl;
};

const clearStreamReconnectTimer = () => {
  if (!streamReconnectTimer) return;
  window.clearTimeout(streamReconnectTimer);
  streamReconnectTimer = null;
};

const scheduleStreamReconnect = () => {
  if (streamReconnectTimer || windowViewMode.value === 'off' || !selectedEntryId.value) return;
  const delay = Math.min(8000, 800 * (2 ** Math.min(streamReconnectAttempt, 4)));
  streamReconnectAttempt += 1;
  streamReconnectTimer = window.setTimeout(() => {
    streamReconnectTimer = null;
    void restartStream({ automatic: true });
  }, delay);
};

const applyFrameStatus = (status: FanxiuGameWindow2FrameStatus) => {
  const now = Date.now();
  const sequence = Number(status.sequence) || 0;
  if (sequence > lastFrameSequence) {
    if (lastFrameSequence && lastFrameSequenceObservedAt) {
      const seconds = Math.max(0.001, (now - lastFrameSequenceObservedAt) / 1000);
      actualFps.value = (sequence - lastFrameSequence) / seconds;
    }
    lastFrameSequence = sequence;
    lastFrameSequenceObservedAt = now;
    frameUnhealthyObservedAt = 0;
    frameHeartbeatReady.value = true;
    streamReconnectAttempt = 0;
    clearStreamReconnectTimer();
    if (streamError.value) streamError.value = '';
    return;
  }

  // A failed ADB capture can still return a fresh cached frame. The backend's
  // `ready` value already accounts for frame age, so a non-zero failure count
  // alone is not a stream interruption. Only surface recovery after the
  // backend has remained unhealthy for a full grace period.
  if (status.ready) {
    frameUnhealthyObservedAt = 0;
    frameHeartbeatReady.value = true;
    streamReconnectAttempt = 0;
    clearStreamReconnectTimer();
    if (streamError.value) streamError.value = '';
    return;
  }
  if (!lastFrameSequence) return;
  if (!frameUnhealthyObservedAt) {
    frameUnhealthyObservedAt = now;
    return;
  }
  if (now - frameUnhealthyObservedAt < FRAME_UNHEALTHY_GRACE_MS) return;
  frameHeartbeatReady.value = false;
  actualFps.value = 0;
  streamError.value = '画面帧长时间未更新，正在重新连接…';
  scheduleStreamReconnect();
};

const pollFrameStatus = async () => {
  if (frameStatusRequestInFlight || windowViewMode.value === 'off' || !selectedEntryId.value) return;
  frameStatusRequestInFlight = true;
  try {
    applyFrameStatus(await getFanxiuGameWindow2FrameStatus(selectedEntryId.value));
  } catch {
    if (!lastFrameSequence) return;
    const now = Date.now();
    if (!frameUnhealthyObservedAt) frameUnhealthyObservedAt = now;
    if (now - frameUnhealthyObservedAt >= FRAME_UNHEALTHY_GRACE_MS) {
      frameHeartbeatReady.value = false;
      actualFps.value = 0;
      streamError.value = '画面状态长时间不可用，正在重新连接…';
      scheduleStreamReconnect();
    }
  } finally {
    frameStatusRequestInFlight = false;
  }
};

const stopFrameStatusPolling = () => {
  if (frameStatusTimer) {
    window.clearInterval(frameStatusTimer);
    frameStatusTimer = null;
  }
};

const startFrameStatusPolling = () => {
  stopFrameStatusPolling();
  void pollFrameStatus();
  frameStatusTimer = window.setInterval(() => void pollFrameStatus(), 1500);
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
  streamReconnectAttempt = 0;
  clearStreamReconnectTimer();
  naturalWidth.value = image.naturalWidth;
  naturalHeight.value = image.naturalHeight;
  const frame = captureCurrentLiveFrameDataUrl();
  if (frame) cacheLiveFrameDataUrl(frame);
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
  try {
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/png');
    cacheLiveFrameDataUrl(dataUrl);
    return dataUrl;
  } catch {
    return '';
  }
};

const waitForCurrentLiveFrameDataUrl = async (timeoutMs: number, signal?: AbortSignal) => {
  const started = window.performance.now();
  while (window.performance.now() - started < timeoutMs) {
    if (signal?.aborted) return '';
    const frame = captureCurrentLiveFrameDataUrl();
    if (frame) return frame;
    await new Promise(resolve => window.setTimeout(resolve, 80));
  }
  if (signal?.aborted) return '';
  return captureCurrentLiveFrameDataUrl();
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

const captureCurrentFrameDataUrl = async (
  use: RuntimeChannelUse = 'frontend',
  options: {
    preferLiveFrame?: boolean;
    liveFrameWaitMs?: number;
    allowScreencapFallback?: boolean;
    cachedScreencapOnly?: boolean;
    screencapTimeoutMs?: number;
    signal?: AbortSignal;
  } = {},
) => {
  if (options.preferLiveFrame) {
    const liveFrame = options.liveFrameWaitMs
      ? await waitForCurrentLiveFrameDataUrl(options.liveFrameWaitMs, options.signal)
      : captureCurrentLiveFrameDataUrl();
    if (liveFrame) return liveFrame;
    const recentLiveFrame = recentLiveFrameDataUrl();
    if (recentLiveFrame) return recentLiveFrame;
  }
  const useAdb = shouldCaptureWithAdb(use);
  if (useAdb && selectedEntryId.value && options.allowScreencapFallback !== false && !options.signal?.aborted) {
    const readScreencap = async (cachedOnly: boolean, timeout: number) => {
      const blob = await screencapFanxiuGameWindow2(selectedEntryId.value, {
        signal: options.signal,
        timeout,
        preferCached: true,
        cachedOnly,
        title: targetTitle.value,
        titleMatch: titleMatch.value,
        mode: 'screen',
        area: captureArea.value,
        crop: cropText.value,
        trimBorder: trimBorderText.value,
        rotate: rotateDegrees.value,
        fixedWidth: fixedFrameWidth.value,
        fixedHeight: fixedFrameHeight.value,
      });
      return blobToDataUrl(blob);
    };
    try {
      return await readScreencap(true, Math.min(options.screencapTimeoutMs ?? 1000, 1000));
    } catch {
      if (!options.cachedScreencapOnly && !options.signal?.aborted) {
        try {
          return await readScreencap(false, options.screencapTimeoutMs ?? (options.signal ? 8000 : 60000));
        } catch {
          // Fall back to the visible live frame if ADB screencap is temporarily unavailable.
        }
      }
    }
  }
  return captureCurrentLiveFrameDataUrl() || recentLiveFrameDataUrl();
};

const compressFrameDataUrlForMatch = async (dataUrl: string) => {
  if (!dataUrl) return '';
  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('当前帧压缩失败'));
    img.src = dataUrl;
  });
  const sourceWidth = image.naturalWidth || image.width;
  const sourceHeight = image.naturalHeight || image.height;
  const maxLongEdge = 1280;
  const scale = Math.min(1, maxLongEdge / Math.max(sourceWidth, sourceHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(sourceWidth * scale));
  canvas.height = Math.max(1, Math.round(sourceHeight * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx || !canvas.width || !canvas.height) return dataUrl;
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.82);
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
  stopFrameStatusPolling();
  clearStreamReconnectTimer();
  resetFrameStatusTracking();
  clearLastLiveFrameCache();
  gameMacroRecording.value = false;
  gameMacroCapturePending.value = false;
  streamEnabled.value = false;
  controlEnabled.value = false;
  serviceStatus.value = null;
  streamToken.value = '';
  streamTokenExpiresAt.value = 0;
  revokeAdbFrameUrl();
  if (streamImageRef.value) streamImageRef.value.src = '';
  await nextTick(syncCanvas);
};

const handleStreamError = () => {
  if (windowViewMode.value === 'off') return;
  streamError.value = '画面连接中断，正在自动恢复…';
  frameHeartbeatReady.value = false;
  resetActualFps();
  void nextTick(syncCanvas);
  scheduleStreamReconnect();
};

const restartStream = async (_options: { automatic?: boolean } = {}) => {
  if (selectedWindowKey.value === 'mumu' && mumuChannel.value !== 'adb') {
    mumuChannel.value = 'adb';
  }
  streamError.value = '';
  shapeDetectLiveBoxes.value = [];
  stopAdbFramePolling();
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
  startFrameStatusPolling();
  void nextTick(syncCanvas);
};

const connectWindow = async (options: { allowStartService?: boolean } = {}) => {
  if (!selectedEntryId.value) return;
  if (windowViewMode.value === 'off') {
    await setWindowViewModeOff();
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
    const pendingPersisted = assetTreeDirty ? await saveAssetTreeNow() : await assetTreeSaveChain;
    if (!pendingPersisted) return;
    const node = createAssetImageNode('');
    const insertTarget = savedFrameInsertTarget();
    const localVersion = assetTreeLocalVersion;
    const result = await saveFanxiuDataAnnotationFrame({
      entry_id: selectedEntryId.value,
      fresh_capture: true,
      asset_node: node,
      parent_id: insertTarget.parentId || undefined,
      after_node_id: insertTarget.afterNodeId || undefined,
      base_revision: assetTreeBackendRevision.value,
    });
    await applySavedFrameTransaction(result, node, localVersion);
    const savedFrameDataUrl = await blobToDataUrl(
      await getFanxiuDataAnnotationImage(selectedEntryId.value, result.filename, Date.now()),
    );
    setAssetImagePreviewUrl(assetImagePreviewKey(node), savedFrameDataUrl);
    await restartStream({ automatic: true });
    ElMessage.success(`已保存到资产树：${result.filename}`);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    saveFrameLoading.value = false;
  }
};

const burstFramePayload = async () => {
  // Prefer the frame already visible on the annotation page. This keeps burst
  // capture aligned with what the user sees and avoids a separate PrintWindow
  // capture returning a not-yet-ready black placeholder.
  const currentFrameDataUrl = await captureCurrentFrameDataUrl('frontend', {
    preferLiveFrame: true,
    liveFrameWaitMs: 400,
    cachedScreencapOnly: true,
    screencapTimeoutMs: 1000,
  });
  return {
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
    current_frame_data_url: currentFrameDataUrl || undefined,
  };
};

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
  if (!selectedEntryId.value || !selectedBurstFilenames.value.length || burstImporting.value) return;
  stopBurstCapture();
  burstImporting.value = true;
  try {
    const response = await importFanxiuGameWindow2BurstFrames(selectedEntryId.value, selectedBurstFilenames.value);
    await loadScreenshotList();
    const importedNodes = response.imported.map((item) => createAssetImageNode('', {
      filename: item.filename,
      width: item.width,
      height: item.height,
    }));
    for (const node of importedNodes) addSavedFrameToAssetTree(node);
    selectedBurstFilenames.value = [];
    ElMessage.success(`已保存 ${response.imported_count} 张到资产树`);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    stopBurstCapture();
    burstImporting.value = false;
  }
};

const saveBurstFrameOnce = async (token: number) => {
  if (
    !selectedEntryId.value
    || !burstCaptureRunning.value
    || token !== burstCaptureToken
    || burstCaptureSaving.value
  ) return;
  burstCaptureSaving.value = true;
  try {
    const result = await saveFanxiuGameWindow2BurstFrame(await burstFramePayload());
    if (!burstCaptureRunning.value || token !== burstCaptureToken) return;
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
  burstCaptureToken += 1;
  if (burstCaptureTimer) {
    window.clearInterval(burstCaptureTimer);
    burstCaptureTimer = null;
  }
  burstCaptureRunning.value = false;
};

const startBurstCapture = () => {
  if (!selectedEntryId.value || burstCaptureRunning.value || burstImporting.value) return;
  const token = burstCaptureToken + 1;
  burstCaptureToken = token;
  burstCaptureRunning.value = true;
  void saveBurstFrameOnce(token);
  const interval = Math.max(200, Math.round(1000 / Math.max(1, Number(fps.value) || 1)));
  burstCaptureTimer = window.setInterval(() => {
    void saveBurstFrameOnce(token);
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

const resetAssetFrame = async (node: DataAnnotationAssetNode) => {
  if (!selectedEntryId.value || node.type !== 'image' || !node.filename) return;
  const result = await saveFanxiuDataAnnotationFrame({
    entry_id: selectedEntryId.value,
    filename: node.filename,
    fresh_capture: true,
  });
  const savedFrameDataUrl = await blobToDataUrl(
    await getFanxiuDataAnnotationImage(selectedEntryId.value, result.filename, Date.now()),
  );
  node.filename = result.filename;
  node.width = result.width;
  node.height = result.height;
  delete node.imageDataUrl;
  setAssetImagePreviewUrl(assetImagePreviewKey(node), savedFrameDataUrl);
  await restartStream({ automatic: true });
  await saveAssetTreeNow();
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
    const currentFrameDataUrl = await captureCurrentFrameDataUrl('save');
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
    await connectWindow({ allowStartService: true });
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
    imageCompareSpacePressed.value = imageCompareDialogVisible.value;
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
  if (event.code === 'Space') {
    screenshotSpacePressed.value = false;
    imageCompareSpacePressed.value = false;
  }
};

const handleWindowBlur = () => {
  screenshotSpacePressed.value = false;
  imageCompareSpacePressed.value = false;
  stopLivePan();
  stopScreenshotPan();
  shapeMaskManualPointer.value = null;
  shapeMaskManualPanState.value = null;
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
    if (shapeDetectingId.value) return;
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
  window.addEventListener('online', recoverSelectedImagePreviewWhenAvailable);
  document.addEventListener('visibilitychange', recoverSelectedImagePreviewWhenAvailable);
  document.addEventListener('visibilitychange', flushAssetTreeWhenHidden);
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
    const entryId = selectedEntryId.value;
    const loadAssetTreeTask = loadEntryAssetTree(entryId);
    const connectWindowTask = windowViewMode.value !== 'off' ? connectWindow() : Promise.resolve();
    await Promise.all([loadAssetTreeTask, connectWindowTask]);
  }
  startPolling();
  void ensureSelectedImagePreview();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  if (assetTreeSaveTimer) {
    window.clearTimeout(assetTreeSaveTimer);
    assetTreeSaveTimer = null;
  }
  if (assetTreeDirty) void enqueueAssetTreeSave();
  stopPolling();
  stopAdbFramePolling();
  stopFrameStatusPolling();
  clearStreamReconnectTimer();
  stopBurstCapture();
  resetFrameStatusTracking();
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('keyup', handleKeyup);
  window.removeEventListener('blur', handleWindowBlur);
  window.removeEventListener('resize', handleWindowResize);
  window.removeEventListener('online', recoverSelectedImagePreviewWhenAvailable);
  document.removeEventListener('visibilitychange', recoverSelectedImagePreviewWhenAvailable);
  document.removeEventListener('visibilitychange', flushAssetTreeWhenHidden);
  window.removeEventListener('click', closeAssetContextMenu);
  window.removeEventListener('click', closeShapeContextMenu);
  stopLivePan();
  stopScreenshotPan();
  stopShapeMaskSampling();
  stopShapeToleranceSampling();
  stopShapeDiscriminatorSampling();
  stopRuntimeTaskPolling();
  stopRecognitionOpsPolling();
  cancelShapeDraft();
  finishShapeDrag();
  resizeObserver?.disconnect();
  revokeAdbFrameUrl();
  releaseAssetImagePreviewUrls();
  releaseBurstPreviewUrls();
  if (streamImageRef.value) streamImageRef.value.src = '';
  revokeScreenshotImageUrl();
  clearMatchResults();
});

type DataAnnotationAssetNode = {
  id: string;
  type: 'folder' | 'image';
  title: string;
  children?: DataAnnotationAssetNode[];
  filename?: string;
  imageDataUrl?: string;
  width?: number;
  height?: number;
  layer?: FrameLayer;
  parentSceneIds?: string;
  shapes?: DataAnnotationShape[];
};

type DataAnnotationShape = {
  id: string;
  kind?: 'shape' | 'group';
  title: string;
  description: string;
  locked?: boolean;
  floating?: boolean;
  jitterEnabled?: boolean;
  jitterRadius?: number;
  isSceneIdentity?: boolean;
  sceneIdentityRole?: ShapeMatchRole;
  sceneJumpTarget?: string;
  loadDirection?: 'none' | 'up' | 'down' | 'left' | 'right';
  loadMode?: 'continuous' | 'paged';
  loadBoundary?: 'bounded' | 'cyclic';
  loadInitialPosition?: 'start' | 'unknown';
  imageMatchRole?: ShapeMatchRole;
  pixelTolerance?: number;
  ocrMatchRole?: ShapeMatchRole;
  ocrEnabled?: boolean;
  ocrText?: string;
  ocrMatchMode?: ShapeOcrMatchMode;
  ocrMaskMode?: ShapeOcrMaskMode;
  ocrMask?: ShapeAlphaMask | null;
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
  children?: DataAnnotationShape[];
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

type RuntimeLogKind = 'start' | 'wait' | 'action' | 'success' | 'stop' | 'error' | 'detail';

type RuntimeLogEntry = FanxiuBehaviorTreeRuntimeLogEntry & {
  id: string;
  time: string;
  kind: RuntimeLogKind | string;
  message: string;
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

type SceneRelationEdgeKind = 'recognition' | 'jump' | 'discriminator';

type SceneRelationEdge = {
  id: string;
  kind: SceneRelationEdgeKind;
  kindLabel: string;
  sourceId: number | null;
  targetId: number | null;
  sourceLabel: string;
  targetLabel: string;
  score?: number | string | null;
  shapeId?: string;
  shapeTitle?: string;
  focusImageId: number | null;
  focusShapeId?: string;
  tooltip: string;
};

type SceneGraphNodeData = {
  label: string;
  imageId: number | null;
  depth?: number;
  issueId?: string;
};

type SceneGraphEdgeData = {
  relationEdge: SceneRelationEdge;
  elkSections?: unknown[];
};

type ShapeDragState = {
  pointerId: number;
  shapeId: string;
  mode: 'move' | 'top-left' | 'bottom-right';
  startClientX: number;
  startClientY: number;
  startBox: Pick<DataAnnotationShape, 'x' | 'y' | 'w' | 'h'>;
};

type ShapeDraftState = {
  pointerId: number;
  startX: number;
  startY: number;
  rect: DOMRect;
};

type ImageCompareSide = 'saved' | 'live';

type ImageComparePoint = {
  x: number;
  y: number;
};

type ImageCompareRect = ImageComparePoint & {
  id: string;
  w: number;
  h: number;
};

type ImageCompareDragState =
  | {
      mode: 'rect';
      pointerId: number;
      start: ImageComparePoint;
      rectId: string;
    }
  | {
      mode: 'pan';
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startOffsetX: number;
      startOffsetY: number;
    };

const DATA_ANNOTATION_DELETED_SHAPES_STORAGE_KEY = 'fanxiu.dataAnnotation.deletedShapes.v1';
const DATA_ANNOTATION_DISCRIMINATOR_GROUPS_KEY = 'fanxiu.dataAnnotation.discriminatorGroups.v1';
const DATA_ANNOTATION_UI_STATE_STORAGE_KEY = 'fanxiu.dataAnnotation.uiState.v1';
const DATA_ANNOTATION_OCCLUSION_MASK_ENABLED_KEY = 'fanxiu.dataAnnotation.occlusionMaskEnabled.v1';
const getDataAnnotationUiStateStorageKey = (entryId = selectedEntryId.value) => (
  entryId ? `${DATA_ANNOTATION_UI_STATE_STORAGE_KEY}.${entryId}` : DATA_ANNOTATION_UI_STATE_STORAGE_KEY
);
const GAME_MACRO_FRAME_MATCH_THRESHOLD = 80;
const RUNTIME_LOG_PAGE_SIZE = 20;
const RUNTIME_LOG_PREVIEW_LIMIT = 80;
const annotationCanvasRef = ref<HTMLElement | null>(null);
const selectedAssetId = ref<string | null>(null);
const selectedShapeId = ref<string | null>(null);
const selectedShapeIds = ref<string[]>([]);
const shapeSelectionAnchorId = ref<string | null>(null);
const assetTreeViewMode = ref<AssetTreeViewMode>('business');
const assetFrameSearchText = ref('');
const alignCurrentSceneLoading = ref(false);
const globalOcclusionMaskEnabled = ref(false);
const copiedShapes = ref<DataAnnotationShape[]>([]);
const expandedAssetNodeIds = ref<string[]>([]);
const expandedShapeNodeIds = ref<string[]>([]);
const deletedShapeIds = ref<Set<string>>(new Set());
const assetImagePreviewUrls = ref<Record<string, string>>({});
const assetImagePreviewLoadingIds = ref<Record<string, boolean>>({});
const assetImagePreviewMissingIds = ref<Record<string, boolean>>({});
const assetImagePreviewRequests = new Map<string, Promise<string>>();
const assetImagePreviewRenderRecoveryKeys = new Set<string>();
let assetImagePreviewEpoch = 0;
const imageCompareDialogVisible = ref(false);
const imageCompareLoading = ref(false);
const imageCompareError = ref('');
const imageCompareSavedCanvasRef = ref<HTMLCanvasElement | null>(null);
const imageCompareLiveCanvasRef = ref<HTMLCanvasElement | null>(null);
const imageCompareSavedImage = ref<HTMLImageElement | null>(null);
const imageCompareLiveImage = ref<HTMLImageElement | null>(null);
const imageCompareCrosshair = ref<ImageComparePoint | null>(null);
const imageCompareRects = ref<ImageCompareRect[]>([]);
const imageCompareDrag = ref<ImageCompareDragState | null>(null);
const imageCompareSpacePressed = ref(false);
const imageCompareView = ref({
  scale: 1,
  offsetX: 0,
  offsetY: 0,
});
const recognitionOpsLoading = ref(false);
const recognitionOpsReport = ref<FanxiuDataAnnotationRecognitionOpsResponse | null>(null);
const recognitionOpsError = ref('');
const selectedRecognitionOpsIssueId = ref<string | null>(null);
const selectedNavigationIncident = ref<FanxiuDataAnnotationNavigationIncident | null>(null);
const navigationIncidentLoading = ref(false);
const navigationIncidentError = ref('');
const selectedNavigationTimelineIndex = ref<number | null>(null);
const selectedRecognitionAmbiguity = ref<FanxiuDataAnnotationRecognitionAmbiguitySummary | null>(null);
const recognitionAmbiguityLoading = ref(false);
const recognitionAmbiguityError = ref('');
const recognitionAmbiguityRecomputing = ref(false);
let recognitionOpsPollTimer: number | null = null;
const imageCompareCanvasSizeOf = (image: HTMLImageElement | null) => {
  const fallbackImage = imageCompareSavedImage.value || imageCompareLiveImage.value;
  const source = image || fallbackImage;
  return {
    width: Math.max(1, source?.naturalWidth || source?.width || 900),
    height: Math.max(1, source?.naturalHeight || source?.height || 1600),
  };
};
const imageCompareSavedCanvasSize = computed(() => imageCompareCanvasSizeOf(imageCompareSavedImage.value));
const imageCompareLiveCanvasSize = computed(() => imageCompareCanvasSizeOf(imageCompareLiveImage.value));
const imageCompareCanvasStyleOf = (size: { width: number; height: number }) => ({
  aspectRatio: `${size.width} / ${size.height}`,
  maxHeight: 'none',
});
const imageCompareSavedCanvasStyle = computed(() => imageCompareCanvasStyleOf(imageCompareSavedCanvasSize.value));
const imageCompareLiveCanvasStyle = computed(() => imageCompareCanvasStyleOf(imageCompareLiveCanvasSize.value));
const runtimeRunning = ref(false);
const runtimeStopRequested = ref(false);
const runtimeRunStatus = ref('');
const runtimeLogDialogVisible = ref(false);
const runtimeLogs = ref<RuntimeLogEntry[]>([]);
const runtimeLogPage = ref(1);
const runtimeLogPageCount = computed(() => Math.max(1, Math.ceil(runtimeLogs.value.length / RUNTIME_LOG_PAGE_SIZE)));
const runtimeLogPageStart = computed(() => {
  if (!runtimeLogs.value.length) return 0;
  return (runtimeLogPage.value - 1) * RUNTIME_LOG_PAGE_SIZE + 1;
});
const runtimeLogPageEnd = computed(() => Math.min(runtimeLogs.value.length, runtimeLogPage.value * RUNTIME_LOG_PAGE_SIZE));
const pagedRuntimeLogs = computed(() => (
  runtimeLogs.value.slice((runtimeLogPage.value - 1) * RUNTIME_LOG_PAGE_SIZE, runtimeLogPage.value * RUNTIME_LOG_PAGE_SIZE)
));
const goRuntimeLogFirstPage = () => {
  runtimeLogPage.value = 1;
};
const runtimeLogKindLabel = (kind: RuntimeLogKind | string) => {
  const labels: Record<string, string> = {
    start: '开始',
    wait: '等待',
    action: '动作',
    success: '成功',
    stop: '停止',
    error: '错误',
    detail: '详情',
  };
  return labels[kind] || kind || '日志';
};
const runtimeTaskStatus = ref<FanxiuBehaviorTreeRuntimeStatus | null>(null);
const runtimeFactsDialogVisible = ref(false);
const runtimeFactsLoading = ref(false);
const runtimeFactsJson = ref('{}');
const runtimeFactsPath = ref('');
const runtimePlanDialogVisible = ref(false);
const runtimePlanLoading = ref(false);
const runtimePlanJson = ref('{}');
const runtimePlanPath = ref('');
const runtimeSchedulerTasks = ref<FanxiuDataAnnotationSchedulerTaskItem[]>([]);
const runtimeSchedulerLoading = ref(false);
const selectedRuntimeTaskType = ref('');
const selectedRuntimeTaskId = ref('');
const runtimeGiftCodesText = ref('');
let runtimeTaskPollTimer: number | null = null;
const runtimeStateKind = computed(() => {
  const status = runtimeTaskStatus.value;
  if (!status) return 'idle';
  if (status.running) return 'running';
  if (status.status === 'stopping') return 'running';
  return 'idle';
});
const runtimeStateText = computed(() => {
  const status = runtimeTaskStatus.value;
  if (!status) return '未连接';
  if (status.running) return '运行中';
  if (status.status === 'stopping') return '停止中';
  return '空转';
});
const runtimeSceneText = computed(() => {
  const scene = runtimeTaskStatus.value?.current_scene;
  return typeof scene === 'number' ? `#${scene}` : '';
});
const runtimePhaseText = computed(() => runtimeTaskStatus.value?.phase || '');
const runtimeTaskTypeLabel = (taskType: string) => {
  const labels: Record<string, string> = {
    gift_code_redeem: '兑换礼包码',
    weekly_gift_code: '每周_礼包码',
    go_scene: '到场景',
    hide_floating_window: '隐藏浮动窗',
    daily_signup: '日常_报名',
    xianqiao_trial: '仙窍_试炼',
    legacy_daily_task: '旧版每日任务',
    legacy_dynamic_task: '旧版动态任务',
  };
  return labels[taskType] || taskType || '任务';
};
const runtimeTaskSourceLabel = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  return task.trigger_description || '';
};
const formatRuntimeScheduleTime = (value: string) => {
  const text = String(value || '').trim();
  if (!text) return '';
  const date = new Date(text.replace(' ', 'T'));
  if (!Number.isFinite(date.getTime())) return text;
  const now = new Date();
  const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  const isSameDate = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  if (isSameDate) return time;
  const monthDayTime = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${time}`;
  if (date.getFullYear() === now.getFullYear()) return monthDayTime;
  return `${date.getFullYear()}-${monthDayTime}`;
};
const runtimeTaskFunctionDefinitions = computed(() => {
  const grouped = new Map<string, { id: string; label: string }>();
  for (const task of runtimeSchedulerTasks.value.filter((item) => item.supported)) {
    if (!grouped.has(task.task_type)) grouped.set(task.task_type, { id: task.task_type, label: runtimeTaskTypeLabel(task.task_type) });
  }
  return Array.from(grouped.values());
});
const selectedRuntimeTaskDefinitions = computed(() => (
  runtimeSchedulerTasks.value.filter((task) => task.task_type === selectedRuntimeTaskType.value && task.supported)
));
const selectedRuntimeTaskDefinition = computed(() => (
  runtimeSchedulerTasks.value.find((task) => task.id === selectedRuntimeTaskId.value) ?? selectedRuntimeTaskDefinitions.value[0] ?? null
));
const selectedRuntimeTaskNeedsGiftCodes = computed(() => selectedRuntimeTaskDefinition.value?.task_type === 'gift_code_redeem');
const parseRuntimeGiftCodes = () => (
  runtimeGiftCodesText.value
    .split(/[\s,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
);
const buildRuntimeTaskPayloadOverride = (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (task.task_type !== 'gift_code_redeem') return {};
  const codes = parseRuntimeGiftCodes();
  return codes.length ? { codes } : {};
};
const selectedRuntimeTaskConfigText = computed(() => {
  const task = selectedRuntimeTaskDefinition.value;
  if (!task) return '';
  const parts = [
    runtimeTaskSourceLabel(task),
    task.schedule_times?.length ? `时间 ${task.schedule_times.join('/')}` : '',
    task.next_time ? `下次 ${formatRuntimeScheduleTime(task.next_time)}` : '',
    `P${task.priority}`,
    task.interruptible ? '可中断' : '不可中断',
    task.last_result ? `上次 ${task.last_result}` : '',
  ].filter(Boolean);
  return parts.join(' · ');
});
const shapeDragState = ref<ShapeDragState | null>(null);
const shapeDraftState = ref<ShapeDraftState | null>(null);
const shapeDraftBox = ref<DataAnnotationShape | null>(null);
const shapeDetectingId = ref<string | null>(null);
const shapeDetectResults = ref<Record<string, string>>({});
const shapeDetectDebugByShapeId = ref<Record<string, FanxiuGameWindow2MatchDebug>>({});
const shapeDetectDebugDialogVisible = ref(false);
const shapeDetectDebugCurrent = ref<FanxiuGameWindow2MatchDebug | null>(null);
const shapeDetectLiveBoxes = ref<FanxiuGameWindow2MatchBox[]>([]);
const shapeDetectSeq = ref(0);
const shapeDetectStopRequestedRef = ref(false);
const shapeDetectLoopEnabled = ref(false);
let shapeDetectStopRequested = false;
let shapeDetectAbortController: AbortController | null = null;
const shapeMaskDialogVisible = ref(false);
type ShapeMaskTarget = 'image' | 'ocr';
const shapeMaskTarget = ref<ShapeMaskTarget>('image');
const shapeMaskFrameCount = ref(0);
const shapeMaskThreshold = ref(36);
type ShapeMaskCaptureMode = 'single' | 'burst';
type ShapeMaskAlgorithm = 'difference' | 'background' | 'ai';
type ShapeMaskManualTool = 'erase' | 'restore';
const shapeMaskCaptureMode = ref<ShapeMaskCaptureMode>('single');
const shapeMaskAlgorithm = ref<ShapeMaskAlgorithm>('difference');
const shapeMaskLivePreviewUrl = ref('');
const shapeMaskResultPreviewUrl = ref('');
const shapeMaskAlphaDataUrl = ref('');
const shapeMaskRunning = ref(false);
const shapeMaskAiRunning = ref(false);
const shapeMaskResetToEmpty = ref(false);
const shapeMaskManualVisible = ref(false);
const shapeMaskManualTool = ref<ShapeMaskManualTool>('erase');
const shapeMaskManualBrushSize = ref(10);
const shapeMaskManualZoom = ref(3);
const shapeMaskManualCanvasWrapRef = ref<HTMLDivElement | null>(null);
const shapeMaskManualCanvasRef = ref<HTMLCanvasElement | null>(null);
const shapeMaskManualUndoStack = ref<Uint8ClampedArray[]>([]);
const shapeMaskManualRedoStack = ref<Uint8ClampedArray[]>([]);
const shapeMaskManualPointer = ref<{
  pointerId: number;
  x: number;
  y: number;
} | null>(null);
const shapeMaskManualPanState = ref<{
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startScrollLeft: number;
  startScrollTop: number;
} | null>(null);
const shapeMaskSamplingFrame = ref<number | null>(null);
const shapeMaskLivePreviewFrame = ref<number | null>(null);
const shapeMaskStats = ref<{
  width: number;
  height: number;
  min: Uint8ClampedArray;
  max: Uint8ClampedArray;
  diffMax: Uint8ClampedArray;
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
type AnnotationTreeNodeHandle = {
  data?: DataAnnotationAssetNode | DataAnnotationShape;
  expanded?: boolean;
  expand?: () => void;
  collapse?: () => void;
};
type AnnotationTreeRef = {
  getNode: (key: string) => AnnotationTreeNodeHandle | null;
  setCurrentKey?: (key: string | null) => void;
  filter?: (value: string) => void;
};
const assetTreeRef = ref<AnnotationTreeRef | null>(null);
const assetTreeScrollRef = ref<HTMLElement | null>(null);
const shapeTreeRef = ref<AnnotationTreeRef | null>(null);
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
const LAYER_TREE_ROOT_IDS = ['__frame_layer_1__', '__frame_layer_2__', '__frame_layer_3__'] as const;
const assetTreeViewModeOptions: Array<{ label: string; value: AssetTreeViewMode }> = [
  { label: '资产树', value: 'business' },
  { label: '识别层', value: 'scene' },
  { label: '识别运维', value: 'recognitionOps' },
];
const shapeTreeProps = {
  children: 'children',
  label: 'title',
};

type DataAnnotationUiState = {
  selectedAssetId?: string | null;
  selectedShapeId?: string | null;
  expandedAssetNodeIds?: string[];
  expandedShapeNodeIds?: string[];
  assetTreeViewMode?: AssetTreeViewMode;
};

const createAssetId = (prefix: string) => prefix + '-' + Date.now() + '-' + Math.random().toString(16).slice(2);

const DEFAULT_ASSET_GROUP_TITLE = '默认';
const OCCLUSION_ASSET_GROUP_TITLE = '遮挡';
const LEGACY_DEFAULT_ASSET_GROUP_TITLES = new Set(['默认分组', '默认资产分组']);
const LEGACY_OCCLUSION_ASSET_GROUP_TITLES = new Set(['遮挡标记']);

const createAssetImageNode = (
  title: string,
  options: Partial<Pick<DataAnnotationAssetNode, 'filename' | 'imageDataUrl' | 'width' | 'height' | 'layer'>> = {},
): DataAnnotationAssetNode => {
  return {
    id: createAssetId('image'),
    type: 'image',
    title,
    ...options,
    shapes: [],
  };
};

const createDefaultAssetTree = (): DataAnnotationAssetNode[] => ([
  {
    id: createAssetId('folder'),
    type: 'folder',
    title: DEFAULT_ASSET_GROUP_TITLE,
    children: [createAssetImageNode('空图')],
  },
]);

const normalizeShapeLoadDirection = (value: unknown): DataAnnotationShape['loadDirection'] => ({
  up: 'up',
  上: 'up',
  down: 'down',
  下: 'down',
  left: 'left',
  左: 'left',
  right: 'right',
  右: 'right',
}[String(value ?? '').trim().toLowerCase()] as DataAnnotationShape['loadDirection'] | undefined) ?? 'none';

const normalizeShapeLoadMode = (value: unknown): NonNullable<DataAnnotationShape['loadMode']> => (
  value === 'paged' ? 'paged' : 'continuous'
);

const normalizeShapeLoadBoundary = (value: unknown): NonNullable<DataAnnotationShape['loadBoundary']> => (
  value === 'cyclic' ? 'cyclic' : 'bounded'
);

const normalizeShapeLoadInitialPosition = (value: unknown): NonNullable<DataAnnotationShape['loadInitialPosition']> => (
  value === 'unknown' ? 'unknown' : 'start'
);

const normalizeShapePixelTolerance = (value: unknown) => {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? clamp(Math.round(numberValue), 0, 255) : DEFAULT_SHAPE_PIXEL_TOLERANCE;
};

const normalizeShapeJitterRadius = (value: unknown) => {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? clamp(Math.round(numberValue), 1, 12) : 4;
};

const normalizeShapeMatchRole = (value: unknown, fallback: ShapeMatchRole = 'off'): ShapeMatchRole => (
  value === 'optional' || value === 'required' || value === 'off' ? value : fallback
);

const normalizeFrameLayer = (value: unknown, fallback: FrameLayer = 3): FrameLayer => {
  if (value === 1 || value === '1' || value === 'layer1' || value === 'Layer 1') return 1;
  if (value === 2 || value === '2' || value === 'layer2' || value === 'Layer 2') return 2;
  if (value === 3 || value === '3' || value === 'layer3' || value === 'Layer 3') return 3;
  if (typeof value === 'number' && Number.isFinite(value)) return Math.min(3, Math.max(1, Math.floor(value))) as FrameLayer;
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Math.min(3, Math.max(1, Math.floor(Number(value.trim())))) as FrameLayer;
  return fallback;
};

const normalizeLegacyShapeMatchRole = (shape: DataAnnotationShape, key: 'imageRole' | 'ocrRole') => {
  const legacyShape = shape as DataAnnotationShape & Record<string, unknown>;
  return normalizeShapeMatchRole(legacyShape[key], 'off');
};

const normalizeShapeOcrMatchMode = (value: unknown): ShapeOcrMatchMode => (
  value === 'exact' || value === 'wildcard' || value === 'regex' ? value : 'contains'
);

const normalizeShapeOcrMaskMode = (value: unknown): ShapeOcrMaskMode => (
  value === 'custom' || value === 'off' || value === 'raw-alpha' ? value : 'inherit-envelope'
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

const normalizeSceneParentIdsText = (
  value: string | number | null | undefined,
  currentSceneId?: number | null,
) => {
  const seen = new Set<number>();
  const parentIds: number[] = [];
  for (const token of String(value ?? '').replace(/，/g, ',').split(',')) {
    const text = token.trim().replace(/^#/, '');
    if (!/^\d+$/.test(text)) continue;
    const sceneId = Number(text);
    if (!Number.isSafeInteger(sceneId) || sceneId <= 0 || sceneId === currentSceneId || seen.has(sceneId)) continue;
    seen.add(sceneId);
    parentIds.push(sceneId);
  }
  return parentIds.join(',');
};

const DAILY_TASK_BLOCK_TEMPLATE_DESCRIPTION = '日常滚动窗口内的单个任务块模板。它本身是普通 shape，同时作为字段子树的父节点；运行时由任务块模板整体浮动，子字段只按父 shape 相对位置读取状态、次数和活跃度。';

const isDailyTaskBlockTemplateShape = (shape: DataAnnotationShape) => (
  shape.id === 'shape-daily-task-block-template' || shape.title === '任务块模板'
);

const normalizeShapes = (
  shapes: DataAnnotationShape[] = [],
  parentIsDailyTaskBlockTemplate = false,
): DataAnnotationShape[] => shapes.flatMap((shape) => {
  if (shape.id === 'scene-identity') {
    return normalizeShapes(shape.children ?? [], parentIsDailyTaskBlockTemplate);
  }
  const isDailyTaskBlockTemplate = isDailyTaskBlockTemplateShape(shape);
  const isDailyTaskBlockField = parentIsDailyTaskBlockTemplate;
  const effectiveFloating = isDailyTaskBlockField ? false : Boolean(shape.floating);
  const normalizedSceneIdentityRole = normalizeShapeMatchRole(shape.sceneIdentityRole, shape.isSceneIdentity ? 'required' : 'off');
  const effectiveSceneIdentityRole = normalizedSceneIdentityRole;
  const legacyImageMatchRole = normalizeLegacyShapeMatchRole(shape, 'imageRole');
  const legacyOcrMatchRole = normalizeLegacyShapeMatchRole(shape, 'ocrRole');
  const normalizedImageMatchRole = normalizeShapeMatchRole(
    shape.imageMatchRole,
    legacyImageMatchRole !== 'off'
      ? legacyImageMatchRole
      : (effectiveFloating ? 'required' : (effectiveSceneIdentityRole !== 'off' ? effectiveSceneIdentityRole : 'off')),
  );
  const normalizedOcrMatchRole = normalizeShapeMatchRole(
    shape.ocrMatchRole,
    legacyOcrMatchRole !== 'off' ? legacyOcrMatchRole : (shape.ocrEnabled ? 'required' : 'off'),
  );
  const shapeRecord = shape as DataAnnotationShape & Record<string, unknown>;
  const normalizedLoadDirection = normalizeShapeLoadDirection(
    shape.loadDirection
      ?? shapeRecord.contentDirection
      ?? shapeRecord.load_direction
      ?? shapeRecord.content_direction
      ?? shapeRecord['窗口加载方向']
      ?? shapeRecord['内容方向'],
  );
  const shapeWithoutLegacyLoadDirection = { ...shapeRecord };
  delete shapeWithoutLegacyLoadDirection.contentDirection;
  delete shapeWithoutLegacyLoadDirection.load_direction;
  delete shapeWithoutLegacyLoadDirection.content_direction;
  delete shapeWithoutLegacyLoadDirection['窗口加载方向'];
  delete shapeWithoutLegacyLoadDirection['内容方向'];
  return [{
    ...shapeWithoutLegacyLoadDirection,
    kind: isDailyTaskBlockTemplate ? 'shape' : (shape.kind === 'group' ? 'group' : 'shape'),
    title: typeof shape.title === 'string' ? shape.title : '',
    description: isDailyTaskBlockTemplate
      ? DAILY_TASK_BLOCK_TEMPLATE_DESCRIPTION
      : (typeof shape.description === 'string' ? shape.description : ''),
    locked: Boolean(shape.locked),
    floating: effectiveFloating,
    jitterEnabled: isDailyTaskBlockField ? false : Boolean(shape.jitterEnabled),
    jitterRadius: normalizeShapeJitterRadius(shape.jitterRadius),
    isSceneIdentity: effectiveSceneIdentityRole !== 'off',
    sceneIdentityRole: effectiveSceneIdentityRole,
    sceneJumpTarget: typeof shape.sceneJumpTarget === 'string'
      ? normalizeSceneJumpTargetText(shape.sceneJumpTarget)
      : (typeof shape.sceneJumpTarget === 'number' ? String(shape.sceneJumpTarget) : ''),
    loadDirection: normalizedLoadDirection,
    ...(normalizeShapeLoadMode(shape.loadMode ?? shapeRecord.load_mode) === 'paged' ? { loadMode: 'paged' as const } : {}),
    ...(normalizeShapeLoadBoundary(shape.loadBoundary ?? shapeRecord.load_boundary) === 'cyclic' ? { loadBoundary: 'cyclic' as const } : {}),
    ...(normalizeShapeLoadInitialPosition(shape.loadInitialPosition ?? shapeRecord.load_initial_position) === 'unknown'
      ? { loadInitialPosition: 'unknown' as const }
      : {}),
    imageMatchRole: normalizedImageMatchRole,
    pixelTolerance: normalizeShapePixelTolerance(shape.pixelTolerance),
    ocrMatchRole: normalizedOcrMatchRole,
    ocrEnabled: normalizedOcrMatchRole !== 'off',
    ocrText: typeof shape.ocrText === 'string' ? shape.ocrText : '',
    ocrMatchMode: normalizeShapeOcrMatchMode(shape.ocrMatchMode),
    ocrMaskMode: normalizeShapeOcrMaskMode(shape.ocrMaskMode),
    ocrMask: shape.ocrMask && typeof shape.ocrMask === 'object'
      ? {
          width: Number(shape.ocrMask.width) || 0,
          height: Number(shape.ocrMask.height) || 0,
          dataUrl: typeof shape.ocrMask.dataUrl === 'string' ? shape.ocrMask.dataUrl : '',
        }
      : null,
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

const normalizeAssetGroupTitle = (title: unknown) => {
  const normalized = typeof title === 'string' ? title.trim() : '';
  if (LEGACY_DEFAULT_ASSET_GROUP_TITLES.has(normalized)) return DEFAULT_ASSET_GROUP_TITLE;
  if (LEGACY_OCCLUSION_ASSET_GROUP_TITLES.has(normalized)) return OCCLUSION_ASSET_GROUP_TITLE;
  return typeof title === 'string' ? title : normalized;
};

const normalizeAssetImageTitle = (title: unknown, filename: unknown) => {
  const normalizedTitle = typeof title === 'string' ? title.trim() : '';
  const normalizedFilename = typeof filename === 'string' ? filename.trim() : '';
  return normalizedFilename && normalizedTitle.toLowerCase() === normalizedFilename.toLowerCase()
    ? ''
    : normalizedTitle;
};

const normalizeAssetTree = (nodes: DataAnnotationAssetNode[]): DataAnnotationAssetNode[] => nodes.map((node) => {
  if (node.type === 'folder') {
    return {
      ...node,
      title: normalizeAssetGroupTitle(node.title),
      children: normalizeAssetTree(node.children ?? []),
    };
  }
  const normalizedNode = { ...node } as DataAnnotationAssetNode & { occlusionMaskEnabled?: boolean };
  delete normalizedNode.occlusionMaskEnabled;
  const normalizedShapes = normalizeShapes(node.shapes ?? []);
  return {
    ...normalizedNode,
    title: normalizeAssetImageTitle(node.title, node.filename),
    filename: typeof node.filename === 'string' ? node.filename : undefined,
    imageDataUrl: typeof node.imageDataUrl === 'string' ? node.imageDataUrl : undefined,
    width: typeof node.width === 'number' ? node.width : undefined,
    height: typeof node.height === 'number' ? node.height : undefined,
    layer: node.layer === 1 ? 1 : undefined,
    parentSceneIds: normalizeSceneParentIdsText(node.parentSceneIds) || undefined,
    shapes: normalizedShapes,
    children: normalizeAssetTree(node.children ?? []),
  };
});

const loadGlobalOcclusionMaskEnabled = () => {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(DATA_ANNOTATION_OCCLUSION_MASK_ENABLED_KEY) === 'true';
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

const assetTree = ref<DataAnnotationAssetNode[]>([]);
const assetTreeBackendHydrating = ref(false);
const assetTreeBackendUpdatedAt = ref(0);
const assetTreeBackendRevision = ref('');
let assetTreeLocalVersion = 0;
let assetTreeLoadedEntryId = '';
let assetTreeSaveTimer: ReturnType<typeof setTimeout> | null = null;
let assetTreeSaveChain: Promise<boolean> = Promise.resolve(true);
let assetTreeDirty = false;
let assetTreeConflictNotified = false;

const cloneAssetTree = (nodes: DataAnnotationAssetNode[]) => (
  JSON.parse(JSON.stringify(nodes)) as DataAnnotationAssetNode[]
);

const assetFrameNumberOf = (node: DataAnnotationAssetNode) => {
  const source = node.filename || node.title || '';
  const match = source.match(/(?:^|#|[^\d])0*(\d+)(?=\.[^.]+$|[^\d]|$)/);
  return match ? Number(match[1]) : null;
};

const assetNodeMergeKey = (node: DataAnnotationAssetNode) => (
  node.type === 'image'
    ? `image:${assetFrameNumberOf(node) ?? (node.filename || node.title || node.id).trim().toLowerCase()}`
    : `folder:${(node.title || node.id).trim()}`
);

const shapeDeleteIdKey = (shapeId: string) => `id:${shapeId}`;

const shapeDeleteNumberKey = (value: number) => String(Math.round(value * 10000));

const isShapeDeletedForImage = (image: DataAnnotationAssetNode, shape: DataAnnotationShape) => (
  deletedShapeIds.value.has(shape.id)
  || deletedShapeIds.value.has(shapeDeleteIdKey(shape.id))
);

const filterDeletedShapesForImage = (
  image: DataAnnotationAssetNode,
  shapes: DataAnnotationShape[] = [],
): DataAnnotationShape[] => shapes.flatMap((shape) => {
  if (isShapeDeletedForImage(image, shape)) return [];
  return [{
    ...shape,
    children: filterDeletedShapesForImage(image, shape.children ?? []),
  }];
});

const filterDeletedShapesFromAssetTree = (nodes: DataAnnotationAssetNode[]): DataAnnotationAssetNode[] => nodes.map((node) => {
  if (node.type === 'folder') {
    return {
      ...node,
      children: filterDeletedShapesFromAssetTree(node.children ?? []),
    };
  }
  return {
    ...node,
    shapes: filterDeletedShapesForImage(node, node.shapes ?? []),
  };
});

const collectAssetNodeMergeKeys = (nodes: DataAnnotationAssetNode[], keys = new Set<string>()) => {
  for (const node of nodes) {
    keys.add(assetNodeMergeKey(node));
    collectAssetNodeMergeKeys(node.children ?? [], keys);
  }
  return keys;
};

const findMergeTargetFolder = (nodes: DataAnnotationAssetNode[], title: string) => {
  for (const node of nodes) {
    if (node.type === 'folder' && node.title === title) return node;
    const found = findMergeTargetFolder(node.children ?? [], title);
    if (found) return found;
  }
  return null;
};

const findAssetNodeByMergeKey = (nodes: DataAnnotationAssetNode[], key: string): DataAnnotationAssetNode | null => {
  for (const node of nodes) {
    if (assetNodeMergeKey(node) === key) return node;
    const found = findAssetNodeByMergeKey(node.children ?? [], key);
    if (found) return found;
  }
  return null;
};

const preferAssetFilename = (left = '', right = '') => {
  if (!left) return right;
  if (!right) return left;
  const leftIsPng = left.toLowerCase().endsWith('.png');
  const rightIsPng = right.toLowerCase().endsWith('.png');
  if (rightIsPng && !leftIsPng) return right;
  return left;
};

const shapeMergeKey = (shape: DataAnnotationShape) => [
  (shape.title || '').trim(),
  shapeDeleteNumberKey(shape.x),
  shapeDeleteNumberKey(shape.y),
  shapeDeleteNumberKey(shape.w),
  shapeDeleteNumberKey(shape.h),
].join(':');

const mergeDuplicateAssetNode = (target: DataAnnotationAssetNode, source: DataAnnotationAssetNode) => {
  target.filename = preferAssetFilename(target.filename, source.filename);
  if (!target.imageDataUrl && source.imageDataUrl) target.imageDataUrl = source.imageDataUrl;
  if (!target.width && source.width) target.width = source.width;
  if (!target.height && source.height) target.height = source.height;
  if (source.type === 'image' && typeof source.parentSceneIds === 'string') {
    target.parentSceneIds = source.parentSceneIds;
  }
  if (target.type === 'folder' && source.type === 'folder') {
    target.children = mergeAssetTreeNodes(target.children ?? [], source.children ?? []);
  }
  if (target.type === 'image' && source.type === 'image') {
    target.shapes = filterDeletedShapesForImage(target, target.shapes ?? []);
    const shapeIds = new Set((target.shapes ?? []).map((shape) => shape.id));
    const shapeKeys = new Set((target.shapes ?? []).map((shape) => shapeMergeKey(shape)));
    const extraShapes = filterDeletedShapesForImage(source, source.shapes ?? [])
      .filter((shape) => !shapeIds.has(shape.id) && !shapeKeys.has(shapeMergeKey(shape)));
    if (extraShapes.length) {
      target.shapes = [
        ...(target.shapes ?? []),
        ...(JSON.parse(JSON.stringify(extraShapes)) as DataAnnotationShape[]),
      ];
    }
  }
};

const removeAssetNodeByReference = (
  nodes: DataAnnotationAssetNode[],
  target: DataAnnotationAssetNode,
): boolean => {
  const index = nodes.indexOf(target);
  if (index >= 0) {
    nodes.splice(index, 1);
    return true;
  }
  return nodes.some((node) => removeAssetNodeByReference(node.children ?? [], target));
};

const compactDuplicateAssetNodes = (nodes: DataAnnotationAssetNode[]) => {
  const merged = cloneAssetTree(nodes);
  const seen = new Map<string, DataAnnotationAssetNode>();
  const visit = (items: DataAnnotationAssetNode[]) => {
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

const mergeAssetTreeNodes = (baseNodes: DataAnnotationAssetNode[], extraNodes: DataAnnotationAssetNode[]) => {
  const merged = cloneAssetTree(baseNodes);
  const knownKeys = collectAssetNodeMergeKeys(merged);
  const appendMissing = (target: DataAnnotationAssetNode[], incoming: DataAnnotationAssetNode[]) => {
    for (const node of incoming) {
      const key = assetNodeMergeKey(node);
      const existing = knownKeys.has(key) ? findAssetNodeByMergeKey(merged, key) : null;
      if (existing) {
        mergeDuplicateAssetNode(existing, node);
        continue;
      }
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

const assetTreesEqual = (left: DataAnnotationAssetNode[], right: DataAnnotationAssetNode[]) => (
  JSON.stringify(left) === JSON.stringify(right)
);

const flushAssetTreeToBackend = async (
  entryId: string,
  tree: DataAnnotationAssetNode[],
  localVersion = assetTreeLocalVersion,
  baseRevision = assetTreeBackendRevision.value,
) => {
  try {
    const response = await saveFanxiuDataAnnotationAssetTree(entryId, tree, baseRevision);
    if (Array.isArray(response.tree) && assetTreeLoadedEntryId === entryId) {
      if (localVersion === assetTreeLocalVersion) {
        const nextTree = filterDeletedShapesFromAssetTree(compactDuplicateAssetNodes(normalizeAssetTree(response.tree as DataAnnotationAssetNode[])));
        if (!assetTreesEqual(nextTree, assetTree.value)) {
          assetTreeBackendHydrating.value = true;
          try {
            assetTree.value = nextTree;
            await nextTick();
          } finally {
            assetTreeBackendHydrating.value = false;
          }
        }
      }
    }
    if (assetTreeLoadedEntryId === entryId) {
      assetTreeBackendUpdatedAt.value = Number(response.updated_at) || assetTreeBackendUpdatedAt.value;
      assetTreeBackendRevision.value = response.revision || assetTreeBackendRevision.value;
      assetTreeConflictNotified = false;
    }
    return true;
  } catch (error) {
    if (getHttpStatus(error) === 409) {
      if (!assetTreeConflictNotified) {
        assetTreeConflictNotified = true;
        ElMessage.warning('另一页面刚刚更新了资产树；本页编辑已保留，请刷新页面后继续');
      }
      return false;
    }
    console.error(error);
    ElMessage.error(getErrorMessage(error));
    return false;
  }
};

const enqueueAssetTreeSave = () => {
  const entryId = assetTreeLoadedEntryId;
  const localVersion = assetTreeLocalVersion;
  const tree = cloneAssetTree(assetTree.value);
  const task = assetTreeSaveChain.then(async () => {
    if (!assetTreeDirty || !entryId) return true;
    const persisted = await flushAssetTreeToBackend(entryId, tree, localVersion, assetTreeBackendRevision.value);
    if (persisted && entryId === assetTreeLoadedEntryId && localVersion === assetTreeLocalVersion) {
      assetTreeDirty = false;
    }
    if (persisted && entryId === assetTreeLoadedEntryId && assetTreeDirty) scheduleAssetTreeBackendSave();
    return persisted;
  });
  assetTreeSaveChain = task.catch(() => false);
  return task;
};

const saveAssetTreeNow = async () => {
  if (assetTreeSaveTimer) {
    window.clearTimeout(assetTreeSaveTimer);
    assetTreeSaveTimer = null;
  }
  await nextTick();
  assetTreeDirty = true;
  return enqueueAssetTreeSave();
};

const flushAssetTreeWhenHidden = () => {
  if (document.visibilityState === 'hidden' && assetTreeDirty) void saveAssetTreeNow();
};

const scheduleAssetTreeBackendSave = () => {
  if (assetTreeBackendHydrating.value || !selectedEntryId.value) return;
  assetTreeDirty = true;
  if (assetTreeSaveTimer) window.clearTimeout(assetTreeSaveTimer);
  assetTreeSaveTimer = window.setTimeout(() => {
    assetTreeSaveTimer = null;
    void enqueueAssetTreeSave();
  }, 400);
};

const loadEntryAssetTree = async (entryId: string) => {
  if (!entryId) return;
  assetTreeBackendHydrating.value = true;
  assetTreeBackendUpdatedAt.value = 0;
  assetTreeBackendRevision.value = '';
  try {
    const response = await getFanxiuDataAnnotationAssetTree(entryId);
    if (response.exists && Array.isArray(response.tree) && response.tree.length) {
      const backendTree = normalizeAssetTree(response.tree as DataAnnotationAssetNode[]);
      assetTree.value = filterDeletedShapesFromAssetTree(compactDuplicateAssetNodes(backendTree));
      assetTreeBackendUpdatedAt.value = Number(response.updated_at) || 0;
      assetTreeBackendRevision.value = response.revision || '';
    } else {
      assetTree.value = [];
      assetTreeBackendUpdatedAt.value = 0;
      assetTreeBackendRevision.value = response.revision || '';
    }
    assetTreeLoadedEntryId = entryId;
    restoreDataAnnotationUiState();
    await syncAssetTreeExpansionFromState();
    await focusImageFromRoute();
    await nextTick();
    assetTreeDirty = false;
    assetTreeConflictNotified = false;
    void nextTick(syncCanvas);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    assetTreeBackendHydrating.value = false;
  }
};

const refreshEntryAssetTreeIfChanged = async () => {
  if (!selectedEntryId.value || assetTreeBackendHydrating.value || assetTreeDirty) return;
  const entryId = selectedEntryId.value;
  const selectedNode = selectedAssetNode.value;
  const selectedKey = selectedNode ? assetNodeMergeKey(selectedNode) : '';
  try {
    const response = await getFanxiuDataAnnotationAssetTree(entryId);
    const backendUpdatedAt = Number(response.updated_at) || 0;
    if (!response.exists || !Array.isArray(response.tree) || response.revision === assetTreeBackendRevision.value) return;
    assetTreeBackendHydrating.value = true;
    const backendTree = normalizeAssetTree(response.tree as DataAnnotationAssetNode[]);
    const latestTree = filterDeletedShapesFromAssetTree(compactDuplicateAssetNodes(backendTree));
    assetTree.value = latestTree;
    assetTreeBackendUpdatedAt.value = backendUpdatedAt;
    assetTreeBackendRevision.value = response.revision || '';
    expandedAssetNodeIds.value = filterExistingAssetNodeIds(expandedAssetNodeIds.value);
    await syncAssetTreeExpansionFromState();
    if (selectedAssetId.value && findAssetNode(latestTree, selectedAssetId.value)) return;
    const restoredNode = selectedKey ? findAssetNodeByMergeKey(latestTree, selectedKey) : null;
    selectedAssetId.value = restoredNode?.id ?? findFirstImageNode(latestTree)?.id ?? null;
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
  const raw = window.localStorage.getItem(DATA_ANNOTATION_DISCRIMINATOR_GROUPS_KEY);
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

const loadDeletedShapeIds = () => {
  if (typeof window !== 'undefined') window.localStorage.removeItem(DATA_ANNOTATION_DELETED_SHAPES_STORAGE_KEY);
  return new Set<string>();
};

const persistDeletedShapeIds = () => {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(DATA_ANNOTATION_DELETED_SHAPES_STORAGE_KEY);
};

deletedShapeIds.value = loadDeletedShapeIds();

const loadDataAnnotationUiState = (): DataAnnotationUiState => {
  if (typeof window === 'undefined') return {};
  const scopedKey = getDataAnnotationUiStateStorageKey();
  const raw = window.localStorage.getItem(scopedKey)
    || (scopedKey === DATA_ANNOTATION_UI_STATE_STORAGE_KEY ? '' : window.localStorage.getItem(DATA_ANNOTATION_UI_STATE_STORAGE_KEY));
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as DataAnnotationUiState;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
};

const persistDataAnnotationUiState = () => {
  if (typeof window === 'undefined' || !selectedEntryId.value) return;
  window.localStorage.setItem(getDataAnnotationUiStateStorageKey(), JSON.stringify({
    selectedAssetId: selectedAssetId.value,
    selectedShapeId: selectedShapeId.value,
    expandedAssetNodeIds: expandedAssetNodeIds.value,
    expandedShapeNodeIds: expandedShapeNodeIds.value,
    assetTreeViewMode: assetTreeViewMode.value,
  }));
};

const findAssetNode = (nodes: DataAnnotationAssetNode[], id: string | null): DataAnnotationAssetNode | null => {
  if (!id) return null;
  for (const node of nodes) {
    if (node.id === id) return node;
    const found = findAssetNode(node.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const findFirstImageNode = (nodes: DataAnnotationAssetNode[]): DataAnnotationAssetNode | null => {
  for (const node of nodes) {
    if (node.type === 'image') return node;
    const found = findFirstImageNode(node.children ?? []);
    if (found) return found;
  }
  return null;
};

const flattenAssetImages = (nodes: DataAnnotationAssetNode[]): DataAnnotationAssetNode[] => nodes.flatMap((node) => [
  ...(node.type === 'image' ? [node] : []),
  ...flattenAssetImages(node.children ?? []),
]);

const collectAssetImageRecords = (
  nodes: DataAnnotationAssetNode[],
): DataAnnotationAssetNode[] => nodes.flatMap((node) => {
  if (node.type === 'folder') return collectAssetImageRecords(node.children ?? []);
  return [
    node,
    ...collectAssetImageRecords(node.children ?? []),
  ];
});

const collectAssetNodeIds = (node: DataAnnotationAssetNode): string[] => [
  node.id,
  ...(node.children ?? []).flatMap(collectAssetNodeIds),
];

const findAssetParentFolder = (
  nodes: DataAnnotationAssetNode[],
  id: string | null,
  parent: DataAnnotationAssetNode | null = null,
): DataAnnotationAssetNode | null => {
  if (!id) return null;
  for (const node of nodes) {
    if (node.id === id) return parent;
    const found = findAssetParentFolder(node.children ?? [], id, node.type === 'folder' ? node : parent);
    if (found) return found;
  }
  return null;
};

const assetImageNumber = (node: DataAnnotationAssetNode) => {
  const fileLikeSource = node.filename || node.title || '';
  const filenameNumber = fileLikeSource.match(/(\d+)(?=\.[^.]+$|$)/)?.[1];
  if (filenameNumber) return Number(filenameNumber);
  const idSceneNumber = String(node.id || '').match(/(?:^|[-_])0*(\d{1,4})(?=[-_])/)?.[1];
  return idSceneNumber ? Number(idSceneNumber) : null;
};

const assetImageIdMark = (node: DataAnnotationAssetNode) => {
  const source = node.filename || node.id;
  const imageNumber = assetImageNumber(node);
  if (imageNumber !== null) return '#' + String(imageNumber);
  const idTail = source.match(/([a-zA-Z0-9]{2,})$/)?.[1] || source;
  return '#' + idTail.slice(-6);
};

const assetNumericImageId = (node: DataAnnotationAssetNode) => {
  if (node.type !== 'image') return null;
  return assetImageNumber(node);
};

const findAssetImageByNumericId = (nodes: DataAnnotationAssetNode[], id: number | null): DataAnnotationAssetNode | null => {
  if (id === null) return null;
  for (const node of nodes) {
    if (assetNumericImageId(node) === id) return node;
    const found = findAssetImageByNumericId(node.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const sceneImageLabel = (imageId: number | null, imagesByNumber: Map<number, DataAnnotationAssetNode>, fallback = '?') => {
  if (imageId === null) return fallback;
  const image = imagesByNumber.get(imageId);
  return image ? `#${imageId} ${image.title || image.filename || '未命名'}` : `#${imageId} 缺失`;
};

const assetImagesByNumber = computed(() => {
  const result = new Map<number, DataAnnotationAssetNode>();
  for (const image of flattenAssetImages(assetTree.value)) {
    const imageId = assetNumericImageId(image);
    if (imageId !== null) result.set(imageId, image);
  }
  return result;
});

const recognitionOpsIssueById = computed(() => {
  const map = new Map<string, FanxiuDataAnnotationRecognitionOpsIssue>();
  for (const issue of recognitionOpsReport.value?.issues ?? []) {
    map.set(issue.id, issue);
  }
  return map;
});

const selectedRecognitionOpsIssue = computed(() => (
  selectedRecognitionOpsIssueId.value ? recognitionOpsIssueById.value.get(selectedRecognitionOpsIssueId.value) ?? null : null
));

const selectedNavigationTimelineItem = computed<FanxiuDataAnnotationNavigationIncidentTimelineItem | null>(() => {
  const timeline = selectedNavigationIncident.value?.timeline ?? [];
  if (!timeline.length) return null;
  return timeline.find((item) => item.index === selectedNavigationTimelineIndex.value) ?? timeline[0] ?? null;
});

const navigationIncidentStatusLabel = (status: string) => ({
  recovering: '恢复中断 · 待复盘',
  recovered_with_fallback: '#424 已恢复 · 待复盘',
  recovered_after_stall: '重规划已恢复 · 待复盘',
  unrecovered: '未恢复 · 待复盘',
}[status] || `${status || '未知'} · 待复盘`);

const navigationIncidentRecordText = (record: object | undefined, key: string) => (
  String((record as Record<string, unknown> | undefined)?.[key] ?? '')
);

const navigationIncidentSceneText = (sceneId: number | null | undefined, score: number | null | undefined) => {
  const scene = sceneId === null || sceneId === undefined ? 'unknown' : `#${sceneId}`;
  return score === null || score === undefined ? scene : `${scene} ${Math.trunc(Number(score) || 0)}%`;
};

const navigationIncidentFrameUrl = (path: string | null | undefined) => (
  path ? selectedNavigationIncident.value?.frame_data_urls?.[path] || '' : ''
);

const navigationIncidentDiagnosticText = computed(() => {
  const diagnostic = selectedNavigationIncident.value?.diagnostic;
  if (!diagnostic) return '';
  const candidates = Array.isArray(diagnostic.candidates) ? diagnostic.candidates : [];
  const ocrTexts = Array.isArray(diagnostic.ocr_texts) ? diagnostic.ocr_texts : [];
  const lines: string[] = [];
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue;
    const item = candidate as Record<string, unknown>;
    lines.push(`#${item.scene_id ?? '?'} ${item.title ?? ''}：场景 ${item.scene_score ?? '--'}%，全图 ${item.frame_similarity ?? '--'}%`);
    const identities = Array.isArray(item.identity_scores) ? item.identity_scores : [];
    for (const identity of identities) {
      if (!identity || typeof identity !== 'object') continue;
      const shape = identity as Record<string, unknown>;
      lines.push(`  ${shape.title ?? 'shape'} ${shape.score ?? '--'}%`);
    }
  }
  if (ocrTexts.length) {
    lines.push(`OCR：${ocrTexts.map((item) => String(item)).join(' / ')}`);
  }
  const suggestion = String(diagnostic.suggestion ?? '');
  if (suggestion) lines.push(`建议：${suggestion}`);
  return lines.join('\n') || JSON.stringify(diagnostic, null, 2);
});

const navigationIncidentIdentityCrops = computed(() => (
  (selectedNavigationIncident.value?.frames ?? [])
    .filter((item) => item.role === 'identity_crop' && Boolean(navigationIncidentFrameUrl(item.path)))
));

const recognitionOpsCacheMissing = computed(() => Boolean(recognitionOpsReport.value?.matrix?.cache_missing));
const recognitionOpsRecomputing = computed(() => Boolean(recognitionOpsReport.value?.recompute?.running));

const recognitionOpsTreeData = computed<RecognitionOpsTreeNode[]>(() => buildRecognitionOpsTree(
  recognitionOpsReport.value?.categories ?? [],
  recognitionOpsReport.value?.issues ?? [],
));

const recognitionAmbiguitySelectionText = computed(() => (
  formatAmbiguitySelectionCounts(selectedRecognitionAmbiguity.value?.selected_scene_counts ?? {})
));

const recognitionAmbiguityFrameUrl = (path: string) => (
  selectedRecognitionAmbiguity.value?.frame_data_urls?.[path] ?? ''
);

const recognitionOpsMatrixText = computed(() => {
  const matrix = recognitionOpsReport.value?.matrix;
  if (!matrix) return '矩阵 --';
  const expected = Number(matrix.expected_node_count || 0);
  const nodeCoverage = expected > 0 && expected !== matrix.node_count ? `${matrix.node_count}/${expected}节点` : `${matrix.node_count}节点`;
  if (matrix.cache_missing) return `矩阵未生成 ${nodeCoverage}`;
  if (matrix.cache_stale || matrix.cache_partial) {
    return `历史矩阵 ${nodeCoverage}`;
  }
  const cacheText = matrix.cache_hit ? '缓存' : '新算';
  return `${cacheText} ${nodeCoverage}`;
});

const recognitionOpsEdgeValue = (value: number | string | null | undefined) => {
  if (value === undefined || value === null || value === '') return '--';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(1).replace(/\.0$/, '') : String(value);
};

const recognitionScorePercentLabel = (value: number | string | null | undefined) => {
  if (value === undefined || value === null || value === '') return '';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  const percent = numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
  return `${Math.trunc(percent)}%`;
};

const recognitionOpsScoreByPair = computed(() => {
  const result = new Map<string, number | string | null | undefined>();
  for (const edge of recognitionOpsReport.value?.edges ?? []) {
    result.set(`${Number(edge.source_id)}:${Number(edge.target_id)}`, edge.score);
  }
  for (const issue of recognitionOpsReport.value?.issues ?? []) {
    for (const edge of issue.edges ?? []) {
      result.set(`${Number(edge.source_id)}:${Number(edge.target_id)}`, edge.score);
    }
  }
  return result;
});

const recognitionPairScore = (sourceId: number | null, targetId: number | null) => {
  if (sourceId === null || targetId === null) return undefined;
  return recognitionOpsScoreByPair.value.get(`${sourceId}:${targetId}`);
};

const stopRecognitionOpsPolling = () => {
  if (!recognitionOpsPollTimer) return;
  window.clearInterval(recognitionOpsPollTimer);
  recognitionOpsPollTimer = null;
};

const startRecognitionOpsPolling = () => {
  if (recognitionOpsPollTimer) return;
  recognitionOpsPollTimer = window.setInterval(() => {
    if (assetTreeViewMode.value !== 'recognitionOps' || !selectedEntryId.value) {
      stopRecognitionOpsPolling();
      return;
    }
    void loadRecognitionOps(false, true);
  }, 2000);
};

const syncRecognitionOpsPolling = (response: FanxiuDataAnnotationRecognitionOpsResponse) => {
  if (response.recompute?.running) {
    startRecognitionOpsPolling();
    return;
  }
  stopRecognitionOpsPolling();
  if (response.recompute?.error) {
    ElMessage.error(response.recompute.error);
  }
};

const loadRecognitionOps = async (recompute = false, silent = false) => {
  if (!selectedEntryId.value) return;
  if (!silent) recognitionOpsLoading.value = true;
  recognitionOpsError.value = '';
  try {
    const response = await getFanxiuDataAnnotationRecognitionOps(selectedEntryId.value, recompute);
    recognitionOpsReport.value = response;
    syncRecognitionOpsPolling(response);
    if (selectedRecognitionOpsIssueId.value && !response.issues.some((issue) => issue.id === selectedRecognitionOpsIssueId.value)) {
      selectedRecognitionOpsIssueId.value = null;
      selectedNavigationIncident.value = null;
      selectedRecognitionAmbiguity.value = null;
      selectedNavigationTimelineIndex.value = null;
    }
  } catch (error) {
    recognitionOpsError.value = getErrorMessage(error);
    stopRecognitionOpsPolling();
  } finally {
    if (!silent) recognitionOpsLoading.value = false;
  }
};

const handleRecognitionOpsNodeClick = (node: RecognitionOpsTreeNode) => {
  if (node.type !== 'issue' || !node.issueId) return;
  selectedRecognitionOpsIssueId.value = node.issueId;
  activeSceneRelationGraphTab.value = 'recognition';
  const issue = recognitionOpsIssueById.value.get(node.issueId);
  if (issue?.incident?.id) {
    void loadNavigationIncident(issue.incident.id);
    selectedRecognitionAmbiguity.value = null;
    recognitionAmbiguityError.value = '';
  } else if (issue?.ambiguity?.signature) {
    void loadRecognitionAmbiguity(issue.ambiguity.signature);
    selectedNavigationIncident.value = null;
    navigationIncidentError.value = '';
    selectedNavigationTimelineIndex.value = null;
  } else {
    selectedNavigationIncident.value = null;
    selectedRecognitionAmbiguity.value = null;
    navigationIncidentError.value = '';
    recognitionAmbiguityError.value = '';
    selectedNavigationTimelineIndex.value = null;
  }
  const firstImageId = issue?.node_ids[0];
  if (firstImageId !== undefined) void focusRecognitionOpsImage(firstImageId);
};

const loadRecognitionAmbiguity = async (signature: string, recompute = false) => {
  if (!selectedEntryId.value) return;
  if (recompute) recognitionAmbiguityRecomputing.value = true;
  else recognitionAmbiguityLoading.value = true;
  recognitionAmbiguityError.value = '';
  try {
    const response = await getFanxiuDataAnnotationRecognitionAmbiguity(selectedEntryId.value, signature, recompute);
    if (selectedRecognitionOpsIssue.value?.ambiguity?.signature !== signature) return;
    selectedRecognitionAmbiguity.value = response.ambiguity;
  } catch (error) {
    recognitionAmbiguityError.value = getErrorMessage(error);
    if (!recompute) selectedRecognitionAmbiguity.value = null;
  } finally {
    recognitionAmbiguityLoading.value = false;
    recognitionAmbiguityRecomputing.value = false;
  }
};

const loadNavigationIncident = async (incidentId: string) => {
  if (!selectedEntryId.value) return;
  navigationIncidentLoading.value = true;
  navigationIncidentError.value = '';
  try {
    const response = await getFanxiuDataAnnotationNavigationIncident(selectedEntryId.value, incidentId);
    if (selectedRecognitionOpsIssue.value?.incident?.id !== incidentId) return;
    selectedNavigationIncident.value = response.incident;
    selectedNavigationTimelineIndex.value = response.incident.timeline[0]?.index ?? null;
  } catch (error) {
    selectedNavigationIncident.value = null;
    navigationIncidentError.value = getErrorMessage(error);
  } finally {
    navigationIncidentLoading.value = false;
  }
};

const focusRecognitionOpsImage = async (imageId: number) => {
  const image = findAssetImageByNumericId(assetTree.value, Number(imageId));
  if (!image) {
    ElMessage.warning(`#${imageId} 不在当前资产树`);
    return;
  }
  await focusAssetImage(image);
};

const sceneRelationKindLabel = (kind: SceneRelationEdgeKind) => ({
  recognition: '识别',
  jump: '跳转',
  discriminator: '区分',
}[kind]);

const buildSceneRelationTooltip = (edge: Omit<SceneRelationEdge, 'tooltip'>) => (
  [
    `${edge.kindLabel}: ${edge.sourceLabel} -> ${edge.targetLabel}`,
    edge.score !== undefined && edge.score !== null ? `score: ${recognitionScorePercentLabel(edge.score)}` : '',
    edge.shapeTitle ? `shape: ${edge.shapeTitle}` : '',
  ].filter(Boolean).join('\n')
);

type EffectiveShapeEntry = {
  shape: DataAnnotationShape;
  sourceSceneId: number;
  unitKey: string;
};

const effectiveSceneImages = (nodes: DataAnnotationAssetNode[]) => {
  const rawImages = collectAssetImageRecords(nodes);
  const imagesByNumber = new Map<number, DataAnnotationAssetNode>();
  for (const image of rawImages) {
    const sceneId = assetNumericImageId(image);
    if (sceneId !== null) imagesByNumber.set(sceneId, image);
  }
  const resolvedEntries = new Map<number, EffectiveShapeEntry[]>();
  const resolving: number[] = [];
  const parseParents = (image: DataAnnotationAssetNode) => (
    String(image.parentSceneIds ?? '')
      .split(/[,，]/)
      .map((item) => Number(item.trim().replace(/^#/, '')))
      .filter((item, index, values) => Number.isInteger(item) && item > 0 && values.indexOf(item) === index)
  );
  const resolve = (sceneId: number): EffectiveShapeEntry[] => {
    const cached = resolvedEntries.get(sceneId);
    if (cached) return cached;
    const image = imagesByNumber.get(sceneId);
    if (!image) throw new Error(`场景 #${sceneId} 不存在`);
    if (resolving.includes(sceneId)) {
      const cycle = [...resolving.slice(resolving.indexOf(sceneId)), sceneId].map((id) => `#${id}`).join(' -> ');
      throw new Error(`检测到循环继承 ${cycle}`);
    }
    resolving.push(sceneId);
    const entries: EffectiveShapeEntry[] = [];
    const seen = new Set<string>();
    const append = (entry: EffectiveShapeEntry) => {
      if (seen.has(entry.unitKey)) return;
      seen.add(entry.unitKey);
      entries.push(entry);
    };
    for (const parentId of parseParents(image)) {
      if (!imagesByNumber.has(parentId)) throw new Error(`场景 #${sceneId} 引用了不存在的父场景 #${parentId}`);
      resolve(parentId).forEach(append);
    }
    for (const shape of image.shapes ?? []) {
      const signature = shape.id || [
        shape.title,
        shape.x,
        shape.y,
        shape.w,
        shape.h,
      ].join(':');
      append({ shape, sourceSceneId: sceneId, unitKey: `${sceneId}:${signature}` });
    }
    resolving.pop();
    resolvedEntries.set(sceneId, entries);
    return entries;
  };

  const result = new Map<number, DataAnnotationAssetNode>();
  for (const [sceneId, image] of imagesByNumber) {
    try {
      result.set(sceneId, { ...image, shapes: resolve(sceneId).map((entry) => entry.shape) });
    } catch (error) {
      console.warn(`Shape 继承解析失败：${getErrorMessage(error)}`);
      result.set(sceneId, image);
    }
  }
  return result;
};

const buildSceneRelationEdges = (nodes: DataAnnotationAssetNode[]) => {
  const effectiveImages = effectiveSceneImages(nodes);
  const imageRecords = collectAssetImageRecords(nodes).map((image) => {
    const imageId = assetNumericImageId(image);
    return imageId === null ? image : (effectiveImages.get(imageId) ?? image);
  });
  const images = imageRecords;
  const imagesByNumber = new Map<number, DataAnnotationAssetNode>();
  for (const image of images) {
    const imageId = assetNumericImageId(image);
    if (imageId !== null) imagesByNumber.set(imageId, image);
  }

  const edges: SceneRelationEdge[] = [];
  const pushEdge = (edge: Omit<SceneRelationEdge, 'tooltip'>) => {
    edges.push({
      ...edge,
      tooltip: buildSceneRelationTooltip(edge),
    });
  };

  for (const image of imageRecords) {
    const sourceId = assetNumericImageId(image);
    if (sourceId === null) continue;
    for (const shape of flattenShapes(image.shapes ?? [])) {
      for (const target of parseSceneJumpEntries(shape.sceneJumpTarget)) {
        const normalizedTarget = Number(String(target.label).replace(/^#/, ''));
        const targetId = Number.isFinite(normalizedTarget) && target.label !== '-1' ? normalizedTarget : null;
        pushEdge({
          id: `jump:${sourceId}:${shape.id}:${target.label}`,
          kind: 'jump',
          kindLabel: sceneRelationKindLabel('jump'),
          sourceId,
          targetId,
          sourceLabel: sceneImageLabel(sourceId, imagesByNumber),
          targetLabel: targetId === null ? target.label : sceneImageLabel(targetId, imagesByNumber),
          shapeId: shape.id,
          shapeTitle: shape.title || 'shape',
          focusImageId: targetId,
        });
      }

      const discriminatorTargetId = shape.discriminator?.targetImageId ?? null;
      if (Number.isFinite(Number(discriminatorTargetId)) && Number(discriminatorTargetId) > 0) {
        const targetId = Math.floor(Number(discriminatorTargetId));
        pushEdge({
          id: `discriminator:${sourceId}:${shape.id}:${targetId}`,
          kind: 'discriminator',
          kindLabel: sceneRelationKindLabel('discriminator'),
          sourceId,
          targetId,
          sourceLabel: sceneImageLabel(sourceId, imagesByNumber),
          targetLabel: sceneImageLabel(targetId, imagesByNumber),
          shapeId: shape.id,
          shapeTitle: shape.title || 'shape',
          focusImageId: targetId,
        });
      }
    }
  }
  return edges.sort((left, right) => (
    left.kind.localeCompare(right.kind)
    || String(left.sourceId ?? '').localeCompare(String(right.sourceId ?? ''))
    || String(left.targetId ?? '').localeCompare(String(right.targetId ?? ''))
    || String(left.shapeTitle ?? '').localeCompare(String(right.shapeTitle ?? ''), 'zh-Hans-CN')
  ));
};

const selectedSceneRelationEdges = computed(() => {
  const imageId = selectedImageNode.value ? assetNumericImageId(selectedImageNode.value) : null;
  if (imageId === null) return { incoming: [] as SceneRelationEdge[], outgoing: [] as SceneRelationEdge[] };
  const edges = buildSceneRelationEdges(assetTree.value);
  return {
    incoming: edges
      .filter((edge) => edge.targetId === imageId)
      .map((edge) => ({ ...edge, focusImageId: edge.sourceId, focusShapeId: edge.shapeId })),
    outgoing: edges
      .filter((edge) => edge.sourceId === imageId)
      .map((edge) => ({ ...edge, focusImageId: edge.targetId, focusShapeId: edge.targetId === imageId ? edge.shapeId : undefined })),
  };
});

const selectedSceneIncomingEdges = computed(() => selectedSceneRelationEdges.value.incoming);
const selectedSceneOutgoingEdges = computed(() => selectedSceneRelationEdges.value.outgoing);
const selectedRecognitionOpsGraphActive = computed(() => (
  assetTreeViewMode.value === 'recognitionOps' && Boolean(selectedRecognitionOpsIssue.value)
));
const selectedSceneRelationCount = computed(() => (
  selectedRecognitionOpsGraphActive.value
    ? (selectedRecognitionOpsIssue.value?.node_ids.length ?? 0) + (selectedRecognitionOpsIssue.value?.edges.length ?? 0)
    : selectedSceneIncomingEdges.value.length + selectedSceneOutgoingEdges.value.length
));
const selectedSceneRelationGraphVisible = computed(() => (
  selectedSceneRelationCount.value > 0
  && (selectedRecognitionOpsGraphActive.value || Boolean(selectedImageNode.value))
));

const sceneGraphNodeId = (imageId: number | null, fallback: string) => (
  imageId === null ? `external:${fallback}` : `scene:${imageId}`
);

const sceneRelationColor = (kind: SceneRelationEdgeKind) => ({
  recognition: '#2563eb',
  jump: '#d97706',
  discriminator: '#7c3aed',
}[kind]);

const sceneRelationGraphKinds = (tab: SceneRelationGraphTab) => (
  tab === 'recognition'
    ? new Set<SceneRelationEdgeKind>(['recognition'])
    : new Set<SceneRelationEdgeKind>(['jump', 'discriminator'])
);

const selectedSceneGraphEmptyText = computed(() => (
  selectedRecognitionOpsGraphActive.value
    ? '无匹配边'
    : activeSceneRelationGraphTab.value === 'recognition'
      ? '无直接识别关系'
      : '无直接跳转关系'
));

const selectedSceneGraphKey = computed(() => (
  selectedRecognitionOpsGraphActive.value
    ? `recognition-ops:${selectedRecognitionOpsIssue.value?.id ?? 'none'}:${selectedImageNode.value?.id ?? 'none'}`
    : `${activeSceneRelationGraphTab.value}:${selectedImageNode.value?.id ?? 'none'}`
));

const sceneRelationGraphEdgeTypes = {
  elk: ElkEdge,
};
const SCENE_GRAPH_NODE_WIDTH = 156;
const SCENE_GRAPH_NODE_HEIGHT = 42;

const buildSelectedSceneGraphRelations = (
  imageId: number | null,
  kinds: Set<SceneRelationEdgeKind>,
) => {
  if (imageId === null) return [];
  const allEdges = buildSceneRelationEdges(assetTree.value)
    .filter((edge) => kinds.has(edge.kind));

  const directEdges = allEdges.filter((edge) => (
    edge.sourceId === imageId || edge.targetId === imageId
  ));
  return directEdges;
};

const selectedSceneGraphRelations = computed(() => {
  const imageId = selectedImageNode.value ? assetNumericImageId(selectedImageNode.value) : null;
  return buildSelectedSceneGraphRelations(imageId, sceneRelationGraphKinds(activeSceneRelationGraphTab.value));
});

const buildFallbackSceneGraphNodes = (baseNodes: Node<SceneGraphNodeData>[]) => {
  const columns = new Map<number, Node<SceneGraphNodeData>[]>();
  for (const node of baseNodes) {
    const depth = Number(node.data?.['depth'] ?? 0);
    const list = columns.get(depth) ?? [];
    list.push(node);
    columns.set(depth, list);
  }
  const maxDepth = Math.max(...[...columns.keys()], 0);
  return [...columns.entries()]
    .sort((left, right) => right[0] - left[0])
    .flatMap(([depth, columnNodes]) => (
      [...columnNodes]
        .sort((left, right) => String(left.data.label).localeCompare(String(right.data.label), 'zh-Hans-CN'))
        .map((node, index) => ({
          ...node,
          position: {
            x: (maxDepth - depth) * 196 + 24,
            y: 88 + (index - (columnNodes.length - 1) / 2) * 62,
          },
        }))
    ));
};

const selectedSceneGraphBaseNodes = computed<Node<SceneGraphNodeData>[]>(() => {
  if (selectedRecognitionOpsGraphActive.value && selectedRecognitionOpsIssue.value) {
    const issue = selectedRecognitionOpsIssue.value;
    const nodeIds = Array.from(new Set([
      ...issue.node_ids.map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0),
      ...issue.edges.flatMap((edge) => [Number(edge.source_id), Number(edge.target_id)])
        .filter((id) => Number.isFinite(id) && id > 0),
    ]));
    const selectedImageId = selectedImageNode.value ? assetNumericImageId(selectedImageNode.value) : null;
    const centerImageId = selectedImageId !== null && nodeIds.includes(selectedImageId)
      ? selectedImageId
      : nodeIds[0] ?? null;
    return nodeIds.map((imageId, index) => {
      const isCenter = imageId === centerImageId;
      return {
        id: sceneGraphNodeId(imageId, String(imageId)),
        position: { x: 0, y: 0 },
        data: {
          label: sceneImageLabel(imageId, assetImagesByNumber.value, `#${imageId}`),
          imageId,
          depth: isCenter ? 0 : index + 1,
          issueId: issue.id,
        },
        style: {
          width: `${SCENE_GRAPH_NODE_WIDTH}px`,
          minHeight: `${SCENE_GRAPH_NODE_HEIGHT}px`,
          padding: '7px 10px',
          color: isCenter ? '#111827' : '#303133',
          fontSize: '12px',
          lineHeight: '16px',
          border: isCenter ? '2px solid #409eff' : '1px solid #dcdfe6',
          borderRadius: '5px',
          background: isCenter ? '#ecf5ff' : '#fff',
          boxShadow: isCenter ? '0 2px 7px rgba(64, 158, 255, 0.16)' : '0 1px 4px rgba(31, 41, 55, 0.12)',
          textAlign: 'center',
          whiteSpace: 'normal',
        },
      };
    });
  }

  const imageId = selectedImageNode.value ? assetNumericImageId(selectedImageNode.value) : null;
  if (imageId === null) return [];

  type GraphNodeRecord = {
    id: string;
    imageId: number | null;
    label: string;
    depth: number;
  };
  const records = new Map<string, GraphNodeRecord>();
  const ensureNode = (id: string, label: string, nodeImageId: number | null, depth: number) => {
    const current = records.get(id);
    if (current) {
      if (current.depth === 0 || depth === 0) {
        current.depth = 0;
      } else if (current.depth > 0 && depth > 0) {
        current.depth = Math.min(current.depth, depth);
      } else if (current.depth < 0 && depth < 0) {
        current.depth = Math.max(current.depth, depth);
      } else if (depth > 0) {
        current.depth = depth;
      }
      return current;
    }
    const record: GraphNodeRecord = {
      id,
      imageId: nodeImageId,
      label,
      depth,
    };
    records.set(id, record);
    return record;
  };

  const incomingByTarget = new Map<number, SceneRelationEdge[]>();
  for (const edge of selectedSceneGraphRelations.value) {
    if (edge.targetId === null) continue;
    const list = incomingByTarget.get(edge.targetId) ?? [];
    list.push(edge);
    incomingByTarget.set(edge.targetId, list);
  }
  const depthByImageId = new Map<number, number>([[imageId, 0]]);
  const queue = [imageId];
  while (queue.length) {
    const targetId = queue.shift();
    if (targetId === undefined) continue;
    const nextDepth = (depthByImageId.get(targetId) ?? 0) + 1;
    for (const edge of incomingByTarget.get(targetId) ?? []) {
      if (edge.sourceId === null) continue;
      const currentDepth = depthByImageId.get(edge.sourceId);
      if (currentDepth !== undefined && currentDepth <= nextDepth) continue;
      depthByImageId.set(edge.sourceId, nextDepth);
      queue.push(edge.sourceId);
    }
  }

  const imagesByNumber = new Map<number, DataAnnotationAssetNode>();
  for (const image of flattenAssetImages(assetTree.value)) {
    const id = assetNumericImageId(image);
    if (id !== null) imagesByNumber.set(id, image);
  }
  const centerId = sceneGraphNodeId(imageId, String(imageId));
  ensureNode(centerId, sceneImageLabel(imageId, imagesByNumber, `#${imageId}`), imageId, 0);

  for (const edge of selectedSceneGraphRelations.value) {
    const sourceId = sceneGraphNodeId(edge.sourceId, edge.sourceLabel);
    const targetId = sceneGraphNodeId(edge.targetId, edge.targetLabel);
    const sourceDepth = edge.sourceId === null
      ? 1
      : edge.sourceId === imageId
        ? 0
        : depthByImageId.get(edge.sourceId) ?? -1;
    const targetDepth = edge.targetId === null
      ? -1
      : edge.targetId === imageId
        ? 0
        : depthByImageId.get(edge.targetId) ?? -1;
    ensureNode(sourceId, edge.sourceLabel, edge.sourceId, sourceDepth);
    ensureNode(targetId, edge.targetLabel, edge.targetId, targetDepth);
  }

  const columns = new Map<number, GraphNodeRecord[]>();
  for (const record of records.values()) {
    const list = columns.get(record.depth) ?? [];
    list.push(record);
    columns.set(record.depth, list);
  }
  const maxDepth = Math.max(...[...columns.keys()], 0);

  const nodes: Node<SceneGraphNodeData>[] = [];
  const pushNode = (record: GraphNodeRecord, variant: string) => {
    nodes.push({
      id: record.id,
      position: { x: 0, y: 0 },
      data: {
        label: record.label,
        imageId: record.imageId,
        depth: record.depth,
      },
      style: {
        width: `${SCENE_GRAPH_NODE_WIDTH}px`,
        minHeight: `${SCENE_GRAPH_NODE_HEIGHT}px`,
        padding: '7px 10px',
        color: variant === 'center' ? '#111827' : '#303133',
        fontSize: '12px',
        lineHeight: '16px',
        border: variant === 'center' ? '2px solid #409eff' : '1px solid #dcdfe6',
        borderRadius: '5px',
        background: variant === 'center' ? '#ecf5ff' : '#fff',
        boxShadow: variant === 'center' ? '0 2px 7px rgba(64, 158, 255, 0.16)' : '0 1px 4px rgba(31, 41, 55, 0.12)',
        textAlign: 'center',
        whiteSpace: 'normal',
      },
    });
  };
  for (const [depth, columnRecords] of [...columns.entries()].sort((left, right) => right[0] - left[0])) {
    const orderedRecords = [...columnRecords].sort((left, right) => (
      String(left.label).localeCompare(String(right.label), 'zh-Hans-CN')
    ));
    orderedRecords.forEach((record) => {
      pushNode(
        record,
        depth === 0 ? 'center' : 'ancestor',
      );
    });
  }
  return nodes;
});

const selectedSceneGraphBaseEdges = computed<Edge<SceneGraphEdgeData>[]>(() => {
  if (selectedRecognitionOpsGraphActive.value && selectedRecognitionOpsIssue.value) {
    const issue = selectedRecognitionOpsIssue.value;
    return issue.edges.map((edge, index) => {
      const sourceId = Number(edge.source_id);
      const targetId = Number(edge.target_id);
      const sourceLabel = sceneImageLabel(sourceId, assetImagesByNumber.value, `#${sourceId}`);
      const targetLabel = sceneImageLabel(targetId, assetImagesByNumber.value, `#${targetId}`);
      const color = sceneRelationColor('recognition');
      const score = edge.score === undefined || edge.score === null ? '' : ` score ${recognitionOpsEdgeValue(edge.score)}`;
      const threshold = edge.threshold === undefined || edge.threshold === null ? '' : ` threshold ${recognitionOpsEdgeValue(edge.threshold)}`;
      const matched = edge.matched === false ? ' unmatched' : ' matched';
      const relationEdge: SceneRelationEdge = {
        id: `recognition-ops:${issue.id}:${sourceId}-${targetId}:${index}`,
        kind: 'recognition',
        kindLabel: '识别',
        sourceId,
        targetId,
        sourceLabel,
        targetLabel,
        score: edge.score,
        focusImageId: targetId,
        tooltip: `${sourceLabel} -> ${targetLabel}${score}${threshold}${matched}`,
      };
      const label = recognitionScorePercentLabel(edge.score);
      return {
        id: `scene-graph:${relationEdge.id}`,
        source: sceneGraphNodeId(sourceId, String(sourceId)),
        target: sceneGraphNodeId(targetId, String(targetId)),
        type: 'elk',
        label,
        animated: true,
        markerEnd: {
          type: SCENE_GRAPH_ARROW_MARKER,
          color,
          width: 16,
          height: 16,
        },
        style: {
          stroke: color,
          strokeWidth: 2,
        },
        data: {
          relationEdge,
        },
      };
    });
  }

  return selectedSceneGraphRelations.value.map((edge, index) => {
  const color = sceneRelationColor(edge.kind);
  const label = edge.kind === 'recognition' ? recognitionScorePercentLabel(edge.score) : '';
  return {
    id: `scene-graph:${edge.id}:${index}`,
    source: sceneGraphNodeId(edge.sourceId, edge.sourceLabel),
    target: sceneGraphNodeId(edge.targetId, edge.targetLabel),
    type: 'elk',
    label,
    animated: edge.kind === 'recognition',
    markerEnd: {
      type: SCENE_GRAPH_ARROW_MARKER,
      color,
      width: 16,
      height: 16,
    },
    style: {
      stroke: color,
      strokeWidth: edge.kind === 'recognition' ? 2 : 1.5,
    },
    data: {
      relationEdge: edge,
    },
  };
  });
});

const selectedSceneGraphNodes = ref<Node<SceneGraphNodeData>[]>([]);
const selectedSceneGraphEdges = ref<Edge<SceneGraphEdgeData>[]>([]);

const layoutSelectedSceneGraph = async () => {
  const baseNodes = selectedSceneGraphBaseNodes.value;
  const baseEdges = selectedSceneGraphBaseEdges.value;
  if (!baseNodes.length) {
    selectedSceneGraphNodes.value = [];
    selectedSceneGraphEdges.value = [];
    return;
  }

  const fallbackNodes = buildFallbackSceneGraphNodes(baseNodes);
  selectedSceneGraphNodes.value = fallbackNodes;
  selectedSceneGraphEdges.value = baseEdges;
  if (!baseEdges.length) return;

  try {
    const sceneRelationGraphElk = await getSceneRelationGraphElk();
    const graph = await sceneRelationGraphElk.layout({
      id: 'scene-relation-graph',
      layoutOptions: {
        'elk.algorithm': 'layered',
        'elk.direction': 'RIGHT',
        'elk.edgeRouting': 'ORTHOGONAL',
        'elk.layered.spacing.nodeNodeBetweenLayers': '72',
        'elk.spacing.nodeNode': '36',
        'elk.layered.spacing.edgeNodeBetweenLayers': '28',
        'elk.layered.spacing.edgeEdgeBetweenLayers': '16',
        'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
        'elk.layered.cycleBreaking.strategy': 'GREEDY',
        'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
      },
      children: baseNodes.map((node) => ({
        id: node.id,
        width: SCENE_GRAPH_NODE_WIDTH,
        height: SCENE_GRAPH_NODE_HEIGHT,
      })),
      edges: baseEdges.map((edge) => ({
        id: edge.id,
        sources: [edge.source],
        targets: [edge.target],
      })),
    });
    const positionById = new Map((graph.children ?? []).map((node: any) => [
      node.id,
      { x: Number(node.x ?? 0), y: Number(node.y ?? 0) },
    ]));
    const sectionsByEdgeId = new Map((graph.edges ?? []).map((edge: any) => [
      edge.id,
      Array.isArray(edge.sections) ? edge.sections : [],
    ]));
    selectedSceneGraphNodes.value = baseNodes.map((node) => ({
      ...node,
      position: positionById.get(node.id) ?? node.position,
    }));
    selectedSceneGraphEdges.value = baseEdges.map((edge) => ({
      ...edge,
      data: {
        ...(edge.data ?? {}),
        elkSections: sectionsByEdgeId.get(edge.id) ?? [],
      },
    }));
  } catch {
    selectedSceneGraphNodes.value = fallbackNodes;
    selectedSceneGraphEdges.value = baseEdges;
  }
};

const handleSceneGraphNodeClick = async ({ node }: { node: Node }) => {
  const imageId = Number(node.data?.imageId);
  if (!Number.isFinite(imageId) || imageId <= 0) return;
  const image = findAssetImageByNumericId(assetTree.value, imageId);
  if (!image) return;
  await focusAssetImage(image);
};

const handleSceneGraphEdgeClick = ({ edge }: { edge: Edge }) => {
  const relationEdge = edge.data?.relationEdge as SceneRelationEdge | undefined;
  if (relationEdge) void selectSceneRelationEdge(relationEdge);
};

const collectShapeAncestorIds = (
  shapes: DataAnnotationShape[],
  id: string,
  ancestors: string[] = [],
): string[] => {
  for (const shape of shapes) {
    if (shape.id === id) return ancestors;
    const found = collectShapeAncestorIds(shape.children ?? [], id, [...ancestors, shape.id]);
    if (found.length || (shape.children ?? []).some((child) => child.id === id)) return found;
  }
  return [];
};

const selectSceneRelationEdge = async (edge: SceneRelationEdge) => {
  const focusImage = findAssetImageByNumericId(assetTree.value, edge.focusImageId);
  if (focusImage) {
    selectedAssetId.value = focusImage.id;
    await nextTick();
    assetTreeRef.value?.setCurrentKey?.(focusImage.id);
    scrollCurrentTreeNodeIntoView('asset-tree');
  }
  if (edge.focusShapeId && focusImage && findShapeById(focusImage.shapes ?? [], edge.focusShapeId)) {
    selectedShapeId.value = edge.focusShapeId;
    expandedShapeNodeIds.value = Array.from(new Set([
      ...expandedShapeNodeIds.value,
      ...collectShapeAncestorIds(focusImage.shapes ?? [], edge.focusShapeId),
    ]));
    await nextTick();
    shapeTreeRef.value?.setCurrentKey?.(edge.focusShapeId);
    scrollCurrentTreeNodeIntoView('shape-tree');
  }
};

const assetTreeNodeClasses = (node: DataAnnotationAssetNode) => {
  return {
    'is-image': node.type === 'image',
    'is-virtual': isVirtualAssetTreeNode(node),
  };
};

const assetTreeNodeTitle = (node: DataAnnotationAssetNode) => {
  if (node.type !== 'image') return '';
  const layerTitle = frameLayerTitle(inferredFrameLayer(node));
  return layerTitle;
};

const findAssetAncestorFolderIds = (
  nodes: DataAnnotationAssetNode[],
  id: string | null,
  ancestors: string[] = [],
): string[] | null => {
  if (!id) return null;
  for (const node of nodes) {
    if (node.id === id) return ancestors;
    const nextAncestors = node.type === 'folder' ? [...ancestors, node.id] : ancestors;
    const found = findAssetAncestorFolderIds(node.children ?? [], id, nextAncestors);
    if (found) return found;
  }
  return null;
};

const findDisplayAssetAncestorIds = (
  nodes: DataAnnotationAssetNode[],
  id: string | null,
  ancestors: string[] = [],
): string[] | null => {
  if (!id) return null;
  for (const node of nodes) {
    if (node.id === id) return ancestors;
    const found = findDisplayAssetAncestorIds(node.children ?? [], id, [...ancestors, node.id]);
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

const findImageNodeByShapeId = (nodes: DataAnnotationAssetNode[], shapeId: string): DataAnnotationAssetNode | null => {
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
  nodes: DataAnnotationAssetNode[],
  id: string | null,
): DataAnnotationAssetNode[] | null => {
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
const selectedSceneParentIds = computed({
  get: () => selectedImageNode.value?.parentSceneIds ?? '',
  set: (value: string) => {
    const image = selectedImageNode.value;
    if (!image) return;
    image.parentSceneIds = value;
  },
});
const normalizeSelectedSceneParentIds = () => {
  const image = selectedImageNode.value;
  if (!image) return;
  const normalized = normalizeSceneParentIdsText(image.parentSceneIds, assetNumericImageId(image));
  if (normalized) {
    image.parentSceneIds = normalized;
  } else {
    delete image.parentSceneIds;
  }
};
watch(
  [selectedSceneGraphBaseNodes, selectedSceneGraphBaseEdges],
  () => {
    void layoutSelectedSceneGraph();
  },
  { immediate: true },
);
const selectedImageTitleText = computed(() => (
  selectedImageNode.value
    ? [assetImageIdMark(selectedImageNode.value), selectedImageNode.value.title.trim()].filter(Boolean).join(' ')
    : '未选择图片'
));
const selectedImageUsesJpegFrame = computed(() => {
  const filename = selectedImageNode.value?.filename?.trim().toLowerCase() || '';
  return filename.endsWith('.jpg') || filename.endsWith('.jpeg');
});
const assetImagePreviewKey = (image: DataAnnotationAssetNode, entryId = selectedEntryId.value) => (
  `${entryId}\u0000${image.id}\u0000${image.filename || ''}`
);
const selectedImagePreviewUrl = computed(() => {
  const image = selectedImageNode.value;
  if (!image) return '';
  const key = assetImagePreviewKey(image);
  if (assetImagePreviewMissingIds.value[key]) return '';
  return assetImagePreviewUrls.value[key] || image.imageDataUrl || '';
});
const selectedImagePreviewLoading = computed(() => {
  const image = selectedImageNode.value;
  return Boolean(image && image.filename && !selectedImagePreviewUrl.value && assetImagePreviewLoadingIds.value[assetImagePreviewKey(image)]);
});
const selectedImagePreviewMissing = computed(() => {
  const image = selectedImageNode.value;
  return Boolean(image && image.filename && !selectedImagePreviewUrl.value && assetImagePreviewMissingIds.value[assetImagePreviewKey(image)]);
});
const selectedImagePlaceholderText = computed(() => {
  const image = selectedImageNode.value;
  if (!image) return '空图';
  if (selectedImagePreviewLoading.value) return '加载中';
  if (selectedImagePreviewMissing.value) return '图片加载失败，点击重试';
  return image.filename ? '等待加载' : '空图';
});
const selectedImageShapes = computed(() => selectedImageNode.value?.shapes ?? []);
const isDrawableShape = (shape: DataAnnotationShape) => shape.kind !== 'group';
const flattenShapes = (shapes: DataAnnotationShape[]): DataAnnotationShape[] => shapes.flatMap((shape) => [
  shape,
  ...flattenShapes(shape.children ?? []),
]);
const selectedImageOcrCandidates = ref<FanxiuDataAnnotationOcrFrameToken[]>([]);
const selectedImageOcrCandidateImageId = ref('');
const imageOcrCandidateCache = new Map<string, Promise<FanxiuDataAnnotationOcrFrameToken[]>>();
let selectedImageOcrProbeSeq = 0;

const shapeHasOcrRule = (shape: DataAnnotationShape) => (
  normalizeShapeMatchRole(shape.ocrMatchRole, shape.ocrEnabled ? 'required' : 'off') !== 'off'
);

const ocrCandidateOverlapsShape = (
  shape: DataAnnotationShape,
  candidate: FanxiuDataAnnotationOcrFrameToken,
  imageWidth: number,
  imageHeight: number,
) => {
  const shapeLeft = shape.x * imageWidth;
  const shapeTop = shape.y * imageHeight;
  const shapeRight = (shape.x + shape.w) * imageWidth;
  const shapeBottom = (shape.y + shape.h) * imageHeight;
  const candidateRight = candidate.x + candidate.w;
  const candidateBottom = candidate.y + candidate.h;
  const intersectionWidth = Math.max(0, Math.min(shapeRight, candidateRight) - Math.max(shapeLeft, candidate.x));
  const intersectionHeight = Math.max(0, Math.min(shapeBottom, candidateBottom) - Math.max(shapeTop, candidate.y));
  const intersectionArea = intersectionWidth * intersectionHeight;
  const candidateArea = Math.max(1, candidate.w * candidate.h);
  const candidateCenterX = candidate.x + candidate.w / 2;
  const candidateCenterY = candidate.y + candidate.h / 2;
  return (
    candidateCenterX >= shapeLeft
    && candidateCenterX <= shapeRight
    && candidateCenterY >= shapeTop
    && candidateCenterY <= shapeBottom
  ) || intersectionArea / candidateArea >= 0.35;
};

const shapeOcrSuggestionTexts = (shape: DataAnnotationShape) => {
  if (!isDrawableShape(shape) || shapeHasOcrRule(shape)) return [];
  if (selectedImageOcrCandidateImageId.value !== selectedImageNode.value?.id) return [];
  const imageWidth = selectedImageNode.value?.width || naturalWidth.value || 0;
  const imageHeight = selectedImageNode.value?.height || naturalHeight.value || 0;
  if (!imageWidth || !imageHeight) return [];
  return Array.from(new Set(
    selectedImageOcrCandidates.value
      .filter((candidate) => candidate.text.trim() && ocrCandidateOverlapsShape(shape, candidate, imageWidth, imageHeight))
      .map((candidate) => candidate.text.trim()),
  ));
};

const isShapeOcrSuggested = (shape: DataAnnotationShape) => shapeOcrSuggestionTexts(shape).length > 0;
const shapeOcrSuggestionTitle = (shape: DataAnnotationShape) => {
  const texts = shapeOcrSuggestionTexts(shape);
  return texts.length ? `OCR 可识别：${texts.join(' / ')}` : '';
};
const selectedImageNeedsOcrSuggestions = computed(() => (
  flattenShapes(selectedImageShapes.value).some((shape) => isDrawableShape(shape) && !shapeHasOcrRule(shape))
));
const isOcclusionAssetGroup = (node: DataAnnotationAssetNode) => (
  node.type === 'folder' && node.title.trim() === OCCLUSION_ASSET_GROUP_TITLE
);
const collectOcclusionAssetImages = (
  nodes: DataAnnotationAssetNode[],
  inOcclusionGroup = false,
): DataAnnotationAssetNode[] => {
  const images: DataAnnotationAssetNode[] = [];
  for (const node of nodes) {
    const nextInOcclusionGroup = inOcclusionGroup || isOcclusionAssetGroup(node);
    if (node.type === 'image' && nextInOcclusionGroup) images.push(node);
    images.push(...collectOcclusionAssetImages(node.children ?? [], nextInOcclusionGroup));
  }
  return images;
};
const findShapeById = (shapes: DataAnnotationShape[], id: string | null): DataAnnotationShape | null => {
  if (!id) return null;
  for (const shape of shapes) {
    if (shape.id === id) return shape;
    const found = findShapeById(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const isLayerTreeRootId = (id: string) => (LAYER_TREE_ROOT_IDS as readonly string[]).includes(id);

const filterExistingAssetNodeIds = (ids: string[]) => ids.filter((id) => (
  isLayerTreeRootId(id) || Boolean(findAssetNode(assetTree.value, id))
));
const filterExistingShapeNodeIds = (ids: string[]) => {
  const image = selectedImageNode.value;
  if (!image) return [];
  return ids.filter((id) => Boolean(findShapeById(image.shapes ?? [], id)));
};

const collectAssetFolderIds = (nodes: DataAnnotationAssetNode[]): string[] => nodes.flatMap((node) => [
  ...(node.type === 'folder' ? [node.id] : []),
  ...collectAssetFolderIds(node.children ?? []),
]);

const collectDisplayedAssetFolderIds = () => (
  assetTreeViewMode.value === 'scene'
    ? [...LAYER_TREE_ROOT_IDS, ...collectAssetFolderIds(assetTree.value)]
    : collectAssetFolderIds(assetTree.value)
);

const hasExpandedAssetTreeNodes = computed(() => {
  const expandedIds = new Set(expandedAssetNodeIds.value);
  return collectDisplayedAssetFolderIds().some((id) => expandedIds.has(id));
});

const collapseAssetTree = async () => {
  expandedAssetNodeIds.value = [];
  await syncAssetTreeExpansionFromState();
};

const syncAssetTreeExpansionFromState = async () => {
  await nextTick();
  const tree = assetTreeRef.value;
  if (!tree) return;
  const expandedIds = new Set(expandedAssetNodeIds.value);
  for (const id of collectDisplayedAssetFolderIds()) {
    const treeNode = tree.getNode(id);
    if (!treeNode) continue;
    const shouldExpand = expandedIds.has(id);
    if (shouldExpand && !treeNode.expanded) {
      treeNode.expand?.();
    } else if (!shouldExpand && treeNode.expanded) {
      treeNode.collapse?.();
    }
  }
};

const collectExpandableShapeIds = (shapes: DataAnnotationShape[]): string[] => shapes.flatMap((shape) => [
  ...((shape.children ?? []).length ? [shape.id] : []),
  ...collectExpandableShapeIds(shape.children ?? []),
]);

const syncShapeTreeExpansionFromState = async () => {
  await nextTick();
  const tree = shapeTreeRef.value;
  if (!tree) return;
  const expandedIds = new Set(expandedShapeNodeIds.value);
  for (const id of collectExpandableShapeIds(selectedImageShapes.value)) {
    const treeNode = tree.getNode(id);
    if (!treeNode) continue;
    const shouldExpand = expandedIds.has(id);
    if (shouldExpand && !treeNode.expanded) {
      treeNode.expand?.();
    } else if (!shouldExpand && treeNode.expanded) {
      treeNode.collapse?.();
    }
  }
};

const restoreDataAnnotationUiState = () => {
  const state = loadDataAnnotationUiState();
  assetTreeViewMode.value = (
    state.assetTreeViewMode === 'scene' || state.assetTreeViewMode === 'recognitionOps'
      ? state.assetTreeViewMode
      : 'business'
  );
  const savedAssetId = typeof state.selectedAssetId === 'string' ? state.selectedAssetId : null;
  const savedAsset = savedAssetId ? findAssetNode(assetTree.value, savedAssetId) : null;
  selectedAssetId.value = savedAsset?.id ?? findFirstImageNode(assetTree.value)?.id ?? null;
  const restoredExpandedAssetNodeIds = Array.isArray(state.expandedAssetNodeIds)
    ? filterExistingAssetNodeIds(normalizeStringIdArray(state.expandedAssetNodeIds))
    : collectAssetFolderIds(assetTree.value);
  expandedAssetNodeIds.value = assetTreeViewMode.value === 'scene'
    ? Array.from(new Set([...restoredExpandedAssetNodeIds, ...collectSceneExpandedNodeIds(selectedAssetId.value)]))
    : restoredExpandedAssetNodeIds;

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

let assetTreeExpansionSyncQueued = false;
const queueAssetTreeExpansionSync = () => {
  if (assetTreeExpansionSyncQueued) return;
  assetTreeExpansionSyncQueued = true;
  void nextTick(async () => {
    assetTreeExpansionSyncQueued = false;
    await syncAssetTreeExpansionFromState();
  });
};

let shapeTreeExpansionSyncQueued = false;
const queueShapeTreeExpansionSync = () => {
  if (shapeTreeExpansionSyncQueued) return;
  shapeTreeExpansionSyncQueued = true;
  void nextTick(async () => {
    shapeTreeExpansionSyncQueued = false;
    await syncShapeTreeExpansionFromState();
  });
};

const scrollCurrentTreeNodeIntoView = (treeClass: string) => {
  document
    .querySelector(`.${treeClass} .el-tree-node.is-current`)
    ?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
};

const focusAssetImage = async (
  image: DataAnnotationAssetNode,
  options: { collapseOtherFolders?: boolean } = {},
) => {
  const expandedIds = assetTreeViewMode.value === 'scene'
    ? (
        options.collapseOtherFolders
          ? (findDisplayAssetAncestorIds(assetTreeDisplayData.value, image.id) ?? [])
          : collectSceneExpandedNodeIds(image.id)
      )
    : (findAssetAncestorFolderIds(assetTree.value, image.id) ?? []);
  expandedAssetNodeIds.value = options.collapseOtherFolders
    ? expandedIds
    : Array.from(new Set([...expandedAssetNodeIds.value, ...expandedIds]));
  selectedAssetId.value = image.id;
  assetTreeRef.value?.setCurrentKey?.(image.id);
  await syncAssetTreeExpansionFromState();
  await nextTick();
  scrollCurrentTreeNodeIntoView('asset-tree');

  expandedShapeNodeIds.value = collectExpandableShapeIds(image.shapes ?? []);
  selectedShapeId.value = flattenShapes(image.shapes ?? [])[0]?.id ?? null;
  selectedShapeIds.value = [];
  shapeSelectionAnchorId.value = selectedShapeId.value;
  await syncShapeTreeExpansionFromState();
  await nextTick();
  scrollCurrentTreeNodeIntoView('shape-tree');
};

const alignAssetTreeToCurrentScene = async () => {
  if (!selectedEntryId.value || alignCurrentSceneLoading.value) return;
  alignCurrentSceneLoading.value = true;
  try {
    const status = await getFanxiuInfoWindowStatus(selectedEntryId.value);
    const sceneId = Number(status.scene?.scene_id);
    if (!Number.isInteger(sceneId) || sceneId <= 0) {
      ElMessage.warning('凡修信息窗尚未识别到当前场景');
      return;
    }
    const image = findAssetImageByNumericId(assetTree.value, sceneId);
    if (!image) {
      ElMessage.warning(`资产树中未找到场景 #${sceneId}`);
      return;
    }
    assetFrameSearchText.value = '';
    await nextTick();
    await focusAssetImage(image, { collapseOtherFolders: true });
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    alignCurrentSceneLoading.value = false;
  }
};

const normalizeAssetSearchText = (value: string) => value.trim().replace(/^#/, '').toLowerCase();

const shapeMatchesAssetSearch = (shape: DataAnnotationShape, needle: string): boolean => {
  const title = shape.title.trim().toLowerCase();
  if (title.includes(needle)) return true;
  return (shape.children ?? []).some((child) => shapeMatchesAssetSearch(child, needle));
};

const findFirstShapeByAssetSearch = (shapes: DataAnnotationShape[], needle: string): DataAnnotationShape | null => {
  for (const shape of shapes) {
    if (shape.title.trim().toLowerCase().includes(needle)) return shape;
    const found = findFirstShapeByAssetSearch(shape.children ?? [], needle);
    if (found) return found;
  }
  return null;
};

const assetNodeMatchesSearchText = (node: DataAnnotationAssetNode, needle: string) => {
  const texts = [
    node.title,
    node.filename,
    node.type === 'image' ? assetImageIdMark(node) : '',
    node.type === 'image' ? String(assetNumericImageId(node) ?? '') : '',
  ];
  return texts.some((text) => String(text || '').trim().toLowerCase().replace(/^#/, '').includes(needle));
};

const filterAssetTreeNode = (value: string, data: DataAnnotationAssetNode) => {
  const needle = normalizeAssetSearchText(value);
  if (!needle) return true;
  if (assetNodeMatchesSearchText(data, needle)) return true;
  return data.type === 'image' && (data.shapes ?? []).some((shape) => shapeMatchesAssetSearch(shape, needle));
};

const findFirstAssetSearchMatch = (
  nodes: DataAnnotationAssetNode[],
  needle: string,
): { image: DataAnnotationAssetNode; shape: DataAnnotationShape | null } | null => {
  for (const node of nodes) {
    if (node.type === 'image') {
      if (assetNodeMatchesSearchText(node, needle)) return { image: node, shape: null };
      const shape = findFirstShapeByAssetSearch(node.shapes ?? [], needle);
      if (shape) return { image: node, shape };
    }
    const found = findFirstAssetSearchMatch(node.children ?? [], needle);
    if (found) return found;
  }
  return null;
};

const expandAssetSearchMatches = (value: string) => {
  const needle = normalizeAssetSearchText(value);
  if (!needle) return;
  const matchedImageIds = flattenAssetImages(assetTree.value)
    .filter((image) => assetNodeMatchesSearchText(image, needle) || (image.shapes ?? []).some((shape) => shapeMatchesAssetSearch(shape, needle)))
    .map((image) => image.id);
  const ancestorIds = matchedImageIds.flatMap((id) => findAssetAncestorFolderIds(assetTree.value, id) ?? []);
  expandedAssetNodeIds.value = Array.from(new Set([...expandedAssetNodeIds.value, ...ancestorIds]));
  void syncAssetTreeExpansionFromState();
};

const searchAssetFrame = async () => {
  const needle = normalizeAssetSearchText(assetFrameSearchText.value);
  if (!needle) {
    ElMessage.warning('请输入编号或名称');
    return;
  }
  const numericId = Number(needle);
  if (!Number.isInteger(numericId) || numericId < 0) {
    const match = findFirstAssetSearchMatch(assetTree.value, needle);
    if (!match) {
      ElMessage.warning(`未找到「${assetFrameSearchText.value.trim()}」`);
      return;
    }
    await focusAssetImage(match.image);
    if (match.shape) selectedShapeId.value = match.shape.id;
    return;
  }

  const image = findAssetImageByNumericId(assetTree.value, numericId);
  if (image) {
    await focusAssetImage(image);
    return;
  }
  const match = findFirstAssetSearchMatch(assetTree.value, needle);
  if (match) {
    await focusAssetImage(match.image);
    if (match.shape) selectedShapeId.value = match.shape.id;
    return;
  }
  ElMessage.warning(`未找到 #${numericId}`);
};

const findAssetImageByTitle = (nodes: DataAnnotationAssetNode[], title: string): DataAnnotationAssetNode | null => {
  const needle = title.trim();
  if (!needle) return null;
  for (const node of nodes) {
    if (node.type === 'image' && node.title.trim() === needle) return node;
    const found = findAssetImageByTitle(node.children ?? [], needle);
    if (found) return found;
  }
  return null;
};

const focusImageFromRoute = async () => {
  const title = getQueryStringValue('focus_image_title');
  if (!title) return;
  const image = findAssetImageByTitle(assetTree.value, title);
  if (!image) {
    ElMessage.warning(`未找到「${title}」`);
    return;
  }
  await focusAssetImage(image);
};

const findShapeParentChildren = (shapes: DataAnnotationShape[], id: string | null): DataAnnotationShape[] | null => {
  if (!id) return null;
  if (shapes.some((shape) => shape.id === id)) return shapes;
  for (const shape of shapes) {
    const found = findShapeParentChildren(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const findShapeParentShape = (shapes: DataAnnotationShape[], id: string | null): DataAnnotationShape | null => {
  if (!id) return null;
  for (const shape of shapes) {
    if ((shape.children ?? []).some((child) => child.id === id)) return shape;
    const found = findShapeParentShape(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};

const isShapeDescendantOf = (shape: DataAnnotationShape, ancestorId: string): boolean => (
  (shape.children ?? []).some((child) => child.id === ancestorId || isShapeDescendantOf(child, ancestorId))
);

const cloneShapeTreeWithNewIds = (shape: DataAnnotationShape): DataAnnotationShape => ({
  ...JSON.parse(JSON.stringify(shape)) as DataAnnotationShape,
  id: createAssetId('shape'),
  children: (shape.children ?? []).map(cloneShapeTreeWithNewIds),
});
const annotationShapes = computed(() => flattenShapes(selectedImageShapes.value).filter(isDrawableShape));
const isShapeLocked = (shape: DataAnnotationShape | null | undefined) => Boolean(shape?.locked);
const editableAnnotationShapes = computed(() => annotationShapes.value.filter((shape) => !isShapeLocked(shape)));
const occlusionMaskShapes = computed(() => (
  collectOcclusionAssetImages(assetTree.value)
    .flatMap((image) => flattenShapes(image.shapes ?? []))
    .filter(isDrawableShape)
));
const occlusionOverlayShapes = computed(() => (
  globalOcclusionMaskEnabled.value ? occlusionMaskShapes.value : []
));
const selectedShape = computed(() => findShapeById(selectedImageShapes.value, selectedShapeId.value));
const selectedShapeLoadMode = computed<NonNullable<DataAnnotationShape['loadMode']>>({
  get: () => normalizeShapeLoadMode(selectedShape.value?.loadMode),
  set: (value) => {
    if (!selectedShape.value) return;
    if (value === 'paged') selectedShape.value.loadMode = 'paged';
    else delete selectedShape.value.loadMode;
  },
});
const selectedShapeLoadBoundary = computed<NonNullable<DataAnnotationShape['loadBoundary']>>({
  get: () => normalizeShapeLoadBoundary(selectedShape.value?.loadBoundary),
  set: (value) => {
    if (!selectedShape.value) return;
    if (value === 'cyclic') selectedShape.value.loadBoundary = 'cyclic';
    else delete selectedShape.value.loadBoundary;
  },
});
const selectedShapeLoadInitialPosition = computed<NonNullable<DataAnnotationShape['loadInitialPosition']>>({
  get: () => normalizeShapeLoadInitialPosition(selectedShape.value?.loadInitialPosition),
  set: (value) => {
    if (!selectedShape.value) return;
    if (value === 'unknown') selectedShape.value.loadInitialPosition = 'unknown';
    else delete selectedShape.value.loadInitialPosition;
  },
});
const selectedShapeCopyCount = computed(() => {
  if (selectedShapeIds.value.length) return selectedShapeIds.value.length;
  return selectedShape.value ? 1 : 0;
});
const selectedShapeDetectResult = computed(() => (
  selectedShapeId.value ? shapeDetectResults.value[selectedShapeId.value] || '' : ''
));
const selectedShapeDetectDebug = computed(() => (
  selectedShapeId.value ? shapeDetectDebugByShapeId.value[selectedShapeId.value] || null : null
));
const selectedShapeImageMatchRole = computed(() => normalizeShapeMatchRole(selectedShape.value?.imageMatchRole));
const selectedShapeOcrMatchRole = computed(() => normalizeShapeMatchRole(selectedShape.value?.ocrMatchRole, selectedShape.value?.ocrEnabled ? 'required' : 'off'));
const selectedShapeShowsOcrMaskControls = computed(() => {
  const shape = selectedShape.value;
  if (!shape || !isDrawableShape(shape)) return false;
  return selectedShapeOcrMatchRole.value !== 'off'
    || Boolean(shape.ocrText?.trim())
    || normalizeShapeOcrMaskMode(shape.ocrMaskMode) !== 'inherit-envelope'
    || Boolean(shape.ocrMask?.dataUrl);
});
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
  const currentRole = normalizeShapeMatchRole(shape.sceneIdentityRole, shape.isSceneIdentity ? 'required' : 'off');
  const nextRole = nextShapeMatchRole(currentRole);
  if (nextRole !== 'off' && !shapePrimaryMatchKind(shape)) {
    ElMessage.warning('请先选择图像或 OCR 识别方式，再标记场景');
    return;
  }
  shape.sceneIdentityRole = nextRole;
  shape.isSceneIdentity = nextRole !== 'off';
};
const canDetectSelectedShape = computed(() => Boolean(
  selectedEntryId.value
  && selectedImageNode.value?.filename
  && selectedShape.value
  && isDrawableShape(selectedShape.value)
  && shapePrimaryMatchKind(selectedShape.value)
  && (!shapeDetectingId.value || shapeDetectingId.value === selectedShape.value.id)
));
const shapeToMatchBox = (shape: DataAnnotationShape, image: DataAnnotationAssetNode): FanxiuGameWindow2MatchBox => {
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

const shapeFloatingScanBox = (shape: DataAnnotationShape, image: DataAnnotationAssetNode): FanxiuGameWindow2MatchBox | undefined => {
  const parent = findShapeParentShape(image.shapes ?? [], shape.id);
  if (!parent || !isDrawableShape(parent)) return undefined;
  return shapeToMatchBox(parent, image);
};

const shapeFloatingParentShape = (shape: DataAnnotationShape, image: DataAnnotationAssetNode): DataAnnotationShape | null => {
  const parent = findShapeParentShape(image.shapes ?? [], shape.id);
  return parent && isDrawableShape(parent) ? parent : null;
};

const resolveFloatingChildBoxFromParentMatch = (
  shape: DataAnnotationShape,
  image: DataAnnotationAssetNode,
  matchedParentBox: FanxiuGameWindow2MatchBox,
): FanxiuGameWindow2MatchBox => {
  const parent = shapeFloatingParentShape(shape, image);
  if (!parent || parent.w <= 0 || parent.h <= 0) return matchedParentBox;
  const relativeX = (shape.x - parent.x) / parent.w;
  const relativeY = (shape.y - parent.y) / parent.h;
  const relativeW = shape.w / parent.w;
  const relativeH = shape.h / parent.h;
  return {
    name: shape.title || matchedParentBox.name || 'shape',
    x: Math.round(matchedParentBox.x + matchedParentBox.w * relativeX),
    y: Math.round(matchedParentBox.y + matchedParentBox.h * relativeY),
    w: Math.round(matchedParentBox.w * relativeW),
    h: Math.round(matchedParentBox.h * relativeH),
  };
};

const shapePixelTolerance = (shape: DataAnnotationShape) => normalizeShapePixelTolerance(shape.pixelTolerance);
const shapeImageMatchRole = (shape: DataAnnotationShape) => normalizeShapeMatchRole(shape.imageMatchRole);
const shapeOcrMatchRole = (shape: DataAnnotationShape) => normalizeShapeMatchRole(shape.ocrMatchRole, shape.ocrEnabled ? 'required' : 'off');
type ShapeMatchKind = 'image' | 'ocr';
const shapeMatchRoleForKind = (shape: DataAnnotationShape, kind: ShapeMatchKind) => (
  kind === 'image' ? shapeImageMatchRole(shape) : shapeOcrMatchRole(shape)
);
const shapeActiveMatchKinds = (shape: DataAnnotationShape): ShapeMatchKind[] => [
  ...(shapeImageMatchRole(shape) !== 'off' ? ['image' as const] : []),
  ...(shapeOcrMatchRole(shape) !== 'off' && shape.ocrText?.trim() ? ['ocr' as const] : []),
];
const shapeCanFloat = (shape: DataAnnotationShape) => Boolean(shapeActiveMatchKinds(shape).length);
const shapePrimaryMatchKind = (shape: DataAnnotationShape): ShapeMatchKind | null => shapeActiveMatchKinds(shape)[0] ?? null;
const shapeHasRequiredMatch = (shape: DataAnnotationShape) => (
  shapeImageMatchRole(shape) === 'required'
  || (shapeOcrMatchRole(shape) === 'required' && Boolean(shape.ocrText?.trim()))
);
const shapeSceneIdentityRole = (shape: DataAnnotationShape) => normalizeShapeMatchRole(shape.sceneIdentityRole, shape.isSceneIdentity ? 'required' : 'off');
const isSceneIdentityShape = (shape: DataAnnotationShape) => shapeSceneIdentityRole(shape) !== 'off';

const imageHasSceneIdentity = (image: DataAnnotationAssetNode) => (
  (image.shapes ?? []).some((shape) => isSceneIdentityShape(shape))
);

const inferredFrameLayer = (image: DataAnnotationAssetNode): FrameLayer => (
  !imageHasSceneIdentity(image) ? 3 : (normalizeFrameLayer(image.layer, 3) === 1 ? 1 : 2)
);

const frameLayerTitle = (layer: FrameLayer) => ({
  1: 'Layer 1：第一识别队列，默认优先识别',
  2: 'Layer 2：第二识别队列，在 Layer 1 之后识别',
  3: 'Layer 3：第三识别队列，通常用于模板、素材和低优先级 scene',
}[layer]);

const frameLayerStyle = (layer: FrameLayer) => ({
  1: { color: '#d93026' },
  2: { color: '#2f8f2f' },
  3: { color: '#303133' },
}[layer]);

const isVirtualAssetTreeNode = (node: DataAnnotationAssetNode | null | undefined) => (
  Boolean(node && (LAYER_TREE_ROOT_IDS as readonly string[]).includes(node.id))
);

const buildSceneLayerProjection = (nodes: DataAnnotationAssetNode[]): DataAnnotationAssetNode[] => {
  const effectiveImages = effectiveSceneImages(nodes);
  const rootsByLayer: Record<FrameLayer, DataAnnotationAssetNode[]> = {
    1: [],
    2: [],
    3: [],
  };
  const visit = (items: DataAnnotationAssetNode[]) => {
    for (const node of items) {
      if (node.type === 'image') {
        const sceneId = assetNumericImageId(node);
        const effectiveNode = sceneId === null ? node : (effectiveImages.get(sceneId) ?? node);
        rootsByLayer[inferredFrameLayer(effectiveNode)].push({ ...node, children: [] });
      }
      visit(node.children ?? []);
    }
  };
  visit(nodes);

  return [
    {
      id: LAYER_TREE_ROOT_IDS[0],
      type: 'folder',
      title: 'Layer 1',
      children: rootsByLayer[1],
    },
    {
      id: LAYER_TREE_ROOT_IDS[1],
      type: 'folder',
      title: 'Layer 2',
      children: rootsByLayer[2],
    },
    {
      id: LAYER_TREE_ROOT_IDS[2],
      type: 'folder',
      title: 'Layer 3',
      children: rootsByLayer[3],
    },
  ];
};

const assetTreeDisplayData = computed(() => (
  assetTreeViewMode.value === 'scene' ? buildSceneLayerProjection(assetTree.value) : assetTree.value
));

const collectSceneExpandedNodeIds = (selectedId: string | null) => Array.from(new Set([
  ...LAYER_TREE_ROOT_IDS,
  ...(findDisplayAssetAncestorIds(assetTreeDisplayData.value, selectedId) ?? []),
]));

const assetTreeDefaultExpandedKeys = computed(() => (
  assetTreeViewMode.value === 'scene'
    ? expandedAssetNodeIds.value
    : expandedAssetNodeIds.value
));

const shapeBoxIou = (a: DataAnnotationShape, b: DataAnnotationShape) => {
  const left = Math.max(a.x, b.x);
  const top = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.w, b.x + b.w);
  const bottom = Math.min(a.y + a.h, b.y + b.h);
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top);
  const union = Math.max(0, a.w * a.h) + Math.max(0, b.w * b.h) - intersection;
  return union > 0 ? intersection / union : 0;
};

const shapeBoxCenterDistance = (a: DataAnnotationShape, b: DataAnnotationShape) => {
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
  image: DataAnnotationAssetNode,
  targetShape: DataAnnotationShape,
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

const buildRuntimeShapeMatchPayload = (
  image: DataAnnotationAssetNode,
  shape: DataAnnotationShape,
  currentFrameDataUrl?: string,
  options: { readOnlyCache?: boolean; saveMatchFrame?: boolean; condition?: 'auto' | 'image' | 'ocr'; debugMatch?: boolean } = {},
): FanxiuGameWindow2MatchPayload | null => {
  if (!selectedEntryId.value || !image.filename) return null;
  const ocrText = (shape.ocrText || '').trim();
  const forceImage = options.condition === 'image';
  const forceOcr = options.condition === 'ocr';
  const ocrEnabled = !forceImage && shapeOcrMatchRole(shape) !== 'off' && Boolean(ocrText);
  const alphaMaskDataUrl = shape.maskEnabled ? shape.alphaMask?.dataUrl || '' : '';
  const ocrMaskMode = normalizeShapeOcrMaskMode(shape.ocrMaskMode);
  const ocrMaskDataUrl = ocrMaskMode === 'custom' ? shape.ocrMask?.dataUrl || '' : '';
  const toleranceMinDataUrl = shape.toleranceEnabled ? shape.toleranceRange?.minDataUrl || '' : '';
  const toleranceMaxDataUrl = shape.toleranceEnabled ? shape.toleranceRange?.maxDataUrl || '' : '';
  const scanEnabled = Boolean(shape.floating && !ocrEnabled);
  const floatingParent = scanEnabled ? shapeFloatingParentShape(shape, image) : null;
  const box = shapeToMatchBox(floatingParent ?? shape, image);
  const scanBox = scanEnabled && !floatingParent ? shapeFloatingScanBox(shape, image) : undefined;
  const jitterEnabled = Boolean(shape.jitterEnabled && !scanEnabled && !ocrEnabled);
  return {
    entry_id: selectedEntryId.value,
    filename: image.filename,
    box,
    scan: scanEnabled,
    scan_box: scanBox,
    pixel_tolerance: shapePixelTolerance(shape),
    alpha_mask_data_url: alphaMaskDataUrl || buildOcclusionAlphaMaskDataUrl(image, shape, box) || undefined,
    ocr_mask_mode: ocrMaskMode,
    ocr_mask_data_url: ocrMaskDataUrl || undefined,
    tolerance_min_data_url: toleranceMinDataUrl || undefined,
    tolerance_max_data_url: toleranceMaxDataUrl || undefined,
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
    current_frame_data_url: currentFrameDataUrl || undefined,
    prefer_cached: false,
    match_strategy: forceOcr || scanEnabled || jitterEnabled ? 'auto' : 'anchor_pixel',
    match_search_radius: jitterEnabled ? normalizeShapeJitterRadius(shape.jitterRadius) : undefined,
    ocr_enabled: ocrEnabled,
    ocr_text: ocrEnabled ? ocrText : undefined,
    ocr_match_mode: shape.ocrMatchMode || 'contains',
    read_only_cache: options.readOnlyCache && ocrEnabled,
    save_match_frame: options.saveMatchFrame ?? true,
    debug_match: Boolean(options.debugMatch && !ocrEnabled),
  };
};

const matchRuntimeShape = async (
  image: DataAnnotationAssetNode,
  shape: DataAnnotationShape,
  currentFrameDataUrl?: string,
  signal?: AbortSignal,
  options: { readOnlyCache?: boolean; saveMatchFrame?: boolean; condition?: 'auto' | 'image' | 'ocr'; timeout?: number; debugMatch?: boolean } = {},
) => {
  const payload = buildRuntimeShapeMatchPayload(image, shape, currentFrameDataUrl, options);
  if (!payload) return null;
  return matchFanxiuGameWindow2Screenshot(payload, { signal, timeout: options.timeout ?? 30000 });
};

const waitShapeDetectLoopInterval = async (seq: number, shapeId: string, delayMs = 1500) => {
  const deadline = Date.now() + delayMs;
  while (Date.now() < deadline) {
    if (shapeDetectStopRequested || shapeDetectSeq.value !== seq || selectedShape.value?.id !== shapeId) {
      return false;
    }
    await sleep(Math.min(100, Math.max(0, deadline - Date.now())));
  }
  return !shapeDetectStopRequested && shapeDetectSeq.value === seq && selectedShape.value?.id === shapeId;
};

const bestRuntimeShapeMatchOf = (
  shape: DataAnnotationShape,
  response: FanxiuGameWindow2MatchResponse,
  image?: DataAnnotationAssetNode | null,
): { box: FanxiuGameWindow2MatchBox; score: number } => {
  const firstMatch = response.matches?.[0];
  const fixedBox = response.fixed_box;
  const isOcrCondition = response.match_strategy === 'ocr';
  const box = isOcrCondition && shape.sceneJumpTarget?.trim()
    ? (response.current_box ?? response.box ?? fixedBox ?? (image ? shapeToMatchBox(shape, image) : response.box))
    : shape.floating
    ? (image && (firstMatch?.box || fixedBox || response.current_box || response.box)
      ? resolveFloatingChildBoxFromParentMatch(shape, image, firstMatch?.box ?? fixedBox ?? response.current_box ?? response.box)
      : firstMatch?.box ?? fixedBox ?? response.current_box ?? response.box)
    : (image ? shapeToMatchBox(shape, image) : response.box);
  const score = Number(
    firstMatch?.similarity
      ?? response.fixed_similarity
      ?? response.similarity
      ?? response.score
      ?? 0,
  );
  return { box, score };
};

const RUNTIME_SHAPE_IMAGE_THRESHOLD = 80;

const shapeImageThreshold = (_shape: DataAnnotationShape) => RUNTIME_SHAPE_IMAGE_THRESHOLD;

const shapeOcrMatched = (response: FanxiuGameWindow2MatchResponse | null | undefined) => (
  Boolean(response?.matches?.length)
);

const shapeImageMatched = (shape: DataAnnotationShape, score: number) => (
  score >= shapeImageThreshold(shape)
);

const formatShapeDetectCombinedResult = (
  shape: DataAnnotationShape,
  results: Array<{
    kind: 'image' | 'ocr';
    response: FanxiuGameWindow2MatchResponse;
    best: { box: FanxiuGameWindow2MatchBox; score: number };
  }>,
) => {
  const parts = results.map(result => {
    if (result.kind === 'ocr') {
      const text = result.response.ocr_text ? `「${result.response.ocr_text}」` : '';
      return `OCR${shapeOcrMatched(result.response) ? '' : '未命中'} ${text}`.trim();
    }
    const score = Math.round(result.best.score);
    return `图像 ${score}%${shapeImageMatched(shape, score) ? '' : ' 未达标'}`;
  });
  if (results.length > 1) {
    const anyMatched = results.some(result => (
      result.kind === 'ocr'
        ? shapeOcrMatched(result.response)
        : shapeImageMatched(shape, result.best.score)
    ));
    parts.push(anyMatched ? '条件满足' : '条件未满足');
  }
  return parts.join('；');
};

const openShapeDetectDebugDialog = () => {
  const debug = selectedShapeDetectDebug.value;
  if (!debug) return;
  shapeDetectDebugCurrent.value = debug;
  shapeDetectDebugDialogVisible.value = true;
};

const detectSelectedShape = async () => {
  const currentId = selectedShape.value?.id ?? '';
  if (shapeDetectingId.value) {
    shapeDetectStopRequested = true;
    shapeDetectStopRequestedRef.value = true;
    shapeDetectAbortController?.abort();
    return;
  }
  const image = selectedImageNode.value;
  const shape = selectedShape.value;
  if (!image || !shape || !isDrawableShape(shape)) return;
  const seq = shapeDetectSeq.value + 1;
  shapeDetectSeq.value = seq;
  shapeDetectingId.value = shape.id;
  shapeDetectStopRequested = false;
  shapeDetectStopRequestedRef.value = false;
  shapeDetectLiveBoxes.value = [];
  shapeDetectResults.value = {
    ...shapeDetectResults.value,
    [shape.id]: '检测中...',
  };
  const nextDebugByShapeId = { ...shapeDetectDebugByShapeId.value };
  delete nextDebugByShapeId[shape.id];
  shapeDetectDebugByShapeId.value = nextDebugByShapeId;
  try {
    while (!shapeDetectStopRequested && shapeDetectSeq.value === seq && selectedShape.value?.id === currentId) {
      const frameAbortController = new AbortController();
      shapeDetectAbortController = frameAbortController;
      const frameDataUrl = await captureCurrentFrameDataUrl('frontend', {
        preferLiveFrame: true,
        liveFrameWaitMs: 600,
        allowScreencapFallback: true,
        cachedScreencapOnly: true,
        screencapTimeoutMs: 600,
        signal: frameAbortController.signal,
      });
      if (shapeDetectAbortController === frameAbortController) shapeDetectAbortController = null;
      if (!frameDataUrl) throw new Error('当前没有可检测画面');
      if (shapeDetectStopRequested || shapeDetectSeq.value !== seq || selectedShape.value?.id !== currentId) break;
      const matchFrameDataUrl = await compressFrameDataUrlForMatch(frameDataUrl);
      const enabledConditions = shapeActiveMatchKinds(shape);
      const conditionResults: Array<{
        kind: 'image' | 'ocr';
        response: FanxiuGameWindow2MatchResponse;
        best: { box: FanxiuGameWindow2MatchBox; score: number };
      }> = [];
      for (const condition of enabledConditions) {
        const abortController = new AbortController();
        shapeDetectAbortController = abortController;
        const conditionFrameDataUrl = condition === 'image'
          ? frameDataUrl
          : (matchFrameDataUrl || frameDataUrl);
        const response = await matchRuntimeShape(
          image,
          shape,
          conditionFrameDataUrl,
          abortController.signal,
          {
            readOnlyCache: true,
            saveMatchFrame: false,
            condition,
            debugMatch: condition === 'image',
            timeout: condition === 'ocr' ? 120000 : 30000,
          },
        );
        if (shapeDetectAbortController === abortController) shapeDetectAbortController = null;
        if (!response) throw new Error('检测参数不完整');
        if (shapeDetectStopRequested || shapeDetectSeq.value !== seq || selectedShape.value?.id !== currentId) break;
        conditionResults.push({
          kind: condition,
          response,
          best: bestRuntimeShapeMatchOf(shape, response, image),
        });
        if (condition === 'image' && response.match_debug) {
          shapeDetectDebugByShapeId.value = {
            ...shapeDetectDebugByShapeId.value,
            [shape.id]: response.match_debug,
          };
        }
      }
      if (shapeDetectStopRequested || shapeDetectSeq.value !== seq || selectedShape.value?.id !== currentId) break;
      if (!conditionResults.length) throw new Error('没有启用可检测条件');
      const best = conditionResults.find(result => (
        result.kind === 'ocr'
          ? shapeOcrMatched(result.response)
          : shapeImageMatched(shape, result.best.score)
      ))?.best ?? conditionResults[0].best;
      shapeDetectResults.value = {
        ...shapeDetectResults.value,
        [shape.id]: formatShapeDetectCombinedResult(shape, conditionResults),
      };
      shapeDetectLiveBoxes.value = [best.box];
      drawOverlay();
      if (!shapeDetectLoopEnabled.value) break;
      const shouldContinue = await waitShapeDetectLoopInterval(seq, currentId);
      if (!shouldContinue) break;
    }
  } catch (error) {
    if (shapeDetectStopRequested) {
      const previousResult = shapeDetectResults.value[shape.id] || '';
      shapeDetectResults.value = {
        ...shapeDetectResults.value,
        [shape.id]: previousResult && previousResult !== '检测中...' ? previousResult : '已停止',
      };
      shapeDetectLiveBoxes.value = [];
      drawOverlay();
      return;
    }
    shapeDetectResults.value = {
      ...shapeDetectResults.value,
      [shape.id]: `检测失败：${getErrorMessage(error)}`,
    };
    shapeDetectLiveBoxes.value = [];
    drawOverlay();
    ElMessage.error(getErrorMessage(error));
  } finally {
    if (shapeDetectSeq.value === seq) {
      shapeDetectingId.value = null;
      shapeDetectStopRequested = false;
      shapeDetectStopRequestedRef.value = false;
      shapeDetectAbortController = null;
    }
  }
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
  transformOrigin: '0 0',
  transform: `translate(${screenshotPanX.value}px, ${screenshotPanY.value}px) scale(${screenshotZoomPercent.value / 100})`,
}));

watch(assetTree, (value) => {
  if (typeof window === 'undefined') return;
  expandedAssetNodeIds.value = filterExistingAssetNodeIds(expandedAssetNodeIds.value);
  queueAssetTreeExpansionSync();
  if (!assetTreeBackendHydrating.value) {
    assetTreeLocalVersion += 1;
  }
  scheduleAssetTreeBackendSave();
}, { deep: true });

watch(discriminatorGroups, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(DATA_ANNOTATION_DISCRIMINATOR_GROUPS_KEY, JSON.stringify(value));
}, { deep: true });

watch(globalOcclusionMaskEnabled, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(DATA_ANNOTATION_OCCLUSION_MASK_ENABLED_KEY, value ? 'true' : 'false');
});

watch(assetFrameSearchText, (value) => {
  assetTreeRef.value?.filter?.(value);
  expandAssetSearchMatches(value);
});

watch(selectedEntryId, () => {
  stopRecognitionOpsPolling();
  recognitionOpsReport.value = null;
  recognitionOpsError.value = '';
  selectedRecognitionOpsIssueId.value = null;
  selectedNavigationIncident.value = null;
  navigationIncidentError.value = '';
  selectedNavigationTimelineIndex.value = null;
  selectedRecognitionAmbiguity.value = null;
  recognitionAmbiguityError.value = '';
  if (assetTreeViewMode.value === 'recognitionOps') void loadRecognitionOps(false);
});

watch(selectedRuntimeTaskType, (value) => {
  const sameTypeTasks = runtimeSchedulerTasks.value.filter((task) => task.task_type === value);
  if (!sameTypeTasks.length) {
    selectedRuntimeTaskId.value = '';
    return;
  }
  if (!sameTypeTasks.some((task) => task.id === selectedRuntimeTaskId.value)) {
    selectedRuntimeTaskId.value = sameTypeTasks[0].id;
  }
});

watch(selectedImageNode, (node, previousNode) => {
  const imageChanged = node?.id !== previousNode?.id;
  selectedImageOcrProbeSeq += 1;
  selectedImageOcrCandidates.value = [];
  selectedImageOcrCandidateImageId.value = '';
  const firstShape = node ? flattenShapes(node.shapes ?? [])[0] ?? null : null;
  selectedShapeId.value = node && selectedShapeId.value && findShapeById(node.shapes ?? [], selectedShapeId.value)
    ? selectedShapeId.value
    : firstShape?.id ?? null;
  selectedShapeIds.value = [];
  shapeSelectionAnchorId.value = selectedShapeId.value;
  expandedShapeNodeIds.value = filterExistingShapeNodeIds(expandedShapeNodeIds.value);
  queueShapeTreeExpansionSync();
  shapeDetectResults.value = {};
  shapeDetectLiveBoxes.value = [];
  drawOverlay();
  if (imageChanged) resetScreenshotViewState();
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

watch(assetTreeViewMode, (mode) => {
  if (mode === 'recognitionOps') {
    if (!recognitionOpsReport.value && !recognitionOpsLoading.value) {
      void loadRecognitionOps(false);
    }
    return;
  }
  stopRecognitionOpsPolling();
  if (mode !== 'scene') return;
  expandedAssetNodeIds.value = Array.from(new Set([
    ...expandedAssetNodeIds.value,
    ...collectSceneExpandedNodeIds(selectedAssetId.value),
  ]));
  queueAssetTreeExpansionSync();
});

watch(
  [selectedEntryId, selectedSceneRelationGraphVisible, activeSceneRelationGraphTab],
  () => {
    if (
      selectedEntryId.value
      && selectedSceneRelationGraphVisible.value
      && activeSceneRelationGraphTab.value === 'recognition'
      && !recognitionOpsReport.value
      && !recognitionOpsLoading.value
    ) {
      void loadRecognitionOps(false, true);
    }
  },
  { immediate: true },
);

watch(
  [selectedAssetId, selectedShapeId, expandedAssetNodeIds, expandedShapeNodeIds, assetTreeViewMode],
  persistDataAnnotationUiState,
  { deep: true },
);

watch(expandedAssetNodeIds, queueAssetTreeExpansionSync, { deep: true });

watch(expandedShapeNodeIds, queueShapeTreeExpansionSync, { deep: true });

watch(runtimeLogs, () => {
  if (runtimeLogPage.value > runtimeLogPageCount.value) goRuntimeLogFirstPage();
}, { deep: true });

watch(runtimeLogDialogVisible, (visible) => {
  if (visible && !runtimeLogs.value.length) void loadRuntimeLogs();
  if (visible) goRuntimeLogFirstPage();
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

const insertAssetNodeAfterSelection = (node: DataAnnotationAssetNode) => {
  const { siblings, insertIndex } = getAssetInsertContext();
  siblings.splice(insertIndex, 0, node);
  selectedAssetId.value = node.id;
};

const addAssetFolder = () => {
  if (assetTreeViewMode.value !== 'business') return;
  const { siblings } = getAssetInsertContext();
  const folderCount = siblings.filter((node) => node.type === 'folder').length + 1;
  const node: DataAnnotationAssetNode = {
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

const isObjectUrl = (url: string) => url.startsWith('blob:');

const revokeAssetImagePreviewUrl = (url: string | undefined) => {
  if (url && isObjectUrl(url)) URL.revokeObjectURL(url);
};

const setAssetImagePreviewUrl = (key: string, url: string) => {
  const previous = assetImagePreviewUrls.value[key];
  if (previous && previous !== url) revokeAssetImagePreviewUrl(previous);
  const missing = { ...assetImagePreviewMissingIds.value };
  delete missing[key];
  assetImagePreviewMissingIds.value = missing;
  assetImagePreviewUrls.value = {
    ...assetImagePreviewUrls.value,
    [key]: url,
  };
};

const releaseAssetImagePreviewUrls = () => {
  assetImagePreviewEpoch += 1;
  Object.values(assetImagePreviewUrls.value).forEach(revokeAssetImagePreviewUrl);
  assetImagePreviewUrls.value = {};
  assetImagePreviewLoadingIds.value = {};
  assetImagePreviewMissingIds.value = {};
  assetImagePreviewRequests.clear();
  assetImagePreviewRenderRecoveryKeys.clear();
};

const blobToObjectUrl = (blob: Blob) => URL.createObjectURL(blob);

const validateAssetImageObjectUrl = (url: string) => new Promise<void>((resolve, reject) => {
  const probe = new Image();
  probe.onload = () => resolve();
  probe.onerror = () => reject(new Error('标注图片内容无法解码'));
  probe.src = url;
});

const waitForAssetImageRetry = (delayMs: number) => new Promise<void>((resolve) => {
  window.setTimeout(resolve, delayMs);
});

const getAssetImageDataUrl = async (image: DataAnnotationAssetNode, options: { force?: boolean } = {}) => {
  const entryId = selectedEntryId.value;
  const key = assetImagePreviewKey(image, entryId);
  if (options.force) {
    const previous = assetImagePreviewUrls.value[key];
    revokeAssetImagePreviewUrl(previous);
    const urls = { ...assetImagePreviewUrls.value };
    const missing = { ...assetImagePreviewMissingIds.value };
    delete urls[key];
    delete missing[key];
    assetImagePreviewUrls.value = urls;
    assetImagePreviewMissingIds.value = missing;
  } else {
    if (assetImagePreviewUrls.value[key]) return assetImagePreviewUrls.value[key];
    if (image.imageDataUrl) return image.imageDataUrl;
    if (assetImagePreviewMissingIds.value[key]) return '';
    const pending = assetImagePreviewRequests.get(key);
    if (pending) return pending;
  }
  if (!entryId || !image.filename) return '';
  const requestEpoch = assetImagePreviewEpoch;
  assetImagePreviewLoadingIds.value = {
    ...assetImagePreviewLoadingIds.value,
    [key]: true,
  };
  let request!: Promise<string>;
  request = (async () => {
    let lastError: unknown = new Error('标注图片加载失败');
    const retryDelays = [0, 300, 900];
    try {
      for (let attempt = 0; attempt < retryDelays.length; attempt += 1) {
        if (retryDelays[attempt]) await waitForAssetImageRetry(retryDelays[attempt]);
        let previewUrl = '';
        try {
          const cacheBust = options.force || attempt > 0 ? Date.now() + attempt : undefined;
          const blob = await getFanxiuDataAnnotationImage(
            entryId,
            image.filename!,
            cacheBust,
          );
          if (!blob.size) throw new Error('标注图片响应为空');
          previewUrl = blobToObjectUrl(blob);
          await validateAssetImageObjectUrl(previewUrl);
          if (requestEpoch !== assetImagePreviewEpoch || selectedEntryId.value !== entryId) {
            revokeAssetImagePreviewUrl(previewUrl);
            return '';
          }
          setAssetImagePreviewUrl(key, previewUrl);
          return previewUrl;
        } catch (error) {
          revokeAssetImagePreviewUrl(previewUrl);
          lastError = error;
          if (getHttpStatus(error) === 404) break;
        }
      }
      if (requestEpoch === assetImagePreviewEpoch && selectedEntryId.value === entryId) {
        assetImagePreviewMissingIds.value = {
          ...assetImagePreviewMissingIds.value,
          [key]: true,
        };
      }
      throw lastError;
    } finally {
      if (requestEpoch === assetImagePreviewEpoch) {
        const next = { ...assetImagePreviewLoadingIds.value };
        delete next[key];
        assetImagePreviewLoadingIds.value = next;
      }
      if (assetImagePreviewRequests.get(key) === request) assetImagePreviewRequests.delete(key);
    }
  })();
  assetImagePreviewRequests.set(key, request);
  return request;
};

const recoverSelectedImagePreview = () => {
  const image = selectedImageNode.value;
  if (!image) return;
  const key = assetImagePreviewKey(image);
  const previous = assetImagePreviewUrls.value[key];
  revokeAssetImagePreviewUrl(previous);
  const urls = { ...assetImagePreviewUrls.value };
  delete urls[key];
  assetImagePreviewUrls.value = urls;
  assetImagePreviewMissingIds.value = {
    ...assetImagePreviewMissingIds.value,
    [key]: true,
  };
  if (image.filename && !assetImagePreviewRenderRecoveryKeys.has(key)) {
    assetImagePreviewRenderRecoveryKeys.add(key);
    void getAssetImageDataUrl(image, { force: true }).catch(() => undefined);
  }
};

const ensureSelectedImagePreview = async () => {
  const image = selectedImageNode.value;
  if (!image || selectedImagePreviewUrl.value || selectedImagePreviewMissing.value) return;
  try {
    await getAssetImageDataUrl(image);
  } catch {
    // Preview loading is opportunistic; matching can still use the saved filename.
  }
};

const retrySelectedImagePreview = () => {
  const image = selectedImageNode.value;
  if (!image || selectedImagePreviewLoading.value) return;
  const key = assetImagePreviewKey(image);
  assetImagePreviewRenderRecoveryKeys.delete(key);
  void getAssetImageDataUrl(image, { force: true }).catch(() => undefined);
};

const recoverSelectedImagePreviewWhenAvailable = () => {
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
  if (selectedImagePreviewMissing.value) {
    retrySelectedImagePreview();
    return;
  }
  void ensureSelectedImagePreview();
};

const imageSourceToDataUrl = async (source: string) => {
  if (source.startsWith('data:')) return source;
  const response = await fetch(source);
  if (!response.ok) throw new Error('读取标注图片失败');
  return blobToDataUrl(await response.blob());
};

const refreshSelectedImageOcrSuggestions = async () => {
  const image = selectedImageNode.value;
  const seq = ++selectedImageOcrProbeSeq;
  if (!image || !selectedImageNeedsOcrSuggestions.value) {
    selectedImageOcrCandidates.value = [];
    selectedImageOcrCandidateImageId.value = '';
    return;
  }
  try {
    const source = await getAssetImageDataUrl(image);
    if (!source) return;
    const cacheKey = `${selectedEntryId.value}:${image.id}:${image.filename || ''}:${source}`;
    let pending = imageOcrCandidateCache.get(cacheKey);
    if (!pending) {
      pending = imageSourceToDataUrl(source)
        .then(recognizeFanxiuDataAnnotationOcrFrame)
        .then((response) => response.tokens)
        .catch((error) => {
          imageOcrCandidateCache.delete(cacheKey);
          throw error;
        });
      imageOcrCandidateCache.set(cacheKey, pending);
    }
    const candidates = await pending;
    if (seq !== selectedImageOcrProbeSeq || selectedImageNode.value?.id !== image.id) return;
    selectedImageOcrCandidates.value = candidates;
    selectedImageOcrCandidateImageId.value = image.id;
  } catch {
    if (seq !== selectedImageOcrProbeSeq || selectedImageNode.value?.id !== image.id) return;
    selectedImageOcrCandidates.value = [];
    selectedImageOcrCandidateImageId.value = '';
  }
};

watch(
  [selectedImageNode, selectedImageNeedsOcrSuggestions],
  () => { void refreshSelectedImageOcrSuggestions(); },
  { immediate: true },
);

const loadImageCompareElement = (src: string) => new Promise<HTMLImageElement>((resolve, reject) => {
  const image = new Image();
  image.onload = () => resolve(image);
  image.onerror = () => reject(new Error('图片加载失败'));
  image.src = src;
});

const imageCompareCanvasOf = (side: ImageCompareSide) => (
  side === 'saved' ? imageCompareSavedCanvasRef.value : imageCompareLiveCanvasRef.value
);

const imageCompareImageOf = (side: ImageCompareSide) => (
  side === 'saved' ? imageCompareSavedImage.value : imageCompareLiveImage.value
);

const getImageCompareCanvasPoint = (event: PointerEvent | WheelEvent, side: ImageCompareSide) => {
  const canvas = imageCompareCanvasOf(side);
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: (event.clientX - rect.left) * (canvas.width / rect.width),
    y: (event.clientY - rect.top) * (canvas.height / rect.height),
  };
};

const getImageCompareImagePoint = (event: PointerEvent | WheelEvent, side: ImageCompareSide) => {
  const point = getImageCompareCanvasPoint(event, side);
  const image = imageCompareImageOf(side);
  if (!point || !image) return null;
  const { scale, offsetX, offsetY } = imageCompareView.value;
  const imageWidth = image.naturalWidth || image.width;
  const imageHeight = image.naturalHeight || image.height;
  const x = Math.max(0, Math.min(imageWidth, (point.x - offsetX) / scale));
  const y = Math.max(0, Math.min(imageHeight, (point.y - offsetY) / scale));
  return {
    x: imageWidth ? x / imageWidth : 0,
    y: imageHeight ? y / imageHeight : 0,
  };
};

const drawImageCompareSide = (side: ImageCompareSide) => {
  const canvas = imageCompareCanvasOf(side);
  const image = imageCompareImageOf(side);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!image) return;
  const { scale, offsetX, offsetY } = imageCompareView.value;
  const imageWidth = image.naturalWidth || image.width;
  const imageHeight = image.naturalHeight || image.height;
  const width = imageWidth * scale;
  const height = imageHeight * scale;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(image, offsetX, offsetY, width, height);

  ctx.lineWidth = 2;
  for (const rect of imageCompareRects.value) {
    const x = offsetX + Math.min(rect.x, rect.x + rect.w) * imageWidth * scale;
    const y = offsetY + Math.min(rect.y, rect.y + rect.h) * imageHeight * scale;
    const w = Math.abs(rect.w) * imageWidth * scale;
    const h = Math.abs(rect.h) * imageHeight * scale;
    const activeRectId = imageCompareDrag.value?.mode === 'rect' ? imageCompareDrag.value.rectId : '';
    ctx.strokeStyle = rect.id === activeRectId ? '#f59e0b' : '#38bdf8';
    ctx.strokeRect(x, y, w, h);
  }

  const crosshair = imageCompareCrosshair.value;
  if (crosshair) {
    const x = offsetX + crosshair.x * imageWidth * scale;
    const y = offsetY + crosshair.y * imageHeight * scale;
    ctx.save();
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.92)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
    ctx.restore();
  }
};

const drawImageCompare = () => {
  drawImageCompareSide('saved');
  drawImageCompareSide('live');
};

const resetImageCompareView = async () => {
  await nextTick();
  const canvas = imageCompareSavedCanvasRef.value || imageCompareLiveCanvasRef.value;
  const image = imageCompareSavedImage.value || imageCompareLiveImage.value;
  if (!canvas || !image) return;
  const imageWidth = image.naturalWidth || image.width;
  const imageHeight = image.naturalHeight || image.height;
  const scale = Math.min(canvas.width / imageWidth, canvas.height / imageHeight);
  imageCompareView.value = {
    scale,
    offsetX: (canvas.width - imageWidth * scale) / 2,
    offsetY: (canvas.height - imageHeight * scale) / 2,
  };
  imageCompareCrosshair.value = { x: 0.5, y: 0.5 };
  drawImageCompare();
};

const clearImageCompareRects = () => {
  imageCompareRects.value = [];
  drawImageCompare();
};

const openImageCompareDialog = async () => {
  const image = selectedImageNode.value;
  if (!image) return;
  imageCompareDialogVisible.value = true;
  imageCompareLoading.value = true;
  imageCompareError.value = '';
  imageCompareRects.value = [];
  imageCompareDrag.value = null;
  try {
    const savedDataUrl = await getAssetImageDataUrl(image);
    const liveDataUrl = await captureCurrentFrameDataUrl();
    if (!savedDataUrl) throw new Error('当前帧截图为空');
    if (!liveDataUrl) throw new Error('当前直播帧为空');
    const [savedImage, liveImage] = await Promise.all([
      loadImageCompareElement(savedDataUrl),
      loadImageCompareElement(liveDataUrl),
    ]);
    imageCompareSavedImage.value = savedImage;
    imageCompareLiveImage.value = liveImage;
    await resetImageCompareView();
  } catch (error) {
    imageCompareError.value = getErrorMessage(error);
  } finally {
    imageCompareLoading.value = false;
  }
};

const closeImageCompareDialog = () => {
  imageCompareSavedImage.value = null;
  imageCompareLiveImage.value = null;
  imageCompareCrosshair.value = null;
  imageCompareRects.value = [];
  imageCompareDrag.value = null;
  imageCompareSpacePressed.value = false;
  imageCompareError.value = '';
};

const handleImageCompareWheel = (event: WheelEvent, side: ImageCompareSide) => {
  const point = getImageCompareCanvasPoint(event, side);
  if (!point) return;
  const current = imageCompareView.value;
  const nextScale = Math.max(0.08, Math.min(8, current.scale * (event.deltaY > 0 ? 0.9 : 1.1)));
  const imageX = (point.x - current.offsetX) / current.scale;
  const imageY = (point.y - current.offsetY) / current.scale;
  imageCompareView.value = {
    scale: nextScale,
    offsetX: point.x - imageX * nextScale,
    offsetY: point.y - imageY * nextScale,
  };
  drawImageCompare();
};

const handleImageComparePointerDown = (event: PointerEvent, side: ImageCompareSide) => {
  const canvas = imageCompareCanvasOf(side);
  if (!canvas) return;
  const imagePoint = getImageCompareImagePoint(event, side);
  if (!imagePoint) return;
  canvas.setPointerCapture(event.pointerId);
  event.preventDefault();
  imageCompareCrosshair.value = imagePoint;
  if (event.shiftKey || event.button === 1 || imageCompareSpacePressed.value) {
    imageCompareDrag.value = {
      mode: 'pan',
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startOffsetX: imageCompareView.value.offsetX,
      startOffsetY: imageCompareView.value.offsetY,
    };
  } else {
    const rectId = createAssetId('compare-rect');
    imageCompareRects.value = [
      ...imageCompareRects.value,
      { id: rectId, x: imagePoint.x, y: imagePoint.y, w: 0, h: 0 },
    ];
    imageCompareDrag.value = {
      mode: 'rect',
      pointerId: event.pointerId,
      start: imagePoint,
      rectId,
    };
  }
  drawImageCompare();
};

const handleImageComparePointerMove = (event: PointerEvent, side: ImageCompareSide) => {
  const drag = imageCompareDrag.value;
  if (drag?.mode === 'pan' && drag.pointerId === event.pointerId) {
    imageCompareView.value = {
      ...imageCompareView.value,
      offsetX: drag.startOffsetX + event.clientX - drag.startClientX,
      offsetY: drag.startOffsetY + event.clientY - drag.startClientY,
    };
    drawImageCompare();
    return;
  }
  const imagePoint = getImageCompareImagePoint(event, side);
  if (!imagePoint) return;
  imageCompareCrosshair.value = imagePoint;
  if (drag?.mode === 'rect' && drag.pointerId === event.pointerId) {
    imageCompareRects.value = imageCompareRects.value.map(rect => (
      rect.id === drag.rectId
        ? { ...rect, w: imagePoint.x - drag.start.x, h: imagePoint.y - drag.start.y }
        : rect
    ));
  }
  drawImageCompare();
};

const handleImageComparePointerUp = (event: PointerEvent) => {
  const drag = imageCompareDrag.value;
  if (drag?.pointerId !== event.pointerId) return;
  if (drag.mode === 'rect') {
    const image = imageCompareSavedImage.value || imageCompareLiveImage.value;
    const minWidth = 3 / Math.max(1, image?.naturalWidth || image?.width || 900);
    const minHeight = 3 / Math.max(1, image?.naturalHeight || image?.height || 1600);
    imageCompareRects.value = imageCompareRects.value.filter(rect => (
      rect.id !== drag.rectId || Math.abs(rect.w) >= minWidth || Math.abs(rect.h) >= minHeight
    ));
  }
  imageCompareDrag.value = null;
  drawImageCompare();
};

const handleImageComparePointerLeave = () => {
  if (!imageCompareDrag.value) drawImageCompare();
};

const insertSavedFrameNode = (node: DataAnnotationAssetNode) => {
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

const addSavedFrameToAssetTree = (node: DataAnnotationAssetNode) => {
  insertSavedFrameNode(node);
};

const savedFrameInsertTarget = () => {
  const selectedNode = selectedAssetNode.value;
  if (selectedNode?.type === 'folder') {
    return { parentId: selectedNode.id, afterNodeId: '' };
  }
  return { parentId: '', afterNodeId: selectedNode?.id || '' };
};

const applySavedFrameTransaction = async (
  result: FanxiuDataAnnotationSaveFrameResponse,
  requestedNode: DataAnnotationAssetNode,
  localVersion: number,
) => {
  if (!Array.isArray(result.tree) || !result.revision) throw new Error('保存响应缺少资产树事务结果');
  const backendTree = filterDeletedShapesFromAssetTree(
    compactDuplicateAssetNodes(normalizeAssetTree(result.tree as DataAnnotationAssetNode[])),
  );
  assetTreeBackendHydrating.value = true;
  try {
    assetTree.value = localVersion === assetTreeLocalVersion
      ? backendTree
      : mergeAssetTreeNodes(backendTree, assetTree.value);
    await nextTick();
  } finally {
    assetTreeBackendHydrating.value = false;
  }
  assetTreeBackendRevision.value = result.revision;
  assetTreeBackendUpdatedAt.value = Number(result.updated_at) || assetTreeBackendUpdatedAt.value;
  assetTreeDirty = localVersion !== assetTreeLocalVersion;
  const savedNode = findAssetNode(assetTree.value, requestedNode.id);
  if (!savedNode || savedNode.type !== 'image') throw new Error('资产树事务未返回新帧节点');
  Object.assign(requestedNode, savedNode);
  selectedAssetId.value = requestedNode.id;
  if (assetTreeDirty) scheduleAssetTreeBackendSave();
};

const saveFrameDataUrlToAssetTree = async (currentFrameDataUrl: string) => {
  if (!selectedEntryId.value) return null;
  const pendingPersisted = assetTreeDirty ? await saveAssetTreeNow() : await assetTreeSaveChain;
  if (!pendingPersisted) throw new Error('资产树存在并发冲突，请刷新后重试');
  const node = createAssetImageNode('');
  const insertTarget = savedFrameInsertTarget();
  const localVersion = assetTreeLocalVersion;
  const result = await saveFanxiuDataAnnotationFrame({
    entry_id: selectedEntryId.value,
    current_frame_data_url: currentFrameDataUrl,
    asset_node: node,
    parent_id: insertTarget.parentId || undefined,
    after_node_id: insertTarget.afterNodeId || undefined,
    base_revision: assetTreeBackendRevision.value,
  });
  await applySavedFrameTransaction(result, node, localVersion);
  setAssetImagePreviewUrl(assetImagePreviewKey(node), currentFrameDataUrl);
  return node;
};

const gameMacroFrameTargetText = (image: DataAnnotationAssetNode) => {
  const numeric = assetNumericImageId(image);
  return numeric !== null ? String(numeric) : image.title.trim();
};

const setGameMacroPendingJumpTarget = (targetImage: DataAnnotationAssetNode) => {
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

const isGameMacroFrameAsset = (image: DataAnnotationAssetNode) => (
  image.type === 'image' && Boolean(image.filename)
);

const matchGameMacroFrameCandidate = async (image: DataAnnotationAssetNode, currentFrameDataUrl: string) => {
  if (!selectedEntryId.value || !image.filename) return null;
  const width = image.width || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth;
  const height = image.height || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight;
  if (!width || !height) return null;
  const response = await matchFanxiuGameWindow2Screenshot({
    entry_id: selectedEntryId.value,
    filename: image.filename,
    box: { x: 0, y: 0, w: width, h: height },
    pixel_tolerance: visualMacroDefaultPixelTolerance.value,
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
  const score = Number(
    response.fixed_pixel_similarity
      ?? response.fixed_exact_pixel_similarity
      ?? response.fixed_similarity
      ?? response.similarity
      ?? 0,
  );
  return { image, score };
};

const matchGameMacroFrameCandidates = async (candidates: DataAnnotationAssetNode[], currentFrameDataUrl: string) => {
  let best: { image: DataAnnotationAssetNode; score: number } | null = null;
  for (const image of candidates) {
    try {
      const result = await matchGameMacroFrameCandidate(image, currentFrameDataUrl);
      if (result && (!best || result.score > best.score)) best = result;
    } catch {
      // 单个旧帧匹配失败不影响宏录制继续保存新帧。
    }
  }
  return best;
};

const findOrCreateGameMacroFrame = async (currentFrameDataUrl: string) => {
  const candidates = flattenAssetImages(assetTree.value)
    .filter(isGameMacroFrameAsset);
  if (candidates.length) {
    const best = await matchGameMacroFrameCandidates(candidates, currentFrameDataUrl);
    if (best && best.score >= GAME_MACRO_FRAME_MATCH_THRESHOLD) return { image: best.image, created: false, score: best.score };
  }
  const image = await saveFrameDataUrlToAssetTree(currentFrameDataUrl);
  if (!image) throw new Error('保存录制帧失败');
  return { image, created: true, score: 0 };
};

const boxToShapeRect = (
  image: DataAnnotationAssetNode,
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

const pointShapeBox = (point: VisualPoint, image: DataAnnotationAssetNode) => {
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

const dragShapeBox = (start: VisualPoint, end: VisualPoint, image: DataAnnotationAssetNode) => {
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

const dragDirectionOf = (start: VisualPoint, end: VisualPoint): NonNullable<DataAnnotationShape['loadDirection']> => {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left';
  return dy >= 0 ? 'down' : 'up';
};

const loadDirectionOfDrag = (start: VisualPoint, end: VisualPoint): DataAnnotationShape['loadDirection'] => ({
  up: 'down',
  down: 'up',
  left: 'right',
  right: 'left',
  none: 'none',
}[dragDirectionOf(start, end)] as DataAnnotationShape['loadDirection']);

const gameMacroDragDurationMs = (durationMs: number) => (
  gameMacroConfig.value.dragDurationMode === 'fixed'
    ? gameMacroConfig.value.defaultDragDurationMs
    : Math.round(durationMs)
);

const buildGameMacroFallbackBox = (
  image: DataAnnotationAssetNode,
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null,
): FanxiuGameWindow2MatchBox => {
  const box = action === 'drag' && endPoint ? dragShapeBox(point, endPoint, image) : pointShapeBox(point, image);
  return { name: action === 'drag' ? '拖拽' : '点击', ...box };
};

const gameMacroFrameSize = (image: DataAnnotationAssetNode) => ({
  width: image.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1,
  height: image.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1,
});

const applyGameMacroShapeAnnotation = (
  image: DataAnnotationAssetNode,
  shape: DataAnnotationShape,
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
  image: DataAnnotationAssetNode,
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null,
  durationMs: number,
  annotation?: GameMacroShapeAnnotation,
) => {
  image.shapes ??= [];
  const box = annotation?.box ?? buildGameMacroFallbackBox(image, action, point, endPoint);
  const rect = boxToShapeRect(image, box);
  const shape: DataAnnotationShape = {
    id: createAssetId('shape'),
    kind: 'shape',
    title: (annotation?.label || '').trim() || (action === 'drag' ? '拖拽' : '点击'),
    description: '',
    locked: false,
    floating: false,
    jitterEnabled: false,
    jitterRadius: 4,
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    loadDirection: action === 'drag' && endPoint ? loadDirectionOfDrag(point, endPoint) : 'none',
    imageMatchRole: 'off',
    pixelTolerance: DEFAULT_SHAPE_PIXEL_TOLERANCE,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    ocrMaskMode: 'inherit-envelope',
    ocrMask: null,
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
  image: DataAnnotationAssetNode,
  shape: DataAnnotationShape,
  action: 'click' | 'drag',
  point: VisualPoint,
  endPoint: VisualPoint | null,
  durationMs: number,
  currentFrameDataUrl: string,
  fallbackBox: FanxiuGameWindow2MatchBox,
) => {
  const size = gameMacroFrameSize(image);
  const direction = action === 'drag' && endPoint ? dragDirectionOf(point, endPoint) : 'none';
  let response: FanxiuDataAnnotationMacroAnnotateResponse | null = null;
  try {
    gameMacroStatusText.value = `录制宏：${shape.title} 已生成，AI 标注中`;
    response = await annotateFanxiuDataAnnotationMacroShape({
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
  if (assetTreeViewMode.value !== 'business') return;
  const node = selectedAssetNode.value;
  const parent = findAssetParentChildren(assetTree.value, selectedAssetId.value);
  if (!node || !parent) return;
  await ElMessageBox.confirm('删除“' + node.title + '”？', '删除节点', { type: 'warning' });
  const index = parent.findIndex((item) => item.id === node.id);
  if (index < 0) return;
  const scrollTop = assetTreeScrollRef.value?.scrollTop ?? 0;
  const deletedIds = new Set(collectAssetNodeIds(node));
  const parentFolder = findAssetParentFolder(assetTree.value, node.id);
  const fallbackNode = parent[index + 1] ?? parent[index - 1] ?? parentFolder ?? null;
  parent.splice(index, 1);
  expandedAssetNodeIds.value = expandedAssetNodeIds.value.filter((id) => !deletedIds.has(id));
  selectedAssetId.value = fallbackNode?.id ?? null;
  selectedShapeId.value = null;
  selectedShapeIds.value = [];
  await nextTick();
  if (assetTreeScrollRef.value) assetTreeScrollRef.value.scrollTop = scrollTop;
};

const toggleAssetFolderNode = (id: string) => {
  const treeNode = assetTreeRef.value?.getNode(id);
  if (!treeNode) {
    setAssetNodeExpanded(id, !expandedAssetNodeIds.value.includes(id));
    return;
  }
  if (treeNode.expanded) {
    setAssetNodeExpanded(id, false);
    treeNode.collapse?.();
  } else {
    setAssetNodeExpanded(id, true);
    treeNode.expand?.();
  }
};

const isAssetTreeExpandIconClick = (event: MouseEvent | undefined) => {
  const target = event?.target;
  return target instanceof Element && Boolean(target.closest('.el-tree-node__expand-icon'));
};

const selectAssetNode = (node: DataAnnotationAssetNode, _treeNode?: unknown, _component?: unknown, event?: MouseEvent) => {
  closeAssetContextMenu();
  const actualNode = findAssetNode(assetTree.value, node.id);
  if (isVirtualAssetTreeNode(node) && !actualNode) {
    if (!isAssetTreeExpandIconClick(event)) toggleAssetFolderNode(node.id);
    return;
  }
  selectedAssetId.value = actualNode?.id ?? node.id;
  if (node.type === 'folder') {
    if (!isAssetTreeExpandIconClick(event)) toggleAssetFolderNode(node.id);
    return;
  }
  void refreshEntryAssetTreeIfChanged();
};

let assetTreeDirectionKeyFrame: number | null = null;
const handleAssetTreeDirectionKey = (event: KeyboardEvent) => {
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (assetTreeDirectionKeyFrame !== null) window.cancelAnimationFrame(assetTreeDirectionKeyFrame);
  assetTreeDirectionKeyFrame = window.requestAnimationFrame(() => {
    assetTreeDirectionKeyFrame = null;
    const activeElement = document.activeElement;
    if (!(activeElement instanceof HTMLElement) || !assetTreeScrollRef.value?.contains(activeElement)) return;
    const treeItem = activeElement.closest<HTMLElement>('[role="treeitem"]');
    const treeNodeKey = treeItem?.dataset.key;
    if (!treeNodeKey) return;
    const node = assetTreeRef.value?.getNode(treeNodeKey)?.data as DataAnnotationAssetNode | undefined;
    if (!node) return;
    const actualNode = findAssetNode(assetTree.value, node.id);
    if (isVirtualAssetTreeNode(node) && !actualNode) return;
    selectedAssetId.value = actualNode?.id ?? node.id;
    if (node.type === 'image') void refreshEntryAssetTreeIfChanged();
  });
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

const openAssetContextMenu = (event: MouseEvent, node: DataAnnotationAssetNode) => {
  event.preventDefault();
  if (assetTreeViewMode.value !== 'business') {
    closeAssetContextMenu();
    return;
  }
  const actualNode = findAssetNode(assetTree.value, node.id);
  if (isVirtualAssetTreeNode(node) && !actualNode) {
    closeAssetContextMenu();
    return;
  }
  selectedAssetId.value = actualNode?.id ?? node.id;
  assetContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    nodeId: actualNode?.id ?? node.id,
  };
};

const findDisplayAssetParentId = (
  nodes: DataAnnotationAssetNode[],
  id: string,
  parentId: string | null = null,
): string | null | undefined => {
  for (const node of nodes) {
    if (node.id === id) return parentId;
    const found = findDisplayAssetParentId(node.children ?? [], id, node.id);
    if (found !== undefined) return found;
  }
  return undefined;
};

const moveAssetChildWithinParent = (
  parentId: string,
  draggingId: string,
  dropId: string,
  type: 'prev' | 'next',
) => {
  const parent = findAssetNode(assetTree.value, parentId);
  const siblings = parent?.children;
  if (!Array.isArray(siblings)) return;
  const sourceIndex = siblings.findIndex((item) => item.id === draggingId);
  const targetIndex = siblings.findIndex((item) => item.id === dropId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return;
  const [item] = siblings.splice(sourceIndex, 1);
  const nextTargetIndex = siblings.findIndex((candidate) => candidate.id === dropId);
  siblings.splice(type === 'prev' ? nextTargetIndex : nextTargetIndex + 1, 0, item);
};

const isAssetDescendantOf = (
  nodes: DataAnnotationAssetNode[],
  childId: string,
  ancestorId: string,
): boolean => {
  const ancestor = findAssetNode(nodes, ancestorId);
  if (!ancestor) return false;
  return Boolean(findAssetNode(ancestor.children ?? [], childId));
};

const moveAssetImageIntoParentImage = (draggingId: string, parentId: string) => {
  if (draggingId === parentId || isAssetDescendantOf(assetTree.value, parentId, draggingId)) return false;
  const parent = findAssetNode(assetTree.value, parentId);
  if (parent?.type !== 'image') return false;
  const sourceSiblings = findAssetParentChildren(assetTree.value, draggingId);
  if (!sourceSiblings) return false;
  const sourceIndex = sourceSiblings.findIndex((item) => item.id === draggingId);
  if (sourceIndex < 0) return false;
  const [item] = sourceSiblings.splice(sourceIndex, 1);
  parent.children = parent.children ?? [];
  parent.children.push(item);
  return true;
};

const allowAssetDrop = (
  draggingNode: { data?: DataAnnotationAssetNode },
  dropNode: { data?: DataAnnotationAssetNode },
  type: 'prev' | 'inner' | 'next',
) => {
  if (assetTreeViewMode.value === 'business') {
    return !isVirtualAssetTreeNode(draggingNode.data) && !isVirtualAssetTreeNode(dropNode.data);
  }
  if (assetTreeViewMode.value !== 'scene') return false;
  const dragging = draggingNode.data;
  const drop = dropNode.data;
  if (!dragging || !drop || dragging.type !== 'image' || drop.type !== 'image') return false;
  if (type === 'inner') {
    return dragging.id !== drop.id && !isAssetDescendantOf(assetTree.value, drop.id, dragging.id);
  }
  const draggingParentId = findDisplayAssetParentId(assetTreeDisplayData.value, dragging.id);
  const dropParentId = findDisplayAssetParentId(assetTreeDisplayData.value, drop.id);
  if (!draggingParentId || draggingParentId !== dropParentId) return false;
  return Boolean(findAssetNode(assetTree.value, draggingParentId));
};

const handleAssetNodeDrop = (
  draggingNode: { data?: DataAnnotationAssetNode },
  dropNode: { data?: DataAnnotationAssetNode },
  type: 'before' | 'after' | 'inner',
) => {
  if (assetTreeViewMode.value !== 'scene') return;
  const dragging = draggingNode.data;
  const drop = dropNode.data;
  if (!dragging || !drop || dragging.type !== 'image' || drop.type !== 'image') return;
  if (type === 'inner') {
    if (!moveAssetImageIntoParentImage(dragging.id, drop.id)) return;
    assetTree.value = [...assetTree.value];
    selectedAssetId.value = dragging.id;
    return;
  }
  const dropType = type === 'before' ? 'prev' : (type === 'after' ? 'next' : null);
  if (!dropType) return;
  const parentId = findDisplayAssetParentId(assetTreeDisplayData.value, drop.id);
  if (!parentId || parentId !== findDisplayAssetParentId(assetTreeDisplayData.value, dragging.id)) return;
  moveAssetChildWithinParent(parentId, dragging.id, drop.id, dropType);
  assetTree.value = [...assetTree.value];
  selectedAssetId.value = dragging.id;
};

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

const renameAssetNode = async (node: DataAnnotationAssetNode) => {
  if (isVirtualAssetTreeNode(node)) return;
  selectedAssetId.value = node.id;
  closeAssetContextMenu();
  const nodeKindText = node.type === 'folder' ? '分组' : '图片昵称';
  try {
    const prompt = ElMessageBox.prompt(nodeKindText + '名称', '重命名' + nodeKindText, {
      inputValue: node.title,
      inputPattern: node.type === 'folder' ? /\S+/ : undefined,
      inputErrorMessage: node.type === 'folder' ? '请输入分组名称' : undefined,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    });
    void selectAssetRenameInputText();
    const result = await prompt;
    const nextTitle = String(result.value ?? '').trim();
    if (node.type === 'image' || nextTitle) node.title = nextTitle;
  } catch {
    // User cancelled.
  }
};

const renameDisplayAssetNode = (node: DataAnnotationAssetNode) => {
  if (assetTreeViewMode.value !== 'business') return;
  const actualNode = findAssetNode(assetTree.value, node.id);
  if (!actualNode) return;
  void renameAssetNode(actualNode);
};

const resetAssetFrameFromContextMenu = async () => {
  selectedAssetId.value = assetContextMenu.value.nodeId || selectedAssetId.value;
  const node = selectedAssetNode.value;
  closeAssetContextMenu();
  await resetAssetFrameWithConfirm(node);
};

const resetSelectedAssetFrameFromHint = async () => {
  await resetAssetFrameWithConfirm(selectedImageNode.value);
};

const resetAssetFrameWithConfirm = async (node: DataAnnotationAssetNode | null) => {
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
  const shape: DataAnnotationShape = {
    id: createAssetId('shape'),
    kind: 'shape',
    title: 'shape ' + (flattenShapes(image.shapes).filter(isDrawableShape).length + 1),
    description: '',
    locked: false,
    floating: false,
    jitterEnabled: false,
    jitterRadius: 4,
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    loadDirection: 'none',
    imageMatchRole: 'off',
    pixelTolerance: DEFAULT_SHAPE_PIXEL_TOLERANCE,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    ocrMaskMode: 'inherit-envelope',
    ocrMask: null,
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
    .filter((shape): shape is DataAnnotationShape => Boolean(shape));
  return selected.filter((shape) => !selected.some((other) => other.id !== shape.id && isShapeDescendantOf(other, shape.id)));
};

const removeShapesByIds = (shapes: DataAnnotationShape[], ids: Set<string>) => {
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
  const pendingDeleteKeys: string[] = [];
  for (const shape of flattenShapes(targets)) {
    pendingDeleteKeys.push(shape.id, shapeDeleteIdKey(shape.id));
  }
  for (const key of pendingDeleteKeys) deletedShapeIds.value.add(key);
  persistDeletedShapeIds();
  removeShapesByIds(image.shapes, new Set(targets.map((shape) => shape.id)));
  image.shapes = filterDeletedShapesForImage(image, image.shapes);
  selectedShapeIds.value = [];
  selectedShapeId.value = flattenShapes(image.shapes)[0]?.id ?? null;
  shapeSelectionAnchorId.value = selectedShapeId.value;
  if (selectedEntryId.value) {
    assetTree.value = filterDeletedShapesFromAssetTree(assetTree.value);
    void saveAssetTreeNow().then((persisted) => {
      if (!persisted) return;
      for (const key of pendingDeleteKeys) deletedShapeIds.value.delete(key);
      persistDeletedShapeIds();
    });
  }
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
  data: DataAnnotationShape,
  _node: unknown,
  _component: unknown,
  event?: MouseEvent,
) => {
  selectShape(data.id, event);
};

const copySelectedShapes = () => {
  const targets = selectedShapeRoots();
  if (!targets.length) return;
  copiedShapes.value = targets.map((shape) => JSON.parse(JSON.stringify(shape)) as DataAnnotationShape);
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

const openShapeTreeContextMenu = (event: MouseEvent, data: DataAnnotationShape) => {
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

const loadRuntimeLogs = async () => {
  try {
    const response = await getFanxiuBehaviorTreeRuntimeLogs(RUNTIME_LOG_PREVIEW_LIMIT);
    runtimeLogs.value = [...response.entries].reverse();
    goRuntimeLogFirstPage();
  } catch {
    // 日志读取失败不影响页面主体功能。
  }
};

const clearRuntimeLogs = async () => {
  const response = await clearFanxiuBehaviorTreeRuntimeLogs();
  runtimeLogs.value = [...response.entries].reverse();
  goRuntimeLogFirstPage();
};

const loadRuntimeFacts = async () => {
  runtimeFactsLoading.value = true;
  try {
    const response = await getFanxiuDataAnnotationWorldFacts();
    runtimeFactsPath.value = response.path;
    runtimeFactsJson.value = JSON.stringify(response.facts, null, 2);
  } catch (error) {
    runtimeFactsJson.value = JSON.stringify({ error: getErrorMessage(error) }, null, 2);
  } finally {
    runtimeFactsLoading.value = false;
  }
};

const openRuntimeFactsDialog = async () => {
  runtimeFactsDialogVisible.value = true;
  await loadRuntimeFacts();
};

const loadRuntimePlan = async () => {
  runtimePlanLoading.value = true;
  try {
    const response = await getFanxiuDataAnnotationSchedulerPlan();
    runtimePlanPath.value = response.path;
    runtimePlanJson.value = JSON.stringify(response, null, 2);
  } catch (error) {
    runtimePlanJson.value = JSON.stringify({ error: getErrorMessage(error) }, null, 2);
  } finally {
    runtimePlanLoading.value = false;
  }
};

const openRuntimePlanDialog = async () => {
  runtimePlanDialogVisible.value = true;
  await loadRuntimePlan();
};

const setRuntimeRunStatus = (message: string, _kind: RuntimeLogKind | 'start' | 'stop' | 'wait' = 'detail') => {
  runtimeRunStatus.value = message;
};

const applyRuntimeTaskStatus = (status: FanxiuBehaviorTreeRuntimeStatus) => {
  runtimeTaskStatus.value = status;
  runtimeRunStatus.value = status.message || status.status || '';
  if (status.logs?.length) {
    runtimeLogs.value = status.logs.map((entry, index) => ({
      id: entry.id || `runtime-${index}`,
      time: entry.time,
      kind: entry.kind as RuntimeLogKind,
      message: entry.message,
      ts: entry.ts || '',
    })).reverse();
    goRuntimeLogFirstPage();
  }
};

const refreshRuntimeTaskStatus = async () => {
  try {
    const status = await getFanxiuBehaviorTreeRuntimeStatus();
    applyRuntimeTaskStatus(status);
  } catch {
    // Runtime 调试状态不是页面主数据，静默等待下一次刷新或用户操作。
  }
};

const loadRuntimeSchedulerTasks = async () => {
  runtimeSchedulerLoading.value = true;
  try {
    const response = await getFanxiuDataAnnotationSchedulerTasks();
    runtimeSchedulerTasks.value = response.tasks;
    if (!selectedRuntimeTaskType.value || !response.tasks.some((task) => task.task_type === selectedRuntimeTaskType.value)) {
      selectedRuntimeTaskType.value = response.tasks[0]?.task_type ?? '';
    }
    if (!selectedRuntimeTaskId.value || !response.tasks.some((task) => task.id === selectedRuntimeTaskId.value && task.task_type === selectedRuntimeTaskType.value)) {
      selectedRuntimeTaskId.value = response.tasks.find((task) => task.task_type === selectedRuntimeTaskType.value)?.id ?? response.tasks[0]?.id ?? '';
    }
  } catch (error) {
    setRuntimeRunStatus(`Scheduler 任务读取失败：${getErrorMessage(error)}`, 'error');
  } finally {
    runtimeSchedulerLoading.value = false;
  }
};

const ensureRuntimeSchedulerTasks = async () => {
  if (runtimeSchedulerTasks.value.length || runtimeSchedulerLoading.value) return;
  await loadRuntimeSchedulerTasks();
};

const stopRuntimeTaskPolling = () => {
  if (runtimeTaskPollTimer !== null) {
    window.clearTimeout(runtimeTaskPollTimer);
    runtimeTaskPollTimer = null;
  }
};

const pollRuntimeTask = async () => {
  stopRuntimeTaskPolling();
  try {
    const status = await getFanxiuBehaviorTreeRuntimeStatus();
    applyRuntimeTaskStatus(status);
    if (status.running || status.status === 'stopping') {
      runtimeTaskPollTimer = window.setTimeout(() => {
        void pollRuntimeTask();
      }, 1000);
      return;
    }
    runtimeRunning.value = false;
    runtimeStopRequested.value = false;
    if (status.status === 'success') ElMessage.success(status.message || '兑换礼包码任务完成');
    if (status.status === 'error') ElMessage.error(status.message || '兑换礼包码任务失败');
  } catch (error) {
    runtimeRunning.value = false;
    runtimeStopRequested.value = false;
    setRuntimeRunStatus(getErrorMessage(error), 'error');
  }
};

const runRuntimeTaskDefinition = async (task: FanxiuDataAnnotationSchedulerTaskItem) => {
  if (!selectedEntryId.value) return;
  const payloadOverride = buildRuntimeTaskPayloadOverride(task);
  runtimeRunning.value = true;
  runtimeStopRequested.value = false;
  runtimeLogs.value = [];
  setRuntimeRunStatus(`Scheduler 手动任务：${task.label}`, 'start');
  try {
    const status = await runNowFanxiuDataAnnotationSchedulerTask(
      selectedEntryId.value,
      task.id,
      payloadOverride,
      true,
      'current',
    );
    applyRuntimeTaskStatus(status);
    await pollRuntimeTask();
    void loadRuntimeSchedulerTasks();
  } catch (error) {
    runtimeRunning.value = false;
    runtimeStopRequested.value = false;
    setRuntimeRunStatus(getErrorMessage(error), 'error');
    ElMessage.error(runtimeRunStatus.value);
  }
};

const runRuntimeSelectedTask = async () => {
  if (!selectedEntryId.value || runtimeRunning.value) return;
  await ensureRuntimeSchedulerTasks();
  const taskDefinition = selectedRuntimeTaskDefinition.value;
  if (!taskDefinition) return;
  await runRuntimeTaskDefinition(taskDefinition);
};

const runRuntimeDueTasks = async () => {
  if (!selectedEntryId.value || runtimeRunning.value) return;
  await ensureRuntimeSchedulerTasks();
  runtimeRunning.value = true;
  runtimeStopRequested.value = false;
  runtimeLogs.value = [];
  setRuntimeRunStatus('Scheduler：执行全部到期任务', 'start');
  try {
    const status = await runDueFanxiuDataAnnotationSchedulerTasks(selectedEntryId.value);
    applyRuntimeTaskStatus(status);
    if (status.running || status.status === 'stopping') await pollRuntimeTask();
    else runtimeRunning.value = false;
  } catch (error) {
    runtimeRunning.value = false;
    runtimeStopRequested.value = false;
    setRuntimeRunStatus(getErrorMessage(error), 'error');
    ElMessage.error(runtimeRunStatus.value);
  }
};

const stopRuntimeTask = async () => {
  runtimeStopRequested.value = true;
  setRuntimeRunStatus('正在停止当前任务', 'stop');
  try {
    const status = await stopFanxiuBehaviorTreeRuntimeCurrentTask(selectedEntryId.value);
    applyRuntimeTaskStatus(status);
  } catch {
    // 停止失败时保留本地停止标记，下一轮轮询会同步真实状态。
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

const cropImageDataUrlByShape = async (imageDataUrl: string, shape: DataAnnotationShape | null, width: number, height: number) => {
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

const cropLiveImageDataByShape = (shape: DataAnnotationShape, width: number, height: number) => {
  const image = streamImageRef.value;
  if (!image || !image.naturalWidth || !image.naturalHeight) return null;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
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

const loadShapeAlphaMask = async (
  shape: DataAnnotationShape,
  width: number,
  height: number,
  target: ShapeMaskTarget = 'image',
) => {
  if (target === 'ocr') {
    if (!shape.ocrMask?.dataUrl) return null;
    return loadAlphaMaskDataUrl(shape.ocrMask.dataUrl, width, height);
  }
  if (!shape.maskEnabled || !shape.alphaMask?.dataUrl) return null;
  return loadAlphaMaskDataUrl(shape.alphaMask.dataUrl, width, height);
};

const loadAlphaMaskDataUrl = async (dataUrl: string, width: number, height: number) => {
  if (!dataUrl) return null;
  const image = await loadMaskImage(dataUrl);
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

const isFullShapeMaskAlpha = (alpha: Uint8ClampedArray | null | undefined) => {
  if (!alpha) return false;
  for (const value of alpha) {
    if (value !== 255) return false;
  }
  return true;
};

const cropImageDataUrlToShape = async (imageDataUrl: string, width: number, height: number) => {
  return cropImageDataUrlByShape(imageDataUrl, selectedShape.value, width, height);
};

const captureLiveShapeImageData = async (width: number, height: number) => {
  const shape = selectedShape.value;
  const selectedImage = selectedImageNode.value;
  if (!shape) return null;
  if (!shape.floating) return cropLiveImageDataByShape(shape, width, height);
  const currentFrameDataUrl = captureCurrentLiveFrameDataUrl();
  if (!currentFrameDataUrl) return null;
  if (shape.floating && selectedImage?.filename) {
    const response = await matchRuntimeShape(selectedImage, shape, currentFrameDataUrl);
    if (!response) return null;
    const best = bestRuntimeShapeMatchOf(shape, response, selectedImage);
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
    const volatilityAlpha = volatility <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((volatility - shapeMaskThreshold.value) * 5));
    const difference = stats.diffMax[index];
    const differenceAlpha = difference <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((difference - shapeMaskThreshold.value) * 5));
    const alpha = Math.min(stats.baseAlpha?.[index] ?? 255, volatilityAlpha, differenceAlpha);
    const offset = index * 4;
    maskImage.data[offset] = alpha;
    maskImage.data[offset + 1] = alpha;
    maskImage.data[offset + 2] = alpha;
    maskImage.data[offset + 3] = 255;
    resultImage.data[offset + 3] = alpha;
  }
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(maskImage);
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(resultImage);
  scheduleShapeMaskManualCanvasRender();
};

const currentShapeMaskAlpha = () => {
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return null;
  const total = stats.width * stats.height;
  const alpha = new Uint8ClampedArray(total);
  for (let index = 0; index < total; index += 1) {
    const volatility = stats.max[index] - stats.min[index];
    const volatilityAlpha = volatility <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((volatility - shapeMaskThreshold.value) * 5));
    const difference = stats.diffMax[index];
    const differenceAlpha = difference <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((difference - shapeMaskThreshold.value) * 5));
    alpha[index] = Math.min(stats.baseAlpha?.[index] ?? 255, volatilityAlpha, differenceAlpha);
  }
  return alpha;
};

const renderShapeMaskManualCanvas = () => {
  if (!shapeMaskManualVisible.value) return;
  const stats = shapeMaskStats.value;
  const canvas = shapeMaskManualCanvasRef.value;
  if (!stats?.reference || !canvas) return;
  const alpha = currentShapeMaskAlpha();
  if (!alpha) return;
  const zoom = shapeMaskManualZoom.value;
  const canvasWidth = Math.max(1, Math.round(stats.width * zoom));
  const canvasHeight = Math.max(1, Math.round(stats.height * zoom));
  if (canvas.width !== canvasWidth) canvas.width = canvasWidth;
  if (canvas.height !== canvasHeight) canvas.height = canvasHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const previewCanvas = document.createElement('canvas');
  previewCanvas.width = stats.width;
  previewCanvas.height = stats.height;
  const previewCtx = previewCanvas.getContext('2d');
  if (!previewCtx) return;
  previewCtx.putImageData(applyAlphaToPreview(stats.reference, alpha), 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(previewCanvas, 0, 0, canvas.width, canvas.height);
};

const scheduleShapeMaskManualCanvasRender = () => {
  if (!shapeMaskManualVisible.value) return;
  void nextTick(() => renderShapeMaskManualCanvas());
};

const commitShapeMaskManualAlpha = (alpha: Uint8ClampedArray) => {
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return;
  stats.baseAlpha = alpha;
  stats.min.fill(255);
  stats.max.fill(0);
  stats.diffMax.fill(0);
  shapeMaskResetToEmpty.value = isFullShapeMaskAlpha(alpha);
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(alphaToMaskImageData(alpha, stats.width, stats.height));
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(stats.reference, alpha));
  scheduleShapeMaskManualCanvasRender();
};

const pushShapeMaskManualUndo = () => {
  const alpha = currentShapeMaskAlpha();
  if (!alpha) return;
  const stack = shapeMaskManualUndoStack.value;
  stack.push(new Uint8ClampedArray(alpha));
  if (stack.length > 30) stack.shift();
  shapeMaskManualRedoStack.value = [];
};

const ensureShapeMaskManualBaseAlpha = () => {
  const alpha = currentShapeMaskAlpha();
  if (!alpha) return false;
  commitShapeMaskManualAlpha(new Uint8ClampedArray(alpha));
  return true;
};

const toggleShapeMaskManualEditor = async () => {
  shapeMaskManualVisible.value = !shapeMaskManualVisible.value;
  if (!shapeMaskManualVisible.value) {
    shapeMaskManualPointer.value = null;
    shapeMaskManualPanState.value = null;
    return;
  }
  pauseShapeMaskSampling();
  if (!shapeMaskStats.value) await initializeShapeMaskSampling(true);
  shapeMaskManualUndoStack.value = [];
  shapeMaskManualRedoStack.value = [];
  ensureShapeMaskManualBaseAlpha();
  await nextTick();
  renderShapeMaskManualCanvas();
};

const setShapeMaskManualZoom = async (
  value: number,
  options?: { anchorClientX?: number; anchorClientY?: number },
) => {
  const wrap = shapeMaskManualCanvasWrapRef.value;
  const currentZoom = shapeMaskManualZoom.value;
  const nextZoom = clamp(Math.round(Number(value) * 4) / 4, 1, 8);
  if (!Number.isFinite(nextZoom) || nextZoom === currentZoom) return;
  if (!wrap) {
    shapeMaskManualZoom.value = nextZoom;
    scheduleShapeMaskManualCanvasRender();
    return;
  }
  const rect = wrap.getBoundingClientRect();
  const anchorClientX = options?.anchorClientX ?? (rect.left + rect.width / 2);
  const anchorClientY = options?.anchorClientY ?? (rect.top + rect.height / 2);
  const anchorX = anchorClientX - rect.left + wrap.scrollLeft;
  const anchorY = anchorClientY - rect.top + wrap.scrollTop;
  const imageX = anchorX / currentZoom;
  const imageY = anchorY / currentZoom;
  shapeMaskManualZoom.value = nextZoom;
  await nextTick();
  renderShapeMaskManualCanvas();
  wrap.scrollLeft = Math.max(0, imageX * nextZoom - (anchorClientX - rect.left));
  wrap.scrollTop = Math.max(0, imageY * nextZoom - (anchorClientY - rect.top));
};

const handleShapeMaskManualWheel = (event: WheelEvent) => {
  if (!(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const delta = event.deltaY > 0 ? -0.25 : 0.25;
  void setShapeMaskManualZoom(shapeMaskManualZoom.value + delta, {
    anchorClientX: event.clientX,
    anchorClientY: event.clientY,
  });
};

const shapeMaskManualPointOf = (event: PointerEvent) => {
  const stats = shapeMaskStats.value;
  const canvas = shapeMaskManualCanvasRef.value;
  if (!stats || !canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: clamp(Math.floor((event.clientX - rect.left) * (stats.width / rect.width)), 0, stats.width - 1),
    y: clamp(Math.floor((event.clientY - rect.top) * (stats.height / rect.height)), 0, stats.height - 1),
  };
};

const paintShapeMaskManualPoint = (alpha: Uint8ClampedArray, x: number, y: number) => {
  const stats = shapeMaskStats.value;
  if (!stats) return;
  const radius = Math.max(0.5, shapeMaskManualBrushSize.value / 2);
  const radiusSquared = radius * radius;
  const value = shapeMaskManualTool.value === 'erase' ? 0 : 255;
  const left = Math.max(0, Math.floor(x - radius));
  const right = Math.min(stats.width - 1, Math.ceil(x + radius));
  const top = Math.max(0, Math.floor(y - radius));
  const bottom = Math.min(stats.height - 1, Math.ceil(y + radius));
  for (let yy = top; yy <= bottom; yy += 1) {
    for (let xx = left; xx <= right; xx += 1) {
      const dx = xx - x;
      const dy = yy - y;
      if (dx * dx + dy * dy > radiusSquared) continue;
      alpha[yy * stats.width + xx] = value;
    }
  }
};

const paintShapeMaskManualLine = (from: { x: number; y: number }, to: { x: number; y: number }) => {
  const stats = shapeMaskStats.value;
  if (!stats?.baseAlpha) return;
  const alpha = new Uint8ClampedArray(stats.baseAlpha);
  const distance = Math.max(Math.abs(to.x - from.x), Math.abs(to.y - from.y), 1);
  for (let step = 0; step <= distance; step += 1) {
    const ratio = step / distance;
    const x = from.x + (to.x - from.x) * ratio;
    const y = from.y + (to.y - from.y) * ratio;
    paintShapeMaskManualPoint(alpha, x, y);
  }
  commitShapeMaskManualAlpha(alpha);
};

const handleShapeMaskManualPointerDown = (event: PointerEvent) => {
  const wrap = shapeMaskManualCanvasWrapRef.value;
  const shouldPan = event.button === 1 || (event.button === 0 && screenshotSpacePressed.value);
  if (shouldPan && wrap) {
    event.preventDefault();
    shapeMaskManualPanState.value = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startScrollLeft: wrap.scrollLeft,
      startScrollTop: wrap.scrollTop,
    };
    shapeMaskManualCanvasRef.value?.setPointerCapture(event.pointerId);
    return;
  }
  if (event.button !== 0) return;
  const point = shapeMaskManualPointOf(event);
  if (!point || !ensureShapeMaskManualBaseAlpha()) return;
  pushShapeMaskManualUndo();
  shapeMaskManualPointer.value = {
    pointerId: event.pointerId,
    x: point.x,
    y: point.y,
  };
  shapeMaskManualCanvasRef.value?.setPointerCapture(event.pointerId);
  paintShapeMaskManualLine(point, point);
};

const handleShapeMaskManualPointerMove = (event: PointerEvent) => {
  const panState = shapeMaskManualPanState.value;
  const wrap = shapeMaskManualCanvasWrapRef.value;
  if (panState && panState.pointerId === event.pointerId && wrap) {
    wrap.scrollLeft = panState.startScrollLeft - (event.clientX - panState.startClientX);
    wrap.scrollTop = panState.startScrollTop - (event.clientY - panState.startClientY);
    return;
  }
  const state = shapeMaskManualPointer.value;
  const point = shapeMaskManualPointOf(event);
  if (!state || state.pointerId !== event.pointerId || !point) return;
  paintShapeMaskManualLine({ x: state.x, y: state.y }, point);
  shapeMaskManualPointer.value = {
    pointerId: event.pointerId,
    x: point.x,
    y: point.y,
  };
};

const handleShapeMaskManualPointerUp = (event: PointerEvent) => {
  const panState = shapeMaskManualPanState.value;
  if (panState && panState.pointerId === event.pointerId) {
    shapeMaskManualPanState.value = null;
    shapeMaskManualCanvasRef.value?.releasePointerCapture(event.pointerId);
    return;
  }
  const state = shapeMaskManualPointer.value;
  if (!state || state.pointerId !== event.pointerId) return;
  shapeMaskManualPointer.value = null;
  shapeMaskManualCanvasRef.value?.releasePointerCapture(event.pointerId);
};

const undoShapeMaskManual = () => {
  const alpha = currentShapeMaskAlpha();
  const previous = shapeMaskManualUndoStack.value.pop();
  if (!previous || !alpha) return;
  shapeMaskManualRedoStack.value.push(new Uint8ClampedArray(alpha));
  commitShapeMaskManualAlpha(previous);
};

const redoShapeMaskManual = () => {
  const alpha = currentShapeMaskAlpha();
  const next = shapeMaskManualRedoStack.value.pop();
  if (!next || !alpha) return;
  shapeMaskManualUndoStack.value.push(new Uint8ClampedArray(alpha));
  commitShapeMaskManualAlpha(next);
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

const shapeMaskPixelStats = (reference: ImageData, index: number) => {
  const offset = index * 4;
  const r = reference.data[offset];
  const g = reference.data[offset + 1];
  const b = reference.data[offset + 2];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  return {
    r,
    g,
    b,
    luma: r * 0.299 + g * 0.587 + b * 0.114,
    saturation: max - min,
  };
};

const estimateShapeMaskBackground = (
  alpha: Uint8ClampedArray,
  reference: ImageData,
  width: number,
  height: number,
) => {
  const border = Math.max(1, Math.min(6, Math.round(Math.min(width, height) * 0.12)));
  const pixels: Array<{ r: number; g: number; b: number; luma: number; saturation: number }> = [];
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (x >= border && y >= border && x < width - border && y < height - border) continue;
      const index = y * width + x;
      if (alpha[index] < 80) continue;
      pixels.push(shapeMaskPixelStats(reference, index));
    }
  }
  if (!pixels.length) return { r: 0, g: 0, b: 0, luma: 0, saturation: 0 };
  pixels.sort((a, b) => a.luma - b.luma);
  const backgroundPixels = pixels.slice(0, Math.max(1, Math.ceil(pixels.length * 0.65)));
  let r = 0;
  let g = 0;
  let b = 0;
  for (const pixel of backgroundPixels) {
    r += pixel.r;
    g += pixel.g;
    b += pixel.b;
  }
  r /= backgroundPixels.length;
  g /= backgroundPixels.length;
  b /= backgroundPixels.length;
  return {
    r,
    g,
    b,
    luma: r * 0.299 + g * 0.587 + b * 0.114,
    saturation: Math.max(r, g, b) - Math.min(r, g, b),
  };
};

const dilateShapeMaskKeep = (
  source: Uint8Array,
  alpha: Uint8ClampedArray,
  width: number,
  height: number,
  radius: number,
) => {
  const total = width * height;
  const result = new Uint8Array(source);
  for (let index = 0; index < total; index += 1) {
    if (!source[index]) continue;
    const x = index % width;
    const y = Math.floor(index / width);
    for (let yy = Math.max(0, y - radius); yy <= Math.min(height - 1, y + radius); yy += 1) {
      for (let xx = Math.max(0, x - radius); xx <= Math.min(width - 1, x + radius); xx += 1) {
        const next = yy * width + xx;
        if (alpha[next] >= 80) result[next] = 1;
      }
    }
  }
  return result;
};

const keepLargestShapeMaskComponents = (
  keep: Uint8Array,
  width: number,
  height: number,
) => {
  const total = width * height;
  const visited = new Uint8Array(total);
  const components: number[][] = [];
  for (let start = 0; start < total; start += 1) {
    if (!keep[start] || visited[start]) continue;
    const queue = [start];
    const component: number[] = [];
    visited[start] = 1;
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
        if (next < 0 || visited[next] || !keep[next]) continue;
        visited[next] = 1;
        queue.push(next);
      }
    }
    components.push(component);
  }
  components.sort((a, b) => b.length - a.length);
  const minKeepSize = Math.max(4, Math.round(total * 0.002));
  const result = new Uint8Array(total);
  for (const component of components.slice(0, 12)) {
    if (component.length < minKeepSize) continue;
    for (const index of component) result[index] = 1;
  }
  return result;
};

const cleanAlphaSemanticForeground = (
  alpha: Uint8ClampedArray,
  reference: ImageData,
  width: number,
  height: number,
) => {
  const total = width * height;
  const bg = estimateShapeMaskBackground(alpha, reference, width, height);

  const colorDistance = (left: ReturnType<typeof shapeMaskPixelStats>, right: ReturnType<typeof shapeMaskPixelStats>) => Math.max(
    Math.abs(left.r - right.r),
    Math.abs(left.g - right.g),
    Math.abs(left.b - right.b),
  );

  const background = new Uint8Array(total);
  const queue: number[] = [];
  const markBackground = (index: number) => {
    if (index < 0 || background[index]) return;
    background[index] = 1;
    queue.push(index);
  };
  for (let x = 0; x < width; x += 1) {
    markBackground(x);
    markBackground((height - 1) * width + x);
  }
  for (let y = 0; y < height; y += 1) {
    markBackground(y * width);
    markBackground(y * width + width - 1);
  }

  const canFloodBackground = (from: number, to: number) => {
    if (to < 0 || background[to]) return false;
    if (alpha[to] < 80) return true;
    const current = shapeMaskPixelStats(reference, from);
    const next = shapeMaskPixelStats(reference, to);
    const bgDistance = colorDistance(next, bg);
    const localDistance = colorDistance(current, next);
    const lumaDistance = Math.abs(next.luma - bg.luma);
    const saturationDistance = Math.abs(next.saturation - bg.saturation);
    return (
      bgDistance <= 34
      || (bgDistance <= 48 && lumaDistance <= 26 && saturationDistance <= 34)
      || (localDistance <= 18 && bgDistance <= 58)
    );
  };

  for (let head = 0; head < queue.length; head += 1) {
    const index = queue[head];
    const x = index % width;
    const y = Math.floor(index / width);
    const neighbors = [
      x > 0 ? index - 1 : -1,
      x < width - 1 ? index + 1 : -1,
      y > 0 ? index - width : -1,
      y < height - 1 ? index + width : -1,
      x > 0 && y > 0 ? index - width - 1 : -1,
      x < width - 1 && y > 0 ? index - width + 1 : -1,
      x > 0 && y < height - 1 ? index + width - 1 : -1,
      x < width - 1 && y < height - 1 ? index + width + 1 : -1,
    ];
    for (const next of neighbors) {
      if (canFloodBackground(index, next)) markBackground(next);
    }
  }

  const candidate = new Uint8Array(total);
  for (let index = 0; index < total; index += 1) {
    candidate[index] = alpha[index] >= 80 && !background[index] ? 1 : 0;
  }

  const visited = new Uint8Array(total);
  const keep = new Uint8Array(total);
  const minComponentSize = Math.max(2, Math.round(total * 0.0002));
  for (let start = 0; start < total; start += 1) {
    if (!candidate[start] || visited[start]) continue;
    const component: number[] = [];
    const componentQueue = [start];
    visited[start] = 1;
    for (let head = 0; head < componentQueue.length; head += 1) {
      const index = componentQueue[head];
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
        if (next < 0 || visited[next] || !candidate[next]) continue;
        visited[next] = 1;
        componentQueue.push(next);
      }
    }
    if (component.length < minComponentSize) continue;
    for (const index of component) keep[index] = 1;
  }

  const cleaned = new Uint8ClampedArray(total);
  for (let index = 0; index < total; index += 1) {
    if (keep[index]) cleaned[index] = 255;
  }
  return cleaned;
};

const applyShapeMaskBackgroundCleanup = (commitBase: boolean) => {
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return;
  const alpha = currentShapeMaskAlpha();
  if (!alpha) return;
  const cleaned = cleanAlphaSemanticForeground(alpha, stats.reference, stats.width, stats.height);
  if (!commitBase) {
    shapeMaskAlphaDataUrl.value = imageDataToDataUrl(alphaToMaskImageData(cleaned, stats.width, stats.height));
    shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(stats.reference, cleaned));
    scheduleShapeMaskManualCanvasRender();
    return;
  }
  stats.baseAlpha = cleaned;
  stats.min.fill(255);
  stats.max.fill(0);
  stats.diffMax.fill(0);
  shapeMaskFrameCount.value = 0;
  shapeMaskResetToEmpty.value = false;
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(alphaToMaskImageData(cleaned, stats.width, stats.height));
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(stats.reference, cleaned));
  scheduleShapeMaskManualCanvasRender();
};

const cleanShapeMaskAlpha = () => applyShapeMaskBackgroundCleanup(true);

const resetShapeMaskAccumulatedDiff = () => {
  const stats = shapeMaskStats.value;
  if (!stats) return;
  stats.min.fill(255);
  stats.max.fill(0);
  stats.diffMax.fill(0);
  shapeMaskFrameCount.value = 0;
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
    const difference = Math.max(
      Math.abs((stats.reference?.data[offset] ?? 0) - frame.data[offset]),
      Math.abs((stats.reference?.data[offset + 1] ?? 0) - frame.data[offset + 1]),
      Math.abs((stats.reference?.data[offset + 2] ?? 0) - frame.data[offset + 2]),
    );
    stats.min[index] = Math.min(stats.min[index], gray);
    stats.max[index] = Math.max(stats.max[index], gray);
    stats.diffMax[index] = Math.max(stats.diffMax[index], difference);
  }
  shapeMaskFrameCount.value += 1;
  if (shapeMaskFrameCount.value % 5 === 1) {
    shapeMaskLivePreviewUrl.value = imageDataToDataUrl(frame);
  }
  refreshShapeMaskPreview();
  if (shapeMaskAlgorithm.value === 'background') applyShapeMaskBackgroundCleanup(false);
};

const updateShapeMaskLivePreview = async () => {
  const stats = shapeMaskStats.value;
  if (!stats) return;
  const frame = await captureLiveShapeImageData(stats.width, stats.height);
  if (!shapeMaskDialogVisible.value || !shapeMaskStats.value || !frame) return;
  shapeMaskLivePreviewUrl.value = imageDataToDataUrl(frame);
};

const scheduleShapeMaskLivePreview = () => {
  if (shapeMaskLivePreviewFrame.value !== null) return;
  shapeMaskLivePreviewFrame.value = window.requestAnimationFrame(async () => {
    shapeMaskLivePreviewFrame.value = null;
    if (!shapeMaskDialogVisible.value || !shapeMaskStats.value) return;
    if (!shapeMaskRunning.value) await updateShapeMaskLivePreview();
    scheduleShapeMaskLivePreview();
  });
};

const stopShapeMaskLivePreview = () => {
  if (shapeMaskLivePreviewFrame.value !== null) {
    window.cancelAnimationFrame(shapeMaskLivePreviewFrame.value);
    shapeMaskLivePreviewFrame.value = null;
  }
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

const stopShapeMaskSampling = () => {
  pauseShapeMaskSampling();
  stopShapeMaskLivePreview();
  shapeMaskManualPointer.value = null;
  shapeMaskManualPanState.value = null;
};

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
  const baseAlpha = useExistingMask ? await loadShapeAlphaMask(shape, size.width, size.height, shapeMaskTarget.value) : null;
  const fullAlpha = new Uint8ClampedArray(total).fill(255);
  const initialAlpha = baseAlpha ?? fullAlpha;
  shapeMaskFrameCount.value = 0;
  shapeMaskResetToEmpty.value = false;
  shapeMaskLivePreviewUrl.value = '';
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(alphaToMaskImageData(initialAlpha, size.width, size.height));
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(reference, initialAlpha));
  shapeMaskStats.value = {
    width: size.width,
    height: size.height,
    min: new Uint8ClampedArray(total).fill(255),
    max: new Uint8ClampedArray(total),
    diffMax: new Uint8ClampedArray(total),
    baseAlpha: initialAlpha,
    reference,
  };
  scheduleShapeMaskManualCanvasRender();
  await updateShapeMaskLivePreview();
  scheduleShapeMaskLivePreview();
};

const resetShapeMaskSampling = async () => {
  pauseShapeMaskSampling();
  const stats = shapeMaskStats.value;
  if (!stats?.reference) {
    await initializeShapeMaskSampling(false);
    return;
  }
  const fullAlpha = new Uint8ClampedArray(stats.width * stats.height).fill(255);
  stats.baseAlpha = fullAlpha;
  stats.min.fill(255);
  stats.max.fill(0);
  stats.diffMax.fill(0);
  shapeMaskFrameCount.value = 0;
  shapeMaskResetToEmpty.value = true;
  shapeMaskAlphaDataUrl.value = imageDataToDataUrl(alphaToMaskImageData(fullAlpha, stats.width, stats.height));
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(applyAlphaToPreview(stats.reference, fullAlpha));
  scheduleShapeMaskManualCanvasRender();
};

const startShapeMaskSampling = async () => {
  if (!shapeMaskStats.value) await initializeShapeMaskSampling(true);
  if (!shapeMaskStats.value || shapeMaskRunning.value) return;
  shapeMaskRunning.value = true;
  scheduleShapeMaskSampling();
};

const runShapeMaskSingleFrame = async () => {
  if (!shapeMaskStats.value) await initializeShapeMaskSampling(true);
  const stats = shapeMaskStats.value;
  if (!stats) return;
  if (shapeMaskAlgorithm.value === 'ai') {
    await runShapeMaskAi();
    return;
  }
  if (shapeMaskAlgorithm.value === 'background') {
    cleanShapeMaskAlpha();
    return;
  }
  resetShapeMaskAccumulatedDiff();
  const frame = await captureLiveShapeImageData(stats.width, stats.height);
  if (!frame) return;
  updateShapeMaskStats(frame);
};

const runShapeMaskAi = async () => {
  if (shapeMaskAiRunning.value) return;
  if (!shapeMaskStats.value) await initializeShapeMaskSampling(true);
  const stats = shapeMaskStats.value;
  if (!stats?.reference) return;
  pauseShapeMaskSampling();
  shapeMaskAiRunning.value = true;
  try {
    const referenceDataUrl = imageDataToDataUrl(stats.reference);
    shapeMaskLivePreviewUrl.value = referenceDataUrl;
    const response = await removeFanxiuDataAnnotationBackground({
      image_data_url: referenceDataUrl,
      model: 'isnet-general-use',
      alpha_matting: false,
      post_process_mask: true,
    });
    const alpha = await loadAlphaMaskDataUrl(response.alpha_mask_data_url, stats.width, stats.height);
    if (!alpha) throw new Error('AI 抠图没有返回有效 alpha');
    commitShapeMaskManualAlpha(alpha);
    if (response.result_data_url) {
      shapeMaskResultPreviewUrl.value = response.result_data_url;
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    shapeMaskAiRunning.value = false;
  }
};

const runSelectedShapeMaskMode = async () => {
  if (shapeMaskRunning.value) {
    pauseShapeMaskSampling();
    return;
  }
  if (shapeMaskAlgorithm.value === 'ai') {
    await runShapeMaskAi();
    return;
  }
  if (shapeMaskCaptureMode.value === 'single') {
    await runShapeMaskSingleFrame();
    return;
  }
  resetShapeMaskAccumulatedDiff();
  await startShapeMaskSampling();
};

const openShapeMaskDialog = async (target: ShapeMaskTarget = 'image') => {
  if (!selectedShape.value || selectedShape.value.kind === 'group') return;
  shapeMaskTarget.value = target;
  shapeMaskDialogVisible.value = true;
  await nextTick();
  await initializeShapeMaskSampling(true);
};

const saveShapeMaskAndClose = () => {
  const shape = selectedShape.value;
  const stats = shapeMaskStats.value;
  if (!shape || !stats || !shapeMaskAlphaDataUrl.value) return;
  if (shapeMaskTarget.value === 'ocr') {
    if (shapeMaskResetToEmpty.value || isFullShapeMaskAlpha(stats.baseAlpha)) {
      shape.ocrMask = null;
      shape.ocrMaskMode = 'inherit-envelope';
      shapeMaskDialogVisible.value = false;
      return;
    }
    shape.ocrMask = {
      width: stats.width,
      height: stats.height,
      dataUrl: shapeMaskAlphaDataUrl.value,
    };
    shape.ocrMaskMode = 'custom';
    shapeMaskDialogVisible.value = false;
    return;
  }
  if (shapeMaskResetToEmpty.value || isFullShapeMaskAlpha(stats.baseAlpha)) {
    shape.alphaMask = null;
    shape.maskEnabled = false;
    shapeMaskDialogVisible.value = false;
    return;
  }
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

const createLinkedShapeForImage = (image: DataAnnotationAssetNode, imageId: number, label: string) => {
  const source = selectedShape.value;
  image.shapes ??= [];
  const existing = image.shapes.find((shape) => shape.discriminatorGroupId === shapeDiscriminatorGroupId.value);
  if (existing) return existing;
  const shape: DataAnnotationShape = {
    id: createAssetId('shape'),
    kind: 'shape',
    title: label || source?.title || image.title,
    description: '',
    locked: Boolean(source?.locked),
    floating: Boolean(source?.floating),
    jitterEnabled: Boolean(source?.jitterEnabled),
    jitterRadius: normalizeShapeJitterRadius(source?.jitterRadius),
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    loadDirection: 'none',
    imageMatchRole: source?.imageMatchRole ?? 'off',
    pixelTolerance: DEFAULT_SHAPE_PIXEL_TOLERANCE,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    ocrMaskMode: 'inherit-envelope',
    ocrMask: null,
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

const selectShapeDiscriminatorCandidate = (shape: DataAnnotationShape, label: string) => {
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
  const imageId = assetNumericImageId(selectedImageNode.value as DataAnnotationAssetNode);
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
  `<section class="data-annotation-help-section"><h4>${title}</h4>${lines.map((line) => `<p>${line}</p>`).join('')}</section>`
);

const showStructuredHelp = (title: string, sections: Array<{ title: string; lines: string[] }>) => {
  ElMessageBox.alert(
    `<div class="data-annotation-help">${sections.map((section) => helpSectionHtml(section.title, section.lines)).join('')}</div>`,
    title,
    {
      confirmButtonText: '知道了',
      dangerouslyUseHTMLString: true,
      customClass: 'data-annotation-help-message',
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
        '单帧/连拍只决定取几帧；差异抠图/连通抠图只决定怎么生成 alpha。',
        '差异抠图按参考图和当前帧差异、连拍波动来扣动态背景；阈值越小，扣除越严格。',
        'AI抠图调用后端 rembg 模型生成 alpha，适合颜色复杂但主体相对明确的图标。',
      ],
    },
    {
      title: '3. 检测效果',
      lines: [
        '保存后，透明像素会被跳过，不参与相似度计算。',
        '连通抠图从边缘背景开始扩散，清掉与边缘连通且颜色相近的区域；两种算法都只更新预览，保存后才写入 shape。',
      ],
    },
  ]);
};

const showShapeMaskCleanHelp = () => {
  showStructuredHelp('连通抠图说明', [
    {
      title: '1. 它做什么',
      lines: ['从方框边缘开始找背景，把与边缘连通且颜色相近的区域设为透明。'],
    },
    {
      title: '2. 适用场景',
      lines: ['图标、文字、按钮叠在复杂背景上时使用，例如大地图、日常入口这类局部锚点。'],
    },
    {
      title: '3. 注意事项',
      lines: [
        '单帧会立即计算一次；连拍会持续采样，直到再次点击暂停。',
        '连通抠图基于当前参考图和已有 alpha 生成结果，不依赖差异阈值。',
        '如果目标本身和背景颜色太接近，可能需要保存前手动检查抠图结果。',
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

const showShapeLoadDirectionHelp = () => {
  showStructuredHelp('窗口加载说明', [
    {
      title: '1. 含义',
      lines: [
        '表示这个窗口继续查看、加载新内容的方向。',
        '方向以窗口视野的前进方向为准，不表示手指、鼠标或底层拖拽方向。',
        '方向只是规范遍历方向，不代表进入窗口时已经位于第一项。',
      ],
    },
    {
      title: '2. 用法',
      lines: [
        '例如选择“↓”，表示下方还有内容，Runtime 会执行“向下加载 / 向下滚动”。',
        '底层需要采用什么拖拽手势由系统自动换算，业务代码不需要处理相反方向。',
      ],
    },
    {
      title: '3. 高级配置',
      lines: [
        '连续适合普通滚动列表；整页（卡片）表示每次操作后等待完整页面吸附，再重新识别。',
        '边界默认有限；循环只在业务事实明确时标注。初始位置默认起始端，特殊窗口可标为未知。',
      ],
    },
    {
      title: '4. 无',
      lines: ['选择“无”表示它不是可滚动加载的窗口，Shape.load() 不会执行滚动。'],
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
        '任务调试台或历史探索流程识别到真实结果后，可以递增对应次数。',
        '保存时会按频数降序排序，让高频路径优先参与下一次匹配。',
      ],
    },
    {
      title: '4. -1',
      lines: [
        '-1 必须单独填写。',
        '它表示这个 shape 是退出、空白、返回原场景类动作，不记录具体目标。',
      ],
    },
    {
      title: '5. 0',
      lines: [
        '0 必须单独填写。',
        '它表示这个 shape 不产生场景跳转记录，但仍然可以作为动作被执行。',
        '适合会触发当前界面状态变化、开关、展开等不切换场景的区域。',
      ],
    },
    {
      title: '6. 分组路径',
      lines: [
        '可以写资产树分组名，例如 登录弹窗。',
        '表示该分组下的一组 scene 都可能进入。',
      ],
    },
    {
      title: '7. 旧写法',
      lines: ['? 不再作为有效配置。未知路径由任务调试台或人工标注补充，不需要写进字段。'],
    },
  ]);
};

const showRuntimeHelp = () => {
  showStructuredHelp('任务调试台说明', [
    {
      title: '1. Runtime',
      lines: [
        '正式任务由后端 Runtime 执行，前端刷新不会中断任务。',
        '单步只触发后端识别 tick，用于检查当前场景感知。',
      ],
    },
    {
      title: '2. Scheduler',
      lines: [
        '执行到期会交给后端 Scheduler 选择已记录且到期的下次触发时间。',
        '手动运行单任务和执行到期任务都会提交 task cell 到同一个 Runtime kernel。',
      ],
    },
    {
      title: '3. 守护',
      lines: [
        '守护开关只控制对应高优先级节点是否参与 tick。',
        '行为树 Runtime 保持常驻；关闭守护不会关闭行为树 Runtime，也不会影响 task cell 入队。',
      ],
    },
    {
      title: '4. 资产树',
      lines: [
        'Runtime 优先使用资产树里已有 scene 和 shape 标注事实。',
        '已有 shape 可用时不猜坐标；缺标注时任务会报错，方便补标。',
      ],
    },
    {
      title: '5. 任务来源',
      lines: [
        '任务列表来自后端 Scheduler。',
        '临时任务可以手动触发，到期任务由 Scheduler 批量发送给 Runtime。',
      ],
    },
  ]);
};

const shapeBoxStyle = (shape: DataAnnotationShape) => ({
  left: (shape.x * 100) + '%',
  top: (shape.y * 100) + '%',
  width: (shape.w * 100) + '%',
  height: (shape.h * 100) + '%',
});

const buildShapeBox = (startX: number, startY: number, endX: number, endY: number): DataAnnotationShape => ({
  id: 'draft-shape',
  kind: 'shape',
  title: '',
  description: '',
  locked: false,
  floating: false,
  jitterEnabled: false,
  jitterRadius: 4,
  isSceneIdentity: false,
  sceneIdentityRole: 'off',
  sceneJumpTarget: '',
  loadDirection: 'none',
  imageMatchRole: 'off',
  pixelTolerance: DEFAULT_SHAPE_PIXEL_TOLERANCE,
  ocrMatchRole: 'off',
  ocrEnabled: false,
  ocrText: '',
  ocrMatchMode: 'contains',
  ocrMaskMode: 'inherit-envelope',
  ocrMask: null,
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

const clampShapeBox = (shape: DataAnnotationShape) => {
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
  const shape: DataAnnotationShape = {
    ...draft,
    id: createAssetId('shape'),
    kind: 'shape',
    title: 'shape ' + (flattenShapes(image.shapes ?? []).filter(isDrawableShape).length + 1),
    description: '',
    locked: false,
    floating: false,
    jitterEnabled: false,
    jitterRadius: 4,
    isSceneIdentity: false,
    sceneIdentityRole: 'off',
    sceneJumpTarget: '',
    loadDirection: 'none',
    imageMatchRole: 'off',
    pixelTolerance: DEFAULT_SHAPE_PIXEL_TOLERANCE,
    ocrMatchRole: 'off',
    ocrEnabled: false,
    ocrText: '',
    ocrMatchMode: 'contains',
    ocrMaskMode: 'inherit-envelope',
    ocrMask: null,
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
  if (!shape || isShapeLocked(shape)) return;
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

const pickShapeAtPoint = (point: { x: number; y: number }, fallbackShapeId: string) => {
  const candidates = editableAnnotationShapes.value.filter((shape) => (
    point.x >= shape.x
    && point.x <= shape.x + shape.w
    && point.y >= shape.y
    && point.y <= shape.y + shape.h
  ));
  if (!candidates.length) return fallbackShapeId;
  candidates.sort((a, b) => {
    const distanceA = Math.hypot(point.x - (a.x + a.w / 2), point.y - (a.y + a.h / 2));
    const distanceB = Math.hypot(point.x - (b.x + b.w / 2), point.y - (b.y + b.h / 2));
    if (distanceA !== distanceB) return distanceA - distanceB;
    return (a.w * a.h) - (b.w * b.h);
  });
  return candidates[0]?.id ?? fallbackShapeId;
};

const pickShapeCornerAtPoint = (
  point: { x: number; y: number },
  fallbackShapeId: string,
  mode: Extract<ShapeDragState['mode'], 'top-left' | 'bottom-right'>,
) => {
  const candidates = editableAnnotationShapes.value;
  if (!candidates.length) return fallbackShapeId;
  const rect = getAnnotationRect();
  const scaleX = rect?.width || 1;
  const scaleY = rect?.height || 1;
  const ranked = candidates.map((shape) => {
    const cornerX = mode === 'top-left' ? shape.x : shape.x + shape.w;
    const cornerY = mode === 'top-left' ? shape.y : shape.y + shape.h;
    return {
      shape,
      distance: Math.hypot((point.x - cornerX) * scaleX, (point.y - cornerY) * scaleY),
    };
  });
  ranked.sort((a, b) => {
    if (a.distance !== b.distance) return a.distance - b.distance;
    return (a.shape.w * a.shape.h) - (b.shape.w * b.shape.h);
  });
  return ranked[0]?.shape.id ?? fallbackShapeId;
};

const pickShapeCornerHitAtPoint = (point: { x: number; y: number }) => {
  const rect = getAnnotationRect();
  if (!rect) return null;
  const handleRadius = 12;
  const hits = editableAnnotationShapes.value.flatMap((shape) => ([
    {
      shape,
      mode: 'top-left' as const,
      distance: Math.hypot((point.x - shape.x) * rect.width, (point.y - shape.y) * rect.height),
    },
    {
      shape,
      mode: 'bottom-right' as const,
      distance: Math.hypot((point.x - (shape.x + shape.w)) * rect.width, (point.y - (shape.y + shape.h)) * rect.height),
    },
  ])).filter((hit) => hit.distance <= handleRadius);
  hits.sort((a, b) => {
    if (a.distance !== b.distance) return a.distance - b.distance;
    return (a.shape.w * a.shape.h) - (b.shape.w * b.shape.h);
  });
  const hit = hits[0];
  return hit ? { shapeId: hit.shape.id, mode: hit.mode } : null;
};

const startShapeMove = (event: PointerEvent, shapeId: string) => {
  const point = getAnnotationPoint(event);
  const cornerHit = point ? pickShapeCornerHitAtPoint(point) : null;
  if (cornerHit) {
    startShapeDrag(event, cornerHit.shapeId, cornerHit.mode);
    return;
  }
  startShapeDrag(event, point ? pickShapeAtPoint(point, shapeId) : shapeId, 'move');
};
const startShapeResize = (event: PointerEvent, shapeId: string, mode: Extract<ShapeDragState['mode'], 'top-left' | 'bottom-right'>) => {
  const point = getAnnotationPoint(event);
  startShapeDrag(event, point ? pickShapeCornerAtPoint(point, shapeId, mode) : shapeId, mode);
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

.runtime-console {
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0;
}

.runtime-console-line {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 18px;
  flex-wrap: wrap;
}

.runtime-console-status,
.runtime-console-tools,
.runtime-console-task,
.runtime-console-actions {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-console-task {
  flex: 1 1 620px;
}

.runtime-console-actions {
  flex: 0 0 auto;
  justify-content: flex-end;
}

.runtime-guard-button {
  min-width: 62px;
}

.runtime-state-chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  color: #606266;
  font-size: 12px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #f5f7fa;
}

.runtime-state-chip.is-running,
.runtime-state-chip.is-guard {
  color: #337ecc;
  border-color: #c6e2ff;
  background: #ecf5ff;
}

.runtime-state-chip.is-error {
  color: #c45656;
  border-color: #fcd3d3;
  background: #fef0f0;
}

.runtime-state-chip.is-success {
  color: #529b2e;
  border-color: #d1edc4;
  background: #f0f9eb;
}

.runtime-status-text,
.runtime-task-config-summary {
  min-width: 0;
  max-width: 420px;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-meta-chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 6px;
  color: #6b7280;
  font-size: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  background: #fff;
}

.runtime-task-config-summary {
  max-width: 260px;
}

.runtime-section-label {
  color: #6b7280;
  font-size: 12px;
  white-space: nowrap;
}

.behavior-function-select {
  width: 144px;
}

.behavior-preset-select {
  width: 168px;
}

.runtime-task-codes-input {
  width: 260px;
}

.daily-find-summary {
  max-width: 360px;
  overflow: hidden;
  color: #337ecc;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-log-list {
  height: 50vh;
  overflow: auto;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.runtime-log-row {
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

.runtime-log-row:last-child {
  border-bottom: 0;
}

.runtime-log-row.is-action {
  background: #eff6ff;
}

.runtime-log-row.is-success {
  background: #f0fdf4;
}

.runtime-log-row.is-error {
  background: #fef2f2;
}

.runtime-log-time,
.runtime-log-kind {
  color: #6b7280;
  white-space: nowrap;
}

.runtime-log-message {
  min-width: 0;
  word-break: break-all;
}

.runtime-log-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 8px;
  color: #6b7280;
  font-size: 12px;
}

.runtime-log-empty {
  display: grid;
  min-height: 180px;
  place-items: center;
  color: #9ca3af;
  font-size: 13px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.runtime-facts-path {
  margin-bottom: 8px;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-facts-json {
  height: 56vh;
  margin: 0;
  overflow: auto;
  padding: 10px 12px;
  color: #1f2937;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre;
  border: 1px solid #e5e7eb;
  background: #f8fafc;
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
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
  color: #303133;
}

.annotation-panel-head {
  align-items: center;
  flex-wrap: wrap;
}

.annotation-panel-actions {
  display: inline-flex;
  flex: 1 1 240px;
  width: auto;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.recognition-ops-toolbar {
  display: inline-flex;
  flex: 1 1 auto;
  min-width: 0;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  color: #606266;
  font-size: 12px;
  font-weight: 500;
}

.asset-view-select {
  width: 82px;
  flex: 0 0 auto;
}

.asset-frame-search {
  width: 96px;
}

.asset-frame-search :deep(.el-input__inner) {
  font-variant-numeric: tabular-nums;
}

.annotation-title-tools {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.image-compare-trigger {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  color: #409eff;
  font-size: 12px;
  line-height: 22px;
  white-space: nowrap;
  cursor: pointer;
}

.image-compare-trigger:hover {
  border-color: #409eff;
  background: #ecf5ff;
}

.image-compare-trigger:disabled {
  color: #a8abb2;
  border-color: #e4e7ed;
  background: #f5f7fa;
  cursor: not-allowed;
}

.jpeg-frame-reset-hint {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #f3d19e;
  border-radius: 4px;
  background: #fdf6ec;
  color: #b88230;
  font-size: 12px;
  line-height: 22px;
  cursor: pointer;
  white-space: nowrap;
}

.jpeg-frame-reset-hint:hover {
  border-color: #e6a23c;
  color: #a16a1b;
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

.recognition-ops-panel {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.recognition-ops-tree-wrap {
  flex: 1 1 auto;
  min-height: 0;
  padding: 6px 8px;
  overflow: auto;
}

.recognition-ops-cache-note {
  padding: 6px 12px 0;
  color: #909399;
  font-size: 12px;
}

.recognition-ops-tree {
  min-width: max-content;
}

.recognition-ops-node {
  display: inline-block;
  max-width: 360px;
  overflow: hidden;
  color: #303133;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recognition-ops-node.is-issue {
  color: #606266;
  font-weight: 400;
}

.recognition-ops-node.is-selected {
  color: #1677ff;
  font-weight: 600;
}

.recognition-ops-empty,
.recognition-ops-error {
  padding: 10px 4px;
  color: #909399;
  font-size: 12px;
}

.recognition-ops-error {
  color: #c45656;
}

.navigation-incident-detail {
  flex: 1 1 auto;
  min-height: 0;
  padding: 10px 12px;
  overflow: auto;
}

.navigation-incident-facts {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 14px;
  color: #606266;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.navigation-incident-facts > span:first-child {
  color: #c45656;
  font-weight: 600;
}

.navigation-incident-trigger {
  margin-top: 8px;
  color: #303133;
  font-size: 13px;
}

.navigation-incident-timeline-wrap {
  max-width: 100%;
  margin-top: 10px;
  overflow: auto;
}

.navigation-incident-timeline {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  color: #303133;
  font-size: 12px;
}

.navigation-incident-timeline th,
.navigation-incident-timeline td {
  padding: 6px 10px;
  border-bottom: 1px solid #ebeef5;
  text-align: left;
  white-space: nowrap;
}

.navigation-incident-timeline th {
  color: #606266;
  font-weight: 600;
  background: #f5f7fa;
}

.navigation-incident-timeline tbody tr {
  cursor: pointer;
}

.navigation-incident-timeline tbody tr:hover,
.navigation-incident-timeline tbody tr.is-selected {
  background: #ecf5ff;
}

.navigation-incident-frame-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 360px));
  gap: 10px;
  margin-top: 12px;
}

.navigation-incident-frame-pair figure {
  margin: 0;
}

.navigation-incident-frame-pair figcaption {
  margin-bottom: 5px;
  color: #606266;
  font-size: 12px;
}

.navigation-incident-frame-pair img,
.navigation-incident-frame-empty {
  display: block;
  width: 100%;
  aspect-ratio: 9 / 16;
  border: 1px solid #dcdfe6;
  background: #f5f7fa;
  object-fit: contain;
}

.navigation-incident-frame-empty {
  display: grid;
  place-items: center;
  color: #909399;
  font-size: 12px;
}

.navigation-incident-step-detail {
  grid-column: 1 / -1;
  color: #606266;
  font-size: 12px;
}

.navigation-incident-diagnostic {
  margin-top: 12px;
  color: #606266;
  font-size: 12px;
}

.navigation-incident-diagnostic pre {
  margin: 8px 0 0;
  padding: 8px 10px;
  overflow: auto;
  color: #303133;
  font-family: inherit;
  line-height: 1.6;
  white-space: pre-wrap;
  background: #f5f7fa;
}

.navigation-incident-crops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.navigation-incident-crops figure {
  width: max-content;
  max-width: 180px;
  margin: 0;
}

.navigation-incident-crops img {
  display: block;
  max-width: 180px;
  max-height: 120px;
  border: 1px solid #dcdfe6;
  object-fit: contain;
}

.navigation-incident-crops figcaption {
  margin-top: 3px;
  overflow: hidden;
  color: #606266;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .navigation-incident-frame-pair {
    grid-template-columns: minmax(0, 1fr);
  }
}

.asset-tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 4px;
  min-width: 0;
  border-left: 3px solid transparent;
  border-radius: 4px;
}

.asset-node-id {
  flex: 0 0 auto;
  min-width: 28px;
  color: #909399;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.asset-node-title {
  flex: 0 1 auto;
  min-width: 0;
}

.asset-node-relation-badge {
  flex: 0 0 auto;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  color: #a16207;
  font-size: 11px;
  line-height: 16px;
  text-align: center;
  background: #fef3c7;
  border-radius: 3px;
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

.scene-relation-graph {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 224px;
  min-height: 224px;
  background: #fafafa;
}

.scene-relation-graph.is-resizing {
  cursor: row-resize;
}

.scene-relation-tabs {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
  align-items: center;
  padding: 6px 8px 0;
  background: #fff;
  border-top: 1px solid #ebeef5;
}

.scene-relation-tab {
  height: 24px;
  padding: 0 10px;
  color: #606266;
  font-size: 12px;
  line-height: 22px;
  background: #fff;
  border: 1px solid #dcdfe6;
  border-radius: 4px 4px 0 0;
  cursor: pointer;
}

.scene-relation-tab:hover {
  color: #409eff;
}

.scene-relation-tab.is-active {
  color: #409eff;
  background: #ecf5ff;
  border-color: #b3d8ff;
  border-bottom-color: #ecf5ff;
}

.scene-relation-flow {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
}

.scene-relation-flow :deep(.vue-flow__node) {
  cursor: pointer;
}

.scene-relation-flow :deep(.vue-flow__node-default) {
  overflow: hidden;
  text-overflow: ellipsis;
}

.scene-relation-flow :deep(.vue-flow__handle) {
  display: none !important;
  opacity: 0 !important;
  pointer-events: none !important;
}

:global(.scene-relation-flow .vue-flow__handle) {
  display: none !important;
  opacity: 0 !important;
  pointer-events: none !important;
}

.scene-relation-flow :deep(.vue-flow__edge) {
  cursor: pointer;
}

.scene-relation-flow :deep(.vue-flow__controls) {
  transform: scale(0.72);
  transform-origin: left bottom;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
}

.scene-relation-empty {
  position: absolute;
  left: 12px;
  top: 38px;
  color: #909399;
  font-size: 12px;
  pointer-events: none;
}

.scene-relation-resizer {
  height: 7px;
  border-top: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  background: linear-gradient(to bottom, #fafafa, #f2f3f5);
  cursor: row-resize;
}

.scene-relation-resizer:hover,
.scene-relation-resizer.is-resizing {
  background: #ecf5ff;
  border-color: #c6e2ff;
}

.scene-edge-column {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 4px 6px;
  min-width: 0;
}

.scene-edge-heading {
  color: #606266;
  font-size: 12px;
  font-weight: 600;
}

.scene-edge-row {
  display: inline-grid;
  grid-template-columns: auto minmax(52px, auto) auto minmax(52px, 1fr) auto;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  padding: 2px 4px;
  color: #303133;
  font-size: 12px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 3px;
  cursor: pointer;
}

.scene-edge-row:hover {
  background: #ecf5ff;
}

.scene-edge-type {
  padding: 0 4px;
  color: #409eff;
  background: #ecf5ff;
  border-radius: 3px;
}

.scene-edge-main,
.scene-edge-target,
.scene-edge-shape {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.scene-edge-target {
  color: #1f7a1f;
}

.scene-edge-shape {
  max-width: 120px;
  color: #909399;
}

.scene-edge-arrow,
.scene-edge-empty {
  color: #909399;
}

.scene-edge-empty {
  font-size: 12px;
}

.annotation-workbench {
  width: 100%;
  max-width: 100%;
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
  padding: 10px 0 10px;
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

.empty-image-surface.is-missing {
  color: #b45309;
  cursor: pointer;
  pointer-events: auto;
  background:
    repeating-linear-gradient(
      45deg,
      rgba(245, 158, 11, 0.08) 0,
      rgba(245, 158, 11, 0.08) 12px,
      rgba(255, 255, 255, 0.82) 12px,
      rgba(255, 255, 255, 0.82) 24px
    ),
    #fffbeb;
}

.annotation-shape {
  position: absolute;
  border: 2px solid #409eff;
  color: #409eff;
  background: transparent;
  box-sizing: border-box;
  cursor: move;
  overflow: visible;
}

.annotation-shape.is-active {
  border-color: #e6a23c;
  color: #e6a23c;
}

.annotation-shape.is-scene-identity {
  border-color: #f56c6c;
  color: #f56c6c;
  background: rgba(245, 108, 108, 0.1);
}

.annotation-shape.is-scene-identity.is-active {
  border-color: #d93026;
  color: #d93026;
  background: rgba(217, 48, 38, 0.14);
}

.annotation-shape.is-locked {
  pointer-events: none;
  border-style: dashed;
  opacity: 0.72;
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
  min-width: 320px;
  min-height: 0;
  overflow-x: auto;
  overflow-y: scroll;
  border: 1px solid #ebeef5;
}

.shape-tree {
  min-width: 100%;
}

.shape-tree :deep(.el-tree__empty-block) {
  min-width: 100%;
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

.shape-tree-node.is-scene-identity {
  color: #d93026;
}

.shape-tree-node.is-ocr-suggested {
  text-decoration-line: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
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
  width: 38px;
}

.shape-jump-field .shape-pixel-tolerance-input :deep(.el-input__wrapper) {
  padding: 0 4px;
}

.shape-jump-field .shape-pixel-tolerance-input :deep(.el-input__inner) {
  padding: 0;
  text-align: center;
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

.shape-ocr-mask-mode-select {
  width: 94px;
}

.shape-jitter-config {
  gap: 4px;
}

.shape-jitter-radius-input {
  width: 34px;
}

.shape-jitter-radius-input :deep(.el-input__wrapper) {
  padding: 0 4px;
}

.shape-jitter-radius-input :deep(.el-input__inner) {
  padding: 0;
  text-align: center;
}

.shape-row-break {
  flex-basis: 100%;
  width: 0;
  height: 0;
}

.shape-jump-field .shape-direction-select {
  width: 44px;
}

.shape-load-config-label {
  padding: 0;
  border: 0;
  border-bottom: 1px dotted var(--el-border-color);
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 20px;
  background: transparent;
  cursor: pointer;
}

.shape-load-config {
  display: grid;
  gap: 10px;
}

.shape-load-config-row {
  display: grid;
  grid-template-columns: 56px max-content;
  align-items: center;
  gap: 8px;
}

.shape-load-config-row > span {
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.shape-load-config p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
}

.scene-parent-field :deep(.el-input) {
  width: 112px;
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

.shape-detect-debug {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.shape-detect-debug-stats {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--el-text-color-regular);
  font-size: 12px;
}

.shape-detect-debug-stats span {
  white-space: nowrap;
}

.shape-detect-debug-images {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
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

.shape-help-button.is-inline {
  flex: 0 0 auto;
  margin-left: -8px;
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
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.shape-mask-control-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 24px;
}

.shape-mask-frame-count {
  flex: 0 0 auto;
  white-space: nowrap;
}

.shape-mask-select {
  flex: 0 0 auto;
}

.shape-mask-select.is-capture {
  width: 76px;
}

.shape-mask-select.is-algorithm {
  width: 112px;
}

.shape-mask-select.is-manual-tool {
  width: 78px;
}

.shape-mask-slider {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 300px;
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

.shape-mask-slider.is-brush {
  flex-basis: 220px;
  width: 220px;
}

.shape-mask-manual {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.shape-mask-manual-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.shape-mask-manual-canvas-wrap {
  max-width: 100%;
  max-height: 320px;
  overflow: auto;
  border: 1px solid #dcdfe6;
  background-color: #fff;
  background-image:
    linear-gradient(45deg, #f1f3f5 25%, transparent 25%),
    linear-gradient(-45deg, #f1f3f5 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #f1f3f5 75%),
    linear-gradient(-45deg, transparent 75%, #f1f3f5 75%);
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
}

.shape-mask-manual-canvas-wrap.is-pan-ready,
.shape-mask-manual-canvas-wrap.is-pan-ready * {
  cursor: grab;
}

.shape-mask-manual-canvas-wrap.is-panning,
.shape-mask-manual-canvas-wrap.is-panning * {
  cursor: grabbing;
}

.shape-mask-manual-canvas {
  display: block;
  cursor: crosshair;
}

.image-compare-dialog {
  min-height: 540px;
}

.image-compare-error {
  padding: 12px;
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 4px;
}

.image-compare-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.image-compare-toolbar > span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #303133;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.image-compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.image-compare-pane {
  min-width: 0;
}

.image-compare-pane-title {
  margin-bottom: 6px;
  color: #606266;
  font-size: 12px;
}

.image-compare-canvas {
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid #dcdfe6;
  background: #0f172a;
  cursor: crosshair;
}

:global(.data-annotation-help-message) {
  width: min(520px, calc(100vw - 32px));
}

:global(.data-annotation-help) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: #303133;
  font-size: 14px;
  line-height: 1.7;
}

:global(.data-annotation-help-section) {
  margin: 0;
}

:global(.data-annotation-help-section h4) {
  margin: 0 0 4px;
  color: #1f2937;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

:global(.data-annotation-help-section p) {
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
