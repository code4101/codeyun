<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { Delete } from '@element-plus/icons-vue';
import {
  disableFanxiuRegionCharacter,
  getFanxiuRegionCharacters,
  getFanxiuRegionData,
  importFanxiuRegionCharacterFromOcr,
  updateFanxiuRegionCharacter,
  type FanxiuRegionAreaItem,
  type FanxiuRegionCharacterItem,
  type FanxiuRegionCharacterSnapshot,
  type FanxiuRegionServerCandidate,
} from '@/api/fanxiu';
import { useUserStore } from '@/store/userStore';

type RegionRow = {
  number: number;
  name: string;
  startDate: string;
  endDate: string;
  startDateText: string;
  endDateText: string;
  dateRangeText: string;
  knownCount: number;
  knownCountLabel: string;
};

type ServerRow = {
  order: number;
  regionName: string;
  rowClassName: string;
  openDate: string;
  openDateText: string;
  name: string;
  mark: ServerMark | null;
};

type KnownRegionSection = RegionRow & {
  serverCountLabel: string;
  servers: ServerRow[];
};

type ServerMark = {
  type: 'past' | 'current';
  label: string;
  title: string;
};

type CharacterImportTarget = {
  regionName: string;
  serverName: string;
};

type CharacterImportContext =
  | { mode: 'global' }
  | { mode: 'server'; target: CharacterImportTarget };

type CharacterGuildGroup = {
  key: string;
  guildName: string;
  strongestScore: number;
  members: FanxiuRegionCharacterItem[];
};

const CROSS_SIZE = 64;
const REGION_VIEW_FILTER_STORAGE_KEY = 'fanxiu:region-data:view-filter:v1';
const ATTACK_UNIT_EXPONENT: Record<string, number> = {
  万: 4,
  亿: 8,
  兆: 12,
  京: 16,
  垓: 20,
  秭: 24,
  穰: 28,
  沟: 32,
  涧: 36,
  正: 40,
  载: 44,
  极: 48,
};

const userStore = useUserStore();
const loadingRegions = ref(false);
const loadingCharacters = ref(false);
const importingCharacter = ref(false);
const pendingCharacterImport = ref<CharacterImportContext | null>(null);
const editingCharacterRoleId = ref('');
const editingCharacterRoleValue = ref('');
const regionRows = ref<RegionRow[]>([]);
const knownRegionSections = ref<KnownRegionSection[]>([]);
const regionCharacterSnapshot = ref<FanxiuRegionCharacterSnapshot>(createEmptyCharacterSnapshot());
const markedRegionNames = ref<string[]>([]);
const hideUnmarkedRegions = ref(false);

const canEdit = computed(() => {
  const username = userStore.user?.username;
  return username === '凡修手游' || userStore.isAdmin;
});

const charactersByServerKey = computed<Record<string, FanxiuRegionCharacterItem[]>>(() => {
  const result: Record<string, FanxiuRegionCharacterItem[]> = {};
  for (const character of regionCharacterSnapshot.value.characters) {
    const serverKey = buildServerKey(character.region_name, character.server_name);
    if (!serverKey) {
      continue;
    }
    if (!result[serverKey]) {
      result[serverKey] = [];
    }
    result[serverKey].push(character);
  }
  for (const characters of Object.values(result)) {
    characters.sort(compareCharactersByAttackDesc);
  }
  return result;
});

const markedRegionNameSet = computed(() => new Set(markedRegionNames.value));

const markedRegionCount = computed(() => markedRegionNames.value.length);

function createEmptyCharacterSnapshot(): FanxiuRegionCharacterSnapshot {
  return { characters: [] };
}

function buildEntityId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function normalizeText(value: unknown): string {
  return String(value ?? '').trim();
}

function buildServerKey(regionName: unknown, serverName: unknown) {
  const regionText = normalizeText(regionName);
  const serverText = normalizeText(serverName);
  return regionText && serverText ? `${regionText}/${serverText}` : '';
}

function buildCharacterKey(character: Pick<FanxiuRegionCharacterItem, 'region_name' | 'server_name' | 'guild_name' | 'role_name'>) {
  return [
    normalizeText(character.region_name),
    normalizeText(character.server_name),
    normalizeText(character.guild_name),
    normalizeText(character.role_name),
  ].join('/');
}

function normalizeCharacterItem(
  item: Partial<FanxiuRegionCharacterItem> | null | undefined,
): FanxiuRegionCharacterItem {
  return {
    id: normalizeText(item?.id) || buildEntityId('fanxiu-region-character'),
    region_name: normalizeText(item?.region_name),
    server_name: normalizeText(item?.server_name),
    guild_name: normalizeText(item?.guild_name),
    role_name: normalizeText(item?.role_name),
    attack: normalizeText(item?.attack),
    cultivation_level: normalizeText(item?.cultivation_level),
    recorded_date: normalizeDateText(item?.recorded_date) || getTodayDateText(),
    disabled: Boolean(item?.disabled),
    created_at: Number(item?.created_at ?? 0) || 0,
    updated_at: Number(item?.updated_at ?? 0) || 0,
    disabled_at: item?.disabled_at == null ? null : Number(item.disabled_at) || null,
  };
}

function normalizeCharacterSnapshot(value: Partial<FanxiuRegionCharacterSnapshot> | null | undefined): FanxiuRegionCharacterSnapshot {
  const rawCharacters = Array.isArray(value?.characters) ? value.characters : [];
  return {
    characters: rawCharacters
      .map(item => normalizeCharacterItem(item))
      .filter(item => !item.disabled && item.region_name && item.server_name && item.role_name && item.attack),
  };
}

function getTodayDateText() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function normalizeDateText(value: unknown) {
  const text = normalizeText(value);
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : '';
}

function normalizeAttackText(value: unknown) {
  return normalizeText(value)
    .replace(/\s+/g, '')
    .replace(/,/g, '')
    .replace(/[０-９．]/g, char => {
      const code = char.charCodeAt(0);
      if (char === '．') return '.';
      return String(code - '０'.charCodeAt(0));
    })
    .replace(/萬/g, '万')
    .replace(/億/g, '亿');
}

function parseAttackPower(value: unknown) {
  const text = normalizeAttackText(value);
  const match = text.match(/^(\d+(?:\.\d+)?)(.*)$/);
  if (!match) {
    return { score: Number.NEGATIVE_INFINITY, coefficient: 0, exponent: 0 };
  }

  const coefficient = Number(match[1]);
  if (!Number.isFinite(coefficient) || coefficient <= 0) {
    return { score: Number.NEGATIVE_INFINITY, coefficient: 0, exponent: 0 };
  }

  let exponent = 0;
  for (const unit of Array.from(match[2])) {
    exponent += ATTACK_UNIT_EXPONENT[unit] ?? 0;
  }

  return {
    score: Math.log10(coefficient) + exponent,
    coefficient,
    exponent,
  };
}

function compareCharactersByAttackDesc(left: FanxiuRegionCharacterItem, right: FanxiuRegionCharacterItem) {
  const leftPower = parseAttackPower(left.attack);
  const rightPower = parseAttackPower(right.attack);
  const scoreDiff = rightPower.score - leftPower.score;
  if (Math.abs(scoreDiff) > 1e-9) {
    return scoreDiff;
  }
  if (rightPower.exponent !== leftPower.exponent) {
    return rightPower.exponent - leftPower.exponent;
  }
  if (rightPower.coefficient !== leftPower.coefficient) {
    return rightPower.coefficient - leftPower.coefficient;
  }
  return normalizeText(left.role_name).localeCompare(normalizeText(right.role_name), 'zh-Hans-CN');
}

function compareGuildGroupsByStrongestDesc(left: CharacterGuildGroup, right: CharacterGuildGroup) {
  if (left.strongestScore !== right.strongestScore) {
    if (!Number.isFinite(left.strongestScore)) return 1;
    if (!Number.isFinite(right.strongestScore)) return -1;
    return right.strongestScore - left.strongestScore;
  }

  return left.guildName.localeCompare(right.guildName, 'zh-Hans-CN');
}

function normalizeMarkedRegionNames(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }

  const regionNameSet = new Set(regionRows.value.map(region => region.name));
  const result: string[] = [];
  for (const item of value) {
    const regionName = normalizeText(item);
    if (regionNameSet.has(regionName) && !result.includes(regionName)) {
      result.push(regionName);
    }
  }
  return result;
}

function persistRegionViewFilter() {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(
    REGION_VIEW_FILTER_STORAGE_KEY,
    JSON.stringify({
      hideUnmarkedRegions: hideUnmarkedRegions.value,
      markedRegionNames: markedRegionNames.value,
    }),
  );
}

function loadRegionViewFilter() {
  const defaultMarkedRegionNames = regionRows.value.filter(region => region.knownCount > 0).map(region => region.name);
  if (typeof window === 'undefined') {
    markedRegionNames.value = defaultMarkedRegionNames;
    return;
  }

  try {
    const raw = window.localStorage.getItem(REGION_VIEW_FILTER_STORAGE_KEY);
    if (!raw) {
      markedRegionNames.value = defaultMarkedRegionNames;
      hideUnmarkedRegions.value = false;
      persistRegionViewFilter();
      return;
    }
    const parsed = JSON.parse(raw);
    markedRegionNames.value = normalizeMarkedRegionNames(parsed?.markedRegionNames);
    hideUnmarkedRegions.value = Boolean(parsed?.hideUnmarkedRegions);
  } catch {
    markedRegionNames.value = defaultMarkedRegionNames;
    hideUnmarkedRegions.value = false;
  }
}

function isRegionMarked(regionName: string) {
  return markedRegionNameSet.value.has(regionName);
}

function setRegionMarked(regionName: string, value: unknown) {
  const nextChecked = Boolean(value);
  const nextSet = new Set(markedRegionNames.value);
  if (nextChecked) {
    nextSet.add(regionName);
  } else {
    nextSet.delete(regionName);
  }
  markedRegionNames.value = regionRows.value.map(region => region.name).filter(name => nextSet.has(name));
  persistRegionViewFilter();
}

function handleHideUnmarkedRegionsChange() {
  persistRegionViewFilter();
}

function formatDate(value: string) {
  const normalized = normalizeDateText(value);
  if (!normalized) {
    return normalizeText(value);
  }
  const [year, month, day] = normalized.split('-');
  return `${year}/${Number(month)}/${Number(day)}`;
}

function formatOptionalDate(value: unknown) {
  const normalized = normalizeDateText(value);
  return normalized ? formatDate(normalized) : '';
}

function getServerRowClassNameByOrder(order: number) {
  const classes = ['server-row'];
  const blockIndex = Math.floor((order - 1) / 8);
  if (blockIndex % 2 === 1) {
    classes.push('server-row--block-alt');
  }

  if (order > 1 && (order - 1) % 8 === 0) {
    classes.push('server-row--boundary-8');
  }

  if (order > 1 && (order - 1) % 16 === 0) {
    classes.push('server-row--boundary-16');
  }

  if (order > 1 && (order - 1) % 32 === 0) {
    classes.push('server-row--boundary-32');
  }

  return classes.join(' ');
}

function getServerRowClassName({ row }: { row: ServerRow }) {
  return row.rowClassName;
}

function buildServerMark(server: FanxiuRegionAreaItem['servers'][number]): ServerMark | null {
  if (!server.mark_type || !server.mark_label) {
    return null;
  }
  if (server.mark_type !== 'past' && server.mark_type !== 'current') {
    return null;
  }
  return {
    type: server.mark_type,
    label: server.mark_label,
    title: server.mark_title || server.mark_label,
  };
}

function buildRegionRows(regions: FanxiuRegionAreaItem[]): RegionRow[] {
  return regions.map(region => {
    const startDate = normalizeDateText(region.start_date);
    const endDate = normalizeDateText(region.end_date);
    const startDateText = formatDate(startDate);
    const endDateText = formatDate(endDate);
    const knownCount = Number(region.known_count ?? region.servers?.length ?? 0) || 0;

    return {
      number: Number(region.number) || 0,
      name: normalizeText(region.name),
      startDate,
      endDate,
      startDateText,
      endDateText,
      dateRangeText: `${startDateText} - ${endDateText}`,
      knownCount,
      knownCountLabel: `${knownCount}/${CROSS_SIZE}`,
    };
  });
}

function buildKnownRegionSections(regions: FanxiuRegionAreaItem[], rows: RegionRow[]): KnownRegionSection[] {
  const rowByName = new Map(rows.map(row => [row.name, row]));
  return regions
    .filter(region => Array.isArray(region.servers) && region.servers.length > 0)
    .map(region => {
      const row = rowByName.get(normalizeText(region.name));
      const servers = [...region.servers]
        .sort((left, right) => (Number(left.order) || 0) - (Number(right.order) || 0))
        .map(server => {
          const order = Number(server.order) || 0;
          const openDate = normalizeDateText(server.open_date);
          return {
            order,
            regionName: normalizeText(region.name),
            rowClassName: getServerRowClassNameByOrder(order),
            openDate,
            openDateText: formatDate(openDate),
            name: normalizeText(server.name),
            mark: buildServerMark(server),
          };
        });
      return {
        ...(row ?? {
          number: Number(region.number) || 0,
          name: normalizeText(region.name),
          startDate: normalizeDateText(region.start_date),
          endDate: normalizeDateText(region.end_date),
          startDateText: formatDate(region.start_date),
          endDateText: formatDate(region.end_date),
          dateRangeText: `${formatDate(region.start_date)} - ${formatDate(region.end_date)}`,
          knownCount: servers.length,
          knownCountLabel: `${servers.length}/${CROSS_SIZE}`,
        }),
        serverCountLabel: `${servers.length}/${CROSS_SIZE}服`,
        servers,
      };
    });
}

const visibleKnownRegionSections = computed(() => {
  if (!hideUnmarkedRegions.value) {
    return knownRegionSections.value;
  }
  return knownRegionSections.value.filter(region => markedRegionNameSet.value.has(region.name));
});

function getRegionServerCandidates(): FanxiuRegionServerCandidate[] {
  return knownRegionSections.value.flatMap(region =>
    region.servers.map(server => ({
      region_name: region.name,
      server_name: server.name,
    })),
  );
}

function getCharactersForServer(row: ServerRow) {
  return charactersByServerKey.value[buildServerKey(row.regionName, row.name)] ?? [];
}

function getCharacterGroupsForServer(row: ServerRow): CharacterGuildGroup[] {
  const groupMap = new Map<string, CharacterGuildGroup>();
  for (const character of getCharactersForServer(row)) {
    const guildName = normalizeText(character.guild_name);
    const key = guildName || '__unguilded__';
    let group = groupMap.get(key);
    if (!group) {
      group = {
        key,
        guildName,
        strongestScore: Number.NEGATIVE_INFINITY,
        members: [],
      };
      groupMap.set(key, group);
    }

    group.members.push(character);
    group.strongestScore = Math.max(group.strongestScore, parseAttackPower(character.attack).score);
  }

  return Array.from(groupMap.values())
    .map(group => ({
      ...group,
      members: [...group.members].sort(compareCharactersByAttackDesc),
    }))
    .sort(compareGuildGroupsByStrongestDesc);
}

function buildServerCandidateFromTarget(target: CharacterImportTarget): FanxiuRegionServerCandidate {
  return {
    region_name: target.regionName,
    server_name: target.serverName,
  };
}

function isGlobalCharacterImportPending() {
  return pendingCharacterImport.value?.mode === 'global';
}

function isPendingCharacterImport(row: ServerRow) {
  const pending = pendingCharacterImport.value;
  return pending?.mode === 'server'
    && pending.target.regionName === row.regionName
    && pending.target.serverName === row.name;
}

function toggleGlobalCharacterImport() {
  if (!canEdit.value) return;

  pendingCharacterImport.value = isGlobalCharacterImportPending() ? null : { mode: 'global' };

  if (pendingCharacterImport.value) {
    ElMessage.info('已准备导入人物截图，请直接粘贴；区服会按截图内容自动识别');
  }
}

function toggleServerCharacterImport(row: ServerRow) {
  if (!canEdit.value) return;

  pendingCharacterImport.value = isPendingCharacterImport(row)
    ? null
    : {
        mode: 'server',
        target: {
          regionName: row.regionName,
          serverName: row.name,
        },
      };

  if (pendingCharacterImport.value) {
    ElMessage.info(`已准备导入 ${row.regionName}/${row.name} 的人物截图，请直接粘贴`);
  }
}

function applyImportedCharacter(item: Partial<FanxiuRegionCharacterItem>) {
  const nextItem = normalizeCharacterItem(item);

  if (!nextItem.region_name || !nextItem.server_name || !nextItem.role_name || !nextItem.attack) {
    return { inserted: false, updated: false };
  }

  const nextKey = buildCharacterKey(nextItem);
  const remainingCharacters = regionCharacterSnapshot.value.characters.filter(character => buildCharacterKey(character) !== nextKey);
  const updated = remainingCharacters.length !== regionCharacterSnapshot.value.characters.length;
  regionCharacterSnapshot.value.characters = [...remainingCharacters, nextItem];
  return { inserted: !updated, updated };
}

async function importCharacterImage(context: CharacterImportContext, image: File) {
  importingCharacter.value = true;
  try {
    const targetServer = context.mode === 'server' ? buildServerCandidateFromTarget(context.target) : null;
    const response = await importFanxiuRegionCharacterFromOcr(image, getRegionServerCandidates(), targetServer);
    const importedItem = context.mode === 'server'
      ? {
          ...response.item,
          region_name: context.target.regionName,
          server_name: context.target.serverName,
        }
      : response.item;
    const { inserted, updated } = applyImportedCharacter(importedItem);

    if (!inserted && !updated) {
      ElMessage.warning('截图里没有可导入的人物数据');
      return;
    }

    const actionText = updated ? '新增记录并刷新展示' : '新增记录';
    const item = normalizeCharacterItem(importedItem);
    pendingCharacterImport.value = null;
    if (response.created === false) {
      ElMessage.info(`${item.region_name}/${item.server_name} ${item.guild_name ? `${item.guild_name}/` : ''}${item.role_name} 攻击 ${item.attack}，未高于旧记录，已保留旧数据`);
      return;
    }

    ElMessage.success(`${actionText} ${item.region_name}/${item.server_name} ${item.guild_name ? `${item.guild_name}/` : ''}${item.role_name}，攻击 ${item.attack}`);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '人物截图导入失败');
  } finally {
    importingCharacter.value = false;
  }
}

async function removeCharacter(characterId: string) {
  if (!canEdit.value) return;
  try {
    const disabledRecord = await disableFanxiuRegionCharacter(characterId);
    regionCharacterSnapshot.value.characters = regionCharacterSnapshot.value.characters.filter(item => item.id !== characterId);
    ElMessage.success(`已禁用 ${disabledRecord.role_name || '人物记录'}`);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '禁用人物记录失败');
  }
}

function isEditingCharacterRole(character: FanxiuRegionCharacterItem) {
  return editingCharacterRoleId.value === character.id;
}

function startCharacterRoleEdit(character: FanxiuRegionCharacterItem) {
  if (!canEdit.value) return;
  editingCharacterRoleId.value = character.id;
  editingCharacterRoleValue.value = character.role_name;
}

function cancelCharacterRoleEdit() {
  editingCharacterRoleId.value = '';
  editingCharacterRoleValue.value = '';
}

async function commitCharacterRoleEdit(character: FanxiuRegionCharacterItem) {
  if (!isEditingCharacterRole(character)) {
    return;
  }

  const nextRoleName = normalizeText(editingCharacterRoleValue.value);
  const previousCharacters = [...regionCharacterSnapshot.value.characters];
  cancelCharacterRoleEdit();
  if (!nextRoleName || nextRoleName === character.role_name) {
    return;
  }

  const previousRoleName = character.role_name;
  character.role_name = nextRoleName;
  try {
    const savedCharacter = normalizeCharacterItem(
      await updateFanxiuRegionCharacter(character.id, { role_name: nextRoleName }),
    );
    const savedKey = buildCharacterKey(savedCharacter);
    regionCharacterSnapshot.value.characters = [
      ...regionCharacterSnapshot.value.characters.filter(item => item.id !== character.id && buildCharacterKey(item) !== savedKey),
      savedCharacter,
    ];
  } catch (error: any) {
    character.role_name = previousRoleName;
    regionCharacterSnapshot.value.characters = previousCharacters;
    ElMessage.error(error?.response?.data?.detail || error?.message || '更新人物姓名失败');
  }
}

function extractClipboardImage(event: ClipboardEvent): File | null {
  const items = Array.from(event.clipboardData?.items || []);
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      return item.getAsFile();
    }
  }
  return null;
}

async function handleWindowPaste(event: ClipboardEvent) {
  const context = pendingCharacterImport.value;
  if (!context || importingCharacter.value || !canEdit.value) {
    return;
  }

  const image = extractClipboardImage(event);
  if (!image) {
    return;
  }

  event.preventDefault();
  event.stopPropagation();
  await importCharacterImage(context, image);
}

async function loadRegionCharacters() {
  loadingCharacters.value = true;
  try {
    regionCharacterSnapshot.value = normalizeCharacterSnapshot(await getFanxiuRegionCharacters());
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取区服人物数据失败');
  } finally {
    loadingCharacters.value = false;
  }
}

async function loadRegionData() {
  loadingRegions.value = true;
  try {
    const snapshot = await getFanxiuRegionData();
    const regions = Array.isArray(snapshot.regions) ? snapshot.regions : [];
    const rows = buildRegionRows(regions);
    regionRows.value = rows;
    knownRegionSections.value = buildKnownRegionSections(regions, rows);
    loadRegionViewFilter();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取区服数据失败');
  } finally {
    loadingRegions.value = false;
  }
}

onMounted(() => {
  window.addEventListener('paste', handleWindowPaste, true);
  void loadRegionData();
  void loadRegionCharacters();
});

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste, true);
});
</script>

<template>
  <div class="server-page" v-loading="loadingRegions || loadingCharacters">
    <div class="page-header">
      <h2 class="page-title">区服</h2>
      <div class="page-actions">
        <el-button
          v-if="canEdit"
          type="primary"
          link
          size="small"
          class="character-import-button"
          :loading="importingCharacter && isGlobalCharacterImportPending()"
          :disabled="importingCharacter && !isGlobalCharacterImportPending()"
          @click="toggleGlobalCharacterImport"
        >
          {{ isGlobalCharacterImportPending() ? '等待粘贴人物截图' : '全局粘贴导入' }}
        </el-button>
        <el-checkbox
          v-model="hideUnmarkedRegions"
          class="hide-filter-checkbox"
          @change="handleHideUnmarkedRegionsChange"
        >
          隐藏未勾选
        </el-checkbox>
        <span class="marked-count">已标记 {{ markedRegionCount }}个</span>
      </div>
    </div>

    <section v-if="!hideUnmarkedRegions" class="server-panel">
      <div class="section-heading">
        <div>
          <h3 class="section-title">大区</h3>
        </div>
        <div class="section-count">{{ regionRows.length }}个大区</div>
      </div>

      <div class="table-wrap">
        <el-table
          :data="regionRows"
          border
          stripe
          size="small"
          table-layout="auto"
          class="server-table"
          :fit="false"
        >
          <el-table-column label="显示" width="68" align="center">
            <template #default="{ row }">
              <el-checkbox
                :model-value="isRegionMarked(row.name)"
                :aria-label="`显示 ${row.name}`"
                @change="value => setRegionMarked(row.name, value)"
              />
            </template>
          </el-table-column>
          <el-table-column prop="number" label="大编号" width="84" align="center" />
          <el-table-column prop="name" label="大区" min-width="132" />
          <el-table-column prop="dateRangeText" label="覆盖时间" min-width="188" />
          <el-table-column prop="knownCountLabel" label="已记录区服" width="110" align="right" />
        </el-table>
      </div>
    </section>

    <section
      v-if="hideUnmarkedRegions && !visibleKnownRegionSections.length"
      class="server-panel empty-filter-panel"
    >
      暂无标记的大区数据
    </section>

    <section v-for="region in visibleKnownRegionSections" :key="region.name" class="server-panel">
      <div class="region-heading">
        <div class="region-heading__main">
          <div class="region-name">#{{ region.number }} {{ region.name }}</div>
        </div>
        <div class="region-count">{{ region.serverCountLabel }}</div>
      </div>

      <div class="summary-strip">
        <div class="summary-item">
          <span class="summary-label">大编号</span>
          <strong class="summary-value">{{ region.number }}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">大区</span>
          <strong class="summary-value">{{ region.name }}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">开服范围</span>
          <strong class="summary-value">{{ region.dateRangeText }}</strong>
        </div>
      </div>

      <div class="table-wrap">
        <el-table
          :data="region.servers"
          border
          stripe
          size="small"
          table-layout="auto"
          class="server-table"
          :fit="false"
          :row-class-name="getServerRowClassName"
        >
          <el-table-column prop="order" label="服序" width="72" align="center" />
          <el-table-column prop="openDateText" label="开服日期" width="118" />
          <el-table-column label="区服" min-width="220">
            <template #default="{ row }">
              <div class="server-name-cell">
                <span class="server-name" :class="row.mark ? `server-name--${row.mark.type}` : ''">
                  {{ row.name }}
                </span>
                <span
                  v-if="row.mark"
                  class="server-mark"
                  :class="`server-mark--${row.mark.type}`"
                  :title="row.mark.title"
                >
                  {{ row.mark.label }}
                </span>
                <el-button
                  v-if="canEdit"
                  type="primary"
                  link
                  size="small"
                  class="server-import-button"
                  :loading="importingCharacter && isPendingCharacterImport(row)"
                  :disabled="importingCharacter && !isPendingCharacterImport(row)"
                  :title="`导入到 ${row.regionName}/${row.name}`"
                  @click.stop="toggleServerCharacterImport(row)"
                >
                  {{ isPendingCharacterImport(row) ? '等待粘贴' : '导入' }}
                </el-button>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="人物数据" min-width="360">
            <template #default="{ row }">
              <div class="character-cell">
                <div v-if="getCharactersForServer(row).length" class="character-list">
                  <div
                    v-for="group in getCharacterGroupsForServer(row)"
                    :key="group.key"
                    class="character-guild-group"
                  >
                    <div v-if="group.guildName" class="character-guild-row">
                      <span class="character-guild">{{ group.guildName }}</span>
                    </div>
                    <div
                      v-for="character in group.members"
                      :key="character.id"
                      class="character-item"
                      :class="{ 'character-item--indented': group.guildName }"
                    >
                      <el-input
                        v-if="isEditingCharacterRole(character)"
                        v-model="editingCharacterRoleValue"
                        size="small"
                        class="character-role-input"
                        autofocus
                        @blur="commitCharacterRoleEdit(character)"
                        @keyup.enter="commitCharacterRoleEdit(character)"
                        @keyup.esc="cancelCharacterRoleEdit"
                      />
                      <span
                        v-else
                        class="character-role"
                        :title="canEdit ? '双击编辑姓名' : undefined"
                        @dblclick.stop="startCharacterRoleEdit(character)"
                      >
                        {{ character.role_name || '-' }}
                      </span>
                      <span class="character-attack">{{ character.attack || '-' }}</span>
                      <span
                        v-if="character.recorded_date"
                        class="character-date"
                        title="记录日期"
                      >
                        {{ formatOptionalDate(character.recorded_date) || character.recorded_date }}
                      </span>
                      <span v-else class="character-date character-date--empty">-</span>
                      <el-button
                        v-if="canEdit"
                        type="danger"
                        link
                        size="small"
                        class="character-disable-button"
                        :icon="Delete"
                        :title="`禁用 ${character.role_name || '人物'}`"
                        :aria-label="`禁用 ${character.role_name || '人物'}`"
                        @click.stop="removeCharacter(character.id)"
                      />
                    </div>
                  </div>
                </div>
                <span v-else class="character-empty">暂无</span>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.server-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding: 20px;
  background: #f5f7fa;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.page-title {
  margin: 0;
  color: #111827;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.3;
}

.page-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.hide-filter-checkbox {
  height: auto;
}

.marked-count {
  padding: 3px 8px;
  border-radius: 6px;
  background: #f1f5f9;
  color: #475569;
  font-size: 13px;
  line-height: 1.4;
}

.server-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.empty-filter-panel {
  color: #94a3b8;
  font-size: 14px;
}

.section-heading,
.region-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #edf0f3;
}

.section-title {
  margin: 0;
  color: #0f172a;
  font-size: 20px;
  font-weight: 650;
  line-height: 1.3;
}

.section-count,
.region-count {
  flex: 0 0 auto;
  padding: 6px 10px;
  border-radius: 6px;
  background: #ecfdf5;
  color: #047857;
  font-size: 14px;
  font-weight: 650;
  line-height: 1.4;
}

.region-heading__main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.region-name {
  color: #0f172a;
  font-size: 20px;
  font-weight: 650;
  line-height: 1.3;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
}

.summary-label {
  font-size: 13px;
  color: #64748b;
}

.summary-value {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
}

.server-table {
  width: max-content;
  min-width: fit-content;
}

.server-table :deep(.el-table__cell) {
  padding-top: 7px;
  padding-bottom: 7px;
}

.server-table :deep(.cell) {
  white-space: nowrap;
  word-break: keep-all;
}

.server-table :deep(.server-row--block-alt td.el-table__cell) {
  background: #f8fafc;
}

.server-table :deep(.server-row--boundary-8 td.el-table__cell) {
  border-top: 2px solid #dbe4ef;
}

.server-table :deep(.server-row--boundary-16 td.el-table__cell) {
  border-top: 3px solid #b8c5d6;
}

.server-table :deep(.server-row--boundary-32 td.el-table__cell) {
  border-top: 4px solid #7c8da6;
}

.server-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.server-name {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
}

.server-name--past {
  color: #dc2626;
  font-weight: 650;
}

.server-name--current {
  color: #047857;
  font-weight: 650;
}

.server-mark {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  vertical-align: middle;
}

.server-mark--past {
  background: #fef2f2;
  color: #dc2626;
}

.server-mark--current {
  background: #ecfdf5;
  color: #047857;
}

.server-import-button {
  flex: 0 0 auto;
}

.character-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.character-list {
  --character-role-width: 106px;
  --character-attack-width: 78px;
  --character-date-width: 74px;
  --character-action-width: 20px;

  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  width: max-content;
}

.character-guild-group {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.character-guild-row {
  display: flex;
  align-items: center;
  min-height: 22px;
}

.character-item {
  display: grid;
  grid-template-columns:
    var(--character-role-width)
    var(--character-attack-width)
    var(--character-date-width)
    var(--character-action-width);
  column-gap: 8px;
  align-items: center;
  min-height: 24px;
  color: #334155;
  font-size: 13px;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
}

.character-item--indented {
  padding-left: 14px;
}

.character-guild {
  padding: 1px 6px;
  border-radius: 6px;
  background: #eff6ff;
  color: #2563eb;
  font-weight: 600;
}

.character-role {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #0f172a;
  font-weight: 600;
}

.character-role-input {
  width: var(--character-role-width);
}

.character-role-input :deep(.el-input__wrapper) {
  min-height: 24px;
}

.character-attack {
  justify-self: end;
  color: #b45309;
  font-weight: 650;
  text-align: right;
  white-space: nowrap;
}

.character-date {
  justify-self: start;
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.character-date--empty {
  color: transparent;
}

.character-disable-button {
  justify-self: center;
  width: var(--character-action-width);
  min-width: var(--character-action-width);
  padding: 0;
}

.character-empty {
  color: #94a3b8;
  font-size: 13px;
}

@media (max-width: 720px) {
  .server-page {
    padding: 12px;
  }

  .server-panel {
    padding: 12px;
  }

  .section-heading,
  .region-heading {
    flex-direction: column;
  }

  .summary-strip {
    flex-direction: column;
  }

  .summary-item {
    align-items: center;
    justify-content: space-between;
  }
}
</style>
