<template>
  <div class="xianzhou-race-layout">
    <div class="main-section">
      <div class="page-header">
        <div class="header-left">
          <h2>活动列表 · 仙舟马拉松</h2>
          <span class="header-tip">直接在表格中修改数量，修改后自动保存。点击行编辑人物文档。</span>
        </div>
        <el-button type="primary" @click="refreshData" :loading="loading">刷新数据</el-button>
      </div>

      <el-card class="partner-card" shadow="never">
        <template #header>
          <div class="card-header">
            <span>伙伴概率</span>
            <el-popover
              placement="bottom-end"
              title="攻略"
              width="380"
              trigger="click"
              popper-class="xianzhou-strategy-popover"
            >
              <template #reference>
                <el-button
                  :icon="QuestionFilled"
                  circle
                  text
                  class="strategy-help-button"
                  title="查看攻略"
                  aria-label="查看攻略"
                />
              </template>
              <div class="strategy-help">
                <p>数量表示该伙伴当前累计拥有的同名张数。</p>
                <ul>
                  <li>第1张用于获得角色，可按绝品0星理解。</li>
                  <li>从第2张开始，每多1张可升1星，因此前6张正好对应绝品0星到5星。</li>
                  <li>第7张会发生质变，进入仙品0星；仙品阶段同样还有1星到5星。</li>
                  <li>速度先按已确认区间估算：每位角色初始仙舟速度240里/秒，每升1级提升24里/秒；仙品阶段规律尚未实测，先标记为待确认。</li>
                  <li>仙舟需要选出5位角色组成队伍。暂不考虑技能互补时，先从已拥有数量、星级/仙品进度和角色强度里挑出更优的5位。</li>
                  <li>角色强度、技能搭配和自动推荐算法先不写死，后续情报补齐后再继续细化。</li>
                </ul>
              </div>
            </el-popover>
          </div>
        </template>
        <div class="partner-table-shell">
          <el-table
            :data="partnerRows"
            border
            stripe
            size="small"
            table-layout="auto"
            class="partner-table"
            row-key="name"
            highlight-current-row
            :current-row-key="currentEditingCharName"
            :fit="false"
            @row-click="handleRowClick"
          >
            <el-table-column type="index" label="编号" width="48" align="center" />
            <el-table-column prop="tier" label="梯队" width="80" />
            <el-table-column prop="probability" label="中奖概率" width="80" align="center" />
            <el-table-column prop="name" label="伙伴" width="96" />
            <el-table-column label="技能" width="72" align="center">
              <template #default="{ row }">
                {{ getSkillName(row) }}
              </template>
            </el-table-column>
            <el-table-column label="功能" min-width="420" class-name="function-column">
              <template #default="{ row }">
                <div class="function-text">
                  {{ getCurrentSkillText(row) }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="数量" width="120" align="center" class-name="count-column">
              <template #default="{ row }">
                <div class="discrete-stepper" @click.stop>
                  <div class="discrete-stepper__value">
                    {{ getPartnerStageLabel(row.count) }}
                  </div>
                  <div class="discrete-stepper__controls">
                    <button
                      type="button"
                      class="discrete-stepper__button"
                      :disabled="!canEditRow(row)"
                      @click.stop="adjustCount(row, 1)"
                    >
                      <el-icon><ArrowUp /></el-icon>
                    </button>
                    <button
                      type="button"
                      class="discrete-stepper__button"
                      :disabled="!canEditRow(row) || !canDecreaseCount(row.count)"
                      @click.stop="adjustCount(row, -1)"
                    >
                      <el-icon><ArrowDown /></el-icon>
                    </button>
                  </div>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>
    </div>

    <div class="editor-section" :class="{ 'is-collapsed': !currentEditingNote }">
      <div v-if="currentEditingNote" class="editor-container">
        <UniversalNoteEditor
          :model-value="currentEditingNote"
          :on-save="handleSave"
          :on-save-keepalive="handleSaveKeepalive"
          empty-text="数据加载中..."
          class="editor-instance"
          @change="onEditorNoteChange"
          :readonly="currentEditingNote?.can_edit === false"
          :show-private-toggle="false"
          :lock-title="true"
          :lock-node-type="true"
          :lock-note-form="true"
        >
        </UniversalNoteEditor>
      </div>
      <div v-else class="empty-editor">
        <el-empty description="点击上方伙伴开始编辑人物文档" :image-size="60" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { ArrowDown, ArrowUp, QuestionFilled } from '@element-plus/icons-vue';
import UniversalNoteEditor from '@/components/UniversalNoteEditor.vue';
import { getFanxiuChars, updateFanxiuChar } from '@/api/fanxiu';
import { noteKey, useNoteStore, type NoteNode } from '@/api/notes';
import { useUserStore } from '@/store/userStore';
import { putJsonKeepalive } from '@/utils/keepaliveRequest';
import {
  deriveLegacySemanticsFromTaxonomy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_MEMO,
  NOTE_KIND_FANXIU_CHAR,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  NOTE_WEIGHT_MODE_LINEAR
} from '@/utils/noteSemantics';

const userStore = useUserStore();
const noteStore = useNoteStore();

interface CharItem {
    name: string;
    tier: '第1梯队' | '第2梯队';
    probability: '1%' | '2%';
    functionText: string;
    skillName?: string;
    skillStages?: PartnerSkillStage[];
    note?: NoteNode;
    count: number;
}

interface PartnerSkillStage {
    stageIndex: number;
    label: string;
    text: string;
}

const loading = ref(false);
const currentEditingNote = ref<NoteNode | undefined>(undefined);
const currentEditingCharName = ref('');

const partnerRows = ref<CharItem[]>([
    { tier: '第1梯队', probability: '1%', name: '凌玉灵', functionText: '冲锋加速，仙品后可减队友冷却', count: 0 },
    { tier: '第1梯队', probability: '1%', name: '大衍神君', functionText: '强化冲刺/瞬移效果，潜行免疫负面', count: 0 },
    { tier: '第1梯队', probability: '1%', name: '黑凤王', functionText: '加速/冲刺触发永久叠速，偏后期成长', count: 0 },
    { tier: '第1梯队', probability: '1%', name: '黛儿', functionText: '稳定冲刺并短时加速，仙品可减冷却', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '南宫婉', functionText: '稳定瞬移，仙品附带后方减速', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '向之礼', functionText: '开局基础速度高，随时间逐步衰减', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '冰凤仙子', functionText: '中点前后两段基础速度提升', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '银月', functionText: '每15秒永久叠基础速度，偏长线成长', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '甲天木', functionText: '有负面则净化瞬移，无负面则短时加速', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '元刹', functionText: '范围内船只越多，基础速度加成越高', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '天元圣皇', functionText: '冲刺超车后追加冲刺，追赶上限高', count: 0 },
    { tier: '第2梯队', probability: '2%', name: '冰魄仙子', functionText: '随机瞬移/加速/冲刺，多效果组合收益', count: 0 },
]);

const PARTNER_TIER_ORDER: Record<CharItem['tier'], number> = {
    第1梯队: 0,
    第2梯队: 1,
};
const PARTNER_BASE_ORDER = new Map(partnerRows.value.map((item, index) => [item.name, index]));

const sortPartnerRowsAfterRefresh = () => {
    partnerRows.value.sort((left, right) => {
        const tierDiff = PARTNER_TIER_ORDER[left.tier] - PARTNER_TIER_ORDER[right.tier];
        if (tierDiff !== 0) {
            return tierDiff;
        }

        const countDiff = normalizeCount(right.count) - normalizeCount(left.count);
        if (countDiff !== 0) {
            return countDiff;
        }

        return (PARTNER_BASE_ORDER.get(left.name) ?? 0) - (PARTNER_BASE_ORDER.get(right.name) ?? 0);
    });
};

const canEdit = computed(() => {
    const notes = partnerRows.value
      .map(item => item.note)
      .filter((note): note is NoteNode => Boolean(note));

    if (notes.length > 0) {
      return notes.some(note => note.can_edit !== false);
    }

    if (!userStore.user) return false;
    return userStore.user.username === '凡修手游' || userStore.isAdmin;
});

const canEditRow = (row: CharItem) => row.note ? row.note.can_edit !== false : canEdit.value;

const LOCAL_STORAGE_KEY = 'fanxiu_xianzhou_marathon_partner_counts';
const FANXIU_NOTE_CATEGORIES = [{ key: NOTE_CATEGORY_DEFAULT, weight: 100 }];
const PARTNER_STAGE_QUALITY_LABELS = ['绝品', '仙品', '神品'];
const CHINESE_STAR_NUMBERS: Record<string, number> = {
    零: 0,
    〇: 0,
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
};

const normalizeCount = (value: unknown) => {
    const numeric = Number(value ?? 0);
    if (!Number.isFinite(numeric)) {
        return 0;
    }
    return Math.max(0, Math.round(numeric));
};

const getPartnerStageLabel = (value: unknown) => {
    const count = normalizeCount(value);
    if (count <= 0) {
        return '未拥有';
    }

    const ownedIndex = count - 1;
    const stageIndex = Math.floor(ownedIndex / 6);
    const star = ownedIndex % 6;
    const quality = PARTNER_STAGE_QUALITY_LABELS[Math.min(stageIndex, PARTNER_STAGE_QUALITY_LABELS.length - 1)];
    const overflow = stageIndex >= PARTNER_STAGE_QUALITY_LABELS.length
        ? `+${(stageIndex - PARTNER_STAGE_QUALITY_LABELS.length + 1) * 6}张`
        : '';

    return `${quality}${star}星${overflow}`;
};

const getPartnerStageIndex = (value: unknown) => {
    const count = normalizeCount(value);
    return count > 0 ? count - 1 : null;
};

const decodeHtmlEntities = (value: string) => {
    if (!value) {
        return '';
    }
    if (typeof document === 'undefined') {
        return value
            .replace(/&nbsp;/g, ' ')
            .replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>')
            .replace(/&amp;/g, '&')
            .replace(/&quot;/g, '"')
            .replace(/&#39;/g, "'");
    }

    const textarea = document.createElement('textarea');
    textarea.innerHTML = value;
    return textarea.value;
};

const normalizeOcrHtmlText = (value: string) => decodeHtmlEntities(
    value
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/(?:p|div|li)>/gi, '\n')
        .replace(/<[^>]+>/g, '')
)
    .replace(/\r/g, '')
    .replace(/\u00a0/g, ' ')
    .trim();

const extractXianzhouOcrText = (content: string | undefined) => {
    const html = String(content || '');
    const patterns = [
        /<pre\b[^>]*data-codeyun-ocr-text=["']xianzhou-skills["'][^>]*>([\s\S]*?)<\/pre>/i,
        /<h3[^>]*>\s*技能OCR\s*<\/h3>\s*<pre\b[^>]*>([\s\S]*?)<\/pre>/i,
    ];

    for (const pattern of patterns) {
        const match = html.match(pattern);
        if (match?.[1]) {
            return normalizeOcrHtmlText(match[1]);
        }
    }

    const skillTitleIndex = html.lastIndexOf('技能OCR');
    if (skillTitleIndex >= 0) {
        const tailMatch = html.slice(skillTitleIndex).match(/<pre\b[^>]*>([\s\S]*?)<\/pre>/i);
        if (tailMatch?.[1]) {
            return normalizeOcrHtmlText(tailMatch[1]);
        }
    }

    return '';
};

const parseXianzhouSkillName = (content: string | undefined) => {
    const ocrText = extractXianzhouOcrText(content);
    const match = ocrText.match(/【([^】]+)】/);
    if (!match?.[1]) {
        return '';
    }

    const rawName = match[1].trim();
    return rawName.split(/[·.．]/)[0]?.trim() || rawName;
};

const parseStarToken = (value: string | undefined) => {
    const token = String(value || '').trim();
    if (!token) {
        return null;
    }
    if (/^\d+$/.test(token)) {
        const numeric = Number(token);
        return Number.isFinite(numeric) ? numeric : null;
    }
    return CHINESE_STAR_NUMBERS[token] ?? null;
};

const parseSkillStageHeader = (line: string) => {
    const initialMatch = line.match(/^初始[：:](.*)$/);
    if (initialMatch) {
        return { stageIndex: 0, label: '绝品0星', text: initialMatch[1].trim() };
    }

    const juepinMatch = line.match(/^([1-5一二三四五])星[：:](.*)$/);
    if (juepinMatch) {
        const star = parseStarToken(juepinMatch[1]);
        if (star !== null && star >= 1 && star <= 5) {
            return { stageIndex: star, label: `绝品${star}星`, text: juepinMatch[2].trim() };
        }
    }

    const xianpinMatch = line.match(/^仙(?:品)?([0-5零〇一二三四五])星[：:](.*)$/);
    if (xianpinMatch) {
        const star = parseStarToken(xianpinMatch[1]);
        if (star !== null && star >= 0 && star <= 5) {
            return { stageIndex: 6 + star, label: `仙品${star}星`, text: xianpinMatch[2].trim() };
        }
    }

    const shenpinMatch = line.match(/^神(?:品)?([0-5零〇一二三四五])星[：:](.*)$/);
    if (shenpinMatch) {
        const star = parseStarToken(shenpinMatch[1]);
        if (star !== null && star >= 0 && star <= 5) {
            return { stageIndex: 12 + star, label: `神品${star}星`, text: shenpinMatch[2].trim() };
        }
    }

    if (/^五星后可升至仙品/.test(line)) {
        const text = line.replace(/^五星后可升至仙品[，,]?\s*/, '').trim() || line;
        return { stageIndex: 6, label: '仙品0星', text };
    }

    return null;
};

const joinSkillLines = (lines: string[]) => lines.reduce((result, line) => {
    const text = line.trim();
    if (!text) {
        return result;
    }
    if (!result) {
        return text;
    }
    if (/^\d+[.．]/.test(text)) {
        if (/[：:]$/.test(result)) {
            return `${result}${text}`;
        }
        return `${result}；${text}`;
    }
    if (/^若/.test(text) && !/[；;，,：:]$/.test(result)) {
        return `${result}；${text}`;
    }
    return `${result}${text}`;
}, '');

const shouldSkipOcrLine = (line: string) => (
    !line
    || /^##\s*图/.test(line)
    || line.startsWith('□')
    || line === '仙舟技能'
    || line.startsWith('仙舟基础速度')
);

const parseXianzhouSkillStages = (content: string | undefined): PartnerSkillStage[] => {
    const ocrText = extractXianzhouOcrText(content);
    if (!ocrText) {
        return [];
    }

    const stages: PartnerSkillStage[] = [];
    let current: { stageIndex: number; label: string; lines: string[] } | null = null;
    const pushCurrent = () => {
        if (!current) {
            return;
        }
        const text = joinSkillLines(current.lines);
        if (text) {
            stages.push({
                stageIndex: current.stageIndex,
                label: current.label,
                text,
            });
        }
    };

    for (const rawLine of ocrText.split('\n')) {
        const line = rawLine.trim();
        if (shouldSkipOcrLine(line)) {
            continue;
        }

        const header = parseSkillStageHeader(line);
        if (header) {
            pushCurrent();
            current = {
                stageIndex: header.stageIndex,
                label: header.label,
                lines: header.text ? [header.text] : [],
            };
            continue;
        }

        if (current && !/^【[^】]+】$/.test(line)) {
            current.lines.push(line);
        }
    }
    pushCurrent();

    const latestByStage = new Map<number, PartnerSkillStage>();
    stages.forEach(stage => latestByStage.set(stage.stageIndex, stage));
    return [...latestByStage.values()].sort((left, right) => left.stageIndex - right.stageIndex);
};

const syncRowSkillStages = (row: CharItem, note: NoteNode | undefined) => {
    row.skillName = parseXianzhouSkillName(note?.content);
    row.skillStages = parseXianzhouSkillStages(note?.content);
};

const getSkillName = (row: CharItem) => row.skillName || '-';

const getCurrentSkillText = (row: CharItem) => {
    const stageIndex = getPartnerStageIndex(row.count);
    if (stageIndex === null) {
        return '未拥有';
    }

    const stages = row.skillStages || [];
    const exact = stages.find(stage => stage.stageIndex === stageIndex);
    if (exact) {
        return exact.text;
    }

    const fallbackStage = [...stages].reverse().find(stage => stage.stageIndex <= stageIndex);
    return fallbackStage?.text || row.functionText;
};

const buildFanxiuTaxonomyPayload = (source: Partial<NoteNode> = {}) => {
    const taxonomy = deriveLegacySemanticsFromTaxonomy(
        FANXIU_NOTE_CATEGORIES,
        NOTE_CATEGORY_DEFAULT,
        NOTE_FORM_MEMO,
        NOTE_KIND_FANXIU_CHAR,
        source.lifecycle_stage ?? source.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
    );

    return {
        note_categories: taxonomy.note_categories,
        primary_category: taxonomy.primary_category,
        note_form: taxonomy.note_form,
        note_scene: taxonomy.note_scene,
        lifecycle_stage: taxonomy.lifecycle_stage,
        note_types: taxonomy.note_types,
        node_type: taxonomy.node_type,
        note_kind: taxonomy.note_kind,
        node_status: taxonomy.node_status
    };
};

const buildFanxiuNotePayload = (source: Partial<NoteNode> = {}) => ({
    ...source,
    ...buildFanxiuTaxonomyPayload(source),
    weight_mode: NOTE_WEIGHT_MODE_LINEAR
});

const loadLocalCounts = () => {
    try {
        const saved = localStorage.getItem(LOCAL_STORAGE_KEY);
        if (saved) {
            return JSON.parse(saved) as Record<string, number>;
        }
    } catch (e) {
        console.error('Failed to load local counts:', e);
    }
    return {};
};

const saveLocalCounts = () => {
    const counts: Record<string, number> = {};
    partnerRows.value.forEach(item => {
        counts[item.name] = item.count;
    });
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(counts));
};

const refreshData = async () => {
    loading.value = true;
    const localCounts = loadLocalCounts();
    
    try {
        const notes = await getFanxiuChars();
        
        partnerRows.value.forEach(item => {
            const note = notes.find(n => n.title === item.name);
            item.note = note;
            syncRowSkillStages(item, note);
            item.count = localCounts[item.name] !== undefined ? normalizeCount(localCounts[item.name]) : 0;
        });
        saveLocalCounts();
        
    } catch (e) {
        console.error(e);
        partnerRows.value.forEach(item => {
            if (localCounts[item.name] !== undefined) {
                item.count = normalizeCount(localCounts[item.name]);
            }
        });
        ElMessage.warning('从云端获取数据失败，当前显示本地缓存数据');
    } finally {
        sortPartnerRowsAfterRefresh();
        loading.value = false;
    }
};

const canDecreaseCount = (value: unknown) => normalizeCount(value) > 0;

const adjustCount = (row: CharItem, delta: 1 | -1) => {
    if (!canEditRow(row)) return;
    const nextValue = normalizeCount(row.count) + delta;
    row.count = Math.max(0, nextValue);
    saveLocalCounts();
};

const handleRowClick = async (row: CharItem) => {
    currentEditingCharName.value = row.name;
    if (row.note) {
        currentEditingNote.value = JSON.parse(JSON.stringify(row.note));
    } else {
        try {
            const newNote = await updateFanxiuChar(row.name, buildFanxiuNotePayload({
                title: row.name,
                content: '',
                start_at: Date.now()
            }));
            row.note = newNote;
            syncRowSkillStages(row, newNote);
            currentEditingNote.value = JSON.parse(JSON.stringify(newNote));
        } catch (e) {
            console.error(e);
            ElMessage.error('初始化笔记失败');
            return;
        }
    }

    setTimeout(() => {
        const editorEl = document.querySelector('.editor-section');
        if (editorEl) {
            editorEl.scrollIntoView({ behavior: 'smooth' });
        }
    }, 100);
};

const resolveCharNameForSave = (note: Partial<NoteNode>) => {
    const noteTitle = typeof note.title === 'string' ? note.title.trim() : '';
    return noteTitle || currentEditingCharName.value;
};

const handleSave = async (note: NoteNode, patch: Partial<NoteNode> = {}) => {
    const charName = resolveCharNameForSave(note);
    const syncRowNote = (updatedNote: NoteNode) => {
        const item = partnerRows.value.find(i => (updatedNote.id && i.note?.id === updatedNote.id) || i.name === charName);
        if (item) {
            item.note = updatedNote;
            syncRowSkillStages(item, updatedNote);
        }
    };

    if (note.id) {
        const updatedNote = await noteStore.updateNote(note.id, buildFanxiuNotePayload({
            ...(Object.keys(patch).length ? patch : note),
            title: charName,
        }));
        if (!updatedNote) throw new Error('保存失败');
        syncRowNote(updatedNote);
        return updatedNote;
    }

    const createdNote = await updateFanxiuChar(charName, buildFanxiuNotePayload({
        ...(Object.keys(patch).length ? patch : note),
        title: charName
    }));
    syncRowNote(createdNote);
    return createdNote;
};

const handleSaveKeepalive = (note: NoteNode, patch: Partial<NoteNode> = {}) => {
    const charName = resolveCharNameForSave(note);
    const payload = buildFanxiuNotePayload({
        ...(Object.keys(patch).length ? patch : note),
        title: charName
    });

    if (note.id) {
        const normalizedPayload: Record<string, any> = { ...payload };
        if (typeof normalizedPayload.start_at === 'number' && normalizedPayload.start_at > 10000000000) {
            normalizedPayload.start_at /= 1000;
        }
        putJsonKeepalive(`/api/notes/${encodeURIComponent(noteKey(note.id))}`, normalizedPayload);
        return;
    }

    const normalizedPayload: Record<string, any> = { ...payload };
    if (typeof normalizedPayload.start_at === 'number' && normalizedPayload.start_at > 10000000000) {
        normalizedPayload.start_at /= 1000;
    }
    putJsonKeepalive(`/api/fanxiu/chars/${encodeURIComponent(charName)}`, normalizedPayload);
};

const onEditorNoteChange = (note: NoteNode) => {
    const item = partnerRows.value.find(i => (note.id && i.note?.id === note.id) || i.name === currentEditingCharName.value);
    if (item) {
        item.note = note;
        syncRowSkillStages(item, note);
    }
    currentEditingNote.value = JSON.parse(JSON.stringify(note));
};

onMounted(() => {
    refreshData();
});

</script>

<style scoped>
.xianzhou-race-layout {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
    background-color: #f5f7fa;
    overflow-x: hidden;
    overflow-y: auto;
}

.main-section {
    padding: 20px;
    border-bottom: 1px solid #e6e6e6;
    background-color: #f5f7fa;
}

.page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
}

.header-left {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.header-tip {
    font-size: 12px;
    color: #909399;
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.strategy-help-button {
    font-size: 16px;
}

.strategy-help {
    color: var(--el-text-color-regular);
    font-size: 13px;
    line-height: 1.7;
}

.strategy-help p {
    margin: 0 0 8px;
}

.strategy-help ul {
    margin: 0;
    padding-left: 18px;
}

.strategy-help li + li {
    margin-top: 4px;
}

.partner-card {
    border-radius: 8px;
}

.partner-table-shell {
    width: 100%;
    overflow-x: auto;
}

.partner-table {
    width: max-content;
    min-width: fit-content;
}

:deep(.partner-table .el-table__cell .cell) {
    white-space: nowrap;
}

.function-text {
    white-space: normal;
    overflow-wrap: break-word;
    word-break: break-word;
    line-height: 1.45;
    padding: 2px 0;
}

:deep(.function-column .cell) {
    white-space: normal;
    overflow: visible;
    text-overflow: clip;
}

:deep(.count-column.el-table__cell) {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

:deep(.count-column .cell) {
    display: flex;
    align-items: stretch;
    justify-content: center;
    height: 100%;
    min-height: 100%;
    padding: 0 !important;
}

.discrete-stepper {
    display: flex;
    width: 100%;
    min-height: 100%;
    height: 100%;
    border: 1px solid var(--el-border-color);
    background: transparent;
    align-self: stretch;
    box-sizing: border-box;
}

.discrete-stepper__value {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 4px;
    font-size: 13px;
    white-space: nowrap;
}

.discrete-stepper__controls {
    width: 24px;
    display: flex;
    flex-direction: column;
    border-left: 1px solid var(--el-border-color);
}

.discrete-stepper__button {
    flex: 1 1 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--el-text-color-regular);
    cursor: pointer;
}

.discrete-stepper__button + .discrete-stepper__button {
    border-top: 1px solid var(--el-border-color);
}

.discrete-stepper__button:disabled {
    cursor: not-allowed;
    color: var(--el-text-color-placeholder);
}

.discrete-stepper__button:not(:disabled):hover {
    background: var(--el-fill-color-light);
}

/* Bottom Editor Section */
.editor-section {
    flex: 1;
    background-color: #fff;
    display: flex;
    flex-direction: column;
    min-height: 600px;
}

.editor-section.is-collapsed {
    background-color: #fafafa;
}

.editor-container {
    display: flex;
    flex-direction: column;
}

.editor-instance {
    padding: 20px;
}

.empty-editor {
    flex: 1;
    display: flex;
    justify-content: center;
    align-items: center;
}

:deep(.el-table__row) {
    cursor: pointer;
}

:deep(.el-table__body tr.current-row > td.el-table__cell) {
    background-color: #ecf5ff !important;
}
</style>
