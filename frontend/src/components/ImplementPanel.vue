<template>
  <div>
    <!-- 实施控制 -->
    <n-card style="background: #16213e; margin-bottom: 16px">
      <n-space vertical :size="12">
        <n-space align="center" :size="12" :wrap="true">
          <n-tooltip trigger="hover" placement="bottom">
            <template #trigger>
              <n-input
                v-model:value="baseBranch"
                size="small"
                style="width: 160px; min-width: 100px"
                placeholder="基础分支"
              >
                <template #prefix>🌿</template>
              </n-input>
            </template>
            <div style="max-width: 280px; font-size: 12px">
              <b>基础分支</b>: Copilot Agent 将基于此分支创建 PR。<br>
              通常为 <code>main</code> 或 <code>master</code>。<br>
              如需基于其他 feature 分支开发，可在此修改。
            </div>
          </n-tooltip>
          <n-button
            type="primary"
            @click="handleStartImplementation"
            :loading="starting"
            :disabled="!project.plan_content || isImplementing"
          >
            🚀 发起实施
          </n-button>
          <n-button @click="refreshStatus" :loading="polling" size="small">
            🔄 刷新状态
          </n-button>
        </n-space>
        <n-input
          v-model:value="customInstructions"
          type="textarea"
          size="small"
          placeholder="附加指令 (可选) — 给 Copilot Agent 的额外提示"
          :autosize="{ minRows: 2, maxRows: 5 }"
        />
      </n-space>
    </n-card>

    <!-- 进度面板 -->
    <n-card style="background: #16213e; margin-bottom: 16px">
      <n-steps :current="implStep" size="small">
        <n-step title="创建任务" :status="stepStatus(1)" description="创建 Issue 并分配 Agent" />
        <n-step title="Agent 编码" :status="stepStatus(2)" :description="workflowDesc" />
        <n-step title="编码完成" :status="stepStatus(3)" description="Workflow 执行结束" />
        <n-step title="进入审查" :status="stepStatus(4)" description="AI 审查实现质量" />
      </n-steps>
    </n-card>

    <!-- 状态详情 -->
    <n-card v-if="implStatus" style="background: #16213e; margin-bottom: 16px">
      <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" bordered size="small">
        <n-descriptions-item label="状态">
          <n-tag :type="implStatusType" size="small">{{ implStatusText }}</n-tag>
        </n-descriptions-item>
        <n-descriptions-item label="Issue" v-if="implStatus.github_issue_number && repoName">
          <n-button text tag="a" :href="`https://github.com/${repoName}/issues/${implStatus.github_issue_number}`" target="_blank">
            #{{ implStatus.github_issue_number }}
          </n-button>
        </n-descriptions-item>
        <n-descriptions-item label="PR" v-if="implStatus.github_pr_number">
          <n-button text tag="a" :href="implStatus.pr_url" target="_blank">
            #{{ implStatus.github_pr_number }} - {{ implStatus.pr_title }}
          </n-button>
        </n-descriptions-item>
        <n-descriptions-item label="分支" v-if="implStatus.branch_name">
          <n-tag size="small" :bordered="false">{{ implStatus.branch_name }}</n-tag>
        </n-descriptions-item>
        <!-- Workflow 状态 -->
        <n-descriptions-item label="Workflow" v-if="implStatus.workflow_status">
          <n-space align="center" :size="6">
            <n-tag :type="workflowTagType" size="small">
              {{ workflowStatusText }}
            </n-tag>
            <n-button
              v-if="implStatus.workflow_url"
              text
              tag="a"
              :href="implStatus.workflow_url"
              target="_blank"
              size="small"
            >
              查看 →
            </n-button>
          </n-space>
        </n-descriptions-item>
        <n-descriptions-item label="变更文件" v-if="implStatus.pr_files_changed">
          {{ implStatus.pr_files_changed }} 个文件
        </n-descriptions-item>
      </n-descriptions>
    </n-card>

    <!-- Agent 完成提示 -->
    <n-card v-if="isAgentDone" style="background: #16213e; margin-bottom: 16px">
      <n-result status="success" title="Copilot Agent 编码完成" :description="agentDoneDesc">
        <template #footer>
          <n-space>
            <n-button type="primary" @click="goToReview">
              🔍 进入审查
            </n-button>
            <n-button v-if="implStatus?.github_pr_number" @click="loadDiff" :loading="loadingDiff" quaternary>
              📝 查看 Diff
            </n-button>
          </n-space>
        </template>
      </n-result>
    </n-card>

    <!-- PR Diff 查看 (可折叠) -->
    <n-card v-if="diffData" title="📝 PR Diff" style="background: #16213e; margin-bottom: 16px">
      <n-collapse>
        <n-collapse-item
          v-for="f in diffData.files"
          :key="f.filename"
          :title="`${f.status === 'added' ? '🟢' : f.status === 'removed' ? '🔴' : '🟡'} ${f.filename}`"
          :name="f.filename"
        >
          <template #header-extra>
            <n-text depth="3" style="font-size: 12px">
              +{{ f.additions }} -{{ f.deletions }}
            </n-text>
          </template>
          <pre style="background: #0d1b2a; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 12px; white-space: pre-wrap">{{ f.patch }}</pre>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <!-- PR 已合并 (遗留兼容) -->
    <n-space v-if="implStatus?.status === 'pr_merged'" style="margin-top: 16px">
      <n-tag type="success" size="large">✅ PR 已合并</n-tag>
    </n-space>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import { implementationApi, studioAuthApi } from '@/api'
import type { Project } from '@/stores/project'

const props = defineProps<{ project: Project }>()
const emit = defineEmits(['status-changed', 'go-review'])
const message = useMessage()

const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
const isMobile = computed(() => windowWidth.value < 768)
function _onResize() { windowWidth.value = window.innerWidth }

const implStatus = ref<any>(null)
const baseBranch = ref('main')
const customInstructions = ref('')
const starting = ref(false)
const polling = ref(false)
const loadingDiff = ref(false)
const diffData = ref<any>(null)
let pollTimer: any = null

const repoName = ref('')

// ── 状态计算 ──────────────────────────────────────────────────

const isImplementing = computed(() =>
  ['implementing', 'reviewing'].includes(props.project.status) && !!implStatus.value?.github_issue_number
)

const isAgentDone = computed(() =>
  implStatus.value?.status === 'agent_done'
)

const agentDoneDesc = computed(() => {
  const conclusion = implStatus.value?.workflow_conclusion
  if (conclusion === 'success') return 'Workflow 执行成功，PR 已就绪。可进入审查阶段。'
  if (conclusion === 'failure') return 'Workflow 执行失败，请检查 Actions 日志后决定是否继续审查。'
  return 'Copilot Agent 编码已完成，可进入审查阶段。'
})

const implStep = computed(() => {
  if (!implStatus.value) return 0
  const s = implStatus.value.status
  if (s === 'pr_merged') return 5
  if (s === 'agent_done') return 3
  if (s === 'pr_created') return 3
  if (s === 'agent_working') return 2
  if (s === 'task_created') return 1
  return 0
})

const implStatusType = computed(() => {
  const m: Record<string, any> = {
    not_started: 'default', task_created: 'info', agent_working: 'warning',
    agent_done: 'success', pr_created: 'success', pr_merged: 'success',
  }
  return m[implStatus.value?.status] || 'default'
})

const implStatusText = computed(() => {
  const m: Record<string, string> = {
    not_started: '未开始', task_created: '任务已创建', agent_working: 'Agent 编码中...',
    agent_done: 'Agent 编码完成', pr_created: 'PR 已创建', pr_merged: 'PR 已合并',
  }
  return m[implStatus.value?.status] || ''
})

// ── Workflow 相关 ─────────────────────────────────────────────

const workflowDesc = computed(() => {
  const ws = implStatus.value?.workflow_status
  if (ws === 'in_progress') return 'Copilot Agent 正在编码...'
  if (ws === 'queued') return '排队等待执行...'
  if (ws === 'completed') return '执行完成'
  return 'Copilot Coding Agent 处理中'
})

const workflowTagType = computed(() => {
  const ws = implStatus.value?.workflow_status
  const wc = implStatus.value?.workflow_conclusion
  if (ws === 'completed' && wc === 'success') return 'success'
  if (ws === 'completed' && wc === 'failure') return 'error'
  if (ws === 'completed') return 'warning'
  if (ws === 'in_progress') return 'warning'
  return 'default'
})

const workflowStatusText = computed(() => {
  const ws = implStatus.value?.workflow_status
  const wc = implStatus.value?.workflow_conclusion
  if (ws === 'completed') {
    const cm: Record<string, string> = { success: '✅ 成功', failure: '❌ 失败', cancelled: '⚪ 取消' }
    return cm[wc] || `完成 (${wc})`
  }
  const sm: Record<string, string> = { in_progress: '🔄 运行中', queued: '⏳ 排队中' }
  return sm[ws] || ws
})

function stepStatus(step: number) {
  if (implStep.value > step) return 'finish'
  if (implStep.value === step) return 'process'
  return 'wait'
}

// ── 操作 ──────────────────────────────────────────────────────

async function refreshStatus() {
  polling.value = true
  try {
    const { data } = await implementationApi.getStatus(props.project.id)
    const prevStatus = implStatus.value?.status
    implStatus.value = data
    // Agent 完成时通知父组件刷新项目状态
    if (data.status === 'agent_done' && prevStatus !== 'agent_done') {
      emit('status-changed')
    }
  } catch {}
  finally { polling.value = false }
}

async function handleStartImplementation() {
  starting.value = true
  try {
    const { data } = await implementationApi.start(props.project.id, {
      custom_instructions: customInstructions.value,
      base_branch: baseBranch.value,
    })
    message.success(data.message)
    emit('status-changed')
    startPolling()
    refreshStatus()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '发起实施失败')
  } finally {
    starting.value = false
  }
}

async function loadDiff() {
  loadingDiff.value = true
  try {
    const { data } = await implementationApi.getDiff(props.project.id)
    diffData.value = data
  } catch (e: any) {
    message.error(e.response?.data?.detail || '加载 Diff 失败')
  } finally {
    loadingDiff.value = false
  }
}

function goToReview() {
  emit('go-review')
}

// ── 轮询 ──────────────────────────────────────────────────────

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => {
    const s = implStatus.value?.status
    if (s === 'agent_working' || s === 'task_created') {
      refreshStatus()
    } else {
      stopPolling()
    }
  }, 15000) // 15秒轮询, 更快响应 workflow 变化
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

onMounted(async () => {
  window.addEventListener('resize', _onResize)
  // 从后端获取工作区配置 (GitHub repo 等)
  try {
    const { data } = await studioAuthApi.workspaceConfig()
    repoName.value = data.github_repo || ''
  } catch { /* ignore */ }

  await refreshStatus()
  const s = implStatus.value?.status
  if (s === 'agent_working' || s === 'task_created') {
    startPolling()
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', _onResize)
  stopPolling()
})
</script>
