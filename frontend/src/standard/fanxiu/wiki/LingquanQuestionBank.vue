<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createFanxiuLingquanQuestion,
  deleteFanxiuLingquanQuestion,
  getFanxiuLingquanQuestions,
  updateFanxiuLingquanQuestion,
  type FanxiuLingquanQuestion,
} from '@/api/fanxiu'

const loading = ref(false)
const query = ref('')
const selectedGroup = ref('')
const items = ref<FanxiuLingquanQuestion[]>([])
const groups = ref<Array<{ name: string; count: number }>>([])
const editorVisible = ref(false)
const editingId = ref('')
const form = reactive({ group_name: '游戏剧情', question: '', answer: '', enabled: true, order_index: 0 })

const visibleGroups = computed(() => {
  const names = selectedGroup.value ? [selectedGroup.value] : groups.value.map(group => group.name)
  return names.map(name => ({ name, items: items.value.filter(item => item.group_name === name) }))
})

async function load() {
  loading.value = true
  try {
    const data = await getFanxiuLingquanQuestions({ query: query.value, group_name: selectedGroup.value })
    items.value = data.items
    groups.value = data.groups
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  Object.assign(form, { group_name: selectedGroup.value || groups.value[0]?.name || '游戏剧情', question: '', answer: '', enabled: true, order_index: 0 })
  editorVisible.value = true
}

function openEdit(item: FanxiuLingquanQuestion) {
  editingId.value = item.id
  Object.assign(form, item)
  editorVisible.value = true
}

async function save() {
  if (!form.question.trim() || !form.answer.trim()) {
    ElMessage.warning('题目和答案不能为空')
    return
  }
  const payload = { ...form, group_name: form.group_name.trim(), question: form.question.trim(), answer: form.answer.trim() }
  if (editingId.value) await updateFanxiuLingquanQuestion(editingId.value, payload)
  else await createFanxiuLingquanQuestion(payload)
  editorVisible.value = false
  ElMessage.success('题库已保存')
  await load()
}

async function removeEditing() {
  if (!editingId.value) return
  await ElMessageBox.confirm(`删除“${form.question}”？`, '删除题目', { type: 'warning' })
  await deleteFanxiuLingquanQuestion(editingId.value)
  editorVisible.value = false
  ElMessage.success('已删除')
  await load()
}

async function chooseGroup(group: string) {
  selectedGroup.value = group
  await load()
}

onMounted(load)
</script>

<template>
  <section class="lingquan-bank" v-loading="loading">
    <div class="bank-tools">
      <el-input v-model="query" clearable placeholder="搜索题目或答案" @keyup.enter="load" @clear="load" />
      <el-button @click="load">搜索</el-button>
      <el-button type="primary" @click="openCreate">添加题目</el-button>
    </div>
    <div class="group-tabs">
      <button type="button" :class="{ active: !selectedGroup }" @click="chooseGroup('')">全部</button>
      <button v-for="group in groups" :key="group.name" type="button" :class="{ active: selectedGroup === group.name }" @click="chooseGroup(group.name)">
        {{ group.name }} <span>{{ group.count }}</span>
      </button>
    </div>
    <div v-for="group in visibleGroups" :key="group.name" class="question-group">
      <h3>{{ group.name }} <span>{{ group.items.length }}</span></h3>
      <el-table :data="group.items" row-key="id" @row-dblclick="openEdit">
        <el-table-column prop="question" label="题目" min-width="420" />
        <el-table-column prop="answer" label="答案" min-width="150" />
        <el-table-column label="状态" width="90">
          <template #default="scope">{{ scope.row.enabled ? '启用' : '停用' }}</template>
        </el-table-column>
      </el-table>
    </div>
    <el-empty v-if="!loading && !items.length" description="没有匹配题目" />

    <el-dialog v-model="editorVisible" :title="editingId ? '编辑题目' : '添加题目'" width="620px">
      <el-form label-width="64px">
        <el-form-item label="分组"><el-input v-model="form.group_name" /></el-form-item>
        <el-form-item label="题目"><el-input v-model="form.question" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="答案"><el-input v-model="form.answer" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="editingId" type="danger" text @click="removeEditing">删除题目</el-button>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.lingquan-bank { padding: 18px 24px 32px; }
.bank-tools { display: flex; gap: 10px; max-width: 760px; }
.group-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }
.group-tabs button { border: 1px solid #d8dee8; background: #fff; border-radius: 16px; padding: 6px 12px; color: #526174; cursor: pointer; }
.group-tabs button.active { color: #315efb; border-color: #91a8ff; background: #f2f5ff; }
.group-tabs span, .question-group h3 span { color: #8a96a8; font-size: 12px; margin-left: 4px; }
.question-group { margin-top: 22px; }
.question-group h3 { margin: 0 0 10px; font-size: 15px; font-weight: 600; color: #293447; }
</style>
