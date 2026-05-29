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
            <div class="control-row behavior-row">
              <div class="control-group behavior-controls">
                <span class="control-label">步进器</span>
                <el-select
                  v-model="selectedStepperTaskId"
                  class="behavior-target-input"
                  size="small"
                  placeholder="选择任务"
                  :disabled="stepperRunning"
                >
                  <el-option
                    v-for="task in stepperTaskDefinitions"
                    :key="task.id"
                    :label="task.label"
                    :value="task.id"
                  />
                </el-select>
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :loading="stepperRunning"
                  :disabled="!selectedEntryId || stepperRunning"
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
                <span v-if="stepperRunStatus" class="behavior-run-status">{{ stepperRunStatus }}</span>
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

            <aside class="annotation-panel">
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
                  <el-button size="small" :icon="Delete" title="删除选中节点" aria-label="删除选中节点" :disabled="!selectedAssetNode" @click="deleteSelectedAsset" />
                </div>
              </div>

              <el-tree
                class="asset-tree"
                :data="assetTree"
                :props="assetTreeProps"
                node-key="id"
                default-expand-all
                highlight-current
                draggable
                :expand-on-click-node="false"
                :current-node-key="selectedAssetId"
                :allow-drop="allowAssetDrop"
                @node-click="selectAssetNode"
                @node-contextmenu="openAssetContextMenu"
              >
                <template #default="{ data }">
                  <span class="asset-tree-node" :class="{ 'is-image': data.type === 'image' }">
                    <el-icon v-if="data.type === 'folder'"><Folder /></el-icon>
                    <span v-else class="asset-node-id">{{ assetImageIdMark(data) }}</span>
                    <span>{{ data.title }}</span>
                  </span>
                </template>
              </el-tree>

              <div
                v-if="assetContextMenu.visible"
                class="asset-context-menu"
                :style="{ left: `${assetContextMenu.x}px`, top: `${assetContextMenu.y}px` }"
                @click.stop
                @contextmenu.prevent
              >
                <button type="button" @click="renameAssetFromContextMenu">
                  重命名
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
                <el-checkbox v-if="selectedImageNode" v-model="selectedImageNode.occlusionMaskEnabled" size="small">
                  遮挡标记
                </el-checkbox>
              </div>
              <div class="annotation-panel-actions">
                <el-button size="small" :icon="Plus" :disabled="!selectedImageNode" title="新建 shape" aria-label="新建 shape" @click="addAnnotationShape" />
                <el-button size="small" :icon="Delete" :disabled="!selectedShape" title="删除 shape" aria-label="删除 shape" @click="deleteSelectedShape" />
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
                        <span>空图</span>
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
                        :class="{ 'is-active': selectedShapeId === shape.id }"
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

                <el-tree
                  class="shape-tree"
                  :data="selectedImageShapes"
                  :props="shapeTreeProps"
                  node-key="id"
                  default-expand-all
                  highlight-current
                  draggable
                  :current-node-key="selectedShapeId"
                  @node-click="node => selectShape(node.id)"
                  @node-contextmenu="openShapeTreeContextMenu"
                >
                  <template #default="{ data }">
                    <span class="shape-tree-node" :class="{ 'is-group': data.kind === 'group' }">
                      {{ data.title || 'shape' }}
                    </span>
                  </template>
                </el-tree>

                <div
                  v-if="shapeContextMenu.visible"
                  class="asset-context-menu shape-context-menu"
                  :style="{ left: `${shapeContextMenu.x}px`, top: `${shapeContextMenu.y}px` }"
                  @click.stop
                  @contextmenu.prevent
                >
                  <button type="button" class="is-danger" @click="deleteShapeFromContextMenu">
                    删除
                  </button>
                </div>
              </div>

              <div v-if="selectedShape" class="shape-fields">
                <el-input v-model="selectedShape.title" size="small" placeholder="标题" />
                <div v-if="selectedShape.kind !== 'group'" class="shape-detect-row">
                  <el-checkbox v-model="selectedShape.isSceneIdentity">
                    场景标识
                  </el-checkbox>
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
  Download,
  Folder,
  Picture,
  Plus,
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
  keyeventFanxiuGameWindow2,
  listFanxiuPseudoCodeCards,
  listFanxiuGameWindow2Screenshots,
  matchFanxiuGameWindow2Screenshot,
  runFanxiuVisualScript,
  saveFanxiuGameWindow2Frame,
  saveFanxiuGameWindow2PreLabel,
  screencapFanxiuGameWindow2,
  startFanxiuPseudoCode,
  stopFanxiuVisualScript,
  textFanxiuGameWindow2,
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

type WindowSceneKey = 'star-cloud-phone' | 'sunlogin' | 'mumu';
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
const GAME_WINDOW_SERVICE_KEY = 'fanxiu-game-window';
const VISUAL_ACTION_MARKER_START = '<!-- codeyun-visual-action-v1';
const VISUAL_ACTION_MARKER_END = '-->';
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
const adbFrameUrl = ref('');
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
let adbFrameTimer: number | null = null;
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
  const width = naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
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
  if (shouldUseAdbFrame()) return '正在获取 ADB 画面';
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
  if (shouldUseAdbFrame()) return '';
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
const liveImageUrl = computed(() => (shouldUseAdbFrame() ? adbFrameUrl.value : streamUrl.value));
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
const connectionButtonType = computed(() => (connectionReady.value ? 'success' : 'info'));
const connectionButtonText = computed(() => {
  if (windowViewMode.value === 'off') return '已关闭';
  if (connectionReady.value) return '运行中';
  if (connectionButtonLoading.value || (streamEnabled.value && (streamToken.value || shouldUseAdbFrame()) && !streamError.value)) return '连接中';
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

const shouldUseAdbFrame = () => selectedWindowKey.value === 'mumu';

const captureCurrentFrameDataUrl = async () => {
  if (shouldUseAdbFrame() && selectedEntryId.value) {
    try {
      const blob = await screencapFanxiuGameWindow2(selectedEntryId.value);
      return await blobToDataUrl(blob);
    } catch {
      // Fall back to the visible live frame if ADB screencap is temporarily unavailable.
    }
  }
  return captureCurrentLiveFrameDataUrl();
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
    if (windowViewMode.value !== 'off') {
      await Promise.all([refreshStreamToken(), loadRuntimeStatus()]);
    }
  }
  startPolling();
  void ensureSelectedImagePreview();
  void nextTick(syncCanvas);
});

onBeforeUnmount(() => {
  stopPolling();
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
  if (streamImageRef.value) streamImageRef.value.src = '';
  revokeScreenshotImageUrl();
  clearMatchResults();
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
  occlusionMaskEnabled?: boolean;
  shapes?: GameWindow3Shape[];
};

type GameWindow3Shape = {
  id: string;
  kind?: 'shape' | 'group';
  title: string;
  description: string;
  isSceneIdentity?: boolean;
  sceneJumpTarget?: string;
  contentDirection?: 'none' | 'up' | 'down' | 'left' | 'right';
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

type StepperTask = {
  id: string;
  type: 'go_scene';
  targetText: string;
  targets: GameWindow3AssetNode[];
};

type StepperTaskDefinition = {
  id: string;
  label: string;
  type: 'go_scene';
  targetText: string;
};

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

type StepperActionEdge = {
  from: GameWindow3AssetNode;
  shape: GameWindow3Shape;
  jumpEntries: SceneJumpEntry[];
  targets: Array<{ image: GameWindow3AssetNode; count: number }>;
  isIndependentExit: boolean;
  hasExperience: boolean;
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
};

const GAME_WINDOW3_STORAGE_KEY = 'fanxiu.gameWindow3.assetTree.v1';
const GAME_WINDOW3_DISCRIMINATOR_GROUPS_KEY = 'fanxiu.gameWindow3.discriminatorGroups.v1';
const annotationCanvasRef = ref<HTMLElement | null>(null);
const selectedAssetId = ref<string | null>(null);
const selectedShapeId = ref<string | null>(null);
const assetImagePreviewUrls = ref<Record<string, string>>({});
const stepperTaskDefinitions: StepperTaskDefinition[] = [
  {
    id: 'go-world',
    label: '到世界',
    type: 'go_scene',
    targetText: '世界',
  },
];
const selectedStepperTaskId = ref(stepperTaskDefinitions[0]?.id ?? '');
const stepperRunning = ref(false);
const stepperStopRequested = ref(false);
const stepperRunStatus = ref('');
const stepperTaskStack = ref<StepperTask[]>([]);
const stepperLastAction = ref<StepperLastAction | null>(null);
const shapeDragState = ref<ShapeDragState | null>(null);
const shapeDraftState = ref<ShapeDraftState | null>(null);
const shapeDraftBox = ref<GameWindow3Shape | null>(null);
const shapeDetectingId = ref<string | null>(null);
const shapeDetectResults = ref<Record<string, string>>({});
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

const createAssetId = (prefix: string) => prefix + '-' + Date.now() + '-' + Math.random().toString(16).slice(2);

const createAssetImageNode = (
  title: string,
  options: Partial<Pick<GameWindow3AssetNode, 'filename' | 'imageDataUrl' | 'width' | 'height'>> = {},
): GameWindow3AssetNode => ({
  id: createAssetId('image'),
  type: 'image',
  title,
  ...options,
  occlusionMaskEnabled: false,
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

const parseSceneJumpEntry = (value: string): SceneJumpEntry | null => {
  const text = value.trim();
  if (!text || text === '?') return null;
  if (text === '-1') return { label: '-1', count: 0 };
  const match = text.match(/^(.+?)\((\d+)\)$/);
  const label = (match ? match[1] : text).trim();
  if (!label || label === '?' || label === '-1') return null;
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

const normalizeShapes = (shapes: GameWindow3Shape[] = []): GameWindow3Shape[] => shapes.flatMap((shape) => {
  if (shape.id === 'scene-identity') {
    return normalizeShapes(shape.children ?? []);
  }
  return [{
    ...shape,
    kind: shape.kind === 'group' ? 'group' : 'shape',
    title: typeof shape.title === 'string' ? shape.title : '',
    description: typeof shape.description === 'string' ? shape.description : '',
    isSceneIdentity: Boolean(shape.isSceneIdentity),
    sceneJumpTarget: typeof shape.sceneJumpTarget === 'string'
      ? normalizeSceneJumpTargetText(shape.sceneJumpTarget)
      : (typeof shape.sceneJumpTarget === 'number' ? String(shape.sceneJumpTarget) : ''),
    contentDirection: normalizeShapeContentDirection(shape.contentDirection),
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
    children: normalizeShapes(shape.children ?? []),
  }];
});

const normalizeAssetTree = (nodes: GameWindow3AssetNode[]): GameWindow3AssetNode[] => nodes.map((node) => {
  if (node.type === 'folder') {
    return {
      ...node,
      children: normalizeAssetTree(node.children ?? []),
    };
  }
  return {
    ...node,
    filename: typeof node.filename === 'string' ? node.filename : undefined,
    imageDataUrl: !node.filename && typeof node.imageDataUrl === 'string' ? node.imageDataUrl : undefined,
    width: typeof node.width === 'number' ? node.width : undefined,
    height: typeof node.height === 'number' ? node.height : undefined,
    occlusionMaskEnabled: Boolean(node.occlusionMaskEnabled),
    shapes: normalizeShapes(node.shapes ?? []),
    children: normalizeAssetTree(node.children ?? []),
  };
});

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

const assetTree = ref<GameWindow3AssetNode[]>(loadAssetTree());

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
const findShapeParentChildren = (shapes: GameWindow3Shape[], id: string | null): GameWindow3Shape[] | null => {
  if (!id) return null;
  if (shapes.some((shape) => shape.id === id)) return shapes;
  for (const shape of shapes) {
    const found = findShapeParentChildren(shape.children ?? [], id);
    if (found) return found;
  }
  return null;
};
const annotationShapes = computed(() => flattenShapes(selectedImageShapes.value).filter(isDrawableShape));
const occlusionMaskShapes = computed(() => (
  collectOcclusionAssetImages(assetTree.value)
    .flatMap((image) => flattenShapes(image.shapes ?? []))
    .filter(isDrawableShape)
));
const occlusionOverlayShapes = computed(() => (
  selectedImageNode.value?.occlusionMaskEnabled ? occlusionMaskShapes.value : []
));
const selectedShape = computed(() => findShapeById(selectedImageShapes.value, selectedShapeId.value));
const selectedShapeDetectResult = computed(() => (
  selectedShapeId.value ? shapeDetectResults.value[selectedShapeId.value] || '' : ''
));
const canDetectSelectedShape = computed(() => Boolean(
  selectedEntryId.value
  && selectedImageNode.value?.filename
  && selectedShape.value
  && isDrawableShape(selectedShape.value)
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
  if (!image.occlusionMaskEnabled || box.w <= 0 || box.h <= 0) return '';
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
  const width = selectedImageNode.value?.width || naturalWidth.value || selectedWindowScene.value.defaults.fixedWidth || 9;
  const height = selectedImageNode.value?.height || naturalHeight.value || selectedWindowScene.value.defaults.fixedHeight || 16;
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

selectedAssetId.value = findFirstImageNode(assetTree.value)?.id ?? null;

watch(assetTree, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_WINDOW3_STORAGE_KEY, JSON.stringify(value));
}, { deep: true });

watch(discriminatorGroups, (value) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(GAME_WINDOW3_DISCRIMINATOR_GROUPS_KEY, JSON.stringify(value));
}, { deep: true });

watch(selectedImageNode, (node) => {
  const firstShape = node ? flattenShapes(node.shapes ?? [])[0] ?? null : null;
  selectedShapeId.value = firstShape?.id ?? null;
  resetScreenshotViewState();
  void ensureSelectedImagePreview();
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
  const blob = await getFanxiuGameWindow2Screenshot(selectedEntryId.value, image.filename);
  const dataUrl = await blobToDataUrl(blob);
  assetImagePreviewUrls.value = {
    ...assetImagePreviewUrls.value,
    [image.id]: dataUrl,
  };
  return dataUrl;
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

const addSavedFrameToAssetTree = (node: GameWindow3AssetNode) => {
  insertAssetNodeAfterSelection(node);
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

const renameAssetFromContextMenu = async () => {
  selectedAssetId.value = assetContextMenu.value.nodeId || selectedAssetId.value;
  const node = selectedAssetNode.value;
  closeAssetContextMenu();
  if (!node) return;
  const nodeKindText = node.type === 'folder' ? '目录' : '图片';
  try {
    const result = await ElMessageBox.prompt(nodeKindText + '名称', '重命名' + nodeKindText, {
      inputValue: node.title,
      inputPattern: /\S+/,
      inputErrorMessage: '请输入' + nodeKindText + '名称',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    });
    const nextTitle = String(result.value ?? '').trim();
    if (nextTitle) node.title = nextTitle;
  } catch {
    // User cancelled.
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
    isSceneIdentity: false,
    sceneJumpTarget: '',
    contentDirection: 'none',
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
};

const deleteSelectedShape = () => {
  const image = selectedImageNode.value;
  if (!image?.shapes || !selectedShapeId.value) return;
  const parent = findShapeParentChildren(image.shapes, selectedShapeId.value);
  const index = parent?.findIndex((shape) => shape.id === selectedShapeId.value) ?? -1;
  if (parent && index >= 0) parent.splice(index, 1);
  selectedShapeId.value = flattenShapes(image.shapes)[0]?.id ?? null;
};

const selectShape = (id: string | null) => {
  selectedShapeId.value = id;
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
  selectedShapeId.value = shapeId;
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

const deleteShapeFromContextMenu = () => {
  selectedShapeId.value = shapeContextMenu.value.shapeId || selectedShapeId.value;
  closeShapeContextMenu();
  deleteSelectedShape();
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
  const occlusionAlphaMaskDataUrl = buildOcclusionAlphaMaskDataUrl(image, shape, box);
  shapeDetectingId.value = shape.id;
  try {
    const response = await matchFanxiuGameWindow2Screenshot({
      entry_id: selectedEntryId.value,
      filename: image.filename,
      box,
      pixel_tolerance: visualMacroDefaultPixelTolerance.value,
      alpha_mask_data_url: occlusionAlphaMaskDataUrl || (shape.maskEnabled ? shape.alphaMask?.dataUrl : undefined),
      tolerance_min_data_url: shape.toleranceEnabled ? shape.toleranceRange?.minDataUrl : undefined,
      tolerance_max_data_url: shape.toleranceEnabled ? shape.toleranceRange?.maxDataUrl : undefined,
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
      current_frame_data_url: await captureCurrentFrameDataUrl(),
    });
    const geometryIssue = matchFrameAspectMismatchText(response);
    if (geometryIssue) ElMessage.warning(geometryIssue);
    const resultText = `原位 ${response.fixed_similarity ?? response.similarity}%`;
    shapeDetectResults.value[shape.id] = resultText;
    ElMessage.success(resultText);
  } catch (error) {
    ElMessage.error(getErrorMessage(error));
  } finally {
    shapeDetectingId.value = null;
  }
};

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

const stepperImageLabel = (image: GameWindow3AssetNode) => `${assetImageIdMark(image)} ${image.title}`;

const stepperSceneIdentityShapes = (image: GameWindow3AssetNode) => (
  flattenShapes(image.shapes ?? []).filter((shape) => isDrawableShape(shape) && shape.isSceneIdentity)
);

const isStepperSceneAsset = (image: GameWindow3AssetNode) => (
  findAssetParentFolder(assetTree.value, image.id)?.title.trim() !== '遮挡标记'
);

const isIndependentExitShape = (shape: GameWindow3Shape) => normalizeSceneJumpTargetText(shape.sceneJumpTarget) === '-1';

const isIndependentSceneImage = (image: GameWindow3AssetNode) => (
  flattenShapes(image.shapes ?? []).some((shape) => isDrawableShape(shape) && isIndependentExitShape(shape))
);

const matchStepperShape = async (
  image: GameWindow3AssetNode,
  shape: GameWindow3Shape | null,
  currentFrameDataUrl: string,
) => {
  if (!selectedEntryId.value || !image.filename) return 0;
  const width = image.width || naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = image.height || naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  const box = shape ? shapeToMatchBox(shape, image) : { name: 'scene', x: 0, y: 0, w: width, h: height };
  if (box.w <= 0 || box.h <= 0) return 0;
  const response = await matchFanxiuGameWindow2Screenshot({
    entry_id: selectedEntryId.value,
    filename: image.filename,
    box,
    pixel_tolerance: visualMacroDefaultPixelTolerance.value,
    alpha_mask_data_url: shape && image.occlusionMaskEnabled
      ? buildOcclusionAlphaMaskDataUrl(image, shape, box) || (shape.maskEnabled ? shape.alphaMask?.dataUrl : undefined)
      : (shape?.maskEnabled ? shape.alphaMask?.dataUrl : undefined),
    tolerance_min_data_url: shape?.toleranceEnabled ? shape.toleranceRange?.minDataUrl : undefined,
    tolerance_max_data_url: shape?.toleranceEnabled ? shape.toleranceRange?.maxDataUrl : undefined,
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
  });
  return Number(response.fixed_similarity ?? response.similarity ?? 0);
};

const matchStepperSceneCandidate = async (
  image: GameWindow3AssetNode,
  currentFrameDataUrl: string,
): Promise<StepperSceneCandidate> => {
  const identityShapes = stepperSceneIdentityShapes(image);
  const full = await matchStepperShape(image, null, currentFrameDataUrl);
  const scores: number[] = [];
  for (const shape of identityShapes) {
    if (stepperStopRequested.value) break;
    try {
      scores.push(await matchStepperShape(image, shape, currentFrameDataUrl));
    } catch {
      scores.push(0);
    }
  }
  const min = scores.length ? Math.min(...scores) : full;
  const average = scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : full;
  const identityScore = min * 0.7 + average * 0.3;
  return {
    image,
    score: scores.length ? identityScore * 0.85 + full * 0.15 : Math.min(full, 30),
    full,
    min,
    average,
    count: scores.length,
  };
};

const matchStepperLayer = async (
  images: GameWindow3AssetNode[],
  currentFrameDataUrl: string,
  concurrency = 4,
  highConfidence = 85,
) => {
  const queue = [...images];
  let best: StepperSceneCandidate | null = null;
  let highHit: StepperSceneCandidate | null = null;
  const workers = Array.from({ length: Math.min(concurrency, queue.length) }, async () => {
    while (queue.length && !stepperStopRequested.value && !highHit) {
      const image = queue.shift();
      if (!image) break;
      try {
        const candidate = await matchStepperSceneCandidate(image, currentFrameDataUrl);
        if (!best || candidate.score > best.score) best = candidate;
        if (candidate.score >= highConfidence) highHit = candidate;
      } catch {
        // A failed candidate should not stop the whole tick.
      }
    }
  });
  await Promise.all(workers);
  return highHit ?? best;
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

const identifyStepperScene = async (
  lastAction: StepperLastAction | null,
): Promise<StepperSceneCandidate | null> => {
  const currentFrameDataUrl = await captureCurrentFrameDataUrl();
  for (const layer of buildStepperCandidateLayers(lastAction)) {
    if (stepperStopRequested.value) return null;
    const candidate = await matchStepperLayer(layer, currentFrameDataUrl);
    if (candidate && candidate.score >= 55) return candidate;
  }
  return null;
};

const isBlankExitShape = (shape: GameWindow3Shape) => shape.title.trim() === '空白';

const buildStepperActionEdges = () => {
  const edges: StepperActionEdge[] = [];
  for (const image of flattenAssetImages(assetTree.value)) {
    if (!isStepperSceneAsset(image)) continue;
    for (const shape of flattenShapes(image.shapes ?? []).filter(isDrawableShape)) {
      const isIndependentExit = isIndependentExitShape(shape);
      const jumpEntries = isIndependentExit ? [] : parseSceneJumpEntries(shape.sceneJumpTarget);
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

const clickStepperShape = async (image: GameWindow3AssetNode, shape: GameWindow3Shape) => {
  if (!selectedEntryId.value) return;
  const width = image.width || naturalWidth.value || fixedFrameWidth.value || selectedWindowScene.value.defaults.fixedWidth || 1;
  const height = image.height || naturalHeight.value || fixedFrameHeight.value || selectedWindowScene.value.defaults.fixedHeight || 1;
  await clickFanxiuGameWindow2({
    ...buildRemoteInputPayloadBase(),
    frame_width: naturalWidth.value || width,
    frame_height: naturalHeight.value || height,
    x: Math.round((shape.x + shape.w / 2) * width),
    y: Math.round((shape.y + shape.h / 2) * height),
  });
};

const incrementSceneJumpExperience = (shape: GameWindow3Shape, target: GameWindow3AssetNode) => {
  if (isIndependentExitShape(shape)) return;
  const targetId = assetNumericImageId(target);
  const label = targetId !== null ? String(targetId) : target.title.trim();
  if (!label) return;
  const entries = parseSceneJumpEntries(shape.sceneJumpTarget).filter((entry) => entry.label !== '-1');
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

const stopStepperRun = () => {
  stepperStopRequested.value = true;
  stepperRunStatus.value = '正在停止';
};

const runStepperToTarget = async () => {
  if (!selectedEntryId.value || stepperRunning.value) return;
  const taskDefinition = stepperTaskDefinitions.find((task) => task.id === selectedStepperTaskId.value) ?? stepperTaskDefinitions[0];
  if (!taskDefinition) {
    ElMessage.warning('没有可运行的步进器任务');
    return;
  }
  const targets = resolveStepperSceneTargets(taskDefinition.targetText);
  if (!targets.length) {
    ElMessage.warning(`没有找到目标场景：${taskDefinition.targetText}`);
    return;
  }
  stepperTaskStack.value = [{
    id: createAssetId('stepper-task'),
    type: taskDefinition.type,
    targetText: taskDefinition.targetText,
    targets,
  }];
  stepperRunning.value = true;
  stepperStopRequested.value = false;
  stepperLastAction.value = null;
  const triedActionEdges = new Set<string>();
  try {
    for (let step = 0; step < 24; step += 1) {
      if (stepperStopRequested.value) break;
      const task = stepperTaskStack.value.at(-1);
      if (!task) break;
      stepperRunStatus.value = `Tick ${step + 1}：识别当前场景`;
      const current = await identifyStepperScene(stepperLastAction.value);
      if (!current) {
        stepperRunStatus.value = `Tick ${step + 1}：过渡中，等待 1 秒`;
        await sleep(1000);
        continue;
      }
      applyStepperObservedTransition(current);
      stepperLastAction.value = null;
      if (task.targets.some((target) => target.id === current.image.id)) {
        stepperRunStatus.value = `已到达 ${stepperImageLabel(current.image)}`;
        ElMessage.success(stepperRunStatus.value);
        selectedAssetId.value = current.image.id;
        stepperTaskStack.value.pop();
        return;
      }
      const next = findStepperNextAction(current.image, task.targets, triedActionEdges);
      if (!next) {
        stepperRunStatus.value = `${stepperImageLabel(current.image)} 没有可尝试动作`;
        ElMessage.warning(stepperRunStatus.value);
        return;
      }
      triedActionEdges.add(`${next.from.id}:${next.shape.id}`);
      stepperRunStatus.value = `Tick ${step + 1}：${stepperImageLabel(current.image)} -> ${next.hasExperience ? '点击' : '探索'} ${next.shape.title || 'shape'}`;
      selectedAssetId.value = current.image.id;
      selectedShapeId.value = next.shape.id;
      stepperLastAction.value = {
        fromImageId: current.image.id,
        shapeId: next.shape.id,
        shapeTitle: next.shape.title,
        isIndependentExit: next.isIndependentExit,
        expectedTargets: next.targets.map((target) => target.image),
      };
      await clickStepperShape(current.image, next.shape);
      await sleep(1000);
    }
    if (stepperStopRequested.value) {
      stepperRunStatus.value = '已停止';
      return;
    }
    stepperRunStatus.value = '超过最大步数，未到达目标';
    ElMessage.warning(stepperRunStatus.value);
  } catch (error) {
    stepperRunStatus.value = getErrorMessage(error);
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

const cropImageDataUrlToShape = async (imageDataUrl: string, width: number, height: number) => {
  return cropImageDataUrlByShape(imageDataUrl, selectedShape.value, width, height);
};

const captureLiveShapeImageData = (width: number, height: number) => {
  const shape = selectedShape.value;
  const image = streamImageRef.value;
  if (!shape || !image?.naturalWidth || !image.naturalHeight) return null;
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
    const alpha = volatility <= shapeMaskThreshold.value
      ? 255
      : Math.max(0, 255 - Math.round((volatility - shapeMaskThreshold.value) * 5));
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
  shapeMaskSamplingFrame.value = window.requestAnimationFrame(() => {
    if (!shapeMaskDialogVisible.value || !shapeMaskStats.value || !shapeMaskRunning.value) return;
    const frame = captureLiveShapeImageData(shapeMaskStats.value.width, shapeMaskStats.value.height);
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

const resetShapeMaskSampling = async () => {
  pauseShapeMaskSampling();
  const image = selectedImageNode.value;
  const size = getSelectedShapePixelSize();
  if (!image || !size) return;
  const imageDataUrl = await getAssetImageDataUrl(image);
  if (!imageDataUrl) return;
  const reference = await cropImageDataUrlToShape(imageDataUrl, size.width, size.height);
  if (!reference) return;
  const total = size.width * size.height;
  shapeMaskFrameCount.value = 0;
  shapeMaskLivePreviewUrl.value = '';
  shapeMaskAlphaDataUrl.value = '';
  shapeMaskResultPreviewUrl.value = imageDataToDataUrl(reference);
  shapeMaskStats.value = {
    width: size.width,
    height: size.height,
    min: new Uint8ClampedArray(total).fill(255),
    max: new Uint8ClampedArray(total),
    reference,
  };
};

const startShapeMaskSampling = async () => {
  if (!shapeMaskStats.value) await resetShapeMaskSampling();
  if (!shapeMaskStats.value || shapeMaskRunning.value) return;
  shapeMaskRunning.value = true;
  scheduleShapeMaskSampling();
};

const openShapeMaskDialog = async () => {
  if (!selectedShape.value || selectedShape.value.kind === 'group') return;
  shapeMaskDialogVisible.value = true;
  await nextTick();
  await resetShapeMaskSampling();
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
  shapeToleranceSamplingFrame.value = window.requestAnimationFrame(() => {
    if (!shapeToleranceDialogVisible.value || !shapeToleranceStats.value || !shapeToleranceRunning.value) return;
    const frame = captureLiveShapeImageData(shapeToleranceStats.value.width, shapeToleranceStats.value.height);
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
    isSceneIdentity: false,
    sceneJumpTarget: '',
    contentDirection: 'none',
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
      title: '5. 目录路径',
      lines: [
        '可以写目录名，例如 登录弹窗。',
        '表示该目录下的一组场景都可能进入。',
      ],
    },
    {
      title: '6. 旧写法',
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
        '识别先匹配上一步动作的历史目标，再匹配独立场景，最后匹配其他场景。',
        '每层内部有限并发匹配，命中高置信结果后停止继续扫描后续候选。',
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
  isSceneIdentity: false,
  sceneJumpTarget: '',
  contentDirection: 'none',
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

const getAnnotationPoint = (event: PointerEvent) => {
  const canvas = annotationCanvasRef.value;
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
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
  const point = getAnnotationPoint(event);
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
    selectedShapeId.value = null;
    return;
  }
  image.shapes ??= [];
  const shape: GameWindow3Shape = {
    ...draft,
    id: createAssetId('shape'),
    kind: 'shape',
    title: 'shape ' + (flattenShapes(image.shapes ?? []).filter(isDrawableShape).length + 1),
    description: '',
    isSceneIdentity: false,
    sceneJumpTarget: '',
    contentDirection: 'none',
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
  const point = getAnnotationPoint(event);
  if (!point) return;
  selectedShapeId.value = null;
  annotationCanvasRef.value?.setPointerCapture(event.pointerId);
  shapeDraftState.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
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

.behavior-target-input {
  width: 120px;
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
  align-items: stretch;
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

.asset-tree {
  flex: 1 1 auto;
  min-height: 0;
  padding: 6px 8px;
  overflow: auto;
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
}

.empty-image-surface {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 13px;
  background-image:
    linear-gradient(45deg, rgba(144, 147, 153, 0.12) 25%, transparent 25%),
    linear-gradient(-45deg, rgba(144, 147, 153, 0.12) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, rgba(144, 147, 153, 0.12) 75%),
    linear-gradient(-45deg, transparent 75%, rgba(144, 147, 153, 0.12) 75%);
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
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

.shape-tree {
  flex: 1 1 0;
  min-width: 180px;
  min-height: 0;
  overflow: auto;
  border: 1px solid #ebeef5;
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
  gap: 18px;
  min-height: 24px;
  flex-wrap: wrap;
}

.shape-action-group {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.shape-jump-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 1;
}

.shape-jump-field span {
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

.shape-jump-field .shape-direction-select {
  width: 44px;
}

.shape-jump-field :deep(.el-input__wrapper) {
  min-height: 24px;
}

.shape-jump-field :deep(.el-input__inner) {
  height: 22px;
  line-height: 22px;
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

  .shape-tree {
    width: 100%;
    max-width: none;
    max-height: 220px;
  }
}

</style>
