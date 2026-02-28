<template>
  <div v-if="project">
    <!-- 顶部: 紧凑信息条 + 内联步骤条 -->
    <div class="project-header-bar" :class="{ 'project-header-bar-mobile': isMobile }">
      <div class="project-header-left">
        <n-button text size="small" @click="$router.push('/projects')" style="padding: 0; font-size: 12px">← 返回</n-button>
        <n-text strong :style="{ fontSize: '14px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: isMobile ? '120px' : 'none' }">{{ project.title }}</n-text>
        <n-tag :type="statusType(project.status)" size="tiny" round>{{ statusLabel(project.status) }}</n-tag>
        <n-tag v-if="project.is_archived" type="default" size="tiny" :bordered="false" round>已归档</n-tag>
      </div>
      <div v-if="!isMobile" class="project-header-steps">
        <div v-for="(step, i) in stepLabels" :key="i"
             class="step-dot-item"
             :class="{ 'step-done': i + 1 < currentStep || (isAtTerminalStage && i + 1 === currentStep), 'step-current': i + 1 === currentStep && !isAtTerminalStage }">
          <span class="step-dot">{{ (i + 1 < currentStep || (isAtTerminalStage && i + 1 === currentStep)) ? '✓' : i + 1 }}</span>
          <span class="step-text">{{ step }}</span>
        </div>
      </div>
      <div class="project-header-right" :class="{ 'project-header-right-mobile': isMobile }">
        <n-button
          v-if="discussModule"
          size="tiny"
          :quaternary="!showPlanPanel"
          :type="showPlanPanel ? 'info' : 'default'"
          @click="showPlanPanel = !showPlanPanel"
          style="font-size: 11px"
        >
          {{ isMobile ? '📋' : getModuleLabel(discussModule, 'plan_tab_label', outputTabLabel) }}
        </n-button>
        <n-button
          v-if="reviewModule && activeTab === reviewModule.tab_key"
          size="tiny"
          :quaternary="!showReviewPanel"
          :type="showReviewPanel ? 'info' : 'default'"
          @click="showReviewPanel = !showReviewPanel"
          style="font-size: 11px"
        >
          {{ isMobile ? '📋' : getModuleLabel(reviewModule, 'plan_output_noun', reviewOutputNoun) }}
        </n-button>
        <n-button v-if="!isMobile" size="tiny" quaternary :type="project.is_archived ? 'warning' : 'default'" @click="toggleArchive">
          {{ project.is_archived ? '取消归档' : '归档项目' }}
        </n-button>
        <n-tag v-if="project.github_issue_number && !isMobile" size="tiny" :bordered="false">Issue #{{ project.github_issue_number }}</n-tag>
        <n-tag v-if="project.github_pr_number && !isMobile" size="tiny" :bordered="false" type="info">PR #{{ project.github_pr_number }}</n-tag>
      </div>
    </div>

    <!-- 移动端: 步骤进度条 -->
    <div v-if="isMobile" class="mobile-step-bar">
      <div v-for="(step, i) in stepLabels" :key="i"
           class="mobile-step-dot"
           :class="{ 'step-done': i + 1 < currentStep || (isAtTerminalStage && i + 1 === currentStep), 'step-current': i + 1 === currentStep && !isAtTerminalStage }">
        <span class="step-dot">{{ (i + 1 < currentStep || (isAtTerminalStage && i + 1 === currentStep)) ? '✓' : i + 1 }}</span>
      </div>
    </div>

    <!-- 主内容 Tabs — 动态工作流模块渲染 -->
    <n-tabs type="line" animated v-model:value="activeTab" size="small" style="--n-tab-padding: 6px 12px">
      <template v-for="mod in visibleModules" :key="mod.tab_key">

        <!-- ═══ ChatPanel: discuss 模式 ═══ -->
        <n-tab-pane
          v-if="getComponentKey(mod) === 'ChatPanel' && mod.config?.mode === 'discuss'"
          :name="mod.tab_key"
          :tab="mod.tab_label"
          :disabled="isTabLocked(mod.tab_key)"
        >
          <div v-if="project.workspace_dir && project.iteration_count > 0" class="workspace-info-bar">
            <n-tag size="small" :bordered="false" type="info">🔄 迭代 #{{ project.iteration_count }}</n-tag>
            <n-tag size="small" :bordered="false">📁 {{ project.workspace_dir }}</n-tag>
          </div>
          <n-alert v-if="isStageReadonly('discussing')" type="info" style="margin-bottom: 8px" :bordered="false">
            讨论阶段已完成，当前为只读模式。如需修改，请在审查阶段点击「继续迭代」。
          </n-alert>
          <div class="discuss-layout" :class="{ 'discuss-layout-mobile': isMobile }">
            <div class="discuss-chat">
              <ChatPanel :key="'discuss-' + project.id" :project="project" :readonly="isStageReadonly('discussing')" @plan-finalized="onPlanFinalized" />
            </div>
            <div v-if="showPlanPanel && !isMobile" class="discuss-plan">
              <div class="plan-panel-header">
                <n-button size="tiny" quaternary circle @click="showPlanPanel = false" style="flex-shrink: 0">✕</n-button>
              </div>
              <div class="plan-panel-body">
                <PlanEditor :project="project" :output-noun="getModuleLabel(mod, 'plan_output_noun', outputNoun)" :finalize-action="getModuleLabel(mod, 'finalize_action', finalizeAction)" @updated="refreshProject" />
              </div>
            </div>
            <!-- 移动端: 设计稿面板全屏覆盖 -->
            <n-drawer v-if="isMobile" v-model:show="showPlanPanel" placement="bottom" :height="'85vh'" :native-scrollbar="false">
              <n-drawer-content :title="getModuleLabel(mod, 'plan_output_noun', outputNoun)" closable>
                <PlanEditor :project="project" :output-noun="getModuleLabel(mod, 'plan_output_noun', outputNoun)" :finalize-action="getModuleLabel(mod, 'finalize_action', finalizeAction)" @updated="refreshProject" />
              </n-drawer-content>
            </n-drawer>
          </div>
        </n-tab-pane>

        <!-- ═══ ChatPanel: review 模式 ═══ -->
        <n-tab-pane
          v-else-if="getComponentKey(mod) === 'ChatPanel' && mod.config?.mode === 'review'"
          :name="mod.tab_key"
          :tab="mod.tab_label"
          :disabled="isTabLocked(mod.tab_key)"
        >
          <div v-if="!reviewPrepared" style="padding: 40px 0; text-align: center;">
            <n-result status="info" title="准备审查环境" description="克隆实施分支、获取变更信息、加载需求文档到 AI 上下文">
              <template #footer>
                <n-space vertical align="center" :size="16">
                  <n-button type="primary" size="large" @click="handlePrepareReview" :loading="preparingReview">
                    🔍 开始审查
                  </n-button>
                  <n-text v-if="project.branch_name" depth="3" style="font-size: 12px">
                    将基于分支 <n-tag size="small" :bordered="false">{{ project.branch_name }}</n-tag> 创建审查工作区
                  </n-text>
                </n-space>
              </template>
            </n-result>
          </div>
          <div v-else>
            <div class="workspace-info-bar">
              <n-tag size="small" :bordered="false" type="success">✅ 审查环境就绪</n-tag>
              <n-tag v-if="reviewInfo.branch" size="small" :bordered="false">🌿 {{ reviewInfo.branch }}</n-tag>
              <n-tag v-if="reviewInfo.diff_stat" size="small" :bordered="false">📊 {{ reviewInfo.diff_stat }}</n-tag>
              <n-tag v-if="reviewInfo.changed_files.length" size="small" :bordered="false">
                📝 {{ reviewInfo.changed_files.length }} 个文件变更
              </n-tag>
              <n-button size="tiny" quaternary @click="handleStartIteration" :loading="startingIteration" v-if="project.status === 'reviewing'">
                🔄 继续迭代
              </n-button>
            </div>
            <div class="discuss-layout" :class="{ 'discuss-layout-mobile': isMobile }">
              <div class="discuss-chat">
                <ChatPanel :key="'review-' + project.id" :project="project" @plan-finalized="onReviewFinalized" />
              </div>
              <div v-if="showReviewPanel && !isMobile" class="discuss-plan">
                <div class="plan-panel-header">
                  <n-button size="tiny" quaternary circle @click="showReviewPanel = false" style="flex-shrink: 0">✕</n-button>
                </div>
                <div class="plan-panel-body">
                  <PlanEditor :project="project" :output-noun="getModuleLabel(mod, 'plan_output_noun', reviewOutputNoun)" :finalize-action="getModuleLabel(mod, 'finalize_action', reviewFinalizeAction)" @updated="refreshProject" />
                </div>
              </div>
              <!-- 移动端: 审查报告面板全屏覆盖 -->
              <n-drawer v-if="isMobile" v-model:show="showReviewPanel" placement="bottom" :height="'85vh'" :native-scrollbar="false">
                <n-drawer-content :title="getModuleLabel(mod, 'plan_output_noun', reviewOutputNoun)" closable>
                  <PlanEditor :project="project" :output-noun="getModuleLabel(mod, 'plan_output_noun', reviewOutputNoun)" :finalize-action="getModuleLabel(mod, 'finalize_action', reviewFinalizeAction)" @updated="refreshProject" />
                </n-drawer-content>
              </n-drawer>
            </div>
          </div>
        </n-tab-pane>

        <!-- ═══ ImplementPanel ═══ -->
        <n-tab-pane
          v-else-if="getComponentKey(mod) === 'ImplementPanel'"
          :name="mod.tab_key"
          :tab="mod.tab_label"
          :disabled="isTabLocked(mod.tab_key)"
        >
          <ImplementPanel :project="project" @status-changed="refreshProject" @go-review="goToReview" />
        </n-tab-pane>

        <!-- ═══ DeployPanel ═══ -->
        <n-tab-pane
          v-else-if="getComponentKey(mod) === 'DeployPanel'"
          :name="mod.tab_key"
          :tab="mod.tab_label"
          :disabled="isTabLocked(mod.tab_key)"
        >
          <DeployPanel :project="project" @deployed="refreshProject" />
        </n-tab-pane>

        <!-- ═══ SnapshotPanel ═══ -->
        <n-tab-pane
          v-else-if="getComponentKey(mod) === 'SnapshotPanel'"
          :name="mod.tab_key"
          :tab="mod.tab_label"
        >
          <SnapshotPanel :project-id="project.id" />
        </n-tab-pane>

      </template>
    </n-tabs>
  </div>
  <n-spin v-else :show="true" style="margin-top: 100px" />
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useProjectStore } from '@/stores/project'
import { implementationApi } from '@/api/index'
import ChatPanel from '@/components/ChatPanel.vue'
import PlanEditor from '@/components/PlanEditor.vue'
import ImplementPanel from '@/components/ImplementPanel.vue'
import DeployPanel from '@/components/DeployPanel.vue'
import SnapshotPanel from '@/components/SnapshotPanel.vue'

const route = useRoute()
const store = useProjectStore()
const message = useMessage()

// ── 响应式检测 ──────────────────────────────────────────
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
const isMobile = computed(() => windowWidth.value < 768)
function _onResize() { windowWidth.value = window.innerWidth }
const _storedTab = sessionStorage.getItem('project_detail_tab')
const activeTab = ref(_storedTab || 'discuss')
watch(activeTab, (v) => sessionStorage.setItem('project_detail_tab', v))
const showPlanPanel = ref(false)
const showReviewPanel = ref(false)

// 审查准备状态
const reviewPrepared = ref(false)
const preparingReview = ref(false)
const startingIteration = ref(false)
const reviewInfo = ref<{
  branch: string
  diff_stat: string
  changed_files: string[]
  workspace_dir: string
}>({ branch: '', diff_stat: '', changed_files: [], workspace_dir: '' })

const project = computed(() => store.currentProject)
const DEFAULT_STEP_LABELS = ['草稿', '讨论', '定稿', '实施', '审核', '部署', '完成']
const DEFAULT_STATUS_ORDER = ['draft', 'discussing', 'planned', 'implementing', 'reviewing', 'deploying', 'deployed']

// ---- 模块名 → 组件 Key 映射 (Fallback, 优先从 type_info.modules 的 component_key 读取) ----
const MODULE_COMPONENT_MAP: Record<string, string> = {
  ai_chat: 'ChatPanel',
  implement: 'ImplementPanel',
  deploy: 'DeployPanel',
  snapshot: 'SnapshotPanel',
}

// ---- 默认工作流模块 (当 type_info.modules 为空时使用) ----
function buildDefaultModules(labels: any) {
  return [
    { module_name: 'ai_chat', tab_key: 'discuss', tab_label: labels?.discuss_tab_label || '💬 讨论 & 设计', component_key: 'ChatPanel', stage_statuses: ['draft', 'discussing', 'planned'], config: { mode: 'discuss', plan_panel: true, plan_output_noun: labels?.output_noun || '设计稿', plan_tab_label: labels?.output_tab_label || '📋 设计稿', finalize_action: labels?.finalize_action || '敲定方案' } },
    { module_name: 'implement', tab_key: 'implement', tab_label: '🔨 实施', component_key: 'ImplementPanel', stage_statuses: ['implementing'], config: {} },
    { module_name: 'ai_chat', tab_key: 'review', tab_label: labels?.review_discuss_tab_label || '💬 审查', component_key: 'ChatPanel', stage_statuses: ['reviewing'], config: { mode: 'review', plan_panel: true, plan_output_noun: labels?.review_output_noun || '审查报告', plan_tab_label: labels?.review_tab_label || '📋 审查报告', finalize_action: labels?.review_finalize_action || '生成报告' } },
    { module_name: 'deploy', tab_key: 'deploy', tab_label: '🚀 部署', component_key: 'DeployPanel', stage_statuses: ['deploying', 'deployed'], config: {} },
    { module_name: 'snapshot', tab_key: 'snapshots', tab_label: '📸 快照', component_key: 'SnapshotPanel', stage_statuses: [], config: { always_visible: true } },
  ]
}

// ---- 工作流模块 (驱动动态 Tab 渲染) ----
const workflowModules = computed(() => {
  const mods = project.value?.type_info?.modules
  if (mods && mods.length > 0) return mods
  return buildDefaultModules(project.value?.type_info?.ui_labels)
})

// 可见模块 (过滤掉阶段不存在的模块)
const visibleModules = computed(() => {
  const allStatuses = stageStatusOrder.value
  return workflowModules.value.filter((mod: any) => {
    if (mod.config?.always_visible) return true
    if (!mod.stage_statuses || mod.stage_statuses.length === 0) return true
    return mod.stage_statuses.some((s: string) => allStatuses.includes(s))
  })
})

// 快捷引用: discuss 和 review 模块
const discussModule = computed(() => workflowModules.value.find((m: any) => m.config?.mode === 'discuss'))
const reviewModule = computed(() => workflowModules.value.find((m: any) => m.config?.mode === 'review'))

// 获取模块的 component_key (优先 module 自带, 回退到映射表)
function getComponentKey(mod: any): string {
  return mod.component_key || MODULE_COMPONENT_MAP[mod.module_name] || ''
}

// 从模块 config 获取文案 (带 fallback)
function getModuleLabel(mod: any, configKey: string, fallback: string): string {
  return mod?.config?.[configKey] || fallback
}

// 兼容旧代码的 UI 文案 computed (从 ui_labels 读取)
const outputNoun = computed(() => project.value?.type_info?.ui_labels?.output_noun || '设计稿')
const outputTabLabel = computed(() => project.value?.type_info?.ui_labels?.output_tab_label || `📋 ${outputNoun.value}`)
const finalizeAction = computed(() => project.value?.type_info?.ui_labels?.finalize_action || '敲定方案')
const reviewOutputNoun = computed(() => project.value?.type_info?.ui_labels?.review_output_noun || '审查报告')
const reviewFinalizeAction = computed(() => project.value?.type_info?.ui_labels?.review_finalize_action || '生成报告')

// 兼容: hasReviewStage (头部按钮仍需要)
const hasReviewStage = computed(() => !!reviewModule.value)

const stepLabels = computed(() => {
  const stages = project.value?.type_info?.stages
  if (stages && stages.length > 0) return stages.map(s => s.label)
  return DEFAULT_STEP_LABELS
})

const stageStatusOrder = computed(() => {
  const stages = project.value?.type_info?.stages
  if (stages && stages.length > 0) return stages.map(s => s.status)
  return DEFAULT_STATUS_ORDER
})


const currentStep = computed(() => {
  const status = project.value?.status || 'draft'
  const order = stageStatusOrder.value
  const idx = order.indexOf(status)
  // 如果找到了, step = idx + 1; 末尾状态（closed/rolled_back）= 最后一步
  if (idx >= 0) return idx + 1
  return order.length
})

// 项目是否已到达类型定义的最终阶段
const isAtTerminalStage = computed(() => {
  const stages = project.value?.type_info?.stages
  const status = project.value?.status
  if (!stages || stages.length === 0 || !status) return false
  return stages[stages.length - 1].status === status
})

function statusType(s: string) {
  // 如果当前状态是类型配置的最终阶段, 显示 success
  const stages = project.value?.type_info?.stages
  if (stages && stages.length > 0 && stages[stages.length - 1].status === s) return 'success'
  const m: Record<string, any> = {
    draft:'default', discussing:'info', planned:'warning', implementing:'warning',
    reviewing:'info', deploying:'warning', deployed:'success', rolled_back:'error',
  }
  return m[s] || 'default'
}

function statusLabel(s: string) {
  // 优先从类型定义的 stages 获取标签
  const stages = project.value?.type_info?.stages
  if (stages) {
    const stage = stages.find(st => st.status === s)
    if (stage) return stage.label
  }
  const m: Record<string, string> = {
    draft:'草稿', discussing:'讨论中', planned:'已定稿', implementing:'实施中',
    reviewing:'审核中', deploying:'部署中', deployed:'已部署', rolled_back:'已回滚',
  }
  return m[s] || s
}

async function refreshProject() {
  const id = Number(route.params.id)
  if (id) await store.fetchProject(id)
}

// ---- 状态 → 默认 Tab 映射 (动态, 从工作流模块构建) ----
const statusTabMap = computed(() => {
  const map: Record<string, string> = {}
  for (const mod of workflowModules.value) {
    for (const status of (mod.stage_statuses || [])) {
      if (!map[status]) map[status] = mod.tab_key
    }
  }
  // rolled_back 特殊处理: 映射到 deploy tab (如果存在)
  const deployMod = workflowModules.value.find((m: any) => getComponentKey(m) === 'DeployPanel')
  if (deployMod && !map['rolled_back']) map['rolled_back'] = deployMod.tab_key
  return map
})

function getDefaultTab(status: string): string {
  return statusTabMap.value[status] || (visibleModules.value[0]?.tab_key || 'discuss')
}

function syncActiveTab() {
  if (!project.value) return
  const targetTab = getDefaultTab(project.value.status)
  // 检查目标 tab 是否在可见模块中
  if (!visibleModules.value.some((m: any) => m.tab_key === targetTab)) return
  activeTab.value = targetTab
}

// ---- 阶段只读管理 ----
function isStageReadonly(stageStatus: string): boolean {
  if (!project.value) return false
  const order = stageStatusOrder.value
  const currentIdx = order.indexOf(project.value.status)
  const stageIdx = order.indexOf(stageStatus)
  if (currentIdx < 0 || stageIdx < 0) return false
  // 当前状态之前的阶段都是只读
  return stageIdx < currentIdx
}

function isTabLocked(tabKey: string): boolean {
  if (!project.value) return false
  const order = stageStatusOrder.value
  const currentStatus = project.value.status
  const currentIdx = order.indexOf(currentStatus)
  if (currentIdx < 0) return false

  // 查找对应的模块定义
  const mod = workflowModules.value.find((m: any) => m.tab_key === tabKey)
  if (!mod) return false
  if (mod.config?.always_visible) return false

  // 包含 draft 或 discussing 的 tab 永不锁 (初始阶段)
  const modStatuses = mod.stage_statuses || []
  if (modStatuses.includes('draft') || modStatuses.includes('discussing')) return false
  if (modStatuses.length === 0) return false

  // 找到该模块最早的阶段 index
  const firstStatus = modStatuses[0]
  const tabIdx = order.indexOf(firstStatus)
  if (tabIdx < 0) return false

  // 允许当前阶段和下一阶段
  return tabIdx > currentIdx + 1
}

// ---- 审查准备 ----
async function handlePrepareReview() {
  if (!project.value) return
  preparingReview.value = true
  try {
    const res = await implementationApi.prepareReview(project.value.id)
    if (res.data.success) {
      reviewPrepared.value = true
      reviewInfo.value = {
        branch: res.data.branch || '',
        diff_stat: res.data.diff_stat || '',
        changed_files: res.data.changed_files || [],
        workspace_dir: res.data.workspace_dir || '',
      }
      await refreshProject()
      message.success('审查环境准备完成')
    } else {
      message.error(res.data.message || '审查环境准备失败')
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '审查环境准备失败')
  } finally {
    preparingReview.value = false
  }
}

// ---- 继续迭代 ----
async function handleStartIteration() {
  if (!project.value) return
  startingIteration.value = true
  try {
    const res = await implementationApi.startIteration(project.value.id)
    if (res.data.success) {
      message.success(`已开始第 ${res.data.iteration} 次迭代`)
      reviewPrepared.value = false
      reviewInfo.value = { branch: '', diff_stat: '', changed_files: [], workspace_dir: '' }
      await refreshProject()
      syncActiveTab()  // 切换到讨论 tab
    } else {
      message.error(res.data.message || '迭代启动失败')
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '迭代启动失败')
  } finally {
    startingIteration.value = false
  }
}

function onPlanFinalized() {
  refreshProject()
  showPlanPanel.value = true
}

function onReviewFinalized() {
  refreshProject()
  showReviewPanel.value = true
}

function goToReview() {
  const rm = reviewModule.value
  activeTab.value = rm?.tab_key || 'review'
  refreshProject()
}

async function toggleArchive() {
  if (!project.value) return
  const nextArchived = !project.value.is_archived
  try {
    await store.updateProject(project.value.id, { is_archived: nextArchived })
    message.success(nextArchived ? '已归档' : '已取消归档')
    await refreshProject()
  } catch {
    message.error('操作失败')
  }
}

// ---- 初次加载后检查状态，可能需要自动恢复审查准备状态 ----
async function initAfterLoad() {
  if (!project.value) return
  syncActiveTab()
  // 如果处于 reviewing 状态且有 workspace_dir，认为审查已准备
  if (project.value.status === 'reviewing' && project.value.workspace_dir) {
    reviewPrepared.value = true
    // 获取工作区信息
    try {
      const res = await implementationApi.getWorkspaceInfo(project.value.id)
      reviewInfo.value = {
        branch: res.data.branch || '',
        diff_stat: '',
        changed_files: [],
        workspace_dir: res.data.workspace_dir || '',
      }
    } catch {
      // 忽略错误，用户可重新准备
    }
  }
}

onMounted(async () => {
  window.addEventListener('resize', _onResize)
  await refreshProject()
  initAfterLoad()
})

onUnmounted(() => {
  window.removeEventListener('resize', _onResize)
})
watch(() => route.params.id, async () => {
  reviewPrepared.value = false
  reviewInfo.value = { branch: '', diff_stat: '', changed_files: [], workspace_dir: '' }
  await refreshProject()
  initAfterLoad()
})
</script>

<style scoped>
/* ============ 紧凑顶部信息条 ============ */
.project-header-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  margin-bottom: 6px;
  background: #212121;
  border-radius: 8px;
  flex-wrap: nowrap;
  min-height: 36px;
}
.project-header-bar-mobile {
  gap: 6px;
  padding: 6px 8px;
  flex-wrap: wrap;
}

/* 工作区信息条 */
.workspace-info-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  margin-bottom: 8px;
  background: rgba(14, 165, 233, 0.06);
  border: 1px solid rgba(14, 165, 233, 0.15);
  border-radius: 6px;
  flex-wrap: wrap;
}
.project-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.project-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  margin-left: auto;
}
.project-header-right-mobile {
  gap: 4px;
  flex-wrap: wrap;
}

/* 内联迷你步骤条 */
.project-header-steps {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 1;
  justify-content: center;
  overflow: hidden;
}
.step-dot-item {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0.35;
  transition: opacity 0.15s;
  white-space: nowrap;
}
.step-dot-item.step-done {
  opacity: 0.55;
}
.step-dot-item.step-current {
  opacity: 1;
}
.step-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  font-size: 9px;
  font-weight: 600;
  background: rgba(255,255,255,0.08);
  color: #aaa;
  flex-shrink: 0;
}
.step-done .step-dot {
  background: #18a058;
  color: #fff;
  font-size: 10px;
}
.step-current .step-dot {
  background: #0ea5e9;
  color: #fff;
  box-shadow: 0 0 6px rgba(14, 165, 233, 0.4);
}
.step-text {
  font-size: 10px;
  color: #888;
}
.step-current .step-text {
  color: #e0e0e0;
  font-weight: 500;
}
/* 设计稿面板布局 */
.discuss-layout {
  display: flex;
  height: calc(100vh - 200px);
  min-height: 400px;
  overflow: hidden;
}
.discuss-layout-mobile {
  height: calc(100vh - 180px);
  height: calc(100dvh - 180px);
  min-height: 300px;
}
.discuss-chat {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
.discuss-plan {
  position: relative;
  width: 42%;
  min-width: 340px;
  max-width: 560px;
  flex-shrink: 0;
  border-left: 1px solid #2a2a3e;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 移动端步骤条 */
.mobile-step-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 8px;
  margin-bottom: 4px;
}
.mobile-step-dot {
  opacity: 0.35;
}
.mobile-step-dot.step-done {
  opacity: 0.6;
}
.mobile-step-dot.step-current {
  opacity: 1;
}
.plan-panel-header {
  display: flex;
  justify-content: flex-end;
  padding: 4px 6px 0;
  flex-shrink: 0;
}
.plan-panel-body {
  flex: 1;
  overflow: hidden;
  padding: 0 10px 8px;
  display: flex;
  flex-direction: column;
}

/* 步骤之间的连线 */
.step-dot-item + .step-dot-item::before {
  content: '';
  display: inline-block;
  width: 16px;
  height: 1px;
  background: rgba(255,255,255,0.12);
  margin-right: 2px;
  flex-shrink: 0;
}
.step-dot-item.step-done + .step-dot-item::before {
  background: #18a058;
}
</style>