<template>
  <n-space vertical :size="16">
    <!-- Copilot OAuth 认证 -->
    <n-card title="🤖 Copilot API 授权" size="small" style="background: #16213e">
      <n-alert v-if="!copilotStatus.authenticated" type="info" style="margin-bottom: 12px">
        授权后可使用 Claude Sonnet 4、Gemini 2.5 Pro、Grok 3 等 Copilot 专属高级模型。
        需要 GitHub Copilot Pro/Pro+ 订阅。
      </n-alert>

      <n-descriptions :column="1" label-placement="left" bordered>
        <n-descriptions-item label="状态">
          <n-tag :type="copilotStatus.authenticated ? 'success' : 'warning'" size="small">
            {{ copilotStatus.authenticated ? '已授权 ✅' : '未授权' }}
          </n-tag>
        </n-descriptions-item>
        <n-descriptions-item label="Session" v-if="copilotStatus.authenticated">
          <n-tag :type="copilotStatus.has_valid_session ? 'success' : 'info'" size="small">
            {{ copilotStatus.has_valid_session ? '有效' : '需要刷新' }}
          </n-tag>
        </n-descriptions-item>
        <n-descriptions-item label="订阅" v-if="copilotUsage">
          <n-tag size="small" type="info">{{ copilotUsage.copilot_plan || copilotUsage.sku }}</n-tag>
        </n-descriptions-item>
      </n-descriptions>

      <!-- 高级请求使用情况 -->
      <template v-if="copilotUsage && copilotUsage.premium_requests">
        <div style="margin-top: 12px">
          <n-space align="center" justify="space-between" style="margin-bottom: 6px">
            <n-text strong style="font-size: 13px">⚡ 高级请求配额</n-text>
            <n-text depth="3" style="font-size: 12px">
              重置日期: {{ copilotUsage.quota_reset_date || '-' }}
            </n-text>
          </n-space>
          <template v-if="copilotUsage.premium_requests.unlimited">
            <n-tag type="success" size="small">无限制</n-tag>
          </template>
          <template v-else>
            <n-space vertical :size="4">
              <n-space align="center" :size="8">
                <n-text style="font-size: 20px; font-weight: bold; font-variant-numeric: tabular-nums">
                  {{ copilotUsage.premium_requests.remaining }}
                </n-text>
                <n-text depth="3" style="font-size: 13px">
                  / {{ copilotUsage.premium_requests.entitlement }} 剩余
                </n-text>
                <n-tag v-if="copilotUsage.premium_requests.overage_count > 0" type="warning" size="small">
                  超额 {{ copilotUsage.premium_requests.overage_count }}
                </n-tag>
              </n-space>
              <n-progress
                type="line"
                :percentage="copilotUsage.premium_requests.percent_remaining"
                :color="copilotUsage.premium_requests.percent_remaining > 30 ? '#18a058' : copilotUsage.premium_requests.percent_remaining > 10 ? '#f0a020' : '#d03050'"
                :rail-color="'rgba(255,255,255,0.08)'"
                :height="8"
                :border-radius="4"
                :show-indicator="false"
              />
              <n-text depth="3" style="font-size: 11px">
                已使用 {{ copilotUsage.premium_requests.used }} 次
                ({{ (100 - (copilotUsage.premium_requests.percent_remaining || 0)).toFixed(1) }}%)
                <template v-if="copilotUsage.premium_requests.overage_permitted">
                  · 允许超额使用
                </template>
              </n-text>
            </n-space>
          </template>
        </div>
      </template>

      <n-space style="margin-top: 12px">
        <template v-if="!copilotStatus.authenticated">
          <template v-if="deviceFlow.active">
            <!-- 设备流进行中 -->
            <n-card size="small" style="background: #1a2744; border: 1px solid #4098fc">
              <n-space vertical align="center" :size="8">
                <n-text>请访问以下网址并输入授权码:</n-text>
                <n-button tag="a" :href="deviceFlow.verification_uri" target="_blank" type="info" size="small">
                  {{ deviceFlow.verification_uri }}
                </n-button>
                <n-space align="center">
                  <n-text strong style="font-size: 24px; letter-spacing: 4px; font-family: monospace">
                    {{ deviceFlow.user_code }}
                  </n-text>
                  <n-button size="tiny" @click="copyCode">📋</n-button>
                </n-space>
                <n-text depth="3" style="font-size: 12px">
                  {{ deviceFlow.polling ? '等待授权中...' : '' }}
                  {{ deviceFlow.message || '' }}
                </n-text>
                <n-progress type="line" :percentage="deviceFlow.progress" :show-indicator="false" />
              </n-space>
            </n-card>
          </template>
          <template v-else>
            <n-button type="primary" @click="startAuth" :loading="authLoading">
              🔐 绑定 Copilot
            </n-button>
          </template>
        </template>
        <template v-else>
          <n-button @click="testCopilot" :loading="testingCopilot" size="small">
            🧪 测试连接
          </n-button>
          <n-button @click="fetchCopilotUsage" :loading="loadingUsage" size="small">
            📊 刷新配额
          </n-button>
          <n-button type="error" @click="logoutCopilot" size="small" ghost>
            🔓 注销
          </n-button>
        </template>
      </n-space>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import { copilotAuthApi, modelApi } from '@/api'

const message = useMessage()

// Copilot OAuth
const copilotStatus = ref<any>({ authenticated: false })
const copilotUsage = ref<any>(null)
const loadingUsage = ref(false)
const authLoading = ref(false)
const testingCopilot = ref(false)
const deviceFlow = ref<any>({
  active: false,
  user_code: '',
  verification_uri: '',
  polling: false,
  message: '',
  progress: 0,
})
let pollTimer: any = null
let progressTimer: any = null

async function fetchCopilotStatus() {
  try {
    const { data } = await copilotAuthApi.status()
    copilotStatus.value = data
    if (data.authenticated) {
      fetchCopilotUsage()
    }
  } catch {}
}

async function fetchCopilotUsage() {
  loadingUsage.value = true
  try {
    const { data } = await copilotAuthApi.usage()
    copilotUsage.value = data
  } catch {
    // 静默失败
  } finally {
    loadingUsage.value = false
  }
}

async function startAuth() {
  authLoading.value = true
  try {
    const { data } = await copilotAuthApi.startDeviceFlow()
    deviceFlow.value = {
      active: true,
      user_code: data.user_code,
      verification_uri: data.verification_uri,
      polling: true,
      message: '请在浏览器中完成授权...',
      progress: 0,
      expires_in: data.expires_in || 900,
    }
    startPolling()
    const totalMs = (data.expires_in || 900) * 1000
    const startTime = Date.now()
    progressTimer = setInterval(() => {
      const elapsed = Date.now() - startTime
      deviceFlow.value.progress = Math.min(100, (elapsed / totalMs) * 100)
    }, 1000)
  } catch (e: any) {
    message.error('启动授权失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    authLoading.value = false
  }
}

function startPolling() {
  pollTimer = setInterval(async () => {
    try {
      const { data } = await copilotAuthApi.pollDeviceFlow()
      if (data.status === 'success') {
        stopPolling()
        deviceFlow.value = { active: false }
        await fetchCopilotStatus()
        await modelApi.refresh()
        message.success('🎉 Copilot 授权成功! Claude、Gemini 等模型已解锁')
      } else if (data.status === 'expired') {
        stopPolling()
        deviceFlow.value = { active: false }
        message.warning('授权码已过期，请重新开始')
      } else {
        deviceFlow.value.message = data.message || '等待授权中...'
      }
    } catch {}
  }, 6000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null }
}

function copyCode() {
  navigator.clipboard.writeText(deviceFlow.value.user_code)
  message.success('已复制授权码')
}

async function testCopilot() {
  testingCopilot.value = true
  try {
    const { data } = await copilotAuthApi.test()
    if (data.success) {
      message.success('✅ ' + data.message)
    } else {
      message.error('❌ ' + data.message)
    }
  } catch (e: any) {
    message.error('测试失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    testingCopilot.value = false
  }
}

async function logoutCopilot() {
  try {
    await copilotAuthApi.logout()
    copilotStatus.value = { authenticated: false }
    copilotUsage.value = null
    await modelApi.refresh()
    message.info('已注销 Copilot 授权')
  } catch (e: any) {
    message.error('注销失败')
  }
}

onMounted(() => {
  fetchCopilotStatus()
})

onUnmounted(() => {
  stopPolling()
})
</script>
