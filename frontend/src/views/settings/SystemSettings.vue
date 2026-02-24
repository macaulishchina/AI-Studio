<template>
  <n-space vertical :size="16">
    <!-- GitHub 连接 -->
    <n-card title="🔗 GitHub 连接" size="small" style="background: #16213e">
      <n-spin :show="checkingGithub">
        <n-descriptions :column="1" label-placement="left" bordered>
          <n-descriptions-item label="状态">
            <n-tag :type="githubStatus.connected ? 'success' : 'error'" size="small">
              {{ githubStatus.connected ? '已连接' : '未连接' }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="仓库" v-if="githubStatus.repo">
            {{ githubStatus.repo }}
          </n-descriptions-item>
          <n-descriptions-item label="默认分支" v-if="githubStatus.default_branch">
            {{ githubStatus.default_branch }}
          </n-descriptions-item>
          <n-descriptions-item label="错误" v-if="githubStatus.error">
            <n-text type="error">{{ githubStatus.error }}</n-text>
          </n-descriptions-item>
        </n-descriptions>
      </n-spin>
      <n-button style="margin-top: 8px" @click="checkGithub" :loading="checkingGithub" size="small">
        🔄 重新检测
      </n-button>
    </n-card>

    <!-- 系统状态 -->
    <n-card title="🖥️ 系统状态" size="small" style="background: #16213e">
      <n-spin :show="loadingStatus">
        <n-descriptions :column="1" label-placement="left" bordered v-if="systemStatus">
          <n-descriptions-item label="Git 分支">
            {{ systemStatus.git?.branch || '-' }}
          </n-descriptions-item>
          <n-descriptions-item label="最近提交">
            <n-space vertical :size="2">
              <n-text v-for="c in (systemStatus.git?.recent_commits || [])" :key="c" code style="font-size: 12px">
                {{ c }}
              </n-text>
            </n-space>
          </n-descriptions-item>
        </n-descriptions>
      </n-spin>
      <n-button style="margin-top: 8px" @click="fetchStatus" :loading="loadingStatus" size="small">
        🔄 刷新
      </n-button>
    </n-card>

    <!-- 容器状态 -->
    <n-card title="🐳 Docker 容器" size="small" style="background: #16213e" v-if="systemStatus?.containers">
      <n-table :bordered="false" size="small">
        <thead><tr><th>容器名</th><th>状态</th><th>端口</th></tr></thead>
        <tbody>
          <tr v-for="c in systemStatus.containers" :key="c.name">
            <td>{{ c.name }}</td>
            <td><n-tag :type="c.status?.includes('Up') ? 'success' : 'error'" size="small">{{ c.status }}</n-tag></td>
            <td style="font-size: 12px">{{ c.ports || '-' }}</td>
          </tr>
        </tbody>
      </n-table>
    </n-card>

    <!-- 外部 API 端点检测 -->
    <n-card title="🔌 外部 API 端点检测" size="small" style="background: #16213e">
      <template #header-extra>
        <n-space :size="8">
          <n-text v-if="probeResult" depth="3" style="font-size: 11px">
            {{ probeResult.ok }}✅ {{ probeResult.warning }}⚠️ {{ probeResult.error }}❌ {{ probeResult.skipped }}⏭
            · {{ probeResult.total_ms }}ms
          </n-text>
          <n-button type="primary" size="small" @click="probeAll" :loading="probingAll">
            🚀 一键全测
          </n-button>
        </n-space>
      </template>

      <n-table :bordered="false" size="small" style="margin-top: 4px">
        <thead>
          <tr>
            <th class="sys-col-group">分组</th>
            <th>端点</th>
            <th class="sys-col-auth">认证</th>
            <th class="sys-col-status">状态</th>
            <th class="sys-col-latency">延迟</th>
            <th class="sys-col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ep in probeEndpoints" :key="ep.id">
            <td class="sys-col-group" style="font-size: 12px; color: #aaa">{{ ep.group }}</td>
            <td>
              <div>
                <n-text style="font-size: 12px; font-family: monospace">{{ ep.name }}</n-text>
              </div>
              <n-text depth="3" style="font-size: 11px">{{ ep.description }}</n-text>
              <!-- 测试后显示消息 -->
              <div v-if="ep._result && ep._result.status !== 'ok'" style="margin-top: 2px">
                <n-text :type="ep._result.status === 'error' ? 'error' : 'warning'" style="font-size: 11px">
                  {{ ep._result.message }}
                </n-text>
              </div>
            </td>
            <td>
              <n-tag size="small" :type="ep.auth_type === 'none' ? 'default' : 'info'" :bordered="false" style="font-size: 10px">
                {{ { none: '无', github_pat: 'PAT', copilot_oauth: 'OAuth', copilot_session: 'Session' }[ep.auth_type] || ep.auth_type }}
              </n-tag>
            </td>
            <td>
              <n-tag v-if="ep._result" size="small" :bordered="false" :type="probeStatusType(ep._result.status)">
                {{ probeStatusLabel(ep._result.status) }}
              </n-tag>
              <n-spin v-else-if="ep._loading" :size="14" />
              <n-text v-else depth="3" style="font-size: 11px">—</n-text>
            </td>
            <td>
              <n-text v-if="ep._result" style="font-size: 12px; font-variant-numeric: tabular-nums">
                {{ ep._result.latency_ms ? ep._result.latency_ms + 'ms' : '—' }}
              </n-text>
            </td>
            <td>
              <n-button size="tiny" quaternary @click="probeOne(ep)" :loading="ep._loading">
                ▶
              </n-button>
            </td>
          </tr>
        </tbody>
      </n-table>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { systemApi, endpointProbeApi } from '@/api'

const message = useMessage()

const githubStatus = ref<any>({})
const systemStatus = ref<any>(null)
const checkingGithub = ref(false)
const loadingStatus = ref(false)

// 端点探测
const probeEndpoints = ref<any[]>([])
const probingAll = ref(false)
const probeResult = ref<any>(null)

function probeStatusType(status: string) {
  return { ok: 'success', warning: 'warning', error: 'error', skipped: 'default' }[status] || 'default'
}
function probeStatusLabel(status: string) {
  return { ok: '正常', warning: '警告', error: '异常', skipped: '跳过' }[status] || status
}

async function fetchProbeEndpoints() {
  try {
    const { data } = await endpointProbeApi.listEndpoints()
    probeEndpoints.value = data.map((ep: any) => ({ ...ep, _result: null, _loading: false }))
  } catch {}
}

async function probeAll() {
  probingAll.value = true
  probeEndpoints.value.forEach((ep: any) => { ep._loading = true; ep._result = null })
  try {
    const { data } = await endpointProbeApi.testAll()
    probeResult.value = data
    for (const r of data.results) {
      const ep = probeEndpoints.value.find((e: any) => e.id === r.id)
      if (ep) { ep._result = r; ep._loading = false }
    }
  } catch (e: any) {
    message.error('探测失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    probingAll.value = false
    probeEndpoints.value.forEach((ep: any) => { ep._loading = false })
  }
}

async function probeOne(ep: any) {
  ep._loading = true
  ep._result = null
  try {
    const { data } = await endpointProbeApi.testOne(ep.id)
    ep._result = data
  } catch (e: any) {
    ep._result = { status: 'error', message: e.response?.data?.detail || e.message, latency_ms: 0 }
  } finally {
    ep._loading = false
  }
}

async function checkGithub() {
  checkingGithub.value = true
  try {
    const { data } = await systemApi.status()
    githubStatus.value = data.github || {}
  } catch {
    githubStatus.value = { connected: false, error: '无法连接设计院服务' }
  } finally {
    checkingGithub.value = false
  }
}

async function fetchStatus() {
  loadingStatus.value = true
  try {
    const { data } = await systemApi.status()
    systemStatus.value = data
    githubStatus.value = data.github || {}
  } catch {}
  finally { loadingStatus.value = false }
}

onMounted(() => {
  fetchStatus()
  fetchProbeEndpoints()
})
</script>

<style scoped>
.sys-col-group { width: 160px; }
.sys-col-auth { width: 70px; }
.sys-col-status { width: 90px; }
.sys-col-latency { width: 70px; }
.sys-col-action { width: 56px; }

@media (max-width: 768px) {
  .sys-col-group { display: none; }
  .sys-col-latency { width: 50px; }
  .sys-col-auth { width: 50px; }
  .sys-col-status { width: 60px; }
  .sys-col-action { width: 40px; }
}
</style>
