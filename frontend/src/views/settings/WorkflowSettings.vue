<template>
  <div>
    <!-- ========== 功能模块管理 ========== -->
    <n-h4 prefix="bar" style="margin-bottom: 8px">📦 功能模块</n-h4>
    <n-text depth="3" style="display: block; margin-bottom: 12px; font-size: 12px">
      功能模块是工作流的构建块 — 每个模块对应一种面板组件（AI 对话、代码实施等）。内置模块不可删除。
    </n-text>
    <n-spin :show="store.loading">
      <n-grid :cols="isMobile ? 1 : 2" :x-gap="12" :y-gap="12" v-if="store.modules.length">
        <n-gi v-for="mod in store.modules" :key="mod.id">
          <n-card size="small" style="background: #1a1a2e" hoverable>
            <n-space align="center" :size="10">
              <span style="font-size: 22px">{{ mod.icon }}</span>
              <div style="flex: 1; min-width: 0">
                <n-space align="center" :size="6">
                  <n-text strong>{{ mod.display_name }}</n-text>
                  <n-tag size="tiny" :bordered="false" round>
                    <code style="font-size: 11px">{{ mod.name }}</code>
                  </n-tag>
                  <n-tag v-if="mod.is_builtin" size="tiny" type="info" round>内置</n-tag>
                </n-space>
                <n-text depth="3" style="font-size: 11px; display: block; margin-top: 1px">
                  {{ mod.description || '无描述' }}
                </n-text>
              </div>
              <n-space :size="4">
                <n-tag size="tiny" :bordered="false" type="info">{{ mod.component_key }}</n-tag>
                <n-switch v-model:value="mod.is_enabled" size="small" @update:value="toggleModule(mod, $event)" />
                <n-button v-if="!mod.is_builtin" size="tiny" quaternary @click="openEditModule(mod)">
                  <template #icon><n-icon :component="CreateOutline" /></template>
                </n-button>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="tiny" quaternary disabled>
                      <template #icon><n-icon :component="CreateOutline" /></template>
                    </n-button>
                  </template>
                  内置模块不可编辑
                </n-tooltip>
                <n-popconfirm v-if="!mod.is_builtin" @positive-click="handleDeleteModule(mod)">
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  确定删除「{{ mod.display_name }}」？
                </n-popconfirm>
              </n-space>
            </n-space>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else description="暂无功能模块" />
    </n-spin>

    <n-divider />

    <!-- ========== 工作流管理 ========== -->
    <n-space justify="space-between" align="center" style="margin-bottom: 12px">
      <div>
        <n-h4 prefix="bar" style="margin-bottom: 2px">🔄 工作流</n-h4>
        <n-text depth="3" style="font-size: 12px">
          工作流定义项目的完整生命周期 — 由功能模块按顺序拼接而成。每种项目类型对应一个工作流。
        </n-text>
      </div>
      <n-button type="primary" size="small" @click="openCreateWorkflow">
        <template #icon><n-icon :component="AddOutline" /></template>
        新建工作流
      </n-button>
    </n-space>

    <n-spin :show="store.loading">
      <n-grid :cols="1" :y-gap="12" v-if="store.workflows.length">
        <n-gi v-for="wf in store.workflows" :key="wf.id">
          <n-card size="small" style="background: #1a1a2e" hoverable>
            <!-- 工作流头部: 名称 + 操作 -->
            <n-space justify="space-between" align="center">
              <n-space align="center" :size="10">
                <span style="font-size: 24px">{{ wf.icon }}</span>
                <div>
                  <n-space align="center" :size="6">
                    <n-text strong>{{ wf.display_name }}</n-text>
                    <n-tag size="tiny" :bordered="false" round>
                      <code style="font-size: 11px">{{ wf.name }}</code>
                    </n-tag>
                    <n-tag v-if="wf.is_builtin" size="tiny" type="info" round>内置</n-tag>
                    <n-tag v-if="!wf.is_enabled" size="tiny" type="warning" round>已禁用</n-tag>
                  </n-space>
                  <n-text depth="3" style="font-size: 12px; display: block; margin-top: 2px">
                    {{ wf.description }}
                  </n-text>
                </div>
              </n-space>
              <n-space :size="8" align="center">
                <n-switch :value="wf.is_enabled" size="small" @update:value="toggleWorkflow(wf, $event)" />
                <n-button v-if="!wf.is_builtin" size="tiny" quaternary @click="openEditWorkflow(wf)">
                  <template #icon><n-icon :component="CreateOutline" /></template>
                </n-button>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="tiny" quaternary disabled>
                      <template #icon><n-icon :component="CreateOutline" /></template>
                    </n-button>
                  </template>
                  内置工作流不可编辑
                </n-tooltip>
                <n-button size="tiny" quaternary @click="handleDuplicate(wf)">
                  <template #icon><n-icon :component="CopyOutline" /></template>
                </n-button>
                <n-popconfirm v-if="!wf.is_builtin" @positive-click="handleDeleteWorkflow(wf)">
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  确定删除「{{ wf.display_name }}」？
                </n-popconfirm>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="tiny" quaternary disabled>
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  内置工作流不可删除
                </n-tooltip>
              </n-space>
            </n-space>

            <!-- 阶段步骤条预览 -->
            <div style="margin-top: 10px; padding: 6px 8px; background: rgba(255,255,255,0.03); border-radius: 6px">
              <n-space :size="4" align="center" :wrap="false" style="overflow-x: auto">
                <template v-for="(stage, i) in wf.stages" :key="i">
                  <n-tag size="tiny" :bordered="false" :type="stage.role ? 'info' : 'default'" round>
                    {{ stage.label }}
                    <template v-if="stage.role">
                      <span style="opacity: 0.6; margin-left: 2px">· {{ stage.role }}</span>
                    </template>
                  </n-tag>
                  <n-text v-if="i < wf.stages.length - 1" depth="3" style="font-size: 10px">→</n-text>
                </template>
              </n-space>
            </div>

            <!-- 模块组装预览 -->
            <div style="margin-top: 6px">
              <n-space :size="6" :wrap="true">
                <n-tag
                  v-for="(mod, i) in wf.modules"
                  :key="i"
                  size="small"
                  :bordered="false"
                  :type="moduleTagType(mod.module_name)"
                  round
                >
                  {{ moduleIcon(mod.module_name) }} {{ mod.tab_label }}
                </n-tag>
              </n-space>
            </div>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else description="暂无工作流" />
    </n-spin>

    <!-- ========== 模块编辑弹窗 ========== -->
    <n-modal v-model:show="showModuleModal" preset="card" :title="moduleForm.id ? '编辑模块' : '新建模块'" style="width: 520px; max-width: 95vw" :bordered="false">
      <n-form :model="moduleForm" label-placement="left" label-width="80" size="small">
        <n-form-item label="标识名">
          <n-input v-model:value="moduleForm.name" :disabled="!!moduleForm.is_builtin" placeholder="如 ai_chat" />
        </n-form-item>
        <n-form-item label="显示名">
          <n-input v-model:value="moduleForm.display_name" placeholder="如 AI 对话" />
        </n-form-item>
        <n-form-item label="图标">
          <n-input v-model:value="moduleForm.icon" style="width: 60px" />
        </n-form-item>
        <n-form-item label="组件 Key">
          <n-select
            v-model:value="moduleForm.component_key"
            :options="componentOptions"
            placeholder="选择 Vue 组件"
          />
        </n-form-item>
        <n-form-item label="描述">
          <n-input v-model:value="moduleForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showModuleModal = false">取消</n-button>
          <n-button type="primary" @click="saveModule" :loading="saving">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- ========== 工作流编辑弹窗 ========== -->
    <n-modal v-model:show="showWorkflowModal" preset="card" :title="wfForm.id ? '编辑工作流' : '新建工作流'" style="width: 720px; max-width: 95vw; max-height: 85vh" :bordered="false">
      <n-tabs type="segment" size="small" animated>
        <!-- 基本信息 -->
        <n-tab-pane name="basic" tab="📝 基本信息">
          <n-form :model="wfForm" label-placement="left" label-width="80" size="small">
            <n-form-item label="标识名">
              <n-input v-model:value="wfForm.name" :disabled="!!wfForm.is_builtin" placeholder="如 requirement" />
            </n-form-item>
            <n-form-item label="显示名">
              <n-input v-model:value="wfForm.display_name" placeholder="如 需求迭代" />
            </n-form-item>
            <n-form-item label="图标">
              <n-input v-model:value="wfForm.icon" style="width: 60px" />
            </n-form-item>
            <n-form-item label="描述">
              <n-input v-model:value="wfForm.description" type="textarea" :rows="2" />
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 阶段定义 -->
        <n-tab-pane name="stages" tab="📊 阶段">
          <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 8px">
            定义项目生命周期的阶段步骤条。每个阶段可绑定一个角色。
          </n-text>
          <n-dynamic-input v-model:value="wfForm.stages" :on-create="createStage" #default="{ value: stage }">
            <n-space :size="8" align="center" style="flex: 1">
              <n-input v-model:value="stage.key" placeholder="key" size="small" style="width: 100px" />
              <n-input v-model:value="stage.label" placeholder="标签" size="small" style="width: 80px" />
              <n-input v-model:value="stage.status" placeholder="status" size="small" style="width: 100px" />
              <n-input v-model:value="stage.role" placeholder="角色 (选填)" size="small" style="width: 120px" />
            </n-space>
          </n-dynamic-input>
        </n-tab-pane>

        <!-- 模块组装 -->
        <n-tab-pane name="modules" tab="🧩 模块组装">
          <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 8px">
            编排模块顺序，每一行对应一个 Tab 面板。同一模块可使用多次（如讨论 + 审查都用 AI 对话）。
          </n-text>
          <n-dynamic-input v-model:value="wfForm.modules" :on-create="createModuleEntry" #default="{ value: entry }">
            <div style="flex: 1; display: flex; flex-direction: column; gap: 6px;">
              <n-space :size="8" align="center">
                <n-select
                  v-model:value="entry.module_name"
                  :options="availableModuleOptions"
                  placeholder="选择模块"
                  size="small"
                  style="width: 140px"
                />
                <n-input v-model:value="entry.tab_key" placeholder="tab_key" size="small" style="width: 100px" />
                <n-input v-model:value="entry.tab_label" placeholder="Tab 标签" size="small" style="width: 140px" />
                <n-input v-model:value="entry.role_name" placeholder="角色 (选填)" size="small" style="width: 120px" />
              </n-space>
              <n-space :size="4" align="center" style="margin-left: 4px">
                <n-text depth="3" style="font-size: 11px">阶段:</n-text>
                <n-select
                  v-model:value="entry.stage_statuses"
                  :options="stageStatusOptions"
                  multiple
                  size="tiny"
                  style="min-width: 200px"
                  placeholder="绑定阶段 status"
                />
                <n-text depth="3" style="font-size: 11px; margin-left: 8px">模式:</n-text>
                <n-select
                  v-model:value="entry.config.mode"
                  :options="[{label:'discuss',value:'discuss'},{label:'review',value:'review'},{label:'(无)',value:''}]"
                  size="tiny"
                  style="width: 100px"
                  placeholder="模式"
                  clearable
                />
              </n-space>
            </div>
          </n-dynamic-input>
        </n-tab-pane>

        <!-- UI 文案 -->
        <n-tab-pane name="labels" tab="🏷️ 文案">
          <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 8px">
            界面显示文案配置。键值对格式 (如 project_noun → 需求)。
          </n-text>
          <n-dynamic-input
            v-model:value="uiLabelPairs"
            :on-create="() => ({ key: '', value: '' })"
            #default="{ value: pair }"
          >
            <n-space :size="8" align="center" style="flex: 1">
              <n-input v-model:value="pair.key" placeholder="键" size="small" style="width: 200px" />
              <n-input v-model:value="pair.value" placeholder="值" size="small" style="flex: 1" />
            </n-space>
          </n-dynamic-input>
        </n-tab-pane>
      </n-tabs>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showWorkflowModal = false">取消</n-button>
          <n-button type="primary" @click="saveWorkflow" :loading="saving">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import { NIcon } from 'naive-ui'
import { AddOutline, CreateOutline, CopyOutline, TrashOutline } from '@vicons/ionicons5'
import { useWorkflowStore, type WorkflowModule, type Workflow, type WorkflowModuleItem } from '@/stores/workflow'

const store = useWorkflowStore()
const message = useMessage()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))
const saving = ref(false)

// -------- 模块编辑 --------
const showModuleModal = ref(false)
const moduleForm = ref<any>({})

function openEditModule(mod: WorkflowModule) {
  moduleForm.value = { ...mod }
  showModuleModal.value = true
}

async function toggleModule(mod: WorkflowModule, enabled: boolean) {
  try {
    await store.updateModule(mod.id, { is_enabled: enabled })
    message.success(enabled ? '已启用' : '已禁用')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}

async function saveModule() {
  saving.value = true
  try {
    if (moduleForm.value.id) {
      await store.updateModule(moduleForm.value.id, moduleForm.value)
    } else {
      await store.createModule(moduleForm.value)
    }
    showModuleModal.value = false
    message.success('已保存')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleDeleteModule(mod: WorkflowModule) {
  try {
    await store.deleteModule(mod.id)
    message.success('已删除')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

const componentOptions = [
  { label: 'ChatPanel — AI 对话', value: 'ChatPanel' },
  { label: 'ImplementPanel — 代码实施', value: 'ImplementPanel' },
  { label: 'DeployPanel — 部署发布', value: 'DeployPanel' },
  { label: 'SnapshotPanel — 快照管理', value: 'SnapshotPanel' },
]

// -------- 工作流编辑 --------
const showWorkflowModal = ref(false)
const wfForm = ref<any>({
  stages: [],
  modules: [],
})
const uiLabelPairs = ref<Array<{key:string, value:string}>>([])

function openCreateWorkflow() {
  wfForm.value = {
    name: '',
    display_name: '',
    icon: '🔄',
    description: '',
    stages: [],
    modules: [],
    ui_labels: {},
  }
  uiLabelPairs.value = []
  showWorkflowModal.value = true
}

function openEditWorkflow(wf: Workflow) {
  wfForm.value = {
    ...wf,
    stages: (wf.stages || []).map(s => ({ ...s })),
    modules: (wf.modules || []).map(m => ({
      ...m,
      stage_statuses: m.stage_statuses || [],
      config: { ...(m.config || {}) },
    })),
  }
  uiLabelPairs.value = Object.entries(wf.ui_labels || {}).map(([key, value]) => ({ key, value }))
  showWorkflowModal.value = true
}

async function saveWorkflow() {
  saving.value = true
  try {
    // 将 uiLabelPairs 转回 ui_labels dict
    const labels: Record<string, string> = {}
    for (const p of uiLabelPairs.value) {
      if (p.key.trim()) labels[p.key.trim()] = p.value
    }
    const payload = {
      ...wfForm.value,
      ui_labels: labels,
    }
    if (wfForm.value.id) {
      await store.updateWorkflow(wfForm.value.id, payload)
    } else {
      await store.createWorkflow(payload)
    }
    showWorkflowModal.value = false
    message.success('已保存')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleWorkflow(wf: Workflow, enabled: boolean) {
  try {
    await store.updateWorkflow(wf.id, { is_enabled: enabled })
    message.success(enabled ? '已启用' : '已禁用')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}

async function handleDuplicate(wf: Workflow) {
  try {
    await store.duplicateWorkflow(wf.id)
    message.success('已复制')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '复制失败')
  }
}

async function handleDeleteWorkflow(wf: Workflow) {
  try {
    await store.deleteWorkflow(wf.id)
    message.success('已删除')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

function createStage() {
  return { key: '', label: '', status: '', role: '' }
}

function createModuleEntry() {
  return {
    module_name: '',
    tab_key: '',
    tab_label: '',
    stage_statuses: [],
    role_name: '',
    config: { mode: '' },
  }
}

const availableModuleOptions = computed(() =>
  store.modules
    .filter(m => m.is_enabled)
    .map(m => ({ label: `${m.icon} ${m.display_name} (${m.name})`, value: m.name }))
)

const stageStatusOptions = computed(() =>
  (wfForm.value.stages || []).map((s: any) => ({
    label: `${s.label || s.key} (${s.status})`,
    value: s.status || s.key,
  }))
)

function moduleTagType(moduleName: string): string {
  const map: Record<string, string> = {
    ai_chat: 'info',
    implement: 'warning',
    deploy: 'success',
    snapshot: 'default',
  }
  return map[moduleName] || 'default'
}

function moduleIcon(moduleName: string): string {
  const mod = store.modules.find(m => m.name === moduleName)
  return mod?.icon || '📦'
}

onMounted(() => {
  store.fetchAll()
})
</script>
