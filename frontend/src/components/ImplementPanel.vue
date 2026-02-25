<template>
  <div>
    <!-- 预检面板 -->
    <n-card v-if="preflightResult && !preflightResult.ready" style="background: #16213e; margin-bottom: 16px">
      <template #header>
        <n-space align="center" :size="8">
          <span>⚠️ 预检未通过</span>
          <n-button size="tiny" @click="runPreflight" :loading="preflighting" quaternary>重新检查</n-button>
        </n-space>
      </template>
      <n-space vertical :size="6">
        <div v-for="c in preflightResult.checks" :key="c.name" style="display: flex; align-items: center; gap: 8px; font-size: 13px">
          <span>{{ c.passed ? '✅' : '❌' }}</span>
          <b>{{ c.name }}</b>
          <n-text depth="3" style="font-size: 12px">{{ c.detail }}</n-text>
        </div>
        <n-alert v-if="preflightResult.errors?.length" type="warning" :bordered="false" style="margin-top: 8px">
          <div v-for="(e, i) in preflightResult.errors" :key="i" style="font-size: 12px">{{ e }}</div>
        </n-alert>
      </n-space>
    </n-card>

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
              通常为 <code>main</code> 或 <code>master</code>。
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
          <!-- 会话追踪链接 -->
          <n-button
            v-if="implStatus?.github_issue_number"
            text
            tag="a"
            href="https://github.com/copilot/agents"
            target="_blank"
            size="small"
            type="info"
          >
            📡 查看 Agent 会话
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
          <n-space align="center" :size="6">
            <n-tag :type="implStatusType" size="small">{{ implStatusText }}</n-tag>
            <n-tag v-if="implStatus.copilot_assigned" type="success" size="small" :bordered="false">
              🤖 Agent 已分配
            </n-tag>
            <n-tag v-else-if="implStatus.github_issue_number && implStatus.status !== 'not_started'" type="warning" size="small" :bordered="false">
              ⚠️ Agent 未分配
            </n-tag>
          </n-space>
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

      <!-- Agent 未分配警告 + 手动重试 -->
      <n-alert
        v-if="implStatus.github_issue_number && !implStatus.copilot_assigned && implStatus.status !== 'not_started' && implStatus.status !== 'agent_working' && implStatus.status !== 'agent_done'"
        type="warning"
        style="margin-top: 12px"
        :bordered="false"
      >
        <template #header>Copilot Agent 未成功分配</template>
        <div style="font-size: 12px">
          Issue 已创建但 Copilot 未被分配。可能原因:
          <ul style="margin: 4px 0; padding-left: 20px">
            <li>Copilot Coding Agent 未在仓库中启用</li>
            <li>Token 权限不足 (需要 Issues + Pull Requests 的 Read &amp; Write)</li>
            <li>仓库 Ruleset 限制了 Bot 操作</li>
          </ul>
          <n-space :size="8" style="margin-top: 8px">
            <n-button
              size="small"
              type="primary"
              tag="a"
              :href="implStatus.issue_url || `https://github.com/${repoName}/issues/${implStatus.github_issue_number}`"
              target="_blank"
            >
              在 GitHub 上手动分配
            </n-button>
            <n-button size="small" @click="refreshStatus">
              🔄 重新检查
            </n-button>
          </n-space>
        </div>
      </n-alert>
    </n-card>

    <!-- 编码过程查看器 (session 信息) -->
    <n-card
      v-if="sessionInfo && sessionInfo.has_session"
      style="background: #16213e; margin-bottom: 16px"
    >
      <template #header>
        <n-space align="center" :size="8">
          <span>📡 编码过程</span>
          <n-button size="tiny" @click="loadSession" :loading="loadingSession" quaternary>刷新</n-button>
        </n-space>
      </template>
      <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" bordered size="small">
        <n-descriptions-item label="Copilot 状态">
          <n-tag :type="sessionStatusType" size="small">{{ sessionStatusText }}</n-tag>
        </n-descriptions-item>
        <n-descriptions-item label="Agent 会话">
          <n-button text tag="a" :href="sessionInfo.session_url" target="_blank" type="info" size="small">
            在 GitHub 上查看会话日志 →
          </n-button>
        </n-descriptions-item>
        <n-descriptions-item label="Issue" v-if="sessionInfo.issue_url">
          <n-button text tag="a" :href="sessionInfo.issue_url" target="_blank" size="small">
            #{{ sessionInfo.issue_number }} →
          </n-button>
        </n-descriptions-item>
        <n-descriptions-item label="PR" v-if="sessionInfo.pr_url">
          <n-button text tag="a" :href="sessionInfo.pr_url" target="_blank" size="small">
            #{{ sessionInfo.pr_number }} →
          </n-button>
        </n-descriptions-item>
        <n-descriptions-item label="分支" v-if="sessionInfo.branch">
          <n-tag size="small" :bordered="false">{{ sessionInfo.branch }}</n-tag>
        </n-descriptions-item>
      </n-descriptions>
      <n-alert type="info" :bordered="false" style="margin-top: 12px; font-size: 12px">
        💡 可以在
        <n-button text tag="a" href="https://github.com/copilot/agents" target="_blank" size="small" type="info">
          GitHub Agents 页面
        </n-button>
        查看 Copilot 的内部思考过程、使用的工具和实时日志。
        PR 创建后也可以在 PR 页面查看 Session Log。
      </n-alert>
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
            <n-button
              v-if="implStatus?.pr_url"
              text
              tag="a"
              :href="implStatus.pr_url"
              target="_blank"
              type="info"
            >
              在 GitHub 上查看 PR →
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

// ── 预检 ─────────────────────────────────────────────────────
const preflightResult = ref<any>(null)
const preflighting = ref(false)

async function runPreflight() {
  preflighting.value = true
  try {
    const { data } = await implementationApi.preflight(props.project.id)
    preflightResult.value = data
    if (data.default_branch) {
      baseBranch.value = data.default_branch
    }
  } catch (e: any) {
    // 预检失败不阻断, 仅记录
    console.warn('预检失败:', e)
  } finally {
    preflighting.value = false
  }
}

// ── 会话监控 ──────────────────────────────────────────────────
const sessionInfo = ref<any>(null)
const loadingSession = ref(false)

async function loadSession() {
  loadingSession.value = true
  try {
    const { data } = await implementationApi.getSession(props.project.id)
    sessionInfo.value = data
  } catch {
    // ignore
  } finally {
    loadingSession.value = false
  }
}

const sessionStatusType = computed(() => {
  const m: Record<string, any> = {
    unknown: 'default', assigned: 'info', working: 'warning',
    completed: 'success', merged: 'success', failed: 'error',
  }
  return m[sessionInfo.value?.copilot_status] || 'default'
})

const sessionStatusText = computed(() => {
  const m: Record<string, string> = {
    unknown: '未知', assigned: '已分配 Agent', working: 'Agent 编码中',
    completed: '编码完成', merged: 'PR 已合并', failed: '失败',
  }
  return m[sessionInfo.value?.copilot_status] || sessionInfo.value?.copilot_status || ''
})

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
    // 同时加载会话信息
    if (data.github_issue_number) {
      loadSession()
    }
  } catch {}
  finally { polling.value = false }
}

async function handleStartImplementation() {
  // 先运行预检
  if (!preflightResult.value) {
    await runPreflight()
    if (preflightResult.value && !preflightResult.value.ready) {
      message.warning('预检未通过, 请检查上方的检查项')
      return
    }
  }

  starting.value = true
  try {
    const { data } = await implementationApi.start(props.project.id, {
      custom_instructions: customInstructions.value,
      base_branch: baseBranch.value,
    })
    if (data.warning) {
      message.warning(data.warning, { duration: 8000 })
    } else {
      message.success(data.message)
    }
    emit('status-changed')
    startPolling()
    refreshStatus()
  } catch (e: any) {
    const detail = e.response?.data?.detail || '发起实施失败'
    message.error(detail, { duration: 10000 })
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
  }, 15000) // 15秒轮询
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
  // 未开始时自动运行预检
  if (!s || s === 'not_started') {
    runPreflight()
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', _onResize)
  stopPolling()
})
</script>
