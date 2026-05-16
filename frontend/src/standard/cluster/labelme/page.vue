<template>
  <div class="device-file-page">
    <section v-if="showEmptyPanel" class="empty-panel">
      <div class="empty-badge">{{ emptyBadgeText }}</div>
      <h2>{{ emptyStateTitle }}</h2>
      <p>{{ emptyStateDescription }}</p>
      <div class="empty-actions">
        <el-button type="primary" @click="router.push('/cluster/tasks')">去设备任务</el-button>
      </div>
    </section>

    <section v-else class="browser-panel" v-loading="isLoadingListing || isLoadingItem">
      <section class="annotation-browser-top">
        <aside class="annotation-overview-panel panel-card">
          <div class="panel-section-head">
            <div>
              <h3>标注概览</h3>
            </div>
            <el-tag size="small" type="info">{{ filteredItems.length }} / {{ annotationItems.length }}</el-tag>
          </div>

          <div class="overview-stats">
            <div class="overview-stat-card">
              <div class="overview-stat-label">当前目录图片</div>
              <strong>{{ annotationItems.length }}</strong>
              <span>{{ recursiveDisplay ? '递归检索' : '当前目录' }}</span>
            </div>

            <div class="overview-stat-card">
              <div class="overview-stat-label">已读取 / 已知标注数</div>
              <strong>{{ visitedItemCount }} / {{ knownShapeCount }}</strong>
              <span>只统计已打开过的标注文件</span>
            </div>
          </div>

          <div class="inspector-block">
            <div class="inspector-block-title">搜索</div>
            <el-input
              v-model="keyword"
              clearable
              class="annotation-search"
              :placeholder="searchPlaceholder"
            />
            <div class="annotation-search-options">
              <el-checkbox v-model="includeAnnotationContentSearch" size="small">
                包含标注正文
              </el-checkbox>
              <span v-if="isLoadingAnnotationContentSearch" class="annotation-search-loading">读取中</span>
            </div>
          </div>

          <slot
            name="overview-after"
            :selected-device="selectedDevice"
            :selected-entry-id="selectedEntryId"
            :selected-path="normalizedPathInput"
            :current-item="currentItem"
            :current-doc="currentDoc"
          />

        </aside>

        <section class="device-directory-panel panel-card">
          <div class="directory-config-row">
            <div class="directory-config-field">
              <span class="directory-config-label">{{ deviceFieldLabel }}</span>
              <el-select
                :model-value="selectedEntryId"
                size="large"
                class="directory-config-select"
                placeholder="选择设备"
                :disabled="isLoadingDevices || !devices.length || isDeviceLocked"
                @update:model-value="handleSelectedEntryChange"
              >
                <el-option
                  v-for="device in devices"
                  :key="device.id"
                  :label="device.name || device.device_id"
                  :value="device.id"
                />
              </el-select>
            </div>

            <div class="directory-config-field directory-config-field-limit">
              <span class="directory-config-label">加载上限</span>
              <el-input-number
                v-model="mediaScanLimitInput"
                size="large"
                class="directory-config-limit"
                :min="MIN_DEVICE_MEDIA_SCAN_LIMIT"
                :max="MAX_DEVICE_MEDIA_SCAN_LIMIT"
                :step="500"
                :precision="0"
                controls-position="right"
                @change="handleMediaScanLimitChange"
              />
            </div>
          </div>

          <div class="directory-toolbar">
            <el-input
              v-model="pathInputValue"
              size="large"
              clearable
              class="directory-path-input"
              :placeholder="pathInputPlaceholder"
              :disabled="!selectedEntryId"
              @keyup.enter="handleSubmitPath"
              @blur="handlePathBlur"
            />
            <el-button
              type="primary"
              size="large"
              class="directory-action-button"
              :loading="isLoadingListing"
              :disabled="!canBrowse"
              @click="handleSubmitPath"
            >
              进入目录
            </el-button>
            <el-button
              size="large"
              class="directory-action-button"
              :disabled="!canGoUp || isLoadingListing"
              @click="goToParentDirectory"
            >
              上一级
            </el-button>
            <el-switch
              :model-value="recursiveDisplay"
              class="directory-recursive-toggle"
              inline-prompt
              active-text="递归检索"
              inactive-text="当前目录"
              :width="112"
              aria-label="是否递归检索"
              @update:model-value="handleRecursiveDisplayChange"
            />
            <span class="directory-section-count">{{ directoryEntries.length }}项</span>
          </div>

          <div v-if="hasFixedRootBoundary" class="directory-fixed-root-hint">
            当前页面限制在 {{ normalizedFixedRootPath }} 及其子目录。
          </div>

          <slot
            name="directory-after"
            :selected-device="selectedDevice"
            :selected-entry-id="selectedEntryId"
            :selected-path="normalizedPathInput"
            :current-item="currentItem"
            :current-doc="currentDoc"
          />

          <div v-if="directoryEntries.length" class="directory-strip">
            <button
              v-for="entry in pagedDirectoryEntries"
              :key="entry.path"
              type="button"
              class="directory-chip"
              @click="void openDirectory(entry.path)"
            >
              <el-icon class="directory-chip-icon"><FolderOpened /></el-icon>
              <span class="directory-chip-name" :title="entry.name">{{ entry.name }}</span>
            </button>
          </div>
          <div v-if="directoryEntries.length > DEFAULT_DIRECTORY_PAGE_SIZE" class="directory-pagination">
            <el-pagination
              small
              background
              :current-page="currentDirectoryPage"
              :page-size="DEFAULT_DIRECTORY_PAGE_SIZE"
              :total="directoryEntries.length"
              layout="prev, pager, next"
              @current-change="handleDirectoryPageChange"
            />
          </div>
          <div v-if="!directoryEntries.length" class="directory-empty-state">
            当前目录下没有子目录
          </div>
        </section>
      </section>

      <section class="annotation-file-panel panel-card">
        <div class="panel-section-head">
          <div>
            <h3>图片列表</h3>
          </div>
        </div>

        <div v-if="filteredItems.length" class="annotation-file-panel-body">
          <div class="annotation-item-list annotation-item-list-grid">
            <button
              v-for="(item, index) in pagedFilteredItems"
              :key="item.id"
              type="button"
              class="annotation-item-card"
              :class="{ 'is-active': item.id === currentItemId }"
              @click="void openItemById(item.id)"
            >
              <div class="annotation-item-index">{{ annotationFileListOffset + index + 1 }}</div>
              <div class="annotation-item-main">
                <div class="annotation-item-name">{{ item.name }}</div>
              </div>
            </button>
          </div>

          <div v-if="filteredItems.length > FILE_LIST_PAGE_SIZE" class="annotation-file-pagination">
            <el-pagination
              small
              background
              :current-page="currentFileListPage"
              :page-size="FILE_LIST_PAGE_SIZE"
              :total="filteredItems.length"
              layout="total, prev, pager, next"
              @current-change="handleFileListPageChange"
            />
          </div>
        </div>

        <div v-else class="annotation-inline-empty">
          {{ annotationItems.length ? '当前筛选没有文件' : '当前目录下没有可标注图片' }}
        </div>
      </section>

      <section class="annotation-editor-layout">
          <section class="annotation-stage-panel panel-card">
            <div class="stage-toolbar">
              <div class="stage-toolbar-main">
                <div class="stage-title-row">
                  <div class="stage-title">{{ currentItem?.name || '未选择图片' }}</div>
                  <el-tag v-if="isLabelmeMode" size="small" :type="isDirty ? 'warning' : 'success'">
                    {{ isDirty ? '未保存' : '已同步' }}
                  </el-tag>
                  <el-button
                    link
                    type="primary"
                    size="small"
                    class="rename-image-button"
                    :loading="isRenamingLabelmeItem"
                    :disabled="!currentItem || !isLabelmeMode"
                    @click="void renameCurrentLabelmeItem()"
                  >
                    重命名
                  </el-button>
                </div>
                <div class="stage-path">
                  {{ currentItem?.relativePath || '先选择设备目录与图片' }}
                </div>
                <div class="stage-meta">
                  {{ currentDoc ? `${currentDoc.imageWidth} × ${currentDoc.imageHeight}` : '--' }}
                </div>
              </div>

              <div class="stage-toolbar-actions">
                <el-popover placement="bottom" :width="300" trigger="click">
                  <template #reference>
                    <el-button circle plain>
                      <el-icon><QuestionFilled /></el-icon>
                    </el-button>
                  </template>
                  <div class="help-popover">
                    <div>1. 用上面的设备和路径定位目录。</div>
                    <div>2. 从上方图片列表切换文件。</div>
                    <div>3. 点“新矩形”后在图上拖动，或在图上右键后点“新建矩形”。</div>
                    <div>4. `Ctrl + S` 保存，`Delete` 删除，`Esc` 取消新建。</div>
                    <div>5. `Ctrl + 滚轮` 或 `Ctrl + +/-` 缩放，`Ctrl + 0` 适应窗口。</div>
                    <div>6. 按住空格后拖动画面，或用鼠标中键拖动画面。</div>
                  </div>
                </el-popover>
                <el-button :disabled="!hasPreviousItem" @click="void openRelativeItem(-1)">上一张</el-button>
                <el-button :disabled="!hasNextItem" @click="void openRelativeItem(1)">下一张</el-button>
                <el-button
                  v-if="isOcrMode"
                  :disabled="!currentItem"
                  :loading="isLoadingOcr"
                  @click="void rerunCurrentOcr()"
                >
                  重新识别
                </el-button>
                <el-button
                  :type="toolMode === 'draw' ? 'primary' : 'default'"
                  :disabled="!currentDoc"
                  @click="toggleDrawMode"
                >
                  {{ toolMode === 'draw' ? '取消新矩形' : '新矩形' }}
                </el-button>
                <el-button :disabled="!selectedShape" @click="deleteSelectedShape">删除选中</el-button>
                <el-button text class="zoom-fit-button" :disabled="!currentDoc" @click="void fitStageToViewport()">
                  适应
                </el-button>
                <div class="zoom-control">
                  <span>缩放</span>
                  <el-slider
                    :model-value="zoomPercent"
                    :min="MIN_ZOOM_PERCENT"
                    :max="MAX_ZOOM_PERCENT"
                    :step="ZOOM_STEP"
                    @update:model-value="handleZoomSliderChange"
                  />
                  <span>{{ zoomPercent }}%</span>
                </div>
                <el-button
                  v-if="isLabelmeMode"
                  type="primary"
                  :disabled="!currentDoc || !isDirty"
                  :loading="isSaving"
                  @click="void saveCurrentDocument()"
                >
                  保存
                </el-button>
                <slot
                  name="stage-toolbar-after"
                  :selected-device="selectedDevice"
                  :selected-entry-id="selectedEntryId"
                  :selected-path="normalizedPathInput"
                  :current-item="currentItem"
                  :current-doc="currentDoc"
                  :selected-shape="selectedShape"
                />
              </div>
            </div>

            <div v-if="currentDoc && currentItem && currentImageUrl" class="stage-body">
              <div class="stage-hint">{{ stageHintText }}</div>
              <div
                ref="stageScrollRef"
                class="stage-scroll"
                :class="stageScrollClasses"
                @wheel="handleStageWheel"
                @scroll.passive="closeStageContextMenu"
                @mousedown.capture="handleStageViewportMouseDown"
                @contextmenu.prevent="handleStageContextMenu"
              >
                <div class="stage-workspace" :style="stageWorkspaceStyle">
                  <div ref="stageRef" class="annotation-stage" :style="stageStyle" @mousedown="handleStageMouseDown">
                    <img class="stage-image" :src="currentImageUrl" :alt="currentItem.name" draggable="false" />

                    <svg
                      class="stage-overlay"
                      :viewBox="`0 0 ${currentDoc.imageWidth} ${currentDoc.imageHeight}`"
                      preserveAspectRatio="none"
                    >
                      <g v-for="shape in currentDoc.editableShapes" :key="shape.id">
                        <rect
                          v-if="shape.shapeType === 'rectangle'"
                          class="annotation-shape annotation-shape--rectangle"
                          :class="{
                            'is-selected': shape.id === selectedShapeId,
                            'is-draw-mode': toolMode === 'draw',
                          }"
                          :x="getRectangleRect(shape).x1"
                          :y="getRectangleRect(shape).y1"
                          :width="getRectangleRect(shape).x2 - getRectangleRect(shape).x1"
                          :height="getRectangleRect(shape).y2 - getRectangleRect(shape).y1"
                          :stroke-width="strokeWidth"
                          @click.stop="selectShape(shape.id)"
                          @mousedown.stop="handleShapeMouseDown(shape.id, $event)"
                        />

                        <polygon
                          v-else-if="shape.shapeType === 'polygon'"
                          class="annotation-shape annotation-shape--polygon"
                          :class="{
                            'is-selected': shape.id === selectedShapeId,
                            'is-draw-mode': toolMode === 'draw',
                          }"
                          :points="getShapeSvgPoints(shape)"
                          :stroke-width="strokeWidth"
                          @click.stop="selectShape(shape.id)"
                          @mousedown.stop="handleShapeMouseDown(shape.id, $event)"
                        />

                        <circle
                          v-else-if="shape.shapeType === 'circle'"
                          class="annotation-shape annotation-shape--circle"
                          :class="{
                            'is-selected': shape.id === selectedShapeId,
                            'is-draw-mode': toolMode === 'draw',
                          }"
                          :cx="getCircleGeometry(shape).cx"
                          :cy="getCircleGeometry(shape).cy"
                          :r="getCircleGeometry(shape).r"
                          :stroke-width="strokeWidth"
                          @click.stop="selectShape(shape.id)"
                          @mousedown.stop="handleShapeMouseDown(shape.id, $event)"
                        />

                        <template v-else-if="shape.shapeType === 'line'">
                          <line
                            class="annotation-hit-stroke"
                            :class="{ 'is-draw-mode': toolMode === 'draw' }"
                            :x1="shape.points[0]?.x ?? 0"
                            :y1="shape.points[0]?.y ?? 0"
                            :x2="shape.points[1]?.x ?? 0"
                            :y2="shape.points[1]?.y ?? 0"
                            :stroke-width="hitStrokeWidth"
                            @click.stop="selectShape(shape.id)"
                            @mousedown.stop="handleShapeMouseDown(shape.id, $event)"
                          />
                          <line
                            class="annotation-shape annotation-shape--line"
                            :class="{
                              'is-selected': shape.id === selectedShapeId,
                              'is-draw-mode': toolMode === 'draw',
                            }"
                            :x1="shape.points[0]?.x ?? 0"
                            :y1="shape.points[0]?.y ?? 0"
                            :x2="shape.points[1]?.x ?? 0"
                            :y2="shape.points[1]?.y ?? 0"
                            :stroke-width="strokeWidth"
                          />
                        </template>

                        <template v-else-if="shape.shapeType === 'linestrip'">
                          <polyline
                            class="annotation-hit-stroke"
                            :class="{ 'is-draw-mode': toolMode === 'draw' }"
                            :points="getShapeSvgPoints(shape)"
                            :stroke-width="hitStrokeWidth"
                            @click.stop="selectShape(shape.id)"
                            @mousedown.stop="handleShapeMouseDown(shape.id, $event)"
                          />
                          <polyline
                            class="annotation-shape annotation-shape--linestrip"
                            :class="{
                              'is-selected': shape.id === selectedShapeId,
                              'is-draw-mode': toolMode === 'draw',
                            }"
                            :points="getShapeSvgPoints(shape)"
                            :stroke-width="strokeWidth"
                          />
                        </template>

                        <template v-if="shape.id === selectedShapeId && toolMode !== 'draw'">
                          <circle
                            v-for="(point, pointIndex) in shape.points"
                            :key="`${shape.id}:${pointIndex}`"
                            class="annotation-handle"
                            :cx="point.x"
                            :cy="point.y"
                            :r="handleRadius"
                            @mousedown.stop="handleShapePointMouseDown(shape.id, pointIndex, $event)"
                          />
                        </template>
                      </g>

                      <rect
                        v-if="draftRect"
                        class="annotation-draft"
                        :x="draftRect.x1"
                        :y="draftRect.y1"
                        :width="draftRect.x2 - draftRect.x1"
                        :height="draftRect.y2 - draftRect.y1"
                        :stroke-width="strokeWidth"
                      />
                    </svg>
                  </div>
                </div>
              </div>
              <Teleport to="body">
                <div
                  v-if="stageContextMenu.visible"
                  ref="stageContextMenuRef"
                  class="stage-context-menu"
                  :style="stageContextMenuStyle"
                  @mousedown.stop
                  @contextmenu.prevent
                >
                  <button type="button" class="stage-context-menu-item" @click="createRectangleFromContextMenu">
                    新建矩形
                  </button>
                </div>
              </Teleport>
            </div>

            <div v-else class="annotation-main-empty">
              <div class="empty-badge subtle">标注面板</div>
              <h3>当前还没有可编辑图片</h3>
              <p>进入一个包含图片的设备目录后，这里会显示当前图片及其 LabelMe 标注内容。</p>
            </div>
          </section>

          <aside class="annotation-inspector-panel panel-card" v-loading="isLoadingOcr">
            <div class="panel-section-head">
              <div>
                <h3>当前标注</h3>
              </div>
              <el-radio-group
                :model-value="annotationSourceMode"
                size="small"
                class="annotation-source-switch"
                @update:model-value="value => void handleAnnotationSourceModeChange(value)"
              >
                <el-radio-button label="labelme">真实标注</el-radio-button>
                <el-radio-button label="ocr">OCR</el-radio-button>
              </el-radio-group>
            </div>
  
            <div v-if="isOcrMode && currentOcrStatus === 'loading'" class="inspector-note">
              正在识别 OCR...
            </div>
            <div v-else-if="isOcrMode && currentOcrStatus === 'error'" class="inspector-note">
              {{ currentOcrError || 'OCR 识别失败' }}
            </div>
            <div v-if="currentDoc?.unsupportedShapeCount" class="inspector-note">
              当前文件里有 {{ currentDoc.unsupportedShapeCount }} 个暂不支持的 shape，会保留但不在这里编辑。
            </div>

            <template v-if="selectedShape">
              <div class="inspector-block">
                <div class="inspector-block-title">文本标签</div>
                <el-input
                  :model-value="selectedShapeLabel"
                  placeholder="输入标签"
                  @update:model-value="updateSelectedShapeLabel"
                />
              </div>

              <div class="inspector-block">
                <div class="inspector-block-head">
                  <div class="inspector-block-title">自定义属性</div>
                  <el-button link type="primary" size="small" @click="addSelectedShapeLabelField">
                    <el-icon><Plus /></el-icon>
                  </el-button>
                </div>

                <div
                  v-if="selectedShapeLabelFields.length"
                  ref="selectedShapeLabelFieldsListRef"
                  class="label-fields-list"
                >
                  <div
                    v-for="(item, index) in selectedShapeLabelFields"
                    :key="item.localId"
                    class="label-field-item"
                    :class="{ 'is-invalid': hasSelectedShapeLabelFieldError(item.localId) }"
                  >
                    <SortableOrderHandle
                      :index="index"
                      :total="selectedShapeLabelFields.length"
                      size="xs"
                    />

                    <el-input
                      v-model="item.key"
                      size="small"
                      class="label-field-key"
                      placeholder="属性名"
                      @input="handleSelectedShapeLabelFieldChange"
                    />

                    <div class="label-field-value-shell">
                      <el-input
                        :model-value="getShapeLabelFieldTextValue(item)"
                        size="small"
                        class="label-field-value"
                        placeholder="属性值"
                        @update:model-value="value => setShapeLabelFieldTextValue(item, value)"
                      />
                    </div>

                    <el-button
                      link
                      type="danger"
                      size="small"
                      class="label-field-delete"
                      @click="removeSelectedShapeLabelField(index)"
                    >
                      <el-icon><Close /></el-icon>
                    </el-button>
                  </div>
                </div>
                <div v-else class="annotation-inline-empty">
                  当前只保存 `text`，需要时再添加自定义属性。
                </div>

                <div v-if="selectedShapeLabelFieldErrors.length" class="inspector-note label-field-errors">
                  <div
                    v-for="error in selectedShapeLabelFieldErrors"
                    :key="`${error.fieldLocalId}:${error.message}`"
                  >
                    {{ error.message }}
                  </div>
                </div>
              </div>

              <div class="inspector-block">
                <div class="inspector-block-head">
                  <div class="inspector-block-title">点集坐标</div>
                  <div class="shape-points-head-actions">
                    <el-tag size="small" type="info">{{ selectedShapeTypeLabel }}</el-tag>
                    <el-button
                      v-if="selectedShapeCanAppendPoint"
                      link
                      type="primary"
                      size="small"
                      @click="appendPointToSelectedShape"
                    >
                      <el-icon><Plus /></el-icon>
                    </el-button>
                  </div>
                </div>
                <div class="shape-points-list">
                  <div class="shape-point-columns" aria-hidden="true">
                    <span class="shape-point-column-spacer"></span>
                    <span class="shape-point-column-label">X</span>
                    <span class="shape-point-column-label">Y</span>
                    <span class="shape-point-column-spacer"></span>
                  </div>
                  <div
                    v-for="pointItem in selectedShapePointRows"
                    :key="`${selectedShape.id}:${pointItem.index}`"
                    class="shape-point-item"
                  >
                    <div class="shape-point-index">{{ pointItem.index + 1 }}</div>
                    <div class="shape-point-axis">
                      <el-input-number
                        :model-value="pointItem.point.x"
                        :step="1"
                        :min="0"
                        :max="currentDoc?.imageWidth || 0"
                        :controls="false"
                        class="shape-point-input"
                        @change="value => updateSelectedShapePointCoordinate(pointItem.index, 'x', value)"
                      />
                    </div>
                    <div class="shape-point-axis">
                      <el-input-number
                        :model-value="pointItem.point.y"
                        :step="1"
                        :min="0"
                        :max="currentDoc?.imageHeight || 0"
                        :controls="false"
                        class="shape-point-input"
                        @change="value => updateSelectedShapePointCoordinate(pointItem.index, 'y', value)"
                      />
                    </div>
                    <el-button
                      v-if="canRemovePointFromShape(selectedShape, pointItem.index)"
                      link
                      type="danger"
                      size="small"
                      class="shape-point-delete"
                      @click="removeSelectedShapePoint(pointItem.index)"
                    >
                      <el-icon><Close /></el-icon>
                    </el-button>
                  </div>
                </div>
              </div>
            </template>
            <div v-else class="annotation-inline-empty">
              先在图里选择一个标注，再在这里改标签和点坐标。
            </div>

            <div class="inspector-block inspector-block-grow">
              <div class="inspector-block-title">标注列表</div>
              <div v-if="currentDoc?.editableShapes.length" class="shape-list">
                <button
                  v-for="(shape, index) in currentDoc.editableShapes"
                  :key="shape.id"
                  type="button"
                  class="shape-card"
                  :class="{ 'is-active': shape.id === selectedShapeId }"
                  @click="selectShape(shape.id)"
                >
                  <div class="shape-card-top">
                    <span class="shape-card-index">{{ index + 1 }}</span>
                    <span class="shape-card-label">{{ shape.labelText || '未命名' }}</span>
                  </div>
                </button>
              </div>
              <div v-else class="annotation-inline-empty">
                当前图片还没有标注。
              </div>
            </div>

            <slot
              name="inspector-after"
              :selected-device="selectedDevice"
              :selected-entry-id="selectedEntryId"
              :selected-path="normalizedPathInput"
              :current-item="currentItem"
              :current-doc="currentDoc"
              :selected-shape="selectedShape"
            />
          </aside>
      </section>

      <slot
        name="page-after"
        :selected-device="selectedDevice"
        :selected-entry-id="selectedEntryId"
        :selected-path="normalizedPathInput"
        :current-item="currentItem"
        :current-doc="currentDoc"
        :selected-shape="selectedShape"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Close, FolderOpened, Plus, QuestionFilled } from '@element-plus/icons-vue';

import {
  fetchDeviceDirectoryItems,
  fetchDeviceFileBlob,
  fetchDeviceFileOcrPreview,
  fetchDeviceFileText,
  fetchDeviceMedia,
  renameDeviceLabelmeAnnotation,
  saveDeviceFileText,
  type DeviceDirectoryItem,
  type DeviceDirectoryListing,
  type DeviceFileSelector,
  type DeviceImageRecord,
  type DeviceOcrShapeType,
} from '@/api/deviceFiles';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { taskStore } from '@/store/taskStore';
import { useSortableList } from '@/utils/useSortableList';

const props = withDefaults(defineProps<{
  fixedDeviceId?: string;
  fixedRootPath?: string;
}>(), {
  fixedDeviceId: '',
  fixedRootPath: '',
});

type LabelMode = 'json' | 'plain';
type SupportedShapeType = 'rectangle' | 'polygon' | 'circle' | 'line' | 'linestrip';
type ShapeLabelFieldType = 'string';
type ShapeLabelFieldStoredValue = string;
type ShapeLabelFieldEditorValue = string;
type ZoomMode = 'fit' | 'manual';
type ShapeCoordinateAxis = 'x' | 'y';
type AnnotationSourceMode = 'labelme' | 'ocr';
type OcrSessionStatus = 'idle' | 'loading' | 'ready' | 'error';

interface Point {
  x: number;
  y: number;
}

interface Rect {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface ShapeLabelFieldItem {
  localId: string;
  key: string;
  type: ShapeLabelFieldType;
  value: ShapeLabelFieldEditorValue;
}

interface ShapeLabelFieldValidationError {
  fieldLocalId: string;
  message: string;
}

interface DocumentLabelFieldValidationError extends ShapeLabelFieldValidationError {
  shapeId: string;
  shapeIndex: number;
}

interface DocumentShapeValidationError {
  shapeId: string;
  shapeIndex: number;
  message: string;
}

interface EditableShape {
  id: string;
  shapeType: SupportedShapeType;
  labelText: string;
  labelMode: LabelMode;
  labelFields: ShapeLabelFieldItem[];
  points: Point[];
  flags: Record<string, unknown>;
  groupId: unknown;
  originalShape: Record<string, unknown> | null;
}

type ShapeOrderEntry = { kind: 'editable'; id: string } | { kind: 'passthrough'; shape: Record<string, unknown> };

interface LabelmeDocument {
  version: string;
  flags: Record<string, unknown>;
  imagePath: string;
  imageData: null;
  imageWidth: number;
  imageHeight: number;
  extras: Record<string, unknown>;
  editableShapes: EditableShape[];
  shapeOrder: ShapeOrderEntry[];
  defaultLabelMode: LabelMode;
  unsupportedShapeCount: number;
}

interface DeviceAnnotationItem {
  id: string;
  name: string;
  relativePath: string;
  folderPath: string;
  absolutePath: string;
  jsonAbsolutePath: string;
  jsonFilename: string;
  size: number;
  modifiedAt: number;
  width: number | null;
  height: number | null;
  cachedShapeCount: number | null;
  annotationSearchContent: string;
  annotationSearchContentLoaded: boolean;
}

interface DragState {
  mode: 'draw' | 'move' | 'move-point';
  shapeId?: string;
  pointIndex?: number;
  anchor: Point;
  initialPoints: Point[];
}

interface PanState {
  startClientX: number;
  startClientY: number;
  startScrollLeft: number;
  startScrollTop: number;
}

interface StageViewportSize {
  width: number;
  height: number;
}

interface StageContextMenuState {
  visible: boolean;
  clientX: number;
  clientY: number;
  imagePoint: Point | null;
}

const DEVICE_ROOT_SENTINEL = '__device_root__';
const DEVICE_ROOT_LABEL = '系统根目录';
const DEFAULT_DIRECTORY_PAGE_SIZE = 20;
const FILE_LIST_PAGE_SIZE = 12;
const DEFAULT_DEVICE_MEDIA_SCAN_LIMIT = 2000;
const MIN_ZOOM_PERCENT = 5;
const MAX_ZOOM_PERCENT = 400;
const ZOOM_STEP = 10;
const MIN_STAGE_VISIBLE_RATIO = 0.2;
const DEFAULT_OCR_SHAPE_TYPE: DeviceOcrShapeType = 'polygon';
const MIN_DEVICE_MEDIA_SCAN_LIMIT = 100;
const MAX_DEVICE_MEDIA_SCAN_LIMIT = 50000;
const ANNOTATION_SEARCH_CONTENT_CONCURRENCY = 6;
const DEVICE_ENTRY_STORAGE_KEY = 'device_labelme_browser_entry';
const DEVICE_PATH_STORAGE_PREFIX = 'device_labelme_browser_path';
const DEVICE_SCAN_LIMIT_STORAGE_SUFFIX = '_scan_limit';
const DEVICE_RECURSIVE_STORAGE_SUFFIX = '_recursive';
const ANNOTATION_SEARCH_EXCLUDED_KEYS = new Set(['imageData', 'imageHeight', 'imageWidth', 'points']);
const DEFAULT_LABEL_TEXT = '新标注';
const MIN_RECT_EDGE = 6;
const SHAPE_TYPE_LABELS: Record<SupportedShapeType, string> = {
  rectangle: '矩形',
  polygon: '多边形',
  circle: '圆形',
  line: '线段',
  linestrip: '折线',
};
const formatShapeTypeDisplay = (shapeType: SupportedShapeType) => `${SHAPE_TYPE_LABELS[shapeType]} / ${shapeType}`;
const SHAPE_MIN_POINT_COUNTS: Record<SupportedShapeType, number> = {
  rectangle: 2,
  polygon: 3,
  circle: 2,
  line: 2,
  linestrip: 2,
};
const SHAPE_FIXED_POINT_COUNTS: Partial<Record<SupportedShapeType, number>> = {
  rectangle: 2,
  circle: 2,
  line: 2,
};

let shapeSeed = 0;
let shapeLabelFieldSeed = 0;
const createShapeId = () => `device-labelme-shape-${++shapeSeed}`;
const createShapeLabelFieldId = () => `device-labelme-field-${++shapeLabelFieldSeed}`;

const route = useRoute();
const router = useRouter();

const getQueryString = (value: unknown) => {
  if (Array.isArray(value)) {
    return typeof value[0] === 'string' ? value[0] : '';
  }
  return typeof value === 'string' ? value : '';
};

const getFirstQueryString = (keys: string[]) => {
  for (const key of keys) {
    const value = getQueryString(route.query[key]);
    if (value) {
      return value;
    }
  }
  return '';
};

// External launch protocol:
// /cluster/labelme?device=codepc_mi15&path=C:\...\dir&image=target.jpg
// /fanxiu/labelme?image=C:\home\chenkunze\data\...\shot.jpg
const getRouteDeviceSelector = () => getFirstQueryString(['entry_id', 'device_id', 'device']);
const getRouteTargetImage = () => getFirstQueryString(['image', 'image_path', 'file', 'absolute_path']);

const loadPersistedEntryId = () => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return '';
  }

  try {
    return (window.localStorage.getItem(DEVICE_ENTRY_STORAGE_KEY) || '').trim();
  } catch {
    return '';
  }
};

const persistSelectedEntryId = (entryId: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }

  try {
    if (entryId) {
      window.localStorage.setItem(DEVICE_ENTRY_STORAGE_KEY, entryId);
    } else {
      window.localStorage.removeItem(DEVICE_ENTRY_STORAGE_KEY);
    }
  } catch {
    // ignore local storage failures
  }
};

const devices = computed(() => taskStore.devices);
const normalizedFixedDeviceId = computed(() => (props.fixedDeviceId || '').trim().toLowerCase());
const selectedEntryId = ref(getQueryString(route.query.entry_id) || loadPersistedEntryId());
const selectedPath = ref(DEVICE_ROOT_SENTINEL);
const pathInputValue = ref('');
const listing = ref<DeviceDirectoryListing | null>(null);
const annotationItems = ref<DeviceAnnotationItem[]>([]);
const currentItemId = ref('');
const recursiveDisplay = ref(false);
const keyword = ref('');
const includeAnnotationContentSearch = ref(false);
const isLoadingDevices = ref(false);
const isLoadingListing = ref(false);
const isLoadingItem = ref(false);
const isLoadingAnnotationContentSearch = ref(false);
const isSaving = ref(false);
const isRenamingLabelmeItem = ref(false);
const mediaScanLimit = ref(DEFAULT_DEVICE_MEDIA_SCAN_LIMIT);
const mediaScanLimitInput = ref(DEFAULT_DEVICE_MEDIA_SCAN_LIMIT);
const currentDirectoryPage = ref(1);
const currentFileListPage = ref(1);
const annotationSourceMode = ref<AnnotationSourceMode>('labelme');
const currentLabelmeDoc = ref<LabelmeDocument | null>(null);
const currentOcrDoc = ref<LabelmeDocument | null>(null);
const currentImageUrl = ref('');
const currentLabelmeDirty = ref(false);
const currentOcrDirty = ref(false);
const currentOcrStatus = ref<OcrSessionStatus>('idle');
const currentOcrError = ref('');
const isLoadingOcr = ref(false);
const zoomPercent = ref(100);
const zoomMode = ref<ZoomMode>('fit');
const toolMode = ref<'select' | 'draw'>('select');
const selectedShapeId = ref('');
const draftRect = ref<Rect | null>(null);
const stageScrollRef = ref<HTMLDivElement | null>(null);
const stageRef = ref<HTMLDivElement | null>(null);
const stageContextMenuRef = ref<HTMLElement | null>(null);
const stageViewportSize = ref<StageViewportSize>({ width: 0, height: 0 });
const stageContextMenu = ref<StageContextMenuState>({
  visible: false,
  clientX: 0,
  clientY: 0,
  imagePoint: null,
});
const selectedShapeLabelFieldsListRef = ref<HTMLElement | null>(null);
const activeDrag = ref<DragState | null>(null);
const activePan = ref<PanState | null>(null);
const isSpacePressed = ref(false);
const normalizeDeviceMatchKey = (value: string) =>
  (value || '').trim().toLowerCase().replace(/[-_\s]+/g, '');

const matchesFixedDevice = (device: { id?: string; device_id?: string; name?: string }, target: string) => {
  const normalizedTarget = normalizeDeviceMatchKey(target);
  const normalizedId = normalizeDeviceMatchKey(device.id || '');
  const normalizedDeviceId = normalizeDeviceMatchKey(device.device_id || '');
  const normalizedDeviceName = normalizeDeviceMatchKey(device.name || '');
  return normalizedId === normalizedTarget || normalizedDeviceId === normalizedTarget || normalizedDeviceName === normalizedTarget;
};
const lockedDevice = computed(() => {
  const targetDeviceId = normalizedFixedDeviceId.value;
  if (!targetDeviceId) {
    return null;
  }
  return devices.value.find((device) => matchesFixedDevice(device, targetDeviceId)) ?? null;
});
const isDeviceLocked = computed(() => Boolean(normalizedFixedDeviceId.value));
const lockedEntryId = computed(() => lockedDevice.value?.id ?? '');
const selectedDevice = computed(() => devices.value.find((device) => device.id === selectedEntryId.value) ?? null);

let directoryLoadVersion = 0;
let itemLoadVersion = 0;
let ocrLoadVersion = 0;
let annotationContentSearchLoadVersion = 0;
let stageResizeObserver: ResizeObserver | null = null;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isAbsolutePath = (value: string) => /^(?:[a-zA-Z]:[\\/]|\\\\|\/|~(?:[\\/]|$))/.test((value || '').trim());
const isDeviceRootPath = (value: string) => (value || '').trim() === DEVICE_ROOT_SENTINEL;

const getPathStorageKey = (entryId: string) => `${DEVICE_PATH_STORAGE_PREFIX}:${entryId || 'default'}`;
const getScanLimitStorageKey = (storageKey: string) => `${storageKey}${DEVICE_SCAN_LIMIT_STORAGE_SUFFIX}`;
const getRecursiveStorageKey = (storageKey: string) => `${storageKey}${DEVICE_RECURSIVE_STORAGE_SUFFIX}`;
const storageKey = computed(() => `device_labelme_browser_${selectedEntryId.value || 'default'}`);

const normalizeMediaScanLimit = (value: unknown) => {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_DEVICE_MEDIA_SCAN_LIMIT;
  }
  return Math.min(MAX_DEVICE_MEDIA_SCAN_LIMIT, Math.max(MIN_DEVICE_MEDIA_SCAN_LIMIT, Math.floor(parsed)));
};

const clampNumber = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const formatPathInput = (value: string) => (isDeviceRootPath(value) ? DEVICE_ROOT_LABEL : value);

const normalizePathInput = (value: string) => {
  const trimmed = (value || '').trim();
  if (!trimmed || trimmed === DEVICE_ROOT_LABEL || trimmed === DEVICE_ROOT_SENTINEL) {
    return DEVICE_ROOT_SENTINEL;
  }
  return isAbsolutePath(trimmed) ? trimmed : '';
};

const normalizeComparablePath = (value: string) => {
  let normalized = (value || '').trim().replace(/\//g, '\\').replace(/\\+/g, '\\');
  if (/^[a-zA-Z]:\\?$/.test(normalized)) {
    return `${normalized.slice(0, 2)}\\`.toLowerCase();
  }
  normalized = normalized.replace(/\\+$/, '');
  return normalized.toLowerCase();
};

const isSameOrSubPath = (candidate: string, root: string) => {
  const normalizedCandidate = normalizeComparablePath(candidate);
  const normalizedRoot = normalizeComparablePath(root);
  return normalizedCandidate === normalizedRoot || normalizedCandidate.startsWith(`${normalizedRoot}\\`);
};

const normalizedFixedRootPath = computed(() => {
  const normalized = normalizePathInput(props.fixedRootPath);
  return normalized && !isDeviceRootPath(normalized) ? normalized : '';
});
const hasFixedRootBoundary = computed(() => Boolean(normalizedFixedRootPath.value));
const emptyBadgeText = '图片标注';
const deviceFieldLabel = computed(() => (isDeviceLocked.value ? '设备（固定）' : '设备'));
const pathInputPlaceholder = computed(() =>
  hasFixedRootBoundary.value
    ? `当前页面限制在 ${normalizedFixedRootPath.value} 及其子目录`
    : '输入绝对路径，例如 D:\\home\\chenkunze\\data'
);

const isPathWithinFixedRoot = (pathValue: string) => {
  if (!hasFixedRootBoundary.value) {
    return true;
  }
  if (isDeviceRootPath(pathValue)) {
    return false;
  }
  return isSameOrSubPath(pathValue, normalizedFixedRootPath.value);
};

const applyPathConstraints = (value: string, options?: { fallbackToRoot?: boolean }) => {
  const normalized = normalizePathInput(value);
  if (!normalized) {
    return '';
  }
  if (!hasFixedRootBoundary.value) {
    return normalized;
  }
  if (isDeviceRootPath(normalized)) {
    return options?.fallbackToRoot === false ? '' : normalizedFixedRootPath.value;
  }
  if (isPathWithinFixedRoot(normalized)) {
    return normalized;
  }
  return options?.fallbackToRoot === false ? '' : normalizedFixedRootPath.value;
};

const loadPersistedPath = (entryId: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return DEVICE_ROOT_SENTINEL;
  }

  try {
    const savedValue = window.localStorage.getItem(getPathStorageKey(entryId)) || '';
    return normalizePathInput(savedValue) || DEVICE_ROOT_SENTINEL;
  } catch {
    return DEVICE_ROOT_SENTINEL;
  }
};

const persistSelectedPath = (entryId: string, value: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(getPathStorageKey(entryId), value || DEVICE_ROOT_SENTINEL);
  } catch {
    // ignore local storage failures
  }
};

const loadPersistedMediaScanLimit = (nextStorageKey: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return DEFAULT_DEVICE_MEDIA_SCAN_LIMIT;
  }
  try {
    const savedValue = window.localStorage.getItem(getScanLimitStorageKey(nextStorageKey)) || '';
    return normalizeMediaScanLimit(savedValue);
  } catch {
    return DEFAULT_DEVICE_MEDIA_SCAN_LIMIT;
  }
};

const persistMediaScanLimit = (nextStorageKey: string, value: number) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(getScanLimitStorageKey(nextStorageKey), String(normalizeMediaScanLimit(value)));
  } catch {
    // ignore local storage failures
  }
};

const loadPersistedRecursiveDisplay = (nextStorageKey: string) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return false;
  }
  try {
    const savedValue = (window.localStorage.getItem(getRecursiveStorageKey(nextStorageKey)) || '').trim().toLowerCase();
    return savedValue === '1' || savedValue === 'true';
  } catch {
    return false;
  }
};

const persistRecursiveDisplay = (nextStorageKey: string, value: boolean) => {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(getRecursiveStorageKey(nextStorageKey), value ? '1' : '0');
  } catch {
    // ignore local storage failures
  }
};

const getAbsoluteParentPath = (value: string) => {
  let current = (value || '').trim();
  if (!current) {
    return '';
  }
  if (/^[a-zA-Z]:[\\/]?$/.test(current)) {
    return '';
  }
  if (/^\\\\[^\\/]+[\\/][^\\/]+[\\/]?$/.test(current)) {
    return '';
  }
  current = current.replace(/[\\/]+$/, '');
  const parent = current.replace(/[\\/][^\\/]+$/, '');
  if (!parent || parent === current) {
    return '';
  }
  if (/^[a-zA-Z]:$/.test(parent)) {
    return `${parent}\\`;
  }
  if (/^\\\\[^\\/]+[\\/][^\\/]+$/.test(parent)) {
    return `${parent}\\`;
  }
  return parent;
};

const resolveInitialPath = (entryId: string) => {
  const targetImage = getRouteTargetImage();
  if (targetImage && isAbsolutePath(targetImage)) {
    const targetDirectory = applyPathConstraints(getAbsoluteParentPath(targetImage), { fallbackToRoot: false });
    if (targetDirectory) {
      return targetDirectory;
    }
  }

  const routePath = applyPathConstraints(getQueryString(route.query.path), { fallbackToRoot: true });
  if (routePath) {
    return routePath;
  }
  const persistedPath = applyPathConstraints(loadPersistedPath(entryId), { fallbackToRoot: true });
  if (persistedPath) {
    return persistedPath;
  }
  return hasFixedRootBoundary.value ? normalizedFixedRootPath.value : DEVICE_ROOT_SENTINEL;
};

const canBrowseFor = (entryId: string, pathValue: string) =>
  Boolean(entryId && (isDeviceRootPath(pathValue) || isAbsolutePath(pathValue)));

selectedPath.value = resolveInitialPath(selectedEntryId.value);
pathInputValue.value = formatPathInput(selectedPath.value);
recursiveDisplay.value = loadPersistedRecursiveDisplay(storageKey.value);
mediaScanLimit.value = loadPersistedMediaScanLimit(storageKey.value);
mediaScanLimitInput.value = mediaScanLimit.value;

const normalizedPathInput = computed(() => normalizePathInput(selectedPath.value));
const canBrowse = computed(() => canBrowseFor(selectedEntryId.value, selectedPath.value));
const isLockedDeviceMissing = computed(() =>
  isDeviceLocked.value && !lockedEntryId.value && devices.value.length > 0
);
const showEmptyPanel = computed(() => !devices.value.length || isLockedDeviceMissing.value);
const emptyStateTitle = computed(() =>
  isLockedDeviceMissing.value ? `未找到设备 ${props.fixedDeviceId}` : '还没有可用设备'
);
const emptyStateDescription = computed(() => {
  if (isLockedDeviceMissing.value) {
    const targetPath = normalizedFixedRootPath.value || '固定目录';
    return `当前页面固定使用设备 ${props.fixedDeviceId}，但当前账号下没有找到对应入口。先到设备任务里添加或授权该设备，再回来加载 ${targetPath} 中的图片和标注文件。`;
  }
  return '先到设备任务里添加本地或远程设备入口，再从设备上下文里加载真实目录中的图片和标注文件。';
});
const searchPlaceholder = computed(() =>
  includeAnnotationContentSearch.value ? '按文件名、路径或标注正文筛选' : '按文件名或相对路径筛选'
);
const listingItems = computed(() => listing.value?.items ?? []);
const directoryEntries = computed(() => listingItems.value.filter((entry) => entry.is_dir));
const directoryPageCount = computed(() =>
  Math.max(1, Math.ceil(directoryEntries.value.length / DEFAULT_DIRECTORY_PAGE_SIZE))
);
const pagedDirectoryEntries = computed(() => {
  const offset = Math.max(0, (Math.max(1, currentDirectoryPage.value) - 1) * DEFAULT_DIRECTORY_PAGE_SIZE);
  return directoryEntries.value.slice(offset, offset + DEFAULT_DIRECTORY_PAGE_SIZE);
});

const annotationItemMatchesKeyword = (item: DeviceAnnotationItem, normalizedKeyword: string) => {
  if (
    item.name.toLowerCase().includes(normalizedKeyword)
    || item.relativePath.toLowerCase().includes(normalizedKeyword)
  ) {
    return true;
  }
  return (
    includeAnnotationContentSearch.value
    && item.annotationSearchContentLoaded
    && item.annotationSearchContent.toLowerCase().includes(normalizedKeyword)
  );
};

const filteredItems = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLowerCase();
  if (!normalizedKeyword) return annotationItems.value;
  return annotationItems.value.filter((item) => annotationItemMatchesKeyword(item, normalizedKeyword));
});
const fileListPageCount = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / FILE_LIST_PAGE_SIZE)));
const annotationFileListOffset = computed(() =>
  Math.max(0, (Math.max(1, currentFileListPage.value) - 1) * FILE_LIST_PAGE_SIZE)
);
const pagedFilteredItems = computed(() =>
  filteredItems.value.slice(annotationFileListOffset.value, annotationFileListOffset.value + FILE_LIST_PAGE_SIZE)
);

const currentFilteredIndex = computed(() =>
  filteredItems.value.findIndex((item) => item.id === currentItemId.value)
);
const hasPreviousItem = computed(() => currentFilteredIndex.value > 0);
const hasNextItem = computed(
  () => currentFilteredIndex.value >= 0 && currentFilteredIndex.value < filteredItems.value.length - 1
);
const currentItem = computed(
  () => annotationItems.value.find((item) => item.id === currentItemId.value) ?? null
);
const currentDoc = computed(() =>
  annotationSourceMode.value === 'ocr' ? currentOcrDoc.value : currentLabelmeDoc.value
);
const isDirty = computed(() =>
  annotationSourceMode.value === 'ocr' ? currentOcrDirty.value : currentLabelmeDirty.value
);
const isLabelmeMode = computed(() => annotationSourceMode.value === 'labelme');
const isOcrMode = computed(() => annotationSourceMode.value === 'ocr');
const selectedShape = computed(
  () => currentDoc.value?.editableShapes.find((shape) => shape.id === selectedShapeId.value) ?? null
);
const selectedShapeLabel = computed(() => selectedShape.value?.labelText ?? '');
const selectedShapeLabelFields = computed(() => selectedShape.value?.labelFields ?? []);
const selectedShapeTypeLabel = computed(() =>
  selectedShape.value ? formatShapeTypeDisplay(selectedShape.value.shapeType) : ''
);
const selectedShapePointRows = computed(() => {
  if (!selectedShape.value) return [];
  return selectedShape.value.points.map((point, index) => ({
    index,
    point,
  }));
});
const visitedItemCount = computed(
  () => annotationItems.value.filter((item) => typeof item.cachedShapeCount === 'number').length
);
const knownShapeCount = computed(() =>
  annotationItems.value.reduce((sum, item) => sum + Math.max(0, item.cachedShapeCount ?? 0), 0)
);
const selectedShapeLabelFieldErrors = computed(() =>
  selectedShape.value ? validateShapeLabelFieldItems(selectedShape.value.labelFields) : []
);

const stageStyle = computed(() => {
  if (!currentDoc.value) return {};
  return {
    width: `${Math.max(1, Math.round((currentDoc.value.imageWidth * zoomPercent.value) / 100))}px`,
    height: `${Math.max(1, Math.round((currentDoc.value.imageHeight * zoomPercent.value) / 100))}px`,
  };
});

const stageContextMenuStyle = computed(() => ({
  left: `${stageContextMenu.value.clientX}px`,
  top: `${stageContextMenu.value.clientY}px`,
}));

const stageWorkspaceStyle = computed(() => {
  if (!currentDoc.value) return {};
  const stageWidth = Math.max(1, Math.round((currentDoc.value.imageWidth * zoomPercent.value) / 100));
  const stageHeight = Math.max(1, Math.round((currentDoc.value.imageHeight * zoomPercent.value) / 100));
  const overscrollX = Math.max(0, Math.round(stageViewportSize.value.width * (1 - MIN_STAGE_VISIBLE_RATIO)));
  const overscrollY = Math.max(0, Math.round(stageViewportSize.value.height * (1 - MIN_STAGE_VISIBLE_RATIO)));
  return {
    width: `${stageWidth + overscrollX * 2}px`,
    height: `${stageHeight + overscrollY * 2}px`,
    minWidth: '100%',
    minHeight: '100%',
  };
});

const strokeWidth = computed(() => {
  if (!currentDoc.value) return 2;
  return Math.max(2, currentDoc.value.imageWidth / 250);
});

const hitStrokeWidth = computed(() => Math.max(14, strokeWidth.value * 4));

const handleRadius = computed(() => {
  const baseRadius = !currentDoc.value ? 5 : Math.max(5, currentDoc.value.imageWidth / 85);
  return Math.round(baseRadius * 0.5 * 100) / 100;
});

const stageHintText = computed(() => {
  if (!currentDoc.value) {
    return '进入目录后开始标注。';
  }
  return '右键菜单可新建矩形；Ctrl + 滚轮 / +/- 缩放，Ctrl + 0 适应窗口；按住空格或中键拖动画面。';
});

const stageScrollClasses = computed(() => ({
  'is-pan-ready': Boolean(currentDoc.value) && isSpacePressed.value && !activePan.value,
  'is-panning': Boolean(activePan.value),
}));

const normalizeZoomPercent = (value: number, options?: { snap?: boolean }) => {
  if (!Number.isFinite(value)) {
    return 100;
  }
  const normalized = options?.snap === false
    ? Math.round(Number(value))
    : Math.round(Number(value) / ZOOM_STEP) * ZOOM_STEP;
  return clampNumber(normalized, MIN_ZOOM_PERCENT, MAX_ZOOM_PERCENT);
};

const readStageViewportSize = (): StageViewportSize => {
  const scrollContainer = stageScrollRef.value;
  if (!scrollContainer || typeof window === 'undefined') {
    return { width: 0, height: 0 };
  }

  const style = window.getComputedStyle(scrollContainer);
  const paddingX = (Number.parseFloat(style.paddingLeft) || 0) + (Number.parseFloat(style.paddingRight) || 0);
  const paddingY = (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0);
  return {
    width: Math.max(0, scrollContainer.clientWidth - paddingX),
    height: Math.max(0, scrollContainer.clientHeight - paddingY),
  };
};

const updateStageViewportSize = () => {
  const nextSize = readStageViewportSize();
  if (
    stageViewportSize.value.width === nextSize.width
    && stageViewportSize.value.height === nextSize.height
  ) {
    return;
  }
  stageViewportSize.value = nextSize;
};

const centerStageViewport = () => {
  const scrollContainer = stageScrollRef.value;
  if (!scrollContainer) return;
  scrollContainer.scrollLeft = Math.max(0, (scrollContainer.scrollWidth - scrollContainer.clientWidth) / 2);
  scrollContainer.scrollTop = Math.max(0, (scrollContainer.scrollHeight - scrollContainer.clientHeight) / 2);
};

const computeFitZoomPercent = () => {
  if (!currentDoc.value || stageViewportSize.value.width <= 0 || stageViewportSize.value.height <= 0) {
    return 100;
  }
  const scale = Math.min(
    stageViewportSize.value.width / currentDoc.value.imageWidth,
    stageViewportSize.value.height / currentDoc.value.imageHeight
  );
  return normalizeZoomPercent(scale * 100, { snap: false });
};

const fitStageToViewport = async () => {
  if (!currentDoc.value) return;
  updateStageViewportSize();
  zoomMode.value = 'fit';
  zoomPercent.value = computeFitZoomPercent();
  await nextTick();
  centerStageViewport();
};

const setZoomPercent = async (
  value: number,
  options?: {
    anchorClientX?: number;
    anchorClientY?: number;
    snap?: boolean;
    nextMode?: ZoomMode;
  }
) => {
  const nextZoom = normalizeZoomPercent(value, { snap: options?.snap });
  const currentZoom = normalizeZoomPercent(zoomPercent.value, { snap: false });
  if (nextZoom === currentZoom) {
    zoomPercent.value = currentZoom;
    if (options?.nextMode) {
      zoomMode.value = options.nextMode;
    }
    return;
  }

  const scrollContainer = stageScrollRef.value;
  const stageElement = stageRef.value;
  if (!scrollContainer || !stageElement || !currentDoc.value) {
    zoomPercent.value = nextZoom;
    if (options?.nextMode) {
      zoomMode.value = options.nextMode;
    }
    return;
  }

  const containerRect = scrollContainer.getBoundingClientRect();
  const currentStageRect = stageElement.getBoundingClientRect();
  const requestedAnchorClientX = options?.anchorClientX ?? (containerRect.left + containerRect.width / 2);
  const requestedAnchorClientY = options?.anchorClientY ?? (containerRect.top + containerRect.height / 2);
  const isAnchorInsideStage =
    requestedAnchorClientX >= currentStageRect.left
    && requestedAnchorClientX <= currentStageRect.right
    && requestedAnchorClientY >= currentStageRect.top
    && requestedAnchorClientY <= currentStageRect.bottom;
  const anchorClientX = isAnchorInsideStage
    ? requestedAnchorClientX
    : (currentStageRect.left + currentStageRect.width / 2);
  const anchorClientY = isAnchorInsideStage
    ? requestedAnchorClientY
    : (currentStageRect.top + currentStageRect.height / 2);
  const currentScale = currentZoom / 100;
  const nextScale = nextZoom / 100;
  const imageX = clampNumber((anchorClientX - currentStageRect.left) / currentScale, 0, currentDoc.value.imageWidth);
  const imageY = clampNumber((anchorClientY - currentStageRect.top) / currentScale, 0, currentDoc.value.imageHeight);
  const previousScrollLeft = scrollContainer.scrollLeft;
  const previousScrollTop = scrollContainer.scrollTop;

  if (options?.nextMode) {
    zoomMode.value = options.nextMode;
  }
  zoomPercent.value = nextZoom;
  await nextTick();

  const nextStageRect = stageElement.getBoundingClientRect();
  const nextAnchorClientX = nextStageRect.left + imageX * nextScale;
  const nextAnchorClientY = nextStageRect.top + imageY * nextScale;

  scrollContainer.scrollLeft = Math.max(0, previousScrollLeft + (nextAnchorClientX - anchorClientX));
  scrollContainer.scrollTop = Math.max(0, previousScrollTop + (nextAnchorClientY - anchorClientY));
};

const handleZoomSliderChange = (value: number | string) => {
  void setZoomPercent(Number(value), { nextMode: 'manual' });
};

const zoomIn = (options?: { anchorClientX?: number; anchorClientY?: number }) => {
  void setZoomPercent(zoomPercent.value + ZOOM_STEP, {
    ...options,
    nextMode: 'manual',
  });
};

const zoomOut = (options?: { anchorClientX?: number; anchorClientY?: number }) => {
  void setZoomPercent(zoomPercent.value - ZOOM_STEP, {
    ...options,
    nextMode: 'manual',
  });
};

const resetZoom = () => {
  void fitStageToViewport();
};

const handleStageWheel = (event: WheelEvent) => {
  closeStageContextMenu();
  if (!(event.ctrlKey || event.metaKey)) {
    return;
  }

  event.preventDefault();
  if (event.deltaY > 0) {
    zoomOut({ anchorClientX: event.clientX, anchorClientY: event.clientY });
    return;
  }
  zoomIn({ anchorClientX: event.clientX, anchorClientY: event.clientY });
};

const beginPan = (event: MouseEvent) => {
  const scrollContainer = stageScrollRef.value;
  if (!scrollContainer) return;

  activePan.value = {
    startClientX: event.clientX,
    startClientY: event.clientY,
    startScrollLeft: scrollContainer.scrollLeft,
    startScrollTop: scrollContainer.scrollTop,
  };
  if (scrollContainer.scrollWidth > scrollContainer.clientWidth || scrollContainer.scrollHeight > scrollContainer.clientHeight) {
    zoomMode.value = 'manual';
  }
  document.addEventListener('mousemove', handleWindowPanMove);
  document.addEventListener('mouseup', handleWindowPanUp);
};

function handleWindowPanMove(event: MouseEvent) {
  const scrollContainer = stageScrollRef.value;
  if (!activePan.value || !scrollContainer) return;

  const deltaX = event.clientX - activePan.value.startClientX;
  const deltaY = event.clientY - activePan.value.startClientY;
  scrollContainer.scrollLeft = Math.max(0, activePan.value.startScrollLeft - deltaX);
  scrollContainer.scrollTop = Math.max(0, activePan.value.startScrollTop - deltaY);
}

function handleWindowPanUp() {
  stopPan();
}

const stopPan = () => {
  activePan.value = null;
  document.removeEventListener('mousemove', handleWindowPanMove);
  document.removeEventListener('mouseup', handleWindowPanUp);
};

const handleStageViewportMouseDown = (event: MouseEvent) => {
  closeStageContextMenu();
  if (!currentDoc.value) return;
  const shouldPan = event.button === 1 || (event.button === 0 && isSpacePressed.value);
  if (!shouldPan) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  beginPan(event);
};

const closeStageContextMenu = () => {
  stageContextMenu.value.visible = false;
  stageContextMenu.value.imagePoint = null;
};

const openStageContextMenu = (event: MouseEvent) => {
  if (!currentDoc.value) return;
  const point = getPointerInImage(event);
  if (!point) {
    closeStageContextMenu();
    return;
  }

  const padding = 12;
  const menuWidth = 144;
  const menuHeight = 48;
  const maxLeft = typeof window === 'undefined'
    ? event.clientX
    : Math.max(padding, window.innerWidth - menuWidth - padding);
  const maxTop = typeof window === 'undefined'
    ? event.clientY
    : Math.max(padding, window.innerHeight - menuHeight - padding);

  stageContextMenu.value = {
    visible: true,
    clientX: clampNumber(event.clientX, padding, maxLeft),
    clientY: clampNumber(event.clientY, padding, maxTop),
    imagePoint: point,
  };
};

const handleStageContextMenu = (event: MouseEvent) => {
  if (!currentDoc.value) return;
  event.preventDefault();
  event.stopPropagation();
  openStageContextMenu(event);
};

const bindStageResizeObserver = () => {
  stageResizeObserver?.disconnect();
  stageResizeObserver = null;

  if (typeof ResizeObserver === 'undefined' || !stageScrollRef.value) {
    updateStageViewportSize();
    return;
  }

  updateStageViewportSize();
  stageResizeObserver = new ResizeObserver(() => {
    updateStageViewportSize();
  });
  stageResizeObserver.observe(stageScrollRef.value);
};

const syncPathInputFromSelection = () => {
  pathInputValue.value = formatPathInput(selectedPath.value);
};

const buildRouteQuery = (
  entryId = selectedEntryId.value,
  pathValue = normalizedPathInput.value,
  targetImage = ''
) => {
  const nextQuery: Record<string, string> = {};
  if (entryId) {
    nextQuery.entry_id = entryId;
  }
  if (!isDeviceRootPath(pathValue)) {
    nextQuery.path = pathValue;
  }
  if (targetImage) {
    nextQuery.image = targetImage;
  }
  return nextQuery;
};

const syncRouteQuery = async (
  mode: 'replace' | 'push' = 'replace',
  options: { targetImage?: string | null } = {}
) => {
  const currentRoutePath = normalizePathInput(getQueryString(route.query.path)) || DEVICE_ROOT_SENTINEL;
  const currentRouteTargetImage = getRouteTargetImage();
  const nextRouteTargetImage = options.targetImage === undefined ? currentRouteTargetImage : (options.targetImage || '');
  const currentQuery = buildRouteQuery(getQueryString(route.query.entry_id), currentRoutePath, currentRouteTargetImage);
  const nextQuery = buildRouteQuery(selectedEntryId.value, normalizedPathInput.value, nextRouteTargetImage);

  if (
    currentQuery.entry_id === nextQuery.entry_id
    && currentQuery.path === nextQuery.path
    && currentQuery.image === nextQuery.image
  ) {
    return false;
  }

  await router[mode]({
    path: route.path,
    query: nextQuery,
  });
  return true;
};

const getParentPathWithinConstraints = (value: string) => {
  const parent = getAbsoluteParentPath(value);
  if (!parent) {
    return '';
  }
  if (!hasFixedRootBoundary.value) {
    return parent;
  }
  return isPathWithinFixedRoot(parent) ? parent : normalizedFixedRootPath.value;
};

const resolveRouteDeviceEntryId = () => {
  const selector = getRouteDeviceSelector();
  if (!selector) {
    return '';
  }
  return devices.value.find((device) => matchesFixedDevice(device, selector))?.id ?? '';
};

const resolveRoutePathFromQuery = () => {
  const targetImage = getRouteTargetImage();
  if (targetImage && isAbsolutePath(targetImage)) {
    const targetDirectory = getAbsoluteParentPath(targetImage);
    const constrainedTargetDirectory = applyPathConstraints(targetDirectory, { fallbackToRoot: false });
    if (constrainedTargetDirectory) {
      return constrainedTargetDirectory;
    }
  }

  const routePath = applyPathConstraints(getQueryString(route.query.path), { fallbackToRoot: true });
  if (routePath) {
    return routePath;
  }
  return '';
};

const applyRouteSelectionFromQuery = () => {
  let changed = false;

  if (!isDeviceLocked.value) {
    const routeEntryId = resolveRouteDeviceEntryId();
    if (routeEntryId && routeEntryId !== selectedEntryId.value) {
      selectedEntryId.value = routeEntryId;
      changed = true;
    }
  } else if (lockedEntryId.value && selectedEntryId.value !== lockedEntryId.value) {
    selectedEntryId.value = lockedEntryId.value;
    changed = true;
  }

  const routePath = resolveRoutePathFromQuery();
  if (routePath && routePath !== selectedPath.value) {
    selectedPath.value = routePath;
    changed = true;
  }

  return changed;
};

const canGoUp = computed(() => {
  if (!canBrowse.value || isDeviceRootPath(normalizedPathInput.value)) {
    return false;
  }
  const parent = getAbsoluteParentPath(normalizedPathInput.value);
  if (!parent) {
    return false;
  }
  if (!hasFixedRootBoundary.value) {
    return true;
  }
  return normalizeComparablePath(normalizedPathInput.value) !== normalizeComparablePath(normalizedFixedRootPath.value)
    && isPathWithinFixedRoot(parent);
});

const stripExtension = (filename: string) => {
  const lastDotIndex = filename.lastIndexOf('.');
  return lastDotIndex >= 0 ? filename.slice(0, lastDotIndex) : filename;
};

const replaceExtension = (filePath: string, nextExtension: string) => {
  const trimmed = (filePath || '').trim();
  const lastSeparatorIndex = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  const lastDotIndex = trimmed.lastIndexOf('.');
  if (lastDotIndex > lastSeparatorIndex) {
    return `${trimmed.slice(0, lastDotIndex)}${nextExtension}`;
  }
  return `${trimmed}${nextExtension}`;
};

const getFileNameFromPath = (value: string) => {
  const trimmed = (value || '').trim().replace(/[\\/]+$/, '');
  const lastSeparatorIndex = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  return lastSeparatorIndex >= 0 ? trimmed.slice(lastSeparatorIndex + 1) : trimmed;
};

const normalizeComparableRelativePath = (value: string) =>
  (value || '').trim().replace(/\//g, '\\').replace(/^\\+/, '').replace(/\\+/g, '\\').toLowerCase();

const getRelativePathWithinBase = (absolutePath: string, basePath: string) => {
  if (!absolutePath || !basePath || isDeviceRootPath(basePath)) {
    return '';
  }

  const normalizedAbsolutePath = normalizeComparablePath(absolutePath);
  const normalizedBasePath = normalizeComparablePath(basePath);
  if (normalizedAbsolutePath === normalizedBasePath) {
    return getFileNameFromPath(absolutePath);
  }
  if (!normalizedAbsolutePath.startsWith(`${normalizedBasePath}\\`)) {
    return '';
  }
  return absolutePath.slice(basePath.replace(/[\\/]+$/, '').length + 1);
};

const roundCoordinate = (value: number) => Math.round(value);
const clonePoint = (point: Point): Point => ({ x: roundCoordinate(point.x), y: roundCoordinate(point.y) });
const clonePoints = (points: Point[]) => points.map(clonePoint);

const normalizeRect = (rect: Rect): Rect => ({
  x1: roundCoordinate(Math.min(rect.x1, rect.x2)),
  y1: roundCoordinate(Math.min(rect.y1, rect.y2)),
  x2: roundCoordinate(Math.max(rect.x1, rect.x2)),
  y2: roundCoordinate(Math.max(rect.y1, rect.y2)),
});

const ensureRectWithinBounds = (rect: Rect, width: number, height: number): Rect => ({
  x1: roundCoordinate(clampNumber(rect.x1, 0, width)),
  y1: roundCoordinate(clampNumber(rect.y1, 0, height)),
  x2: roundCoordinate(clampNumber(rect.x2, 0, width)),
  y2: roundCoordinate(clampNumber(rect.y2, 0, height)),
});

const toPoint = (value: unknown): Point | null => {
  if (!Array.isArray(value) || value.length < 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x: roundCoordinate(x), y: roundCoordinate(y) };
};

const normalizeShapeType = (value: unknown): SupportedShapeType | null => {
  if (value === 'rectangle' || value === 'polygon' || value === 'circle' || value === 'line' || value === 'linestrip') {
    return value;
  }
  return null;
};

const clampPointWithinBounds = (point: Point, width: number, height: number): Point => ({
  x: roundCoordinate(clampNumber(point.x, 0, width)),
  y: roundCoordinate(clampNumber(point.y, 0, height)),
});

const getRectangleRect = (shape: Pick<EditableShape, 'points'>): Rect => {
  const [firstPoint, secondPoint] = shape.points;
  if (!firstPoint || !secondPoint) {
    return { x1: 0, y1: 0, x2: 0, y2: 0 };
  }
  return normalizeRect({
    x1: firstPoint.x,
    y1: firstPoint.y,
    x2: secondPoint.x,
    y2: secondPoint.y,
  });
};

const getCircleGeometry = (shape: Pick<EditableShape, 'points'>) => {
  const [centerPoint, radiusPoint] = shape.points;
  if (!centerPoint || !radiusPoint) {
    return { cx: 0, cy: 0, r: 0 };
  }
  return {
    cx: centerPoint.x,
    cy: centerPoint.y,
    r: Math.hypot(radiusPoint.x - centerPoint.x, radiusPoint.y - centerPoint.y),
  };
};

const getShapeSvgPoints = (shape: Pick<EditableShape, 'points'>) =>
  shape.points.map((point) => `${point.x},${point.y}`).join(' ');

const validateShapePoints = (
  shapeType: SupportedShapeType,
  points: Point[]
): { ok: true } | { ok: false; message: string } => {
  const fixedPointCount = SHAPE_FIXED_POINT_COUNTS[shapeType];
  if (fixedPointCount && points.length !== fixedPointCount) {
    return { ok: false, message: `${SHAPE_TYPE_LABELS[shapeType]}需要 ${fixedPointCount} 个点` };
  }

  const minPointCount = SHAPE_MIN_POINT_COUNTS[shapeType];
  if (points.length < minPointCount) {
    return { ok: false, message: `${SHAPE_TYPE_LABELS[shapeType]}至少需要 ${minPointCount} 个点` };
  }

  if (shapeType === 'rectangle') {
    const rect = getRectangleRect({ points });
    if ((rect.x2 - rect.x1) <= 0 || (rect.y2 - rect.y1) <= 0) {
      return { ok: false, message: '矩形需要两个不同的对角点' };
    }
  }

  if (shapeType === 'circle') {
    const { r } = getCircleGeometry({ points });
    if (r <= 0) {
      return { ok: false, message: '圆形需要圆心和有效的圆周点' };
    }
  }

  if (shapeType === 'line') {
    const [startPoint, endPoint] = points;
    if (!startPoint || !endPoint || (startPoint.x === endPoint.x && startPoint.y === endPoint.y)) {
      return { ok: false, message: '线段需要不同的起点和终点' };
    }
  }

  if (shapeType === 'linestrip') {
    const hasDistinctSegment = points.some((point, index) => {
      const nextPoint = points[index + 1];
      return nextPoint && (point.x !== nextPoint.x || point.y !== nextPoint.y);
    });
    if (!hasDistinctSegment) {
      return { ok: false, message: '折线至少需要一段有效线段' };
    }
  }

  if (shapeType === 'polygon') {
    const uniqueVertices = new Set(points.map((point) => `${roundCoordinate(point.x)},${roundCoordinate(point.y)}`));
    if (uniqueVertices.size < 3) {
      return { ok: false, message: '多边形至少需要 3 个不同顶点' };
    }
  }

  return { ok: true };
};

const normalizeShapeLabelFieldText = (value: unknown) => {
  if (typeof value === 'string') {
    return value;
  }
  if (value == null) {
    return '';
  }
  if (Array.isArray(value) || isRecord(value)) {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
};

const createShapeLabelFieldItem = (
  key: unknown = '',
  type: unknown = 'string',
  value: unknown = ''
): ShapeLabelFieldItem => {
  return {
    localId: createShapeLabelFieldId(),
    key: typeof key === 'string' ? key : '',
    type: 'string',
    value: normalizeShapeLabelFieldText(value),
  };
};

const shapeLabelExtrasToFieldItems = (extras: Record<string, unknown>) =>
  Object.entries(extras).map(([key, value]) =>
    createShapeLabelFieldItem(key, 'string', value)
  );

const serializeShapeLabelFieldValue = (
  _type: ShapeLabelFieldType,
  value: unknown
): { ok: true; value: ShapeLabelFieldStoredValue } | { ok: false; message: string } => {
  return { ok: true, value: normalizeShapeLabelFieldText(value) };
};

const validateShapeLabelFieldItems = (items: ShapeLabelFieldItem[]): ShapeLabelFieldValidationError[] => {
  const errors: ShapeLabelFieldValidationError[] = [];
  const keyCounts = new Map<string, number>();

  for (const item of items) {
    const key = item.key.trim();
    if (key) {
      keyCounts.set(key, (keyCounts.get(key) ?? 0) + 1);
    }
  }

  for (const item of items) {
    const key = item.key.trim();
    if (!key) {
      errors.push({
        fieldLocalId: item.localId,
        message: '自定义属性名不能为空',
      });
      continue;
    }

    if ((keyCounts.get(key) ?? 0) > 1) {
      errors.push({
        fieldLocalId: item.localId,
        message: `属性名“${key}”重复`,
      });
    }

    const serialized = serializeShapeLabelFieldValue(item.type, item.value);
    if (!serialized.ok) {
      errors.push({
        fieldLocalId: item.localId,
        message: `${key}: ${serialized.message}`,
      });
    }
  }

  return errors;
};

const collectDocumentLabelFieldValidationErrors = (doc: LabelmeDocument): DocumentLabelFieldValidationError[] => {
  const errors: DocumentLabelFieldValidationError[] = [];
  doc.editableShapes.forEach((shape, shapeIndex) => {
    for (const error of validateShapeLabelFieldItems(shape.labelFields)) {
      errors.push({
        ...error,
        shapeId: shape.id,
        shapeIndex,
      });
    }
  });
  return errors;
};

const collectDocumentShapeValidationErrors = (doc: LabelmeDocument): DocumentShapeValidationError[] => {
  const errors: DocumentShapeValidationError[] = [];
  doc.editableShapes.forEach((shape, shapeIndex) => {
    const validation = validateShapePoints(shape.shapeType, shape.points);
    if (!validation.ok) {
      errors.push({
        shapeId: shape.id,
        shapeIndex,
        message: validation.message,
      });
    }
  });
  return errors;
};

const shapeLabelFieldItemsToObject = (items: ShapeLabelFieldItem[]) => {
  const extras: Record<string, unknown> = {};
  for (const item of items) {
    const key = item.key.trim();
    if (!key) continue;
    const serialized = serializeShapeLabelFieldValue(item.type, item.value);
    if (!serialized.ok) continue;
    extras[key] = serialized.value;
  }
  return extras;
};

const parseShapeLabel = (
  rawLabel: string
): { text: string; mode: LabelMode; extras: Record<string, unknown> } => {
  if (!rawLabel) {
    return {
      text: '',
      mode: 'json',
      extras: { score: -1 },
    };
  }

  try {
    const parsed = JSON.parse(rawLabel);
    if (isRecord(parsed)) {
      const { text, ...rest } = parsed;
      return {
        text: typeof text === 'string' ? text : normalizeShapeLabelFieldText(text),
        mode: 'json',
        extras: rest,
      };
    }
  } catch {
    // Keep plain label text.
  }

  return {
    text: rawLabel,
    mode: 'plain',
    extras: {},
  };
};

const encodeShapeLabel = (shape: EditableShape) => {
  const labelExtras = shapeLabelFieldItemsToObject(shape.labelFields);
  if (shape.labelMode === 'plain' && !Object.keys(labelExtras).length) {
    return shape.labelText;
  }

  return JSON.stringify({
    text: shape.labelText,
    ...labelExtras,
  });
};

const normalizeEditableShape = (value: unknown): EditableShape | null => {
  if (!isRecord(value)) {
    return null;
  }

  const shapeType = normalizeShapeType(value.shape_type);
  if (!shapeType) {
    return null;
  }

  const rawPoints = Array.isArray(value.points) ? value.points : [];
  const normalizedPoints = rawPoints.map(toPoint);
  if (!normalizedPoints.length || normalizedPoints.some((point) => !point)) {
    return null;
  }

  const points = normalizedPoints.filter((point): point is Point => Boolean(point));
  const pointValidation = validateShapePoints(shapeType, points);
  if (!pointValidation.ok) {
    return null;
  }

  const rawLabel = typeof value.label === 'string' ? value.label : '';
  const parsedLabel = parseShapeLabel(rawLabel);

  return {
    id: createShapeId(),
    shapeType,
    labelText: parsedLabel.text,
    labelMode: parsedLabel.mode,
    labelFields: shapeLabelExtrasToFieldItems(parsedLabel.extras),
    points: clonePoints(points),
    flags: isRecord(value.flags) ? { ...value.flags } : {},
    groupId: value.group_id ?? null,
    originalShape: { ...value },
  };
};

const createEmptyDocument = (
  imageFilename: string,
  imageWidth: number,
  imageHeight: number
): LabelmeDocument => ({
  version: '5.1.7',
  flags: {},
  imagePath: imageFilename,
  imageData: null,
  imageWidth,
  imageHeight,
  extras: {},
  editableShapes: [],
  shapeOrder: [],
  defaultLabelMode: 'json',
  unsupportedShapeCount: 0,
});

const createOcrPlaceholderDocument = (sourceDoc: LabelmeDocument): LabelmeDocument => ({
  version: sourceDoc.version,
  flags: { ...sourceDoc.flags },
  imagePath: sourceDoc.imagePath,
  imageData: null,
  imageWidth: sourceDoc.imageWidth,
  imageHeight: sourceDoc.imageHeight,
  extras: { ...sourceDoc.extras },
  editableShapes: [],
  shapeOrder: [],
  defaultLabelMode: sourceDoc.defaultLabelMode,
  unsupportedShapeCount: 0,
});

const buildDocumentFromValue = (
  rawValue: unknown,
  imageFilename: string,
  imageWidth: number,
  imageHeight: number
): LabelmeDocument => {
  if (!isRecord(rawValue)) {
    return createEmptyDocument(imageFilename, imageWidth, imageHeight);
  }
  const parsed = rawValue;

  const sourceShapes = Array.isArray(parsed.shapes) ? parsed.shapes : [];
  const editableShapes: EditableShape[] = [];
  const shapeOrder: ShapeOrderEntry[] = [];
  let defaultLabelMode: LabelMode = 'json';

  for (const sourceShape of sourceShapes) {
    const normalizedShape = normalizeEditableShape(sourceShape);
    if (normalizedShape) {
      editableShapes.push(normalizedShape);
      shapeOrder.push({ kind: 'editable', id: normalizedShape.id });
      if (normalizedShape.labelMode === 'plain') {
        defaultLabelMode = 'plain';
      }
      continue;
    }

    if (isRecord(sourceShape)) {
      shapeOrder.push({ kind: 'passthrough', shape: { ...sourceShape } });
    }
  }

  const extras = { ...parsed };
  delete extras.version;
  delete extras.flags;
  delete extras.shapes;
  delete extras.imagePath;
  delete extras.imageData;
  delete extras.imageWidth;
  delete extras.imageHeight;

  return {
    version: typeof parsed.version === 'string' ? parsed.version : '5.1.7',
    flags: isRecord(parsed.flags) ? { ...parsed.flags } : {},
    imagePath: typeof parsed.imagePath === 'string' ? parsed.imagePath : imageFilename,
    imageData: null,
    imageWidth,
    imageHeight,
    extras,
    editableShapes,
    shapeOrder,
    defaultLabelMode,
    unsupportedShapeCount: shapeOrder.filter((entry) => entry.kind === 'passthrough').length,
  };
};

const buildDocumentFromText = (
  rawText: string,
  imageFilename: string,
  imageWidth: number,
  imageHeight: number
): LabelmeDocument => {
  if (!rawText.trim()) {
    return createEmptyDocument(imageFilename, imageWidth, imageHeight);
  }

  const parsed = JSON.parse(rawText);
  return buildDocumentFromValue(parsed, imageFilename, imageWidth, imageHeight);
};

const buildPayloadFromDocument = (doc: LabelmeDocument, item: DeviceAnnotationItem) => {
  const editableById = new Map(doc.editableShapes.map((shape) => [shape.id, shape]));
  const shapes = doc.shapeOrder
    .map((entry) => {
      if (entry.kind === 'passthrough') {
        return entry.shape;
      }

      const shape = editableById.get(entry.id);
      if (!shape) return null;

      return {
        ...(shape.originalShape ? { ...shape.originalShape } : {}),
        label: encodeShapeLabel(shape),
        points: shape.points.map((point) => [roundCoordinate(point.x), roundCoordinate(point.y)]),
        group_id: shape.groupId ?? null,
        shape_type: shape.shapeType,
        flags: shape.flags ?? {},
      };
    })
    .filter(Boolean);

  return {
    ...doc.extras,
    version: doc.version,
    flags: doc.flags,
    shapes,
    imagePath: item.name,
    imageData: null,
    imageHeight: doc.imageHeight,
    imageWidth: doc.imageWidth,
  };
};

const collectAnnotationSearchTokens = (value: unknown): string[] => {
  if (typeof value === 'string') {
    return [value];
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return [String(value)];
  }
  if (Array.isArray(value)) {
    return value.flatMap(collectAnnotationSearchTokens);
  }
  if (isRecord(value)) {
    return Object.entries(value).flatMap(([key, nestedValue]) => {
      if (ANNOTATION_SEARCH_EXCLUDED_KEYS.has(key)) {
        return [];
      }
      return [key, ...collectAnnotationSearchTokens(nestedValue)];
    });
  }
  return [];
};

const buildAnnotationSearchContentFromValue = (value: unknown) =>
  collectAnnotationSearchTokens(value).join('\n');

const buildAnnotationSearchContentFromText = (rawText: string) => {
  const trimmed = rawText.trim();
  if (!trimmed) {
    return '';
  }
  try {
    return buildAnnotationSearchContentFromValue(JSON.parse(trimmed));
  } catch {
    return rawText;
  }
};

const loadImageResourceFromBlob = async (blob: Blob) => {
  const url = URL.createObjectURL(blob);
  try {
    const dimensions = await new Promise<{ width: number; height: number }>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({
        width: image.naturalWidth || image.width,
        height: image.naturalHeight || image.height,
      });
      image.onerror = () => reject(new Error('Failed to decode image'));
      image.src = url;
    });

    return {
      url,
      width: dimensions.width,
      height: dimensions.height,
    };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
};

const replaceCurrentImageUrl = (nextUrl: string) => {
  if (currentImageUrl.value) {
    URL.revokeObjectURL(currentImageUrl.value);
  }
  currentImageUrl.value = nextUrl;
};

const setCurrentSourceDirty = (dirty: boolean) => {
  if (annotationSourceMode.value === 'ocr') {
    currentOcrDirty.value = dirty;
  } else {
    currentLabelmeDirty.value = dirty;
  }
};

const syncSelectedShapeForCurrentDoc = () => {
  const doc = currentDoc.value;
  if (!doc) {
    selectedShapeId.value = '';
    return;
  }
  if (selectedShapeId.value && doc.editableShapes.some((shape) => shape.id === selectedShapeId.value)) {
    return;
  }
  selectedShapeId.value = doc.editableShapes[0]?.id ?? '';
};

const clearCurrentDocument = () => {
  stopPan();
  ++ocrLoadVersion;
  currentItemId.value = '';
  currentLabelmeDoc.value = null;
  currentOcrDoc.value = null;
  selectedShapeId.value = '';
  draftRect.value = null;
  zoomMode.value = 'fit';
  zoomPercent.value = 100;
  isSpacePressed.value = false;
  toolMode.value = 'select';
  currentLabelmeDirty.value = false;
  currentOcrDirty.value = false;
  currentOcrStatus.value = 'idle';
  currentOcrError.value = '';
  if (currentImageUrl.value) {
    URL.revokeObjectURL(currentImageUrl.value);
    currentImageUrl.value = '';
  }
};

const mapAnnotationItem = (record: DeviceImageRecord): DeviceAnnotationItem | null => {
  if ((record.kind ?? 'image') !== 'image') {
    return null;
  }

  const absolutePath = record.absolute_path || record.path;
  if (!absolutePath) {
    return null;
  }

  return {
    id: record.id,
    name: record.name,
    relativePath: record.relative_path,
    folderPath: record.folder_path || '',
    absolutePath,
    jsonAbsolutePath: replaceExtension(absolutePath, '.json'),
    jsonFilename: `${stripExtension(record.name)}.json`,
    size: record.size,
    modifiedAt: record.modified_at,
    width: typeof record.width === 'number' ? record.width : null,
    height: typeof record.height === 'number' ? record.height : null,
    cachedShapeCount: null,
    annotationSearchContent: '',
    annotationSearchContentLoaded: false,
  };
};

const buildAnnotationItemFromAbsolutePath = (absolutePath: string): DeviceAnnotationItem | null => {
  const normalizedAbsolutePath = normalizePathInput(absolutePath);
  if (!normalizedAbsolutePath || isDeviceRootPath(normalizedAbsolutePath)) {
    return null;
  }
  if (hasFixedRootBoundary.value && !isPathWithinFixedRoot(normalizedAbsolutePath)) {
    return null;
  }

  const name = getFileNameFromPath(normalizedAbsolutePath);
  const folderPath = getAbsoluteParentPath(normalizedAbsolutePath);
  if (!name || !folderPath) {
    return null;
  }

  const relativePath = getRelativePathWithinBase(normalizedAbsolutePath, normalizedPathInput.value) || name;
  return {
    id: `absolute:${normalizedAbsolutePath}`,
    name,
    relativePath,
    folderPath,
    absolutePath: normalizedAbsolutePath,
    jsonAbsolutePath: replaceExtension(normalizedAbsolutePath, '.json'),
    jsonFilename: `${stripExtension(name)}.json`,
    size: 0,
    modifiedAt: 0,
    width: null,
    height: null,
    cachedShapeCount: null,
    annotationSearchContent: '',
    annotationSearchContentLoaded: false,
  };
};

const findAnnotationItemByTargetImage = (targetImage: string) => {
  const target = (targetImage || '').trim();
  if (!target) {
    return null;
  }

  if (isAbsolutePath(target)) {
    const comparableTarget = normalizeComparablePath(target);
    return annotationItems.value.find((item) => normalizeComparablePath(item.absolutePath) === comparableTarget) ?? null;
  }

  const comparableTarget = normalizeComparableRelativePath(target);
  return annotationItems.value.find((item) =>
    normalizeComparableRelativePath(item.relativePath) === comparableTarget
    || normalizeComparableRelativePath(item.name) === comparableTarget
  ) ?? null;
};

const ensureAnnotationItemForTargetImage = (targetImage: string) => {
  const existingItem = findAnnotationItemByTargetImage(targetImage);
  if (existingItem) {
    return existingItem;
  }
  if (!isAbsolutePath(targetImage)) {
    return null;
  }

  const directItem = buildAnnotationItemFromAbsolutePath(targetImage);
  if (!directItem) {
    return null;
  }
  annotationItems.value = [directItem, ...annotationItems.value];
  return directItem;
};

const updateItemCache = (item: DeviceAnnotationItem, doc: LabelmeDocument) => {
  item.cachedShapeCount = doc.editableShapes.length;
  if (item.annotationSearchContentLoaded) {
    item.annotationSearchContent = buildAnnotationSearchContentFromValue(buildPayloadFromDocument(doc, item));
  }
};

const syncCurrentItemCache = () => {
  if (!currentItem.value || !currentLabelmeDoc.value) return;
  updateItemCache(currentItem.value, currentLabelmeDoc.value);
};

const getShapeById = (shapeId: string) =>
  currentDoc.value?.editableShapes.find((shape) => shape.id === shapeId) ?? null;

const markDirty = () => {
  setCurrentSourceDirty(true);
  if (annotationSourceMode.value === 'labelme') {
    syncCurrentItemCache();
  }
};

const buildDirectoryListPayload = () => ({
  absolute_path: normalizedPathInput.value,
});

const buildMediaListPayload = () => ({
  absolute_path: normalizedPathInput.value,
  recursive: recursiveDisplay.value,
  scan_limit: mediaScanLimit.value,
  sort_mode: 'path' as const,
  limit: 0,
});

const buildItemPayload = (item: DeviceAnnotationItem, json = false): DeviceFileSelector => ({
  absolute_path: json ? item.jsonAbsolutePath : item.absolutePath,
});

const loadAnnotationSearchContent = async () => {
  if (
    !includeAnnotationContentSearch.value
    || !keyword.value.trim()
    || !selectedEntryId.value
    || !annotationItems.value.length
    || isLoadingAnnotationContentSearch.value
  ) {
    return;
  }

  const itemsToLoad = annotationItems.value.filter((item) => !item.annotationSearchContentLoaded);
  if (!itemsToLoad.length) {
    return;
  }

  const requestVersion = annotationContentSearchLoadVersion;
  const entryId = selectedEntryId.value;
  let nextIndex = 0;
  isLoadingAnnotationContentSearch.value = true;

  const loadNextItem = async () => {
    while (requestVersion === annotationContentSearchLoadVersion && entryId === selectedEntryId.value) {
      const item = itemsToLoad[nextIndex++];
      if (!item) {
        return;
      }

      try {
        const textResult = await fetchDeviceFileText(entryId, {
          absolute_path: item.jsonAbsolutePath,
        });
        if (requestVersion !== annotationContentSearchLoadVersion || entryId !== selectedEntryId.value) {
          return;
        }
        item.annotationSearchContent = buildAnnotationSearchContentFromText(textResult.text);
      } catch (error: any) {
        if (requestVersion !== annotationContentSearchLoadVersion || entryId !== selectedEntryId.value) {
          return;
        }
        if (error?.response?.status !== 404) {
          console.warn('Failed to load annotation content for search', error);
        }
        item.annotationSearchContent = '';
      }
      item.annotationSearchContentLoaded = true;
    }
  };

  try {
    await Promise.all(
      Array.from(
        { length: Math.min(ANNOTATION_SEARCH_CONTENT_CONCURRENCY, itemsToLoad.length) },
        loadNextItem
      )
    );
  } finally {
    if (requestVersion === annotationContentSearchLoadVersion && entryId === selectedEntryId.value) {
      isLoadingAnnotationContentSearch.value = false;
    }
  }
};

const confirmDiscardUnsavedChanges = (reason: string) => {
  if (!currentLabelmeDirty.value) return true;
  return window.confirm(`当前真实标注未保存，${reason}会丢失修改。是否继续？`);
};

const confirmOverwriteCurrentOcrEdits = () => {
  if (!currentOcrDirty.value) return true;
  return window.confirm('重新识别会覆盖当前 OCR 临时修改。是否继续？');
};

const applyActiveDocumentState = () => {
  selectedShapeId.value = currentDoc.value?.editableShapes[0]?.id ?? '';
  toolMode.value = 'select';
  draftRect.value = null;
};

const ensureOcrDocument = async (
  options: { force?: boolean; showErrorMessage?: boolean } = {}
) => {
  if (!selectedEntryId.value || !currentItem.value) return;
  if (!options.force && currentOcrDoc.value) {
    syncSelectedShapeForCurrentDoc();
    return;
  }
  if (options.force && !confirmOverwriteCurrentOcrEdits()) {
    return;
  }

  const requestVersion = ++ocrLoadVersion;
  currentOcrStatus.value = 'loading';
  currentOcrError.value = '';
  isLoadingOcr.value = true;
  if (!currentOcrDoc.value && currentLabelmeDoc.value) {
    currentOcrDoc.value = createOcrPlaceholderDocument(currentLabelmeDoc.value);
    currentOcrDirty.value = false;
  }

  try {
    const response = await fetchDeviceFileOcrPreview(selectedEntryId.value, {
      absolute_path: currentItem.value.absolutePath,
      shape_type: DEFAULT_OCR_SHAPE_TYPE,
    });
    if (requestVersion !== ocrLoadVersion || currentItem.value?.absolutePath !== response.absolute_path) {
      return;
    }

    const nextDoc = buildDocumentFromValue(
      response.document,
      currentItem.value.name,
      currentLabelmeDoc.value?.imageWidth ?? currentItem.value.width ?? 0,
      currentLabelmeDoc.value?.imageHeight ?? currentItem.value.height ?? 0
    );
    currentOcrDoc.value = nextDoc;
    currentOcrDirty.value = false;
    currentOcrStatus.value = 'ready';
    if (annotationSourceMode.value === 'ocr') {
      applyActiveDocumentState();
      await nextTick();
      bindStageResizeObserver();
      syncSelectedShapeForCurrentDoc();
    }
  } catch (error: any) {
    if (requestVersion !== ocrLoadVersion) {
      return;
    }
    currentOcrStatus.value = 'error';
    currentOcrError.value = error?.response?.data?.detail || error?.message || 'OCR 识别失败';
    if (options.showErrorMessage !== false) {
      ElMessage.error(currentOcrError.value);
    }
  } finally {
    if (requestVersion === ocrLoadVersion) {
      isLoadingOcr.value = false;
    }
  }
};

const rerunCurrentOcr = async () => {
  await ensureOcrDocument({ force: true });
};

const handleAnnotationSourceModeChange = async (nextMode: AnnotationSourceMode | string | number) => {
  const normalizedMode = nextMode === 'ocr' ? 'ocr' : 'labelme';
  if (normalizedMode === annotationSourceMode.value) {
    return;
  }
  annotationSourceMode.value = normalizedMode;
  applyActiveDocumentState();
  if (normalizedMode === 'ocr') {
    await ensureOcrDocument();
  } else {
    syncSelectedShapeForCurrentDoc();
  }
};

const openItemById = async (
  itemId: string,
  options: { skipConfirm?: boolean; routeMode?: 'replace' | 'push' | false } = {}
) => {
  const item = annotationItems.value.find((candidate) => candidate.id === itemId);
  if (!item) return;
  if (item.id === currentItemId.value && currentDoc.value) return;

  if (!options.skipConfirm && !confirmDiscardUnsavedChanges('切换图片')) {
    return;
  }

  if (!selectedEntryId.value) {
    return;
  }

  const requestVersion = ++itemLoadVersion;
  isLoadingItem.value = true;
  try {
    const imageBlob = await fetchDeviceFileBlob(selectedEntryId.value, buildItemPayload(item));
    const imageResource = await loadImageResourceFromBlob(imageBlob);

    try {
      let jsonText = '';
      try {
        const textResult = await fetchDeviceFileText(selectedEntryId.value, {
          absolute_path: item.jsonAbsolutePath,
        });
        jsonText = textResult.text;
      } catch (error: any) {
        if (error?.response?.status !== 404) {
          throw error;
        }
      }

      if (requestVersion !== itemLoadVersion) {
        URL.revokeObjectURL(imageResource.url);
        return;
      }

      const nextDoc = buildDocumentFromText(jsonText, item.name, imageResource.width, imageResource.height);
      item.annotationSearchContent = buildAnnotationSearchContentFromText(jsonText);
      item.annotationSearchContentLoaded = true;
      replaceCurrentImageUrl(imageResource.url);
      ++ocrLoadVersion;
      currentItemId.value = item.id;
      currentLabelmeDoc.value = nextDoc;
      currentLabelmeDirty.value = false;
      currentOcrDoc.value = null;
      currentOcrDirty.value = false;
      currentOcrStatus.value = 'idle';
      currentOcrError.value = '';
      if (annotationSourceMode.value === 'ocr') {
        currentOcrDoc.value = createOcrPlaceholderDocument(nextDoc);
      }
      applyActiveDocumentState();
      zoomMode.value = 'fit';
      updateItemCache(item, nextDoc);
      await nextTick();
      bindStageResizeObserver();
      await fitStageToViewport();
      if (annotationSourceMode.value === 'ocr') {
        void ensureOcrDocument({ showErrorMessage: false });
      }
      if (options.routeMode !== false) {
        await syncRouteQuery(options.routeMode ?? 'replace', { targetImage: item.absolutePath });
      }
    } catch (error) {
      URL.revokeObjectURL(imageResource.url);
      throw error;
    }
  } catch (error) {
    console.error('Failed to open device annotation item', error);
    ElMessage.error('加载图片或标注失败');
  } finally {
    if (requestVersion === itemLoadVersion) {
      isLoadingItem.value = false;
    }
  }
};

const openRouteTargetImageIfNeeded = async () => {
  const targetImage = getRouteTargetImage();
  if (!targetImage || !selectedEntryId.value) {
    return false;
  }

  const item = ensureAnnotationItemForTargetImage(targetImage);
  if (!item) {
    ElMessage.warning(`未找到可标注图片：${targetImage}`);
    return false;
  }

  await openItemById(item.id, { skipConfirm: true, routeMode: 'replace' });
  return true;
};

const saveCurrentDocument = async () => {
  if (!selectedEntryId.value || !currentItem.value || !currentLabelmeDoc.value || annotationSourceMode.value !== 'labelme') return;

  const shapeValidationErrors = collectDocumentShapeValidationErrors(currentLabelmeDoc.value);
  if (shapeValidationErrors.length) {
    const firstError = shapeValidationErrors[0];
    selectedShapeId.value = firstError.shapeId;
    ElMessage.error(`保存前请修正第 ${firstError.shapeIndex + 1} 个标注的点集：${firstError.message}`);
    return;
  }

  const labelValidationErrors = collectDocumentLabelFieldValidationErrors(currentLabelmeDoc.value);
  if (labelValidationErrors.length) {
    const firstError = labelValidationErrors[0];
    selectedShapeId.value = firstError.shapeId;
    ElMessage.error(`保存前请修正第 ${firstError.shapeIndex + 1} 个标注的属性：${firstError.message}`);
    return;
  }

  isSaving.value = true;
  try {
    const payload = buildPayloadFromDocument(currentLabelmeDoc.value, currentItem.value);
    await saveDeviceFileText(selectedEntryId.value, {
      absolute_path: currentItem.value.jsonAbsolutePath,
      text: `${JSON.stringify(payload, null, 2)}\n`,
    });

    currentLabelmeDirty.value = false;
    updateItemCache(currentItem.value, currentLabelmeDoc.value);
    ElMessage.success('标注已保存');
  } catch (error) {
    console.error('Failed to save device annotation', error);
    ElMessage.error('保存标注失败');
  } finally {
    isSaving.value = false;
  }
};

const renameCurrentLabelmeItem = async () => {
  if (!selectedEntryId.value || !currentItem.value) {
    return;
  }
  if (isDeviceRootPath(normalizedPathInput.value)) {
    ElMessage.warning('请先进入一个具体目录');
    return;
  }
  if (currentLabelmeDirty.value) {
    ElMessage.warning('请先保存当前标注，再重命名文件');
    return;
  }

  let targetRelativePath = '';
  try {
    const promptResult = await ElMessageBox.prompt(
      '输入相对当前目录的目标路径，例如 d/e.jpg。图片文件和同名 JSON 会一起移动。',
      '重命名图片与标注',
      {
        inputValue: currentItem.value.relativePath,
        inputPlaceholder: '例如 d/e.jpg',
        confirmButtonText: '重命名',
        cancelButtonText: '取消',
      }
    );
    targetRelativePath = String(promptResult.value ?? '').trim();
  } catch {
    return;
  }

  if (!targetRelativePath) {
    ElMessage.warning('请输入目标相对路径');
    return;
  }

  const sourceItem = currentItem.value;
  const executeRename = (overwrite: boolean) =>
    renameDeviceLabelmeAnnotation(selectedEntryId.value, {
      absolute_path: sourceItem.absolutePath,
      base_absolute_path: normalizedPathInput.value,
      target_relative_path: targetRelativePath,
      overwrite,
    });

  isRenamingLabelmeItem.value = true;
  try {
    let result;
    try {
      result = await executeRename(false);
    } catch (error: any) {
      if (error?.response?.status !== 409) {
        throw error;
      }

      const detail = error?.response?.data?.detail;
      const targetPath = detail?.target_relative_path || targetRelativePath;
      await ElMessageBox.confirm(
        `目标 "${targetPath}" 的图片或标注 JSON 已存在，是否覆盖？`,
        '确认覆盖',
        {
          type: 'warning',
          confirmButtonText: '覆盖',
          cancelButtonText: '取消',
        }
      );
      result = await executeRename(true);
    }

    ElMessage.success(result.overwritten ? '已覆盖并重命名' : '已重命名');
    await loadDirectory();
    const renamedItem = annotationItems.value.find(
      (item) =>
        item.absolutePath === result.target_image_absolute_path
        || item.relativePath === result.target_relative_path
    );
    if (renamedItem) {
      await openItemById(renamedItem.id, { skipConfirm: true });
    }
  } catch (error: any) {
    if (error === 'cancel' || error?.action === 'cancel') {
      return;
    }
    console.error('Failed to rename labelme annotation pair', error);
    ElMessage.error(error?.response?.data?.detail?.message || error?.response?.data?.detail || '重命名失败');
  } finally {
    isRenamingLabelmeItem.value = false;
  }
};

const loadDirectory = async () => {
  if (!canBrowse.value || isDeviceRootPath(normalizedPathInput.value)) {
    const requestVersion = ++directoryLoadVersion;
    isLoadingListing.value = true;
    try {
      if (isDeviceRootPath(normalizedPathInput.value) && selectedEntryId.value) {
        listing.value = await fetchDeviceDirectoryItems(selectedEntryId.value, buildDirectoryListPayload());
      } else {
        listing.value = null;
      }
      annotationItems.value = [];
      clearCurrentDocument();
      currentDirectoryPage.value = 1;
      currentFileListPage.value = 1;
      await syncRouteQuery();
    } catch (error) {
      console.error('Failed to load root directory list', error);
      if (requestVersion === directoryLoadVersion) {
        listing.value = null;
        annotationItems.value = [];
        clearCurrentDocument();
        ElMessage.error('读取目录失败');
      }
    } finally {
      if (requestVersion === directoryLoadVersion) {
        isLoadingListing.value = false;
      }
    }
    return;
  }

  const previousItemId = currentItemId.value;
  const requestVersion = ++directoryLoadVersion;
  const entryId = selectedEntryId.value;
  const pathValue = normalizedPathInput.value;
  isLoadingListing.value = true;
  currentDirectoryPage.value = 1;
  currentFileListPage.value = 1;
  try {
    const [directoryResult, mediaResult] = await Promise.all([
      fetchDeviceDirectoryItems(entryId, buildDirectoryListPayload()),
      fetchDeviceMedia(entryId, buildMediaListPayload()),
    ]);

    if (
      requestVersion !== directoryLoadVersion
      || selectedEntryId.value !== entryId
      || normalizedPathInput.value !== pathValue
    ) {
      return;
    }

    listing.value = directoryResult;
    const nextItems = mediaResult.media
      .map(mapAnnotationItem)
      .filter((item): item is DeviceAnnotationItem => Boolean(item));
    annotationItems.value = nextItems;

    await syncRouteQuery();

    if (await openRouteTargetImageIfNeeded()) {
      return;
    }

    if (previousItemId && nextItems.some((item) => item.id === previousItemId)) {
      currentItemId.value = previousItemId;
      return;
    }

    if (nextItems.length) {
      await openItemById(nextItems[0].id, { skipConfirm: true });
    } else {
      clearCurrentDocument();
    }
  } catch (error) {
    console.error('Failed to list device annotation directory', error);
    listing.value = null;
    annotationItems.value = [];
    clearCurrentDocument();
    ElMessage.error(
      normalizedPathInput.value
        ? `目录读取失败：${normalizedPathInput.value}`
        : '读取设备文件失败'
    );
  } finally {
    if (
      requestVersion === directoryLoadVersion
      && selectedEntryId.value === entryId
      && normalizedPathInput.value === pathValue
    ) {
      isLoadingListing.value = false;
    }
  }
};

const openRelativeItem = async (offset: number) => {
  const nextIndex = currentFilteredIndex.value + offset;
  if (nextIndex < 0 || nextIndex >= filteredItems.value.length) return;
  await openItemById(filteredItems.value[nextIndex].id);
};

const commitPathInput = async (options?: { load?: boolean; mode?: 'push' | 'replace' }) => {
  const rawNormalizedPath = normalizePathInput(pathInputValue.value);
  if (!rawNormalizedPath) {
    syncPathInputFromSelection();
    if (options?.load) {
      ElMessage.warning('请输入绝对路径');
    }
    return false;
  }

  const normalizedPath = applyPathConstraints(rawNormalizedPath, { fallbackToRoot: false });
  if (!normalizedPath) {
    syncPathInputFromSelection();
    if (options?.load && hasFixedRootBoundary.value) {
      ElMessage.warning(`当前页面只允许浏览 ${normalizedFixedRootPath.value} 及其子目录`);
    }
    return false;
  }

  if (!confirmDiscardUnsavedChanges('切换目录')) {
    syncPathInputFromSelection();
    return false;
  }

  selectedPath.value = normalizedPath;
  syncPathInputFromSelection();
  if (options?.load) {
    await syncRouteQuery(options.mode ?? 'push', { targetImage: null });
    await loadDirectory();
  }
  return true;
};

const handleSubmitPath = () => {
  void commitPathInput({ load: true, mode: 'push' });
};

const handlePathBlur = () => {
  void commitPathInput();
};

const openDirectory = async (path: string) => {
  if (!confirmDiscardUnsavedChanges('切换目录')) {
    return;
  }
  selectedPath.value = path;
  syncPathInputFromSelection();
  await syncRouteQuery('push', { targetImage: null });
  await loadDirectory();
};

const goToParentDirectory = async () => {
  if (!canGoUp.value) {
    return;
  }
  if (!confirmDiscardUnsavedChanges('切换目录')) {
    return;
  }
  selectedPath.value = getParentPathWithinConstraints(normalizedPathInput.value);
  syncPathInputFromSelection();
  await syncRouteQuery('push', { targetImage: null });
  await loadDirectory();
};

const handleDirectoryPageChange = (page: number) => {
  currentDirectoryPage.value = Math.min(directoryPageCount.value, Math.max(1, Math.floor(page || 1)));
};

const handleFileListPageChange = (page: number) => {
  currentFileListPage.value = Math.min(fileListPageCount.value, Math.max(1, Math.floor(page || 1)));
};

const handleSelectedEntryChange = async (nextEntryId: string) => {
  if (isDeviceLocked.value) {
    return;
  }
  if (nextEntryId === selectedEntryId.value) {
    return;
  }
  if (!confirmDiscardUnsavedChanges('切换设备')) {
    return;
  }
  selectedEntryId.value = nextEntryId;
};

const handleRecursiveDisplayChange = async (nextValue: boolean) => {
  if (nextValue === recursiveDisplay.value) {
    return;
  }
  if (!confirmDiscardUnsavedChanges('刷新文件列表')) {
    return;
  }
  recursiveDisplay.value = nextValue;
  persistRecursiveDisplay(storageKey.value, nextValue);
  if (canBrowse.value) {
    await loadDirectory();
  }
};

const handleMediaScanLimitChange = async (nextLimit?: number) => {
  const normalizedLimit = normalizeMediaScanLimit(nextLimit);
  mediaScanLimitInput.value = normalizedLimit;
  if (normalizedLimit === mediaScanLimit.value) {
    return;
  }
  if (!confirmDiscardUnsavedChanges('刷新文件列表')) {
    mediaScanLimitInput.value = mediaScanLimit.value;
    return;
  }
  mediaScanLimit.value = normalizedLimit;
  persistMediaScanLimit(storageKey.value, normalizedLimit);
  if (canBrowse.value) {
    await loadDirectory();
  }
};

const selectShape = (shapeId: string) => {
  selectedShapeId.value = shapeId;
  toolMode.value = 'select';
};

const toggleDrawMode = () => {
  closeStageContextMenu();
  if (!currentDoc.value) return;
  if (toolMode.value === 'draw') {
    toolMode.value = 'select';
    draftRect.value = null;
    return;
  }
  selectedShapeId.value = '';
  toolMode.value = 'draw';
};

const beginRectangleDraw = (point: Point) => {
  draftRect.value = { x1: point.x, y1: point.y, x2: point.x, y2: point.y };
  beginDrag({
    mode: 'draw',
    anchor: point,
    initialPoints: [point],
  });
};

const addShape = (rect: Rect) => {
  if (!currentDoc.value) return;
  const normalizedRect = normalizeRect(rect);
  if ((normalizedRect.x2 - normalizedRect.x1) < MIN_RECT_EDGE || (normalizedRect.y2 - normalizedRect.y1) < MIN_RECT_EDGE) {
    return;
  }

  const shape: EditableShape = {
    id: createShapeId(),
    shapeType: 'rectangle',
    labelText: DEFAULT_LABEL_TEXT,
    labelMode: currentDoc.value.defaultLabelMode,
    labelFields: currentDoc.value.defaultLabelMode === 'json'
      ? [createShapeLabelFieldItem('score', 'string', '-1')]
      : [],
    points: [
      { x: normalizedRect.x1, y: normalizedRect.y1 },
      { x: normalizedRect.x2, y: normalizedRect.y2 },
    ],
    flags: {},
    groupId: null,
    originalShape: null,
  };

  currentDoc.value.editableShapes.push(shape);
  currentDoc.value.shapeOrder.push({ kind: 'editable', id: shape.id });
  selectedShapeId.value = shape.id;
  toolMode.value = 'select';
  markDirty();
};

const createDefaultRectAtPoint = (point: Point): Rect | null => {
  if (!currentDoc.value) return null;
  const rectWidth = clampNumber(roundCoordinate(currentDoc.value.imageWidth * 0.12), 48, 180);
  const rectHeight = clampNumber(roundCoordinate(currentDoc.value.imageHeight * 0.08), 40, 120);
  const halfWidth = rectWidth / 2;
  const halfHeight = rectHeight / 2;
  return ensureRectWithinBounds(
    normalizeRect({
      x1: point.x - halfWidth,
      y1: point.y - halfHeight,
      x2: point.x + halfWidth,
      y2: point.y + halfHeight,
    }),
    currentDoc.value.imageWidth,
    currentDoc.value.imageHeight
  );
};

const createRectangleFromContextMenu = () => {
  const point = stageContextMenu.value.imagePoint;
  closeStageContextMenu();
  if (!point) return;
  const rect = createDefaultRectAtPoint(point);
  if (!rect) return;
  addShape(rect);
};

const deleteSelectedShape = () => {
  if (!currentDoc.value || !selectedShape.value) return;
  const targetId = selectedShape.value.id;
  currentDoc.value.editableShapes = currentDoc.value.editableShapes.filter((shape) => shape.id !== targetId);
  currentDoc.value.shapeOrder = currentDoc.value.shapeOrder.filter(
    (entry) => entry.kind !== 'editable' || entry.id !== targetId
  );
  selectedShapeId.value = currentDoc.value.editableShapes[0]?.id ?? '';
  markDirty();
};

const updateSelectedShapeLabel = (value: string | number) => {
  if (!selectedShape.value) return;
  selectedShape.value.labelText = String(value ?? '');
  markDirty();
};

const getShapeLabelFieldTextValue = (item: ShapeLabelFieldItem) =>
  String(item.value ?? '');

const hasSelectedShapeLabelFieldError = (fieldLocalId: string) =>
  selectedShapeLabelFieldErrors.value.some((error) => error.fieldLocalId === fieldLocalId);

const handleSelectedShapeLabelFieldChange = () => {
  if (!selectedShape.value) return;
  markDirty();
};

const setShapeLabelFieldTextValue = (item: ShapeLabelFieldItem, value: string | number) => {
  item.value = String(value ?? '');
  handleSelectedShapeLabelFieldChange();
};

const addSelectedShapeLabelField = () => {
  if (!selectedShape.value) return;
  selectedShape.value.labelFields.push(createShapeLabelFieldItem());
  markDirty();
};

const removeSelectedShapeLabelField = (index: number) => {
  if (!selectedShape.value) return;
  selectedShape.value.labelFields.splice(index, 1);
  markDirty();
};

const moveSelectedShapeLabelField = (oldIndex: number, newIndex: number) => {
  if (!selectedShape.value) return;
  const reordered = [...selectedShape.value.labelFields];
  const [moved] = reordered.splice(oldIndex, 1);
  if (!moved) return;
  reordered.splice(newIndex, 0, moved);
  selectedShape.value.labelFields = reordered;
  markDirty();
};

useSortableList({
  listRef: selectedShapeLabelFieldsListRef,
  getDeps: () => [selectedShapeId.value, selectedShapeLabelFields.value.length] as const,
  isEnabled: () => Boolean(selectedShape.value) && selectedShapeLabelFields.value.length > 1,
  onReorder: moveSelectedShapeLabelField,
});

const canAppendPointToShape = (shape: EditableShape | null) =>
  shape?.shapeType === 'polygon' || shape?.shapeType === 'linestrip';

const canRemovePointFromShape = (shape: EditableShape, pointIndex: number) =>
  pointIndex >= 0 && pointIndex < shape.points.length && shape.points.length > SHAPE_MIN_POINT_COUNTS[shape.shapeType];

const selectedShapeCanAppendPoint = computed(() => canAppendPointToShape(selectedShape.value));

const getSuggestedNextPoint = (shape: EditableShape, width: number, height: number): Point => {
  const lastPoint = shape.points[shape.points.length - 1] ?? { x: width / 2, y: height / 2 };
  const previousPoint = shape.points[shape.points.length - 2] ?? null;
  if (!previousPoint) {
    return clampPointWithinBounds({ x: lastPoint.x + 24, y: lastPoint.y + 24 }, width, height);
  }

  const deltaX = Math.abs(lastPoint.x - previousPoint.x) < 1 ? 24 : (lastPoint.x - previousPoint.x);
  const deltaY = Math.abs(lastPoint.y - previousPoint.y) < 1 ? 24 : (lastPoint.y - previousPoint.y);
  return clampPointWithinBounds(
    {
      x: lastPoint.x + deltaX,
      y: lastPoint.y + deltaY,
    },
    width,
    height
  );
};

const appendPointToSelectedShape = () => {
  if (!selectedShape.value || !currentDoc.value || !canAppendPointToShape(selectedShape.value)) return;
  selectedShape.value.points = [
    ...selectedShape.value.points,
    getSuggestedNextPoint(selectedShape.value, currentDoc.value.imageWidth, currentDoc.value.imageHeight),
  ];
  markDirty();
};

const removeSelectedShapePoint = (pointIndex: number) => {
  if (!selectedShape.value || !canRemovePointFromShape(selectedShape.value, pointIndex)) return;
  selectedShape.value.points = selectedShape.value.points.filter((_, index) => index !== pointIndex);
  markDirty();
};

const updateSelectedShapePointCoordinate = (
  pointIndex: number,
  axis: ShapeCoordinateAxis,
  value: string | number | null | undefined
) => {
  if (!selectedShape.value || !currentDoc.value || value === null || value === undefined) return;
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return;

  const nextPoints = clonePoints(selectedShape.value.points);
  const targetPoint = nextPoints[pointIndex];
  if (!targetPoint) return;
  targetPoint[axis] = roundCoordinate(clampNumber(
    numericValue,
    0,
    axis === 'x' ? currentDoc.value.imageWidth : currentDoc.value.imageHeight
  ));
  selectedShape.value.points = nextPoints;
  markDirty();
};

const getPointerInImage = (event: MouseEvent, clamp = false): Point | null => {
  if (!stageRef.value || !currentDoc.value) return null;
  const bounds = stageRef.value.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) return null;

  const relativeX = event.clientX - bounds.left;
  const relativeY = event.clientY - bounds.top;
  if (!clamp && (relativeX < 0 || relativeY < 0 || relativeX > bounds.width || relativeY > bounds.height)) {
    return null;
  }

  const x = (relativeX / bounds.width) * currentDoc.value.imageWidth;
  const y = (relativeY / bounds.height) * currentDoc.value.imageHeight;
  return {
    x: roundCoordinate(clamp ? clampNumber(x, 0, currentDoc.value.imageWidth) : x),
    y: roundCoordinate(clamp ? clampNumber(y, 0, currentDoc.value.imageHeight) : y),
  };
};

const stopDrag = () => {
  activeDrag.value = null;
  document.removeEventListener('mousemove', handleWindowMouseMove);
  document.removeEventListener('mouseup', handleWindowMouseUp);
};

const beginDrag = (state: DragState) => {
  activeDrag.value = state;
  document.addEventListener('mousemove', handleWindowMouseMove);
  document.addEventListener('mouseup', handleWindowMouseUp);
};

const translatePointsWithinBounds = (
  initialPoints: Point[],
  deltaX: number,
  deltaY: number,
  width: number,
  height: number
): Point[] => {
  if (!initialPoints.length) return [];
  const minX = Math.min(...initialPoints.map((point) => point.x));
  const maxX = Math.max(...initialPoints.map((point) => point.x));
  const minY = Math.min(...initialPoints.map((point) => point.y));
  const maxY = Math.max(...initialPoints.map((point) => point.y));
  const nextDeltaX = clampNumber(deltaX, -minX, width - maxX);
  const nextDeltaY = clampNumber(deltaY, -minY, height - maxY);
  return initialPoints.map((point) => ({
    x: point.x + nextDeltaX,
    y: point.y + nextDeltaY,
  }));
};

const handleStageMouseDown = (event: MouseEvent) => {
  closeStageContextMenu();
  if (!currentDoc.value) return;
  const point = getPointerInImage(event);
  if (!point) return;

  if (toolMode.value !== 'draw' || event.button !== 0) return;
  event.preventDefault();
  beginRectangleDraw(point);
};

const handleShapeMouseDown = (shapeId: string, event: MouseEvent) => {
  if (!currentDoc.value || toolMode.value === 'draw' || event.button !== 0) return;
  const point = getPointerInImage(event);
  const shape = getShapeById(shapeId);
  if (!point || !shape) return;

  selectedShapeId.value = shapeId;
  beginDrag({
    mode: 'move',
    shapeId,
    anchor: point,
    initialPoints: clonePoints(shape.points),
  });
};

const handleShapePointMouseDown = (shapeId: string, pointIndex: number, event: MouseEvent) => {
  if (!currentDoc.value || toolMode.value === 'draw' || event.button !== 0) return;
  const point = getPointerInImage(event);
  const shape = getShapeById(shapeId);
  if (!point || !shape) return;

  selectedShapeId.value = shapeId;
  beginDrag({
    mode: 'move-point',
    shapeId,
    pointIndex,
    anchor: point,
    initialPoints: clonePoints(shape.points),
  });
};

function handleWindowMouseMove(event: MouseEvent) {
  if (!activeDrag.value || !currentDoc.value) return;
  const point = getPointerInImage(event, true);
  if (!point) return;

  if (activeDrag.value.mode === 'draw') {
    draftRect.value = normalizeRect({
      x1: activeDrag.value.anchor.x,
      y1: activeDrag.value.anchor.y,
      x2: point.x,
      y2: point.y,
    });
    return;
  }

  const shape = getShapeById(activeDrag.value.shapeId || '');
  if (!shape) return;

  if (activeDrag.value.mode === 'move') {
    shape.points = translatePointsWithinBounds(
      activeDrag.value.initialPoints,
      point.x - activeDrag.value.anchor.x,
      point.y - activeDrag.value.anchor.y,
      currentDoc.value.imageWidth,
      currentDoc.value.imageHeight
    );
    markDirty();
    return;
  }

  if (activeDrag.value.mode === 'move-point' && typeof activeDrag.value.pointIndex === 'number') {
    const nextPoints = clonePoints(activeDrag.value.initialPoints);
    const targetPoint = nextPoints[activeDrag.value.pointIndex];
    if (!targetPoint) return;
    nextPoints[activeDrag.value.pointIndex] = clampPointWithinBounds(
      point,
      currentDoc.value.imageWidth,
      currentDoc.value.imageHeight
    );
    shape.points = nextPoints;
    markDirty();
  }
}

function handleWindowMouseUp() {
  if (activeDrag.value?.mode === 'draw' && draftRect.value) {
    addShape(draftRect.value);
  }
  draftRect.value = null;
  stopDrag();
}

const handleWindowKeyDown = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null;
  const isEditingText =
    !!target &&
    (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);

  if (!isEditingText && event.code === 'Space') {
    isSpacePressed.value = true;
    event.preventDefault();
    return;
  }

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    void saveCurrentDocument();
    return;
  }

  if (!isEditingText && (event.ctrlKey || event.metaKey) && !event.altKey) {
    if (event.key === '+' || event.key === '=' || event.code === 'NumpadAdd') {
      event.preventDefault();
      zoomIn();
      return;
    }
    if (event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract') {
      event.preventDefault();
      zoomOut();
      return;
    }
    if (event.key === '0' || event.code === 'Numpad0') {
      event.preventDefault();
      resetZoom();
      return;
    }
  }

  if (event.key === 'Escape') {
    if (stageContextMenu.value.visible) {
      closeStageContextMenu();
      return;
    }
    if (toolMode.value === 'draw' || activeDrag.value?.mode === 'draw') {
      toolMode.value = 'select';
      draftRect.value = null;
      stopDrag();
    }
    return;
  }

  if (!isEditingText && event.key === 'Delete') {
    event.preventDefault();
    deleteSelectedShape();
  }
};

const handleWindowKeyUp = (event: KeyboardEvent) => {
  if (event.code === 'Space') {
    isSpacePressed.value = false;
  }
};

const handleWindowBlur = () => {
  isSpacePressed.value = false;
  closeStageContextMenu();
  stopPan();
};

function handleDocumentMouseDown(event: MouseEvent) {
  if (!stageContextMenu.value.visible) return;
  const target = event.target as Node | null;
  if (target && stageContextMenuRef.value?.contains(target)) {
    return;
  }
  closeStageContextMenu();
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!currentLabelmeDirty.value) return;
  event.preventDefault();
  event.returnValue = '';
}

watch(annotationItems, () => {
  annotationContentSearchLoadVersion += 1;
  isLoadingAnnotationContentSearch.value = false;
  if (includeAnnotationContentSearch.value && keyword.value.trim()) {
    void loadAnnotationSearchContent();
  }
});

watch(includeAnnotationContentSearch, (nextValue) => {
  if (!nextValue) {
    annotationContentSearchLoadVersion += 1;
    isLoadingAnnotationContentSearch.value = false;
    return;
  }
  if (keyword.value.trim()) {
    void loadAnnotationSearchContent();
  }
});

watch(
  () => keyword.value.trim(),
  (nextKeyword) => {
    if (!nextKeyword) {
      annotationContentSearchLoadVersion += 1;
      isLoadingAnnotationContentSearch.value = false;
      return;
    }
    if (includeAnnotationContentSearch.value) {
      void loadAnnotationSearchContent();
    }
  }
);

watch(selectedPath, (nextPath) => {
  persistSelectedPath(selectedEntryId.value, nextPath || DEVICE_ROOT_SENTINEL);
  syncPathInputFromSelection();
});

watch(directoryPageCount, (nextPageCount) => {
  if (currentDirectoryPage.value > nextPageCount) {
    currentDirectoryPage.value = nextPageCount;
  }
});

watch(fileListPageCount, (nextPageCount) => {
  if (currentFileListPage.value > nextPageCount) {
    currentFileListPage.value = nextPageCount;
  }
});

watch(currentFilteredIndex, (nextIndex) => {
  if (nextIndex < 0) {
    currentFileListPage.value = 1;
    return;
  }
  currentFileListPage.value = Math.floor(nextIndex / FILE_LIST_PAGE_SIZE) + 1;
});

watch(
  () => [currentItemId.value, annotationSourceMode.value],
  () => {
    closeStageContextMenu();
  }
);

watch(stageScrollRef, () => {
  bindStageResizeObserver();
});

watch(
  () => [
    currentDoc.value?.imageWidth ?? 0,
    currentDoc.value?.imageHeight ?? 0,
    stageViewportSize.value.width,
    stageViewportSize.value.height,
    zoomMode.value,
  ] as const,
  ([imageWidth, imageHeight, viewportWidth, viewportHeight, nextZoomMode]) => {
    if (!imageWidth || !imageHeight || !viewportWidth || !viewportHeight || nextZoomMode !== 'fit') {
      return;
    }
    void fitStageToViewport();
  }
);

watch(
  () => [
    route.query.entry_id,
    route.query.device_id,
    route.query.device,
    route.query.path,
    route.query.image,
    route.query.image_path,
    route.query.file,
    route.query.absolute_path,
  ],
  async () => {
    const previousEntryId = selectedEntryId.value;
    const previousPath = selectedPath.value;
    const changed = applyRouteSelectionFromQuery();
    if (changed && (previousEntryId !== selectedEntryId.value || previousPath !== selectedPath.value)) {
      if (previousEntryId === selectedEntryId.value && canBrowse.value) {
        await loadDirectory();
      }
      return;
    }
    if (canBrowse.value) {
      await openRouteTargetImageIfNeeded();
    }
  }
);

watch(lockedEntryId, (nextEntryId) => {
  if (!isDeviceLocked.value) {
    return;
  }
  if (!nextEntryId) {
    if (selectedEntryId.value) {
      selectedEntryId.value = '';
    }
    return;
  }
  if (selectedEntryId.value !== nextEntryId) {
    selectedEntryId.value = nextEntryId;
  }
});

watch(selectedEntryId, async (nextEntryId) => {
  if (isDeviceLocked.value && lockedEntryId.value && nextEntryId !== lockedEntryId.value) {
    selectedEntryId.value = lockedEntryId.value;
    return;
  }
  persistSelectedEntryId(nextEntryId);
  listing.value = null;
  annotationItems.value = [];
  clearCurrentDocument();

  const nextStorageKey = `device_labelme_browser_${nextEntryId || 'default'}`;
  mediaScanLimit.value = loadPersistedMediaScanLimit(nextStorageKey);
  mediaScanLimitInput.value = mediaScanLimit.value;
  recursiveDisplay.value = loadPersistedRecursiveDisplay(nextStorageKey);
  selectedPath.value = resolveInitialPath(nextEntryId);
  syncPathInputFromSelection();

  await syncRouteQuery();
  if (canBrowse.value) {
    await loadDirectory();
  }
});

onBeforeRouteLeave(() => {
  if (!currentLabelmeDirty.value) return true;
  return window.confirm('当前真实标注未保存，离开页面会丢失修改。是否继续？');
});

if (typeof window !== 'undefined') {
  window.addEventListener('keydown', handleWindowKeyDown);
  window.addEventListener('keyup', handleWindowKeyUp);
  window.addEventListener('blur', handleWindowBlur);
  window.addEventListener('beforeunload', handleBeforeUnload);
  document.addEventListener('mousedown', handleDocumentMouseDown);
}

onMounted(async () => {
  isLoadingDevices.value = true;
  try {
    await taskStore.fetchDevices();
  } finally {
    isLoadingDevices.value = false;
  }

  if (!devices.value.length) {
    selectedEntryId.value = '';
    return;
  }

  if (isDeviceLocked.value) {
    if (!lockedEntryId.value) {
      selectedEntryId.value = '';
      return;
    }
    if (selectedEntryId.value !== lockedEntryId.value) {
      selectedEntryId.value = lockedEntryId.value;
      return;
    }
  } else if (!selectedEntryId.value || !devices.value.some((device) => device.id === selectedEntryId.value)) {
    selectedEntryId.value = devices.value[0].id;
    return;
  }

  applyRouteSelectionFromQuery();
  await syncRouteQuery();
  if (canBrowse.value) {
    await loadDirectory();
  }
});

onBeforeUnmount(() => {
  stopPan();
  stopDrag();
  stageResizeObserver?.disconnect();
  stageResizeObserver = null;
  if (currentImageUrl.value) {
    URL.revokeObjectURL(currentImageUrl.value);
  }
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', handleWindowKeyDown);
    window.removeEventListener('keyup', handleWindowKeyUp);
    window.removeEventListener('blur', handleWindowBlur);
    window.removeEventListener('beforeunload', handleBeforeUnload);
    document.removeEventListener('mousedown', handleDocumentMouseDown);
  }
});
</script>

<style scoped>
.device-file-page {
  --annotation-editor-height: 80vh;
  min-height: 100%;
  padding: 24px;
  box-sizing: border-box;
  background:
    radial-gradient(circle at top left, rgba(14, 116, 144, 0.1), transparent 24%),
    radial-gradient(circle at top right, rgba(22, 163, 74, 0.08), transparent 20%),
    linear-gradient(180deg, #f2f8fb 0%, #edf4f5 100%);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.empty-panel,
.browser-panel,
.panel-card {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
}

.empty-panel {
  padding: 56px 24px;
  text-align: center;
}

.empty-badge {
  width: fit-content;
  min-width: 96px;
  height: 42px;
  margin: 0 auto 18px;
  padding: 0 18px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f766e 0%, #2563eb 100%);
  color: #ffffff;
  font-size: 13px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  box-shadow: 0 18px 36px rgba(15, 118, 110, 0.24);
}

.empty-badge.subtle {
  margin: 0 0 12px;
  min-width: auto;
  height: auto;
  padding: 6px 12px;
  background: #edf3f7;
  color: #486070;
  box-shadow: none;
}

.empty-panel h2 {
  margin: 0 0 10px;
  color: #0f172a;
}

.empty-panel p {
  max-width: 620px;
  margin: 0 auto;
  color: #475569;
}

.empty-actions {
  margin-top: 22px;
}

.browser-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 640px;
}

.annotation-browser-top {
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

.device-directory-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.annotation-overview-panel {
  justify-content: flex-start;
}

.overview-stats {
  display: grid;
  gap: 12px;
}

.overview-stat-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.overview-stat-label {
  color: #64748b;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.overview-stat-card strong {
  font-size: 28px;
  line-height: 1;
  color: #0f172a;
}

.overview-stat-card span {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.overview-meta-grid {
  grid-template-columns: 32px minmax(0, 1fr);
}

.directory-config-row {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 220px;
  gap: 16px;
  align-items: end;
}

.directory-config-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.directory-config-label {
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.directory-config-select,
.directory-config-limit {
  width: 100%;
}

.directory-config-row :deep(.el-select__wrapper),
.directory-config-row :deep(.el-input-number) {
  border-radius: 16px;
}

.directory-config-row :deep(.el-input-number) {
  width: 100%;
}

.directory-config-row :deep(.el-input__inner),
.directory-config-row :deep(.el-input-number .el-input__inner) {
  text-align: left;
}

.directory-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-width: 0;
}

.directory-path-input {
  flex: 1 1 420px;
  min-width: 280px;
}

.directory-action-button {
  min-width: 104px;
}

.directory-recursive-toggle {
  align-self: center;
  display: inline-flex;
  align-items: center;
  height: 40px;
  margin: 0;
}

.directory-recursive-toggle :deep(.el-switch__core) {
  height: 40px;
  min-height: 40px;
  border-radius: 999px;
}

.directory-recursive-toggle :deep(.el-switch__core .el-switch__inner .is-text) {
  font-size: 13px;
  font-weight: 600;
}

.directory-recursive-toggle :deep(.el-switch__action) {
  width: 24px;
  height: 24px;
}

.directory-section-count {
  margin-left: auto;
  min-width: 58px;
  min-height: 36px;
  padding: 0 10px;
  border-radius: 999px;
  background: #f8fafc;
  border: 1px solid rgba(148, 163, 184, 0.2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #64748b;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.directory-toolbar :deep(.el-input__wrapper) {
  border-radius: 16px;
}

.directory-fixed-root-hint {
  padding: 10px 12px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px solid rgba(148, 163, 184, 0.22);
  color: #556877;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.directory-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 6px;
  align-content: start;
}

.directory-chip {
  border: none;
  border-radius: 10px;
  background: rgba(248, 250, 252, 0.72);
  padding: 7px 10px;
  width: 100%;
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #0f172a;
  cursor: pointer;
  font-size: 11px;
  text-align: left;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.directory-chip:hover,
.directory-chip:focus-visible {
  background: rgba(239, 246, 255, 0.96);
  color: #1d4ed8;
}

.directory-chip-icon {
  color: #b45309;
  flex-shrink: 0;
  font-size: 12px;
}

.directory-chip-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.directory-pagination {
  display: flex;
  justify-content: flex-end;
}

.directory-empty-state {
  min-height: 116px;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.34);
  background:
    radial-gradient(circle at top right, rgba(59, 130, 246, 0.08), transparent 34%),
    linear-gradient(180deg, rgba(248, 250, 252, 0.98) 0%, rgba(241, 245, 249, 0.95) 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  color: #64748b;
  text-align: center;
}

.annotation-file-panel-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.annotation-file-pagination {
  display: flex;
  justify-content: flex-end;
}

.annotation-editor-layout {
  flex: 0 0 auto;
  min-height: 0;
  height: var(--annotation-editor-height);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
}

.panel-card {
  min-height: 0;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.annotation-source-switch {
  flex: 0 0 auto;
}

.annotation-source-switch :deep(.el-radio-button__inner) {
  min-width: 64px;
  padding-inline: 12px;
}

.section-kicker {
  color: #64748b;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.panel-section-head h3,
.annotation-main-empty h3 {
  margin: 0;
  color: #0f172a;
  font-size: 22px;
  line-height: 1.15;
}

.annotation-search :deep(.el-input__wrapper) {
  border-radius: 14px;
}

.annotation-search-options {
  margin-top: 8px;
  min-height: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.annotation-search-options :deep(.el-checkbox) {
  height: auto;
  margin-right: 0;
}

.annotation-search-loading {
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.annotation-item-list,
.shape-list {
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.annotation-item-list-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  overflow: visible;
}

.annotation-item-card,
.shape-card {
  border: 1px solid #dbe4ea;
  border-radius: 14px;
  background: #ffffff;
  padding: 10px;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}

.annotation-item-card {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  min-height: 52px;
}

.annotation-item-card:hover,
.annotation-item-card:focus-visible,
.shape-card:hover,
.shape-card:focus-visible {
  border-color: #c8d4dc;
  box-shadow: 0 10px 20px rgba(15, 23, 42, 0.06);
}

.annotation-item-card.is-active,
.shape-card.is-active {
  border-color: #d35f1a;
  background: #fff6f0;
}

.annotation-item-index,
.shape-card-index {
  width: 28px;
  height: 28px;
  border-radius: 10px;
  background: #eef3f7;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #435867;
  font-size: 12px;
  font-weight: 700;
}

.annotation-item-main {
  min-width: 0;
  display: block;
}

.annotation-item-name,
.shape-card-label {
  display: block;
  min-width: 0;
  font-size: 14px;
  font-weight: 700;
  color: #163042;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.annotation-inline-empty {
  margin-top: 2px;
  color: #728392;
  font-size: 12px;
  line-height: 1.4;
}

.annotation-stage-panel {
  padding: 0;
  overflow: hidden;
}

.annotation-inspector-panel {
  min-height: 0;
}

.stage-toolbar {
  padding: 18px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.stage-toolbar-main {
  min-width: 0;
}

.stage-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.stage-title {
  font-size: 22px;
  font-weight: 800;
  color: #173042;
}

.stage-path {
  margin-top: 6px;
  color: #687b8a;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-meta {
  margin-top: 4px;
  color: #8a99a6;
  font-size: 12px;
  line-height: 1.4;
}

.stage-toolbar-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.zoom-fit-button {
  padding-inline: 4px;
}

.zoom-control {
  width: 220px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  color: #556877;
  font-size: 13px;
}

.help-popover {
  display: flex;
  flex-direction: column;
  gap: 8px;
  line-height: 1.6;
  color: #4d6170;
}

.stage-body {
  flex: 1;
  min-height: 0;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stage-hint {
  color: #5d7080;
  font-size: 13px;
}

.stage-context-menu {
  position: fixed;
  z-index: 2200;
  min-width: 132px;
  padding: 6px;
  border: 1px solid rgba(209, 221, 232, 0.92);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(10px);
}

.stage-context-menu-item {
  width: 100%;
  border: 0;
  border-radius: 10px;
  background: transparent;
  padding: 9px 12px;
  text-align: left;
  color: #173042;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.stage-context-menu-item:hover {
  background: rgba(64, 158, 255, 0.08);
}

.stage-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border-top: 1px solid rgba(148, 163, 184, 0.14);
  background:
    linear-gradient(180deg, rgba(243, 248, 251, 0.95), rgba(235, 242, 247, 0.92)),
    radial-gradient(circle at top left, rgba(235, 161, 84, 0.18), transparent 28%);
  padding: 16px;
}

.stage-scroll.is-pan-ready,
.stage-scroll.is-pan-ready * {
  cursor: grab !important;
}

.stage-scroll.is-panning,
.stage-scroll.is-panning * {
  cursor: grabbing !important;
}

.stage-workspace {
  min-width: 100%;
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
}

.annotation-stage {
  position: relative;
  flex: 0 0 auto;
  user-select: none;
}

.stage-image,
.stage-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}

.stage-image {
  object-fit: contain;
}

.annotation-shape {
  fill: rgba(27, 132, 232, 0.12);
  stroke: #1b84e8;
  cursor: move;
}

.annotation-shape.is-selected {
  fill: rgba(211, 95, 26, 0.18);
  stroke: #d35f1a;
}

.annotation-shape.is-draw-mode {
  pointer-events: none;
}

.annotation-shape--line,
.annotation-shape--linestrip {
  fill: none;
  pointer-events: none;
}

.annotation-hit-stroke {
  fill: none;
  stroke: rgba(27, 132, 232, 0.001);
  stroke-linecap: round;
  stroke-linejoin: round;
  cursor: move;
}

.annotation-hit-stroke.is-draw-mode {
  pointer-events: none;
}

.annotation-handle {
  fill: #ffffff;
  stroke: #d35f1a;
  stroke-width: 2px;
  cursor: move;
}

.annotation-draft {
  fill: rgba(211, 95, 26, 0.12);
  stroke: #d35f1a;
  stroke-dasharray: 10 8;
}

.annotation-main-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 28px;
  color: #657786;
}

.annotation-main-empty p {
  margin: 0;
  line-height: 1.7;
}

.meta-grid {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 10px 12px;
}

.meta-label {
  color: #738493;
  font-size: 12px;
}

.meta-value {
  color: #223a49;
  font-size: 13px;
  word-break: break-all;
}

.inspector-note {
  color: #7b5c35;
  background: #fff5e8;
  border-radius: 12px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.6;
}

.inspector-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.inspector-block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.inspector-block-grow {
  flex: 1;
  min-height: 0;
}

.inspector-block-title {
  color: #516472;
  font-size: 13px;
  font-weight: 700;
}

.shape-points-head-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.shape-points-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.shape-point-columns,
.shape-point-item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) minmax(0, 1fr) 24px;
  column-gap: 8px;
  align-items: center;
}

.shape-point-columns {
  padding: 0 8px;
}

.shape-point-column-label {
  color: #627482;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}

.shape-point-item {
  padding: 6px 8px;
  border-radius: 10px;
  border: 1px solid #dbe4ea;
  background: #ffffff;
}

.shape-point-index {
  width: 28px;
  height: 28px;
  border-radius: 10px;
  background: #eef3f7;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #435867;
  font-size: 12px;
  font-weight: 700;
}

.shape-point-axis {
  min-width: 0;
}

.shape-point-input {
  width: 100%;
  min-width: 0;
}

.shape-point-axis :deep(.el-input-number) {
  width: 100%;
}

.shape-point-axis :deep(.el-input__wrapper) {
  border-radius: 10px;
}

.shape-point-delete {
  justify-self: center;
  min-width: 24px;
}

.label-fields-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.label-field-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  column-gap: 8px;
  row-gap: 8px;
  align-items: start;
  padding: 10px;
  border-radius: 14px;
  border: 1px solid #dbe4ea;
  background: #ffffff;
}

.label-field-item.is-invalid {
  border-color: rgba(217, 83, 79, 0.45);
  background: #fff7f7;
}

.label-field-key,
.label-field-value {
  width: 100%;
}

.label-field-item :deep(.sortable-order-handle) {
  grid-column: 1;
  grid-row: 1 / span 2;
  align-self: start;
}

.label-field-key,
.label-field-value-shell {
  min-width: 0;
}

.label-field-key {
  grid-column: 2;
  grid-row: 1;
}

.label-field-value-shell {
  grid-column: 2 / 3;
  grid-row: 2;
  min-width: 0;
}

.label-field-value.is-json :deep(.el-textarea__inner) {
  min-height: 72px;
  font-family: Consolas, 'Courier New', monospace;
}

.label-field-delete {
  grid-column: 3;
  grid-row: 1;
  align-self: center;
  justify-self: center;
  min-width: 24px;
}

.label-field-errors {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.shape-card-top {
  display: flex;
  align-items: center;
  gap: 10px;
}

@media (max-width: 1320px) {
  .annotation-browser-top {
    grid-template-columns: 1fr;
  }

  .annotation-editor-layout {
    grid-template-columns: 1fr;
  }

  .annotation-item-list-grid {
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  }
}

@media (max-width: 980px) {
  .device-file-page {
    --annotation-editor-height: 80vh;
    padding: 16px;
  }

  .annotation-browser-top,
  .directory-config-row {
    grid-template-columns: 1fr;
  }

  .directory-toolbar,
  .stage-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .directory-path-input {
    min-width: 0;
  }

  .directory-action-button,
  .directory-section-count {
    margin-left: 0;
  }

  .annotation-item-list-grid {
    grid-template-columns: 1fr;
  }

  .annotation-editor-layout {
    grid-template-columns: 1fr;
  }

  .stage-toolbar-actions {
    justify-content: flex-start;
  }

  .zoom-control {
    width: 100%;
  }

}
</style>
