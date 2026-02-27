<template>
  <div class="impl-panel">
    <!-- ═══════════ 预检警告 (仅预检未通过时) ═══════════ -->
    <n-alert
      v-if="preflightResult && !preflightResult.ready"
      type="warning"
      :bordered="false"
      closable
      style="margin-bottom: 16px"
    >
      <template #header>
        <n-space align="center" :size="8">
          预检未通过
          <n-button size="tiny" @click="runPreflight" :loading="preflighting" quaternary>重新检查</n-button>
        </n-space>
      </template>
      <div style="display: flex; flex-wrap: wrap; gap: 12px 24px; margin-top: 4px">
        <div v-for="c in preflightResult.checks" :key="c.name" style="display: flex; align-items: center; gap: 6px; font-size: 13px">
          <span>{{ c.passed ? '✅' : '❌' }}</span>
          <span style="font-weight: 500">{{ c.name }}</span>
          <n-text depth="3" style="font-size: 12px">{{ c.detail }}</n-text>
        </div>
      </div>
    </n-alert>

    <!-- ═══════════ 区域 1: 操作栏 ═══════════ -->
    <n-card style="background: #16213e; margin-bottom: 16px" :content-style="{ padding: '16px' }">
      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <!-- 分支选择 -->
        <n-tooltip trigger="hover" placement="bottom">
          <template #trigger>
            <n-input
              v-model:value="baseBranch"
              size="small"
              style="width: 140px"
              placeholder="基础分支"
              :disabled="isImplementing"
            >
              <template #prefix><span style="font-size: 13px">🌿</span></template>
            </n-input>
          </template>
          Copilot Agent 将基于此分支创建 PR
        </n-tooltip>

        <!-- 发起 / 刷新 -->
        <n-button
          type="primary"
          size="small"
          @click="handleStartImplementation"
          :loading="starting"
          :disabled="!project.plan_content || isImplementing"
        >
          🚀 发起实施
        </n-button>
        <n-button @click="refreshStatus" :loading="polling" size="small" quaternary>
          🔄 刷新
        </n-button>

        <!-- 快捷链接 (已有 Issue 后显示) -->
        <template v-if="implStatus?.github_issue_number">
          <n-divider vertical style="margin: 0 2px" />
          <n-button
            v-if="implStatus.github_issue_number && repoName"
            text size="small" tag="a"
            :href="`https://github.com/${repoName}/issues/${implStatus.github_issue_number}`"
            target="_blank"
          >
            Issue #{{ implStatus.github_issue_number }}
          </n-button>
          <n-button
            v-if="implStatus.github_pr_number"
            text size="small" tag="a" type="info"
            :href="implStatus.pr_url"
            target="_blank"
          >
            PR #{{ implStatus.github_pr_number }}
          </n-button>
          <n-button
            text size="small" tag="a"
            href="https://github.com/copilot/agents"
            target="_blank"
            style="opacity: 0.7"
          >
            📡 会话日志
          </n-button>
        </template>

        <!-- 右侧状态 pill -->
        <div style="margin-left: auto; display: flex; align-items: center; gap: 6px">
          <n-tag v-if="implStatus" :type="implStatusType" size="small" round>
            {{ implStatusText }}
          </n-tag>
          <n-tag
            v-if="implStatus?.copilot_assigned || agentEverWorked"
            type="success" size="small" round :bordered="false"
          >
            🤖 Agent
          </n-tag>
          <n-tag
            v-else-if="implStatus?.github_issue_number && implStatus?.status === 'task_created'"
            type="warning" size="small" round :bordered="false"
          >
            ⚠️ 未分配
          </n-tag>
        </div>
      </div>

      <!-- 附加指令 (仅未开始/刚创建时展开, 其余折叠) -->
      <n-collapse
        v-if="!isImplementing || implStatus?.status === 'task_created'"
        :default-expanded-names="isImplementing ? [] : ['instructions']"
        style="margin-top: 12px"
      >
        <n-collapse-item name="instructions" title="附加指令 (可选)">
          <n-input
            v-model:value="customInstructions"
            type="textarea"
            size="small"
            placeholder="给 Copilot Agent 的额外提示，如架构约束、编码风格等"
            :autosize="{ minRows: 2, maxRows: 5 }"
          />
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <!-- ═══════════ 区域 2: 进度 + 状态 (合并为一张卡) ═══════════ -->
    <n-card
      v-if="implStatus && implStatus.status !== 'not_started'"
      style="background: #16213e; margin-bottom: 16px"
      :content-style="{ padding: '20px' }"
    >
      <!-- 进度条 -->
      <n-steps :current="implStep" size="small" style="margin-bottom: 20px">
        <n-step title="创建任务" :status="stepStatus(1)" />
        <n-step title="Agent 编码" :status="stepStatus(2)" />
        <n-step title="编码完成" :status="stepStatus(3)" />
        <n-step title="进入审查" :status="stepStatus(4)" />
      </n-steps>

      <!-- 信息网格: 替代冗余的 descriptions -->
      <div class="info-grid">
        <!-- PR 信息 -->
        <div v-if="implStatus.github_pr_number" class="info-item">
          <span class="info-label">PR</span>
          <n-button text tag="a" :href="implStatus.pr_url" target="_blank" size="small">
            #{{ implStatus.github_pr_number }} — {{ implStatus.pr_title }}
          </n-button>
        </div>

        <!-- 分支 -->
        <div v-if="implStatus.branch_name" class="info-item">
          <span class="info-label">分支</span>
          <n-tag size="small" :bordered="false" style="font-family: monospace; font-size: 12px">
            {{ implStatus.branch_name }}
          </n-tag>
        </div>

        <!-- Workflow -->
        <div v-if="implStatus.workflow_status" class="info-item">
          <span class="info-label">Workflow</span>
          <n-space align="center" :size="6">
            <n-tag :type="workflowTagType" size="small">{{ workflowStatusText }}</n-tag>
            <n-button v-if="implStatus.workflow_url" text tag="a" :href="implStatus.workflow_url" target="_blank" size="small" style="opacity: 0.7">
              查看 →
            </n-button>
          </n-space>
        </div>

        <!-- 变更文件 -->
        <div v-if="implStatus.pr_files_changed" class="info-item">
          <span class="info-label">变更</span>
          <span style="font-size: 13px">{{ implStatus.pr_files_changed }} 个文件</span>
        </div>
      </div>

      <!-- Agent 未分配警告 (紧凑内联) -->
      <n-alert
        v-if="showAgentWarning"
        type="warning"
        :bordered="false"
        style="margin-top: 16px"
      >
        <template #header>Copilot Agent 未成功分配</template>
        <div style="font-size: 12px">
          可能原因: Copilot 未启用 · Token 权限不足 · Ruleset 阻止
          <n-space :size="8" style="margin-top: 8px">
            <n-button
              size="tiny" type="primary" tag="a"
              :href="implStatus.issue_url || `https://github.com/${repoName}/issues/${implStatus.github_issue_number}`"
              target="_blank"
            >
              手动分配 →
            </n-button>
            <n-button size="tiny" @click="refreshStatus" quaternary>重新检查</n-button>
          </n-space>
        </div>
      </n-alert>

      <!-- Session 提示 (简化版, 仅在编码中时显示) -->
      <div
        v-if="implStatus.status === 'agent_working'"
        style="margin-top: 16px; padding: 12px; background: rgba(99,226,184,0.06); border-radius: 8px; font-size: 12px; color: rgba(255,255,255,0.65)"
      >
        💡 Copilot Agent 正在编码中。可以在
        <n-button text tag="a" href="https://github.com/copilot/agents" target="_blank" size="small" type="info">
          GitHub Agents 页面
        </n-button>
        查看实时思考过程和日志。PR 创建后也可在 PR 页面查看 Session Log。
      </div>
    </n-card>

    <!-- ═══════════ 区域 3: 编码完成 → 操作 ═══════════ -->
    <n-card
      v-if="isAgentDone"
      style="background: linear-gradient(135deg, #16213e 0%, #1a3a2a 100%); margin-bottom: 16px; border: 1px solid rgba(99,226,184,0.2)"
      :content-style="{ padding: '24px' }"
    >
      <div style="text-align: center; margin-bottom: 16px">
        <div style="font-size: 40px; margin-bottom: 8px">✅</div>
        <div style="font-size: 18px; font-weight: 600; color: #63e2b8">Copilot Agent 编码完成</div>
        <n-text depth="3" style="font-size: 13px">{{ agentDoneDesc }}</n-text>
      </div>
      <n-space justify="center" :size="12">
        <n-button type="primary" @click="goToReview" size="small">
          🔍 进入审查
        </n-button>
        <n-button v-if="implStatus?.github_pr_number" @click="loadDiff" :loading="loadingDiff" size="small" quaternary>
          📝 查看 Diff
        </n-button>
        <n-button
          v-if="implStatus?.pr_url"
          text tag="a" size="small" type="info"
          :href="implStatus.pr_url"
          target="_blank"
        >
          在 GitHub 上查看 PR →
        </n-button>
      </n-space>
    </n-card>

    <!-- PR 已合并 -->
    <n-card
      v-if="implStatus?.status === 'pr_merged'"
      style="background: linear-gradient(135deg, #16213e 0%, #1a3a2a 100%); margin-bottom: 16px; border: 1px solid rgba(99,226,184,0.2)"
      :content-style="{ padding: '20px', textAlign: 'center' }"
    >
      <div style="font-size: 36px; margin-bottom: 6px">🎉</div>
      <div style="font-size: 16px; font-weight: 600; color: #63e2b8">PR 已合并</div>
    </n-card>

    <!-- ═══════════ PR Diff 查看 (可折叠) ═══════════ -->
    <n-card v-if="diffData" style="background: #16213e; margin-bottom: 16px" :content-style="{ padding: '12px 16px' }">
      <template #header>
        <span style="font-size: 14px">📝 PR Diff</span>
      </template>
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

// Agent 曾经工作的证据 (copilot/* 分支存在, 或状态已进入编码/完成阶段)
const agentEverWorked = computed(() => {
  const s = implStatus.value?.status
  const branch = implStatus.value?.branch_name || ''
  return (
    branch.startsWith('copilot/') ||
    ['agent_working', 'agent_done', 'pr_created', 'pr_merged'].includes(s)
  )
})

const showAgentWarning = computed(() => {
  if (!implStatus.value?.github_issue_number) return false
  if (implStatus.value.copilot_assigned || agentEverWorked.value) return false
  return implStatus.value.status === 'task_created'
})

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

<style scoped>
.impl-panel :deep(.n-card) {
  border-radius: 10px;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px 20px;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}

.info-label {
  flex-shrink: 0;
  width: 60px;
  font-size: 12px;
  color: rgba(255,255,255,0.4);
}

@media (max-width: 768px) {
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
