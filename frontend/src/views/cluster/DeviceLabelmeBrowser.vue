<template>
  <div class="device-file-page">
    <section v-if="!devices.length" class="empty-panel">
      <div class="empty-badge">图片标注</div>
      <h2>还没有可用设备</h2>
      <p>先到设备任务里添加本地或远程设备入口，再从设备上下文里加载真实目录中的图片和标注文件。</p>
      <div class="empty-actions">
        <el-button type="primary" @click="router.push('/cluster/tasks')">去设备任务</el-button>
      </div>
    </section>

    <section v-else class="browser-panel" v-loading="isLoadingListing || isLoadingItem">
      <section class="annotation-browser-top">
        <aside class="annotation-overview-panel panel-card">
          <div class="panel-section-head">
            <div>
              <div class="section-kicker">浏览文件</div>
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
              <div class="overview-stat-label">已读取 / 已知框数</div>
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
              placeholder="按文件名或相对路径筛选"
            />
          </div>

        </aside>

        <section class="device-directory-panel panel-card">
          <div class="directory-config-row">
            <div class="directory-config-field">
              <span class="directory-config-label">设备</span>
              <el-select
                :model-value="selectedEntryId"
                size="large"
                class="directory-config-select"
                placeholder="选择设备"
                :disabled="isLoadingDevices || !devices.length"
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
              placeholder="输入绝对路径，例如 D:\\home\\chenkunze\\data"
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

      <section class="annotation-layout">
        <aside class="annotation-list-panel panel-card">
          <div class="panel-section-head">
            <div>
              <div class="section-kicker">标注文件</div>
              <h3>图片列表</h3>
            </div>
            <el-tag size="small" type="info">{{ filteredItems.length }} 张</el-tag>
          </div>

          <div v-if="filteredItems.length" class="annotation-item-list">
            <button
              v-for="(item, index) in filteredItems"
              :key="item.id"
              type="button"
              class="annotation-item-card"
              :class="{ 'is-active': item.id === currentItemId }"
              @click="void openItemById(item.id)"
            >
              <div class="annotation-item-index">{{ index + 1 }}</div>
              <div class="annotation-item-main">
                <div class="annotation-item-name">{{ item.name }}</div>
                <div class="annotation-item-path">{{ item.relativePath }}</div>
              </div>
              <el-tag size="small" :type="getItemTagType(item)">
                {{ getItemStatusLabel(item) }}
              </el-tag>
            </button>
          </div>

          <div v-else class="annotation-inline-empty">
            {{ annotationItems.length ? '当前筛选没有文件' : '当前目录下没有可标注图片' }}
          </div>
        </aside>

        <section class="annotation-stage-panel panel-card">
          <div class="stage-toolbar">
            <div class="stage-toolbar-main">
              <div class="stage-title-row">
                <div class="stage-title">{{ currentItem?.name || '未选择图片' }}</div>
                <el-tag size="small" :type="isDirty ? 'warning' : 'success'">
                  {{ isDirty ? '未保存' : '已同步' }}
                </el-tag>
                <el-tag v-if="currentDoc" size="small" type="info">
                  {{ currentDoc.editableShapes.length }} 框
                </el-tag>
              </div>
              <div class="stage-path">
                {{ currentItem?.relativePath || '先选择设备目录与图片' }}
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
                  <div>2. 从左侧图片列表切换文件。</div>
                  <div>3. 点“新框”后在图上拖动，或直接拖动已有矩形和四角。</div>
                  <div>4. `Ctrl + S` 保存，`Delete` 删除，`Esc` 取消画框。</div>
                </div>
              </el-popover>
              <el-button :disabled="!hasPreviousItem" @click="void openRelativeItem(-1)">上一张</el-button>
              <el-button :disabled="!hasNextItem" @click="void openRelativeItem(1)">下一张</el-button>
              <el-button
                :type="toolMode === 'draw' ? 'primary' : 'default'"
                :disabled="!currentDoc"
                @click="toggleDrawMode"
              >
                {{ toolMode === 'draw' ? '取消新框' : '新框' }}
              </el-button>
              <el-button :disabled="!selectedShape" @click="deleteSelectedShape">删除选中</el-button>
              <div class="zoom-control">
                <span>缩放</span>
                <el-slider
                  :model-value="zoomPercent"
                  :min="40"
                  :max="220"
                  :step="10"
                  @update:model-value="zoomPercent = Number($event)"
                />
                <span>{{ zoomPercent }}%</span>
              </div>
              <el-button
                type="primary"
                :disabled="!currentDoc || !isDirty"
                :loading="isSaving"
                @click="void saveCurrentDocument()"
              >
                保存
              </el-button>
            </div>
          </div>

          <div v-if="currentDoc && currentItem && currentImageUrl" class="stage-body">
            <div class="stage-hint">{{ stageHintText }}</div>
            <div class="stage-scroll">
              <div ref="stageRef" class="annotation-stage" :style="stageStyle" @mousedown="handleStageMouseDown">
                <img class="stage-image" :src="currentImageUrl" :alt="currentItem.name" draggable="false" />

                <svg
                  class="stage-overlay"
                  :viewBox="`0 0 ${currentDoc.imageWidth} ${currentDoc.imageHeight}`"
                  preserveAspectRatio="none"
                >
                  <g v-for="(shape, index) in currentDoc.editableShapes" :key="shape.id">
                    <rect
                      class="annotation-rect"
                      :class="{
                        'is-selected': shape.id === selectedShapeId,
                        'is-draw-mode': toolMode === 'draw',
                      }"
                      :x="shape.rect.x1"
                      :y="shape.rect.y1"
                      :width="shape.rect.x2 - shape.rect.x1"
                      :height="shape.rect.y2 - shape.rect.y1"
                      :stroke-width="strokeWidth"
                      @click.stop="selectShape(shape.id)"
                      @mousedown.stop="handleShapeMouseDown(shape.id, $event)"
                    />

                    <template v-if="shape.id === selectedShapeId && toolMode !== 'draw'">
                      <circle
                        v-for="handle in resizeHandles"
                        :key="handle.key"
                        class="annotation-handle"
                        :cx="getHandlePosition(shape.rect, handle.key).x"
                        :cy="getHandlePosition(shape.rect, handle.key).y"
                        :r="handleRadius"
                        @mousedown.stop="handleResizeMouseDown(shape.id, handle.key, $event)"
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

          <div v-else class="annotation-main-empty">
            <div class="empty-badge subtle">标注面板</div>
            <h3>当前还没有可编辑图片</h3>
            <p>进入一个包含图片的设备目录后，这里会显示当前图片及其 LabelMe 标注内容。</p>
          </div>
        </section>

        <aside class="annotation-inspector-panel panel-card">
          <div class="panel-section-head">
            <div>
              <div class="section-kicker">属性</div>
              <h3>当前标注</h3>
            </div>
          </div>

          <div class="meta-grid">
            <div class="meta-label">图片</div>
            <div class="meta-value">{{ currentItem?.absolutePath || '--' }}</div>
            <div class="meta-label">JSON</div>
            <div class="meta-value">{{ currentItem?.jsonAbsolutePath || '--' }}</div>
            <div class="meta-label">尺寸</div>
            <div class="meta-value">
              {{ currentDoc ? `${currentDoc.imageWidth} × ${currentDoc.imageHeight}` : '--' }}
            </div>
          </div>

          <div v-if="currentDoc?.unsupportedShapeCount" class="inspector-note">
            当前文件里有 {{ currentDoc.unsupportedShapeCount }} 个非矩形 shape，会保留但不在这里编辑。
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

                  <el-select
                    v-model="item.type"
                    size="small"
                    class="label-field-type"
                    @change="handleSelectedShapeLabelFieldTypeChange(item)"
                  >
                    <el-option label="文本" value="string" />
                    <el-option label="数值" value="number" />
                    <el-option label="布尔" value="boolean" />
                    <el-option label="JSON" value="json" />
                  </el-select>

                  <div class="label-field-value-shell">
                    <el-input
                      v-if="item.type === 'string'"
                      :model-value="getShapeLabelFieldTextValue(item)"
                      size="small"
                      class="label-field-value"
                      placeholder="属性值"
                      @update:model-value="value => setShapeLabelFieldTextValue(item, value)"
                    />

                    <el-input
                      v-else-if="item.type === 'number'"
                      :model-value="getShapeLabelFieldTextValue(item)"
                      size="small"
                      class="label-field-value"
                      placeholder="输入数值"
                      @update:model-value="value => setShapeLabelFieldNumberValue(item, value)"
                    />

                    <el-switch
                      v-else-if="item.type === 'boolean'"
                      :model-value="getShapeLabelFieldBooleanValue(item)"
                      size="small"
                      @update:model-value="value => setShapeLabelFieldBooleanValue(item, value)"
                    />

                    <el-input
                      v-else
                      :model-value="getShapeLabelFieldTextValue(item)"
                      type="textarea"
                      autosize
                      size="small"
                      class="label-field-value is-json"
                      placeholder='输入有效 JSON，例如 {"x": 1}'
                      @update:model-value="value => setShapeLabelFieldJsonValue(item, value)"
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
              <div class="inspector-block-title">坐标</div>
              <div class="coordinate-grid">
                <label class="coordinate-field">
                  <span>左</span>
                  <el-input-number
                    :model-value="selectedShape.rect.x1"
                    :step="1"
                    :min="0"
                    :max="currentDoc?.imageWidth || 0"
                    controls-position="right"
                    @change="value => updateSelectedShapeCoordinate('x1', value)"
                  />
                </label>
                <label class="coordinate-field">
                  <span>上</span>
                  <el-input-number
                    :model-value="selectedShape.rect.y1"
                    :step="1"
                    :min="0"
                    :max="currentDoc?.imageHeight || 0"
                    controls-position="right"
                    @change="value => updateSelectedShapeCoordinate('y1', value)"
                  />
                </label>
                <label class="coordinate-field">
                  <span>右</span>
                  <el-input-number
                    :model-value="selectedShape.rect.x2"
                    :step="1"
                    :min="0"
                    :max="currentDoc?.imageWidth || 0"
                    controls-position="right"
                    @change="value => updateSelectedShapeCoordinate('x2', value)"
                  />
                </label>
                <label class="coordinate-field">
                  <span>下</span>
                  <el-input-number
                    :model-value="selectedShape.rect.y2"
                    :step="1"
                    :min="0"
                    :max="currentDoc?.imageHeight || 0"
                    controls-position="right"
                    @change="value => updateSelectedShapeCoordinate('y2', value)"
                  />
                </label>
              </div>
            </div>
          </template>
          <div v-else class="annotation-inline-empty">
            先在图里选择一个矩形框，再在这里改标签和坐标。
          </div>

          <div class="inspector-block inspector-block-grow">
            <div class="inspector-block-title">矩形列表</div>
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
                <div class="shape-card-meta">{{ formatShapeSummary(shape.rect) }}</div>
              </button>
            </div>
            <div v-else class="annotation-inline-empty">
              当前图片还没有矩形框。
            </div>
          </div>
        </aside>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { Close, FolderOpened, Plus, QuestionFilled } from '@element-plus/icons-vue';

import {
  fetchDeviceDirectoryItems,
  fetchDeviceFileBlob,
  fetchDeviceFileText,
  fetchDeviceMedia,
  saveDeviceFileText,
  type DeviceDirectoryItem,
  type DeviceDirectoryListing,
  type DeviceFileSelector,
  type DeviceImageRecord,
} from '@/api/deviceFiles';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import { taskStore } from '@/store/taskStore';
import { useSortableList } from '@/utils/useSortableList';

type LabelMode = 'json' | 'plain';
type ResizeHandleKey = 'nw' | 'ne' | 'sw' | 'se';
type ShapeLabelFieldType = 'string' | 'number' | 'boolean' | 'json';
type ShapeLabelFieldStoredValue = string | number | boolean | Record<string, unknown> | unknown[] | null;
type ShapeLabelFieldEditorValue = string | boolean;

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

interface EditableShape {
  id: string;
  labelText: string;
  labelMode: LabelMode;
  labelFields: ShapeLabelFieldItem[];
  rect: Rect;
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
}

interface DragState {
  mode: 'draw' | 'move' | 'resize';
  shapeId?: string;
  handle?: ResizeHandleKey;
  anchor: Point;
  initialRect: Rect;
}

const DEVICE_ROOT_SENTINEL = '__device_root__';
const DEVICE_ROOT_LABEL = '系统根目录';
const DEFAULT_DIRECTORY_PAGE_SIZE = 20;
const DEFAULT_DEVICE_MEDIA_SCAN_LIMIT = 2000;
const MIN_DEVICE_MEDIA_SCAN_LIMIT = 100;
const MAX_DEVICE_MEDIA_SCAN_LIMIT = 50000;
const DEVICE_PATH_STORAGE_PREFIX = 'device_labelme_browser_path';
const DEVICE_SCAN_LIMIT_STORAGE_SUFFIX = '_scan_limit';
const DEVICE_RECURSIVE_STORAGE_SUFFIX = '_recursive';
const DEFAULT_LABEL_TEXT = '新标注';
const MIN_RECT_EDGE = 6;
const STANDARD_NUMBER_PATTERN = /^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$/;
const BOOLEAN_TRUE_TOKENS = new Set(['true', '1', 'yes', 'y', 'on']);
const BOOLEAN_FALSE_TOKENS = new Set(['false', '0', 'no', 'n', 'off', '']);
const resizeHandles: Array<{ key: ResizeHandleKey }> = [
  { key: 'nw' },
  { key: 'ne' },
  { key: 'sw' },
  { key: 'se' },
];

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

const devices = computed(() => taskStore.devices);
const selectedEntryId = ref(getQueryString(route.query.entry_id));
const selectedPath = ref(DEVICE_ROOT_SENTINEL);
const pathInputValue = ref('');
const listing = ref<DeviceDirectoryListing | null>(null);
const annotationItems = ref<DeviceAnnotationItem[]>([]);
const currentItemId = ref('');
const recursiveDisplay = ref(false);
const keyword = ref('');
const isLoadingDevices = ref(false);
const isLoadingListing = ref(false);
const isLoadingItem = ref(false);
const isSaving = ref(false);
const mediaScanLimit = ref(DEFAULT_DEVICE_MEDIA_SCAN_LIMIT);
const mediaScanLimitInput = ref(DEFAULT_DEVICE_MEDIA_SCAN_LIMIT);
const currentDirectoryPage = ref(1);
const currentDoc = ref<LabelmeDocument | null>(null);
const currentImageUrl = ref('');
const isDirty = ref(false);
const zoomPercent = ref(100);
const toolMode = ref<'select' | 'draw'>('select');
const selectedShapeId = ref('');
const draftRect = ref<Rect | null>(null);
const stageRef = ref<HTMLDivElement | null>(null);
const selectedShapeLabelFieldsListRef = ref<HTMLElement | null>(null);
const activeDrag = ref<DragState | null>(null);

let directoryLoadVersion = 0;
let itemLoadVersion = 0;

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

const resolveInitialPath = (entryId: string) => {
  const routePath = normalizePathInput(getQueryString(route.query.path));
  if (routePath) {
    return routePath;
  }
  return loadPersistedPath(entryId);
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
const listingItems = computed(() => listing.value?.items ?? []);
const directoryEntries = computed(() => listingItems.value.filter((entry) => entry.is_dir));
const directoryPageCount = computed(() =>
  Math.max(1, Math.ceil(directoryEntries.value.length / DEFAULT_DIRECTORY_PAGE_SIZE))
);
const pagedDirectoryEntries = computed(() => {
  const offset = Math.max(0, (Math.max(1, currentDirectoryPage.value) - 1) * DEFAULT_DIRECTORY_PAGE_SIZE);
  return directoryEntries.value.slice(offset, offset + DEFAULT_DIRECTORY_PAGE_SIZE);
});

const filteredItems = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLowerCase();
  if (!normalizedKeyword) return annotationItems.value;
  return annotationItems.value.filter((item) =>
    item.name.toLowerCase().includes(normalizedKeyword)
    || item.relativePath.toLowerCase().includes(normalizedKeyword)
  );
});

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
const selectedShape = computed(
  () => currentDoc.value?.editableShapes.find((shape) => shape.id === selectedShapeId.value) ?? null
);
const selectedShapeLabel = computed(() => selectedShape.value?.labelText ?? '');
const selectedShapeLabelFields = computed(() => selectedShape.value?.labelFields ?? []);
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

const strokeWidth = computed(() => {
  if (!currentDoc.value) return 2;
  return Math.max(2, currentDoc.value.imageWidth / 250);
});

const handleRadius = computed(() => {
  if (!currentDoc.value) return 7;
  return Math.max(7, currentDoc.value.imageWidth / 60);
});

const stageHintText = computed(() => {
  if (!currentDoc.value) {
    return '进入目录后开始标注。';
  }
  if (toolMode.value === 'draw') {
    return '拖动图片区域创建新的矩形框，按 Esc 取消。';
  }
  if (selectedShape.value) {
    return '拖动矩形可移动，拖四角可缩放，右侧可改标签与坐标。';
  }
  return '点击“新框”开始标注，或先在图上选择已有矩形。';
});

const syncPathInputFromSelection = () => {
  pathInputValue.value = formatPathInput(selectedPath.value);
};

const buildRouteQuery = (entryId = selectedEntryId.value, pathValue = normalizedPathInput.value) => {
  const nextQuery: Record<string, string> = {};
  if (entryId) {
    nextQuery.entry_id = entryId;
  }
  if (!isDeviceRootPath(pathValue)) {
    nextQuery.path = pathValue;
  }
  return nextQuery;
};

const syncRouteQuery = async (mode: 'replace' | 'push' = 'replace') => {
  const currentRoutePath = normalizePathInput(getQueryString(route.query.path)) || DEVICE_ROOT_SENTINEL;
  const currentQuery = buildRouteQuery(getQueryString(route.query.entry_id), currentRoutePath);
  const nextQuery = buildRouteQuery();

  if (currentQuery.entry_id === nextQuery.entry_id && currentQuery.path === nextQuery.path) {
    return false;
  }

  await router[mode]({
    path: route.path,
    query: nextQuery,
  });
  return true;
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

const canGoUp = computed(() => canBrowse.value && Boolean(getAbsoluteParentPath(normalizedPathInput.value)));

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

const roundCoordinate = (value: number) => Math.round(value * 100) / 100;

const normalizeRect = (rect: Rect): Rect => ({
  x1: Math.min(rect.x1, rect.x2),
  y1: Math.min(rect.y1, rect.y2),
  x2: Math.max(rect.x1, rect.x2),
  y2: Math.max(rect.y1, rect.y2),
});

const ensureRectWithinBounds = (rect: Rect, width: number, height: number): Rect => ({
  x1: clampNumber(rect.x1, 0, width),
  y1: clampNumber(rect.y1, 0, height),
  x2: clampNumber(rect.x2, 0, width),
  y2: clampNumber(rect.y2, 0, height),
});

const toPoint = (value: unknown): Point | null => {
  if (!Array.isArray(value) || value.length < 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y };
};

const normalizeShapeLabelFieldType = (type: unknown): ShapeLabelFieldType => {
  if (type === 'number' || type === 'boolean' || type === 'json') {
    return type;
  }
  return 'string';
};

const normalizeShapeLabelFieldText = (value: unknown) => (value == null ? '' : String(value));

const parseShapeLabelFieldNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  if (!trimmed || !STANDARD_NUMBER_PATTERN.test(trimmed)) {
    return null;
  }

  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

const parseShapeLabelFieldBoolean = (value: unknown): boolean | null => {
  if (typeof value === 'boolean') {
    return value;
  }

  const parsedNumber = parseShapeLabelFieldNumber(value);
  if (parsedNumber !== null) {
    return parsedNumber !== 0;
  }

  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (BOOLEAN_TRUE_TOKENS.has(normalized)) return true;
    if (BOOLEAN_FALSE_TOKENS.has(normalized)) return false;
    return normalized.length > 0;
  }

  if (value == null) {
    return false;
  }
  return Boolean(value);
};

const inferShapeLabelFieldType = (value: unknown): ShapeLabelFieldType => {
  if (typeof value === 'boolean') return 'boolean';
  if (typeof value === 'number' && Number.isFinite(value)) return 'number';
  if (Array.isArray(value) || value === null || isRecord(value)) return 'json';
  return 'string';
};

const normalizeShapeLabelFieldEditorValue = (
  type: ShapeLabelFieldType,
  value: unknown
): ShapeLabelFieldEditorValue => {
  if (type === 'boolean') {
    return parseShapeLabelFieldBoolean(value) ?? false;
  }
  if (type === 'json') {
    if (typeof value === 'string') {
      return value;
    }
    return JSON.stringify(value ?? null, null, 2);
  }
  if (type === 'number') {
    if (typeof value === 'boolean') return value ? '1' : '0';
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    if (typeof value === 'string') {
      const parsed = parseShapeLabelFieldNumber(value);
      return parsed === null ? value : value.trim();
    }
  }
  return normalizeShapeLabelFieldText(value);
};

const convertShapeLabelFieldValue = (
  type: ShapeLabelFieldType,
  value: unknown
): ShapeLabelFieldEditorValue => {
  if (type === 'string') {
    return typeof value === 'boolean' ? (value ? 'true' : 'false') : normalizeShapeLabelFieldText(value);
  }
  if (type === 'number') {
    if (typeof value === 'boolean') return value ? '1' : '0';
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    const text = normalizeShapeLabelFieldText(value);
    const parsed = parseShapeLabelFieldNumber(text);
    return parsed === null ? text : text.trim();
  }
  if (type === 'boolean') {
    return parseShapeLabelFieldBoolean(value) ?? false;
  }
  if (typeof value === 'string') {
    return value;
  }
  return JSON.stringify(value ?? null, null, 2);
};

const createShapeLabelFieldItem = (
  key: unknown = '',
  type: unknown = 'string',
  value: unknown = ''
): ShapeLabelFieldItem => {
  const normalizedType = normalizeShapeLabelFieldType(type);
  return {
    localId: createShapeLabelFieldId(),
    key: typeof key === 'string' ? key : '',
    type: normalizedType,
    value: normalizeShapeLabelFieldEditorValue(normalizedType, value),
  };
};

const shapeLabelExtrasToFieldItems = (extras: Record<string, unknown>) =>
  Object.entries(extras).map(([key, value]) =>
    createShapeLabelFieldItem(key, inferShapeLabelFieldType(value), value)
  );

const serializeShapeLabelFieldValue = (
  type: ShapeLabelFieldType,
  value: unknown
): { ok: true; value: ShapeLabelFieldStoredValue } | { ok: false; message: string } => {
  if (type === 'string') {
    return { ok: true, value: normalizeShapeLabelFieldText(value) };
  }
  if (type === 'boolean') {
    return { ok: true, value: parseShapeLabelFieldBoolean(value) ?? false };
  }
  if (type === 'number') {
    const parsed = parseShapeLabelFieldNumber(value);
    if (parsed === null) {
      return { ok: false, message: '数值字段需要有效数字' };
    }
    return { ok: true, value: parsed };
  }

  const text = normalizeShapeLabelFieldText(value).trim();
  if (!text) {
    return { ok: false, message: 'JSON 字段不能为空' };
  }
  try {
    return { ok: true, value: JSON.parse(text) as ShapeLabelFieldStoredValue };
  } catch {
    return { ok: false, message: 'JSON 字段格式无效' };
  }
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
  if (!isRecord(value) || value.shape_type !== 'rectangle') {
    return null;
  }

  const points = Array.isArray(value.points) ? value.points : [];
  if (points.length < 2) {
    return null;
  }

  const firstPoint = toPoint(points[0]);
  const secondPoint = toPoint(points[1]);
  if (!firstPoint || !secondPoint) {
    return null;
  }

  const rawLabel = typeof value.label === 'string' ? value.label : '';
  const parsedLabel = parseShapeLabel(rawLabel);

  return {
    id: createShapeId(),
    labelText: parsedLabel.text,
    labelMode: parsedLabel.mode,
    labelFields: shapeLabelExtrasToFieldItems(parsedLabel.extras),
    rect: normalizeRect({
      x1: firstPoint.x,
      y1: firstPoint.y,
      x2: secondPoint.x,
      y2: secondPoint.y,
    }),
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
  if (!isRecord(parsed)) {
    return createEmptyDocument(imageFilename, imageWidth, imageHeight);
  }

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
        points: [
          [roundCoordinate(shape.rect.x1), roundCoordinate(shape.rect.y1)],
          [roundCoordinate(shape.rect.x2), roundCoordinate(shape.rect.y2)],
        ],
        group_id: shape.groupId ?? null,
        shape_type: 'rectangle',
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

const clearCurrentDocument = () => {
  currentItemId.value = '';
  currentDoc.value = null;
  selectedShapeId.value = '';
  draftRect.value = null;
  toolMode.value = 'select';
  isDirty.value = false;
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
  };
};

const updateItemCache = (item: DeviceAnnotationItem, doc: LabelmeDocument) => {
  item.cachedShapeCount = doc.editableShapes.length;
};

const syncCurrentItemCache = () => {
  if (!currentItem.value || !currentDoc.value) return;
  updateItemCache(currentItem.value, currentDoc.value);
};

const getShapeById = (shapeId: string) =>
  currentDoc.value?.editableShapes.find((shape) => shape.id === shapeId) ?? null;

const markDirty = () => {
  isDirty.value = true;
  syncCurrentItemCache();
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

const confirmDiscardUnsavedChanges = (reason: string) => {
  if (!isDirty.value) return true;
  return window.confirm(`当前标注未保存，${reason}会丢失修改。是否继续？`);
};

const getItemStatusLabel = (item: DeviceAnnotationItem) => {
  if (item.id === currentItemId.value && isDirty.value) return '未保存';
  if (typeof item.cachedShapeCount === 'number') {
    return item.cachedShapeCount > 0 ? `${item.cachedShapeCount} 框` : '空标注';
  }
  return '未读';
};

const getItemTagType = (item: DeviceAnnotationItem): '' | 'success' | 'info' | 'warning' => {
  if (item.id === currentItemId.value && isDirty.value) return 'warning';
  if (typeof item.cachedShapeCount === 'number' && item.cachedShapeCount > 0) return 'success';
  return 'info';
};

const openItemById = async (
  itemId: string,
  options: { skipConfirm?: boolean } = {}
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
      replaceCurrentImageUrl(imageResource.url);
      currentItemId.value = item.id;
      currentDoc.value = nextDoc;
      selectedShapeId.value = nextDoc.editableShapes[0]?.id ?? '';
      toolMode.value = 'select';
      draftRect.value = null;
      zoomPercent.value = 100;
      isDirty.value = false;
      updateItemCache(item, nextDoc);
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

const saveCurrentDocument = async () => {
  if (!selectedEntryId.value || !currentItem.value || !currentDoc.value) return;

  const validationErrors = collectDocumentLabelFieldValidationErrors(currentDoc.value);
  if (validationErrors.length) {
    const firstError = validationErrors[0];
    selectedShapeId.value = firstError.shapeId;
    ElMessage.error(`保存前请修正第 ${firstError.shapeIndex + 1} 个框的属性：${firstError.message}`);
    return;
  }

  isSaving.value = true;
  try {
    const payload = buildPayloadFromDocument(currentDoc.value, currentItem.value);
    await saveDeviceFileText(selectedEntryId.value, {
      absolute_path: currentItem.value.jsonAbsolutePath,
      text: `${JSON.stringify(payload, null, 2)}\n`,
    });

    isDirty.value = false;
    updateItemCache(currentItem.value, currentDoc.value);
    ElMessage.success('标注已保存');
  } catch (error) {
    console.error('Failed to save device annotation', error);
    ElMessage.error('保存标注失败');
  } finally {
    isSaving.value = false;
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
  const normalizedPath = normalizePathInput(pathInputValue.value);
  if (!normalizedPath) {
    syncPathInputFromSelection();
    if (options?.load) {
      ElMessage.warning('请输入绝对路径');
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
    await syncRouteQuery(options.mode ?? 'push');
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
  await syncRouteQuery('push');
  await loadDirectory();
};

const goToParentDirectory = async () => {
  if (!canGoUp.value) {
    return;
  }
  if (!confirmDiscardUnsavedChanges('切换目录')) {
    return;
  }
  selectedPath.value = getAbsoluteParentPath(normalizedPathInput.value);
  syncPathInputFromSelection();
  await syncRouteQuery('push');
  await loadDirectory();
};

const handleDirectoryPageChange = (page: number) => {
  currentDirectoryPage.value = Math.min(directoryPageCount.value, Math.max(1, Math.floor(page || 1)));
};

const handleSelectedEntryChange = async (nextEntryId: string) => {
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
  if (!currentDoc.value) return;
  if (toolMode.value === 'draw') {
    toolMode.value = 'select';
    draftRect.value = null;
    return;
  }
  selectedShapeId.value = '';
  toolMode.value = 'draw';
};

const addShape = (rect: Rect) => {
  if (!currentDoc.value) return;
  const normalizedRect = normalizeRect(rect);
  if ((normalizedRect.x2 - normalizedRect.x1) < MIN_RECT_EDGE || (normalizedRect.y2 - normalizedRect.y1) < MIN_RECT_EDGE) {
    return;
  }

  const shape: EditableShape = {
    id: createShapeId(),
    labelText: DEFAULT_LABEL_TEXT,
    labelMode: currentDoc.value.defaultLabelMode,
    labelFields: currentDoc.value.defaultLabelMode === 'json'
      ? [createShapeLabelFieldItem('score', 'number', -1)]
      : [],
    rect: normalizedRect,
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
  item.type === 'boolean' ? '' : String(item.value ?? '');

const getShapeLabelFieldBooleanValue = (item: ShapeLabelFieldItem) =>
  item.type === 'boolean' ? Boolean(item.value) : false;

const hasSelectedShapeLabelFieldError = (fieldLocalId: string) =>
  selectedShapeLabelFieldErrors.value.some((error) => error.fieldLocalId === fieldLocalId);

const handleSelectedShapeLabelFieldChange = () => {
  if (!selectedShape.value) return;
  markDirty();
};

const handleSelectedShapeLabelFieldTypeChange = (item: ShapeLabelFieldItem) => {
  item.value = convertShapeLabelFieldValue(item.type, item.value);
  handleSelectedShapeLabelFieldChange();
};

const setShapeLabelFieldTextValue = (item: ShapeLabelFieldItem, value: string | number) => {
  if (item.type === 'boolean') return;
  item.value = String(value ?? '');
  handleSelectedShapeLabelFieldChange();
};

const setShapeLabelFieldNumberValue = (item: ShapeLabelFieldItem, value: string | number) => {
  if (item.type !== 'number') return;
  item.value = String(value ?? '');
  handleSelectedShapeLabelFieldChange();
};

const setShapeLabelFieldBooleanValue = (item: ShapeLabelFieldItem, value: string | number | boolean) => {
  if (item.type !== 'boolean') return;
  item.value = Boolean(value);
  handleSelectedShapeLabelFieldChange();
};

const setShapeLabelFieldJsonValue = (item: ShapeLabelFieldItem, value: string | number) => {
  if (item.type !== 'json') return;
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

const updateSelectedShapeCoordinate = (key: keyof Rect, value: string | number | null | undefined) => {
  if (!selectedShape.value || !currentDoc.value || value === null || value === undefined) return;
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return;

  const limit = key === 'x1' || key === 'x2' ? currentDoc.value.imageWidth : currentDoc.value.imageHeight;
  const nextRect = normalizeRect({
    ...selectedShape.value.rect,
    [key]: clampNumber(numericValue, 0, limit),
  });
  selectedShape.value.rect = ensureRectWithinBounds(nextRect, currentDoc.value.imageWidth, currentDoc.value.imageHeight);
  markDirty();
};

const getHandlePosition = (rect: Rect, key: ResizeHandleKey): Point => {
  switch (key) {
    case 'nw':
      return { x: rect.x1, y: rect.y1 };
    case 'ne':
      return { x: rect.x2, y: rect.y1 };
    case 'sw':
      return { x: rect.x1, y: rect.y2 };
    case 'se':
      return { x: rect.x2, y: rect.y2 };
  }
};

const formatShapeSummary = (rect: Rect) => {
  const width = Math.max(0, Math.round(rect.x2 - rect.x1));
  const height = Math.max(0, Math.round(rect.y2 - rect.y1));
  return `${Math.round(rect.x1)}, ${Math.round(rect.y1)} · ${width} × ${height}`;
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
    x: clamp ? clampNumber(x, 0, currentDoc.value.imageWidth) : x,
    y: clamp ? clampNumber(y, 0, currentDoc.value.imageHeight) : y,
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

const translateRectWithinBounds = (
  initialRect: Rect,
  deltaX: number,
  deltaY: number,
  width: number,
  height: number
): Rect => {
  const rectWidth = initialRect.x2 - initialRect.x1;
  const rectHeight = initialRect.y2 - initialRect.y1;
  const nextX1 = clampNumber(initialRect.x1 + deltaX, 0, Math.max(0, width - rectWidth));
  const nextY1 = clampNumber(initialRect.y1 + deltaY, 0, Math.max(0, height - rectHeight));
  return {
    x1: nextX1,
    y1: nextY1,
    x2: nextX1 + rectWidth,
    y2: nextY1 + rectHeight,
  };
};

const resizeRectFromHandle = (
  initialRect: Rect,
  handle: ResizeHandleKey,
  point: Point,
  width: number,
  height: number
): Rect => {
  const clampedPoint = {
    x: clampNumber(point.x, 0, width),
    y: clampNumber(point.y, 0, height),
  };
  const nextRect = { ...initialRect };
  if (handle.includes('w')) nextRect.x1 = clampedPoint.x;
  else nextRect.x2 = clampedPoint.x;
  if (handle.includes('n')) nextRect.y1 = clampedPoint.y;
  else nextRect.y2 = clampedPoint.y;
  return ensureRectWithinBounds(normalizeRect(nextRect), width, height);
};

const handleStageMouseDown = (event: MouseEvent) => {
  if (!currentDoc.value || toolMode.value !== 'draw' || event.button !== 0) return;
  const point = getPointerInImage(event);
  if (!point) return;

  event.preventDefault();
  draftRect.value = { x1: point.x, y1: point.y, x2: point.x, y2: point.y };
  beginDrag({
    mode: 'draw',
    anchor: point,
    initialRect: { x1: point.x, y1: point.y, x2: point.x, y2: point.y },
  });
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
    initialRect: { ...shape.rect },
  });
};

const handleResizeMouseDown = (shapeId: string, handle: ResizeHandleKey, event: MouseEvent) => {
  if (!currentDoc.value || toolMode.value === 'draw' || event.button !== 0) return;
  const point = getPointerInImage(event);
  const shape = getShapeById(shapeId);
  if (!point || !shape) return;

  selectedShapeId.value = shapeId;
  beginDrag({
    mode: 'resize',
    shapeId,
    handle,
    anchor: point,
    initialRect: { ...shape.rect },
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
    shape.rect = translateRectWithinBounds(
      activeDrag.value.initialRect,
      point.x - activeDrag.value.anchor.x,
      point.y - activeDrag.value.anchor.y,
      currentDoc.value.imageWidth,
      currentDoc.value.imageHeight
    );
    markDirty();
    return;
  }

  if (activeDrag.value.mode === 'resize' && activeDrag.value.handle) {
    shape.rect = resizeRectFromHandle(
      activeDrag.value.initialRect,
      activeDrag.value.handle,
      point,
      currentDoc.value.imageWidth,
      currentDoc.value.imageHeight
    );
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

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    void saveCurrentDocument();
    return;
  }

  if (event.key === 'Escape') {
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

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!isDirty.value) return;
  event.preventDefault();
  event.returnValue = '';
}

watch(selectedPath, (nextPath) => {
  persistSelectedPath(selectedEntryId.value, nextPath || DEVICE_ROOT_SENTINEL);
  syncPathInputFromSelection();
});

watch(directoryPageCount, (nextPageCount) => {
  if (currentDirectoryPage.value > nextPageCount) {
    currentDirectoryPage.value = nextPageCount;
  }
});

watch(
  () => [route.query.entry_id, route.query.path],
  ([nextEntryId, nextPath]) => {
    const normalizedEntryId = getQueryString(nextEntryId);
    const explicitPath = normalizePathInput(getQueryString(nextPath)) || '';
    if (normalizedEntryId && normalizedEntryId !== selectedEntryId.value) {
      selectedEntryId.value = normalizedEntryId;
    }
    if (explicitPath && explicitPath !== selectedPath.value) {
      selectedPath.value = explicitPath;
    }
  }
);

watch(selectedEntryId, async (nextEntryId) => {
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
  if (!isDirty.value) return true;
  return window.confirm('当前标注未保存，离开页面会丢失修改。是否继续？');
});

if (typeof window !== 'undefined') {
  window.addEventListener('keydown', handleWindowKeyDown);
  window.addEventListener('beforeunload', handleBeforeUnload);
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

  if (!selectedEntryId.value || !devices.value.some((device) => device.id === selectedEntryId.value)) {
    selectedEntryId.value = devices.value[0].id;
    return;
  }

  await syncRouteQuery();
  if (canBrowse.value) {
    await loadDirectory();
  }
});

onBeforeUnmount(() => {
  stopDrag();
  if (currentImageUrl.value) {
    URL.revokeObjectURL(currentImageUrl.value);
  }
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', handleWindowKeyDown);
    window.removeEventListener('beforeunload', handleBeforeUnload);
  }
});
</script>

<style scoped>
.device-file-page {
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

.annotation-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr) 320px;
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

.section-kicker {
  color: #64748b;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.panel-section-head h3,
.annotation-main-empty h3 {
  margin: 4px 0 0;
  color: #0f172a;
  font-size: 22px;
  line-height: 1.15;
}

.annotation-search :deep(.el-input__wrapper) {
  border-radius: 14px;
}

.annotation-item-list,
.shape-list {
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.annotation-item-card,
.shape-card {
  border: 1px solid #dbe4ea;
  border-radius: 16px;
  background: #ffffff;
  padding: 12px;
  cursor: pointer;
  text-align: left;
}

.annotation-item-card {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
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
}

.annotation-item-name,
.shape-card-label {
  font-size: 14px;
  font-weight: 700;
  color: #163042;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.annotation-item-path,
.shape-card-meta,
.annotation-inline-empty {
  margin-top: 4px;
  color: #728392;
  font-size: 12px;
  line-height: 1.6;
}

.annotation-stage-panel {
  padding: 0;
  overflow: hidden;
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

.stage-toolbar-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
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

.annotation-stage {
  position: relative;
  margin: 0 auto;
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

.annotation-rect {
  fill: rgba(27, 132, 232, 0.12);
  stroke: #1b84e8;
  cursor: move;
}

.annotation-rect.is-selected {
  fill: rgba(211, 95, 26, 0.18);
  stroke: #d35f1a;
}

.annotation-rect.is-draw-mode {
  pointer-events: none;
}

.annotation-handle {
  fill: #ffffff;
  stroke: #d35f1a;
  stroke-width: 2px;
  cursor: nwse-resize;
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

.coordinate-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.label-fields-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.label-field-item {
  display: grid;
  grid-template-columns: auto minmax(96px, 1fr) 104px minmax(0, 1.3fr) auto;
  gap: 8px;
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
.label-field-type,
.label-field-value {
  width: 100%;
}

.label-field-value-shell {
  min-width: 0;
}

.label-field-value.is-json :deep(.el-textarea__inner) {
  min-height: 72px;
  font-family: Consolas, 'Courier New', monospace;
}

.label-field-delete {
  min-width: 24px;
}

.label-field-errors {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.coordinate-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #627482;
  font-size: 12px;
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

  .annotation-layout {
    grid-template-columns: 300px minmax(0, 1fr);
  }

  .annotation-inspector-panel {
    grid-column: 1 / -1;
  }
}

@media (max-width: 980px) {
  .device-file-page {
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

  .annotation-layout {
    grid-template-columns: 1fr;
  }

  .stage-toolbar-actions {
    justify-content: flex-start;
  }

  .zoom-control {
    width: 100%;
  }

  .label-field-item {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }

  .label-field-type,
  .label-field-value-shell {
    grid-column: 2 / 3;
  }
}
</style>
