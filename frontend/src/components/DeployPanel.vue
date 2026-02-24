<template>
  <div>
    <!-- 部署操作 -->
    <n-space style="margin-bottom: 16px" :wrap="true">
      <n-button type="primary" @click="handleDeploy(false)" :loading="deploying">
        🚀 Review & Deploy
      </n-button>
      <n-button type="warning" @click="handleDeploy(true)" :loading="deploying">
        ⚡ 直接部署 (跳过 Review)
      </n-button>
    </n-space>

    <!-- 当前部署状态（终端风格日志） -->
    <n-card v-if="currentDeployment" title="🖥️ 部署日志" style="background: #0d1b2a; margin-bottom: 16px">
      <template #header-extra>
        <n-tag :type="deployStatusType(currentDeployment.status)" size="small">
          {{ deployStatusLabel(currentDeployment.status) }}
        </n-tag>
      </template>
      <div
        ref="logRef"
        style="
          background: #0a0a0a;
          color: #00ff00;
          font-family: 'Courier New', monospace;
          font-size: 12px;
          padding: 12px;
          border-radius: 8px;
          max-height: 400px;
          overflow-y: auto;
          white-space: pre-wrap;
          line-height: 1.6;
          word-break: break-all;
        "
      >{{ deployLogs }}</div>
    </n-card>

    <!-- 部署历史 -->
    <n-card title="📜 部署历史" size="small" style="background: #16213e">
      <n-timeline v-if="deployments.length">
        <n-timeline-item
          v-for="d in deployments"
          :key="d.id"
          :type="deployTimelineType(d.status)"
          :title="deployTypeLabel(d.deploy_type)"
          :content="d.error_message || ''"
          :time="formatDate(d.started_at)"
        >
          <template #header>
            <n-space align="center" :size="8">
              <n-text>{{ deployTypeLabel(d.deploy_type) }}</n-text>
              <n-tag :type="deployStatusType(d.status)" size="small">
                {{ deployStatusLabel(d.status) }}
              </n-tag>
            </n-space>
          </template>
        </n-timeline-item>
      </n-timeline>
      <n-empty v-else description="暂无部署记录" />
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { deploymentApi } from '@/api'
import type { Project } from '@/stores/project'

const props = defineProps<{ project: Project }>()
const emit = defineEmits(['deployed'])
const message = useMessage()
const dialog = useDialog()

const deployments = ref<any[]>([])
const currentDeployment = ref<any>(null)
const deployLogs = ref('')
const deploying = ref(false)
const logRef = ref<HTMLElement>()
let ws: WebSocket | null = null

function scrollLogToBottom() {
  nextTick(() => {
    if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight
  })
}

async function handleDeploy(skipReview: boolean) {
  const action = skipReview ? '直接合并并部署' : '确认后部署'
  dialog.warning({
    title: '确认部署',
    content: `即将${action}到生产环境。部署前会自动创建快照，失败会自动回滚。继续？`,
    positiveText: '开始部署',
    negativeText: '取消',
    onPositiveClick: async () => {
      deploying.value = true
      deployLogs.value = '🚀 部署任务已提交...\n'

      try {
        const { data } = await deploymentApi.deploy(props.project.id, { skip_review: skipReview })
        currentDeployment.value = data

        // 连接 WebSocket 获取实时日志
        connectWS(data.id)
      } catch (e: any) {
        message.error(e.response?.data?.detail || '部署失败')
        deploying.value = false
      }
    },
  })
}

function connectWS(deploymentId: number) {
  const url = deploymentApi.wsUrl(props.project.id, deploymentId)
  ws = new WebSocket(url)

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'log') {
        deployLogs.value += data.message + '\n'
        scrollLogToBottom()
      } else if (data.type === 'history') {
        deployLogs.value = data.logs
        scrollLogToBottom()
      } else if (data.type === 'done') {
        deploying.value = false
        if (data.status === 'healthy') {
          message.success('✅ 部署成功!')
          emit('deployed')
        } else {
          message.warning(`部署状态: ${data.status}`)
        }
        fetchDeployments()
      }
    } catch {}
  }

  ws.onerror = () => {
    deploying.value = false
    message.error('WebSocket 连接异常')
  }

  ws.onclose = () => {
    deploying.value = false
  }
}

function deployStatusType(s: string) {
  const m: Record<string, any> = {
    pending:'default', building:'info', deploying:'warning',
    healthy:'success', failed:'error', rolled_back:'warning',
  }
  return m[s] || 'default'
}

function deployStatusLabel(s: string) {
  const m: Record<string, string> = {
    pending:'等待中', building:'构建中', deploying:'部署中',
    healthy:'健康 ✅', failed:'失败 ❌', rolled_back:'已回滚 🔄',
  }
  return m[s] || s
}

function deployTypeLabel(t: string) {
  const m: Record<string, string> = {
    merge_deploy:'Review & Deploy', direct_deploy:'Direct Deploy',
    preview:'分支预览', rollback:'回滚',
  }
  return m[t] || t
}

function deployTimelineType(s: string) {
  const m: Record<string, any> = {
    healthy:'success', failed:'error', rolled_back:'warning',
    deploying:'info', building:'info',
  }
  return m[s] || 'default'
}

function formatDate(d: string) {
  return new Date(d).toLocaleString('zh-CN')
}

async function fetchDeployments() {
  try {
    const { data } = await deploymentApi.list(props.project.id)
    deployments.value = data
  } catch {}
}

onMounted(() => fetchDeployments())
onUnmounted(() => { if (ws) ws.close() })
</script>
