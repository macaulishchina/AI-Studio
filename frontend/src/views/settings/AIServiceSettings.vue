<template>
  <n-space vertical :size="16">
    <n-alert type="info" :bordered="false">
      配置 AI 模型服务提供商。GitHub Models 使用全局 Token（与工作目录无关），Copilot / Anti-Gravity 通过 OAuth 授权，第三方提供商需要自行填写 API Key。
      启用后，其模型会自动出现在讨论和实施的模型下拉列表中。
    </n-alert>

    <!-- 提供商卡片 -->
    <n-spin :show="loading">
      <n-space vertical :size="12">
        <!-- ===== 已配置的提供商 (内置 + 已填Key) ===== -->
        <template v-for="p in configuredProviders" :key="p.slug">
          <!-- Copilot -->
          <n-card v-if="p.slug === 'copilot'" size="small" :style="cardStyle(p)">
            <template #header>
              <n-space align="center" :size="8">
                <span v-html="getProviderIcon(p.slug, p.name, 20)" style="display:inline-flex"></span>
                <n-text strong>{{ p.name }}</n-text>
                <n-tag :type="copilotStatus.authenticated ? 'success' : 'default'" size="small">
                  {{ copilotStatus.authenticated ? '已授权' : '未授权' }}
                </n-tag>
                <n-tag v-if="copilotUsage?.copilot_plan" size="small" type="info">
                  {{ copilotUsage.copilot_plan }}
                </n-tag>
              </n-space>
            </template>
            <template #header-extra>
              <n-switch :value="copilotStatus.authenticated" :disabled="true" size="small" />
            </template>

            <n-text depth="3" style="font-size: 12px">{{ p.description }}</n-text>

            <!-- 高级请求配额 -->
            <template v-if="copilotUsage && copilotUsage.premium_requests && copilotStatus.authenticated">
              <div style="margin-top: 12px">
                <n-space align="center" justify="space-between" style="margin-bottom: 6px">
                  <n-text strong style="font-size: 13px">⚡ 高级请求配额</n-text>
                  <n-text depth="3" style="font-size: 12px">
                    重置: {{ copilotUsage.quota_reset_date || '-' }}
                  </n-text>
                </n-space>
                <template v-if="copilotUsage.premium_requests.unlimited">
                  <n-tag type="success" size="small">无限制</n-tag>
                </template>
                <template v-else>
                  <n-space vertical :size="4">
                    <n-space align="center" :size="8">
                      <n-text style="font-size: 18px; font-weight: bold; font-variant-numeric: tabular-nums">
                        {{ copilotUsage.premium_requests.remaining }}
                      </n-text>
                      <n-text depth="3" style="font-size: 13px">
                        / {{ copilotUsage.premium_requests.entitlement }} 剩余
                      </n-text>
                      <n-tag :type="quotaUsedPercent > 90 ? 'error' : quotaUsedPercent > 70 ? 'warning' : 'success'" size="small">
                        已用 {{ quotaUsedPercent }}%
                      </n-tag>
                    </n-space>
                    <n-progress
                      type="line"
                      :percentage="quotaUsedPercent"
                      :color="quotaUsedPercent < 70 ? '#18a058' : quotaUsedPercent < 90 ? '#f0a020' : '#d03050'"
                      :rail-color="'rgba(255,255,255,0.08)'"
                      :height="6"
                      :border-radius="4"
                      :show-indicator="false"
                    />
                  </n-space>
                </template>
              </div>
            </template>

            <!-- OAuth 流程 -->
            <n-space style="margin-top: 12px">
              <template v-if="!copilotStatus.authenticated">
                <template v-if="deviceFlow.active">
                  <n-card size="small" style="background: #1a2744; border: 1px solid #4098fc; width: 100%">
                    <n-space vertical align="center" :size="8">
                      <n-text>请访问以下网址并输入授权码:</n-text>
                      <n-button tag="a" :href="deviceFlow.verification_uri" target="_blank" type="info" size="small">
                        {{ deviceFlow.verification_uri }}
                      </n-button>
                      <n-space align="center">
                        <n-text strong style="font-size: 22px; letter-spacing: 4px; font-family: monospace">
                          {{ deviceFlow.user_code }}
                        </n-text>
                        <n-button size="tiny" @click="copyCode">📋</n-button>
                      </n-space>
                      <n-progress type="line" :percentage="deviceFlow.progress" :show-indicator="false" />
                    </n-space>
                  </n-card>
                </template>
                <template v-else>
                  <n-button type="primary" @click="startAuth" :loading="authLoading" size="small">
                    🔐 绑定 Copilot
                  </n-button>
                </template>
              </template>
              <template v-else>
                <n-button @click="testCopilot" :loading="testingCopilot" size="small">🧪 测试</n-button>
                <n-button @click="fetchCopilotUsage" :loading="loadingUsage" size="small">🔄 刷新配额</n-button>
                <n-button @click="startAuth" :loading="authLoading" size="small">➕ 添加账号</n-button>
                <n-button type="error" @click="logoutCopilot" size="small" ghost>🔓 注销</n-button>
              </template>
            </n-space>

            <!-- 多账号切换 -->
            <template v-if="copilotAccounts.length > 1 && copilotStatus.authenticated">
              <div style="margin-top: 10px">
                <n-space align="center" :size="8">
                  <n-text depth="3" style="font-size: 12px">账号:</n-text>
                  <n-select
                    :value="copilotActiveAccountIndex"
                    :options="copilotAccountOptions"
                    size="small"
                    style="width: 220px"
                    @update:value="switchCopilotAccount"
                  />
                  <n-popconfirm @positive-click="removeCopilotAccount(copilotActiveAccountIndex)">
                    <template #trigger>
                      <n-button size="tiny" type="error" ghost :disabled="copilotAccounts.length <= 1">🗑️</n-button>
                    </template>
                    确认删除此 Copilot 账号？
                  </n-popconfirm>
                </n-space>
              </div>
            </template>
          </n-card>

          <!-- Anti-Gravity -->
          <n-card v-else-if="p.slug === 'antigravity'" size="small" :style="cardStyle(p)">
            <template #header>
              <n-space align="center" :size="8">
                <span v-html="getProviderIcon(p.slug, p.name, 20)" style="display:inline-flex"></span>
                <n-text strong>{{ p.name }}</n-text>
                <n-tag :type="agStatus.authenticated ? 'success' : 'default'" size="small">
                  {{ agStatus.authenticated ? '已授权' : '未授权' }}
                </n-tag>
                <n-tag v-if="agUsage?.subscription" size="small" type="info">
                  {{ agUsage.subscription }}
                </n-tag>
              </n-space>
            </template>
            <template #header-extra>
              <n-switch :value="agStatus.authenticated" :disabled="true" size="small" />
            </template>

            <n-text depth="3" style="font-size: 12px">{{ p.description }}</n-text>

            <!-- 使用信息 -->
            <template v-if="agUsage && agStatus.authenticated">
              <div style="margin-top: 12px">
                <n-space align="center" :size="12" style="margin-bottom: 6px">
                  <n-text strong style="font-size: 13px">📊 使用信息</n-text>
                  <n-tag v-if="agUsage.user_email" size="small">{{ agUsage.user_email }}</n-tag>
                </n-space>
                <n-space vertical :size="4">
                  <n-text depth="3" style="font-size: 12px">
                    可用模型: {{ agUsage.available_models || 0 }} 个
                  </n-text>
                  <n-text depth="3" style="font-size: 12px">
                    {{ agUsage.rate_limits?.description || '' }}
                  </n-text>
                  <n-text v-if="agUsage.rate_limits?.plan_details" depth="3" style="font-size: 12px">
                    {{ agUsage.rate_limits.plan_details }}
                  </n-text>
                </n-space>
              </div>
            </template>

            <!-- OAuth 流程 -->
            <n-space style="margin-top: 12px">
              <template v-if="!agStatus.authenticated">
                <template v-if="agDeviceFlow.active">
                  <n-card size="small" style="background: #1a2744; border: 1px solid #4098fc; width: 100%">
                    <n-space vertical align="center" :size="8">
                      <n-text>请访问以下网址并用 Google 账号登录授权:</n-text>
                      <n-button tag="a" :href="agDeviceFlow.verification_url" target="_blank" type="info" size="small">
                        {{ agDeviceFlow.verification_url }}
                      </n-button>
                      <n-space align="center">
                        <n-text strong style="font-size: 22px; letter-spacing: 4px; font-family: monospace">
                          {{ agDeviceFlow.user_code }}
                        </n-text>
                        <n-button size="tiny" @click="copyAgCode">📋</n-button>
                      </n-space>
                      <n-progress type="line" :percentage="agDeviceFlow.progress" :show-indicator="false" />
                    </n-space>
                  </n-card>
                </template>
                <template v-else>
                  <n-button type="primary" @click="startAgAuth" :loading="agAuthLoading" size="small">
                    🔐 绑定 Google 账号
                  </n-button>
                </template>
              </template>
              <template v-else>
                <n-button @click="testAntigravity" :loading="testingAg" size="small">🧪 测试</n-button>
                <n-button @click="fetchAgUsage" :loading="loadingAgUsage" size="small">🔄 刷新信息</n-button>
                <n-button type="error" @click="logoutAntigravity" size="small" ghost>🔓 注销</n-button>
              </template>
            </n-space>
          </n-card>

          <!-- GitHub Models -->
          <n-card v-else-if="p.slug === 'github'" size="small" :style="cardStyle(p)">
            <template #header>
              <n-space align="center" :size="8">
                <span v-html="getProviderIcon(p.slug, p.name, 20)" style="display:inline-flex"></span>
                <n-text strong>{{ p.name }}</n-text>
                <n-tag :type="p.api_key_set ? 'success' : 'default'" size="small">
                  {{ p.api_key_set ? '已配置 Token' : '未配置 Token' }}
                </n-tag>
              </n-space>
            </template>
            <template #header-extra>
              <n-switch :value="true" :disabled="true" size="small" />
            </template>
            <n-text depth="3" style="font-size: 12px">{{ p.description }}</n-text>

            <n-space vertical :size="8" style="margin-top: 10px">
              <n-input-group>
                <n-input-group-label style="width: 80px">Token</n-input-group-label>
                <n-input
                  v-model:value="githubTokenInput"
                  :placeholder="p.api_key_set ? `已设置 (${p.api_key_hint})` : '输入 GitHub PAT (Models 权限)'"
                  type="password"
                  show-password-on="click"
                  size="small"
                  style="flex: 1"
                />
                <n-button
                  size="small"
                  type="primary"
                  :disabled="!githubTokenInput"
                  :loading="savingGithubToken"
                  @click="saveGithubToken"
                >保存</n-button>
              </n-input-group>

              <n-text depth="3" style="font-size: 12px">
                当前状态：{{ p.api_key_set ? `已配置（${p.api_key_hint}）` : '未配置' }}
              </n-text>
            </n-space>

            <n-space style="margin-top: 10px">
              <n-button size="small" :loading="testingGithub" @click="testProvider(p)">🧪 验证 Token</n-button>
              <n-button size="small" type="warning" ghost :disabled="!p.api_key_set" @click="clearGithubToken">清除 Token</n-button>
            </n-space>
          </n-card>

          <!-- 已配置的第三方 -->
          <n-card v-else size="small" :style="cardStyle(p)">
            <template #header>
              <n-space align="center" :size="8">
                <span v-html="getProviderIcon(p.slug, p.name, 20)" style="display:inline-flex"></span>
                <n-text strong>{{ p.name }}</n-text>
                <n-tag v-if="p.is_preset" size="small">预设</n-tag>
                <n-tag type="success" size="small">已配置</n-tag>
              </n-space>
            </template>
            <template #header-extra>
              <n-switch :value="p.enabled" @update:value="(v: boolean) => toggleProvider(p, v)" size="small" />
            </template>
            <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 10px">{{ p.description }}</n-text>
            <n-space vertical :size="8">
              <n-input-group>
                <n-input-group-label style="width: 80px">API Key</n-input-group-label>
                <n-input v-model:value="editingKeys[p.slug]" :placeholder="`已设置 (${p.api_key_hint})`" type="password" show-password-on="click" size="small" style="flex: 1" />
                <n-button size="small" type="primary" :disabled="!editingKeys[p.slug]" @click="saveApiKey(p)" :loading="saving[p.slug]">保存</n-button>
              </n-input-group>
              <n-input-group v-if="!p.is_builtin">
                <n-input-group-label style="width: 80px">Base URL</n-input-group-label>
                <n-input v-model:value="editingUrls[p.slug]" :placeholder="p.base_url" size="small" style="flex: 1" />
                <n-button size="small" :disabled="!editingUrls[p.slug] || editingUrls[p.slug] === p.base_url" @click="saveBaseUrl(p)">更新</n-button>
              </n-input-group>
            </n-space>
            <n-space style="margin-top: 10px">
              <n-button size="small" @click="testProvider(p)" :loading="testing[p.slug]">🧪 测试连接</n-button>
              <n-button size="small" @click="fetchModels(p)">📋 查看模型</n-button>
              <n-popconfirm v-if="!p.is_builtin && !p.is_preset" @positive-click="deleteProvider(p)">
                <template #trigger><n-button size="small" type="error" ghost>删除</n-button></template>
                确认删除提供商 {{ p.name }}？
              </n-popconfirm>
            </n-space>
          </n-card>
        </template>

        <!-- ===== 未配置的第三方 (默认折叠) ===== -->
        <n-card v-if="unconfiguredProviders.length" size="small" style="background: #212121; opacity: 0.85">
          <n-space align="center" :size="8" @click="showUnconfigured = !showUnconfigured" style="cursor: pointer; user-select: none">
            <n-text strong style="font-size: 13px">📦 更多预设提供商</n-text>
            <n-tag size="small">{{ unconfiguredProviders.length }} 个未配置</n-tag>
            <n-text depth="3" style="font-size: 11px">{{ showUnconfigured ? '▼ 收起' : '▶ 展开配置' }}</n-text>
          </n-space>

          <n-space v-if="showUnconfigured" vertical :size="12" style="margin-top: 12px">
            <n-card v-for="p in unconfiguredProviders" :key="p.slug" size="small" style="background: #212121; opacity: 0.9">
              <template #header>
                <n-space align="center" :size="8">
                  <span v-html="getProviderIcon(p.slug, p.name, 20)" style="display:inline-flex"></span>
                  <n-text strong>{{ p.name }}</n-text>
                  <n-tag v-if="p.is_preset" size="small">预设</n-tag>
                </n-space>
              </template>
              <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 10px">{{ p.description }}</n-text>
              <n-space vertical :size="8">
                <n-input-group>
                  <n-input-group-label style="width: 80px">API Key</n-input-group-label>
                  <n-input v-model:value="editingKeys[p.slug]" placeholder="输入 API Key" type="password" show-password-on="click" size="small" style="flex: 1" />
                  <n-button size="small" type="primary" :disabled="!editingKeys[p.slug]" @click="saveApiKey(p)" :loading="saving[p.slug]">保存</n-button>
                </n-input-group>
                <n-input-group v-if="!p.is_builtin">
                  <n-input-group-label style="width: 80px">Base URL</n-input-group-label>
                  <n-input v-model:value="editingUrls[p.slug]" :placeholder="p.base_url" size="small" style="flex: 1" />
                  <n-button size="small" :disabled="!editingUrls[p.slug] || editingUrls[p.slug] === p.base_url" @click="saveBaseUrl(p)">更新</n-button>
                </n-input-group>
              </n-space>
            </n-card>
          </n-space>
        </n-card>
      </n-space>
    </n-spin>

    <!-- 添加自定义提供商 -->
    <n-card size="small" style="border-style: dashed; cursor: pointer" @click="showAddModal = true">
      <n-space justify="center" align="center" :size="8">
        <n-icon size="18"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg></n-icon>
        <n-text>添加自定义 AI 服务提供商 (OpenAI 兼容)</n-text>
      </n-space>
    </n-card>

    <!-- 添加提供商弹窗 -->
    <n-modal v-model:show="showAddModal" preset="dialog" title="添加自定义 AI 服务提供商" positive-text="添加" negative-text="取消" @positive-click="addProvider" :loading="addingProvider" style="width: 520px; max-width: 95vw">
      <n-form :model="newProvider" label-placement="left" label-width="90">
        <n-form-item label="标识 (slug)">
          <n-input v-model:value="newProvider.slug" placeholder="英文小写, 如 siliconflow" />
        </n-form-item>
        <n-form-item label="名称">
          <n-input v-model:value="newProvider.name" placeholder="显示名称, 如 SiliconFlow" />
        </n-form-item>
        <n-form-item label="Base URL">
          <n-input v-model:value="newProvider.base_url" placeholder="https://api.siliconflow.cn/v1" />
        </n-form-item>
        <n-form-item label="API Key">
          <n-input v-model:value="newProvider.api_key" placeholder="sk-..." type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="图标">
          <n-input v-model:value="newProvider.icon" placeholder="🔌" style="width: 80px" />
        </n-form-item>
      </n-form>
    </n-modal>

    <!-- 模型列表弹窗 -->
    <n-modal v-model:show="showModelsModal" preset="card" :title="`${modelsModalProvider} - 可用模型`" style="width: 600px; max-width: 95vw">
      <n-spin :show="fetchingModels">
        <n-alert v-if="modelsResult.message" type="info" style="margin-bottom: 8px">
          {{ modelsResult.message }}
        </n-alert>
        <div v-if="modelsResult.models && modelsResult.models.length" style="max-height:400px;overflow-y:auto">
          <div v-for="(m, i) in modelsResult.models" :key="m.name"
               :style="`display:flex;justify-content:space-between;align-items:center;padding:8px 0;${i < modelsResult.models.length - 1 ? 'border-bottom:1px solid rgba(255,255,255,0.06)' : ''}`">
            <n-text style="font-size:13px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ m.friendly_name || m.name }}</n-text>
            <n-text depth="3" style="font-size:12px;flex-shrink:0;margin-left:16px;font-family:monospace;white-space:nowrap">{{ m.name }}</n-text>
          </div>
        </div>
        <n-empty v-else description="暂无模型数据" />
      </n-spin>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import { providerApi, copilotAuthApi, antigravityAuthApi, modelApi, systemApi } from '@/api'
import { getProviderIcon } from '@/utils/providerIcons'

const message = useMessage()

// ==================== 提供商列表 ====================
const providers = ref<any[]>([])
const loading = ref(false)
const showUnconfigured = ref(false)
const editingKeys = reactive<Record<string, string>>({})
const editingUrls = reactive<Record<string, string>>({})
const saving = reactive<Record<string, boolean>>({})
const testing = reactive<Record<string, boolean>>({})
const githubTokenInput = ref('')
const savingGithubToken = ref(false)
const testingGithub = computed(() => testing['github'] || false)

async function loadProviders() {
  loading.value = true
  try {
    const { data } = await providerApi.list()
    providers.value = data
  } catch (e: any) {
    message.error('加载提供商失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// 已配置: 内置 + 已填 API Key 的第三方
const BUILTIN_SLUGS = ['copilot', 'github', 'antigravity']
const configuredProviders = computed(() => {
  const builtins = providers.value.filter((p: any) => BUILTIN_SLUGS.includes(p.slug))
  const configured = providers.value.filter((p: any) => !BUILTIN_SLUGS.includes(p.slug) && p.api_key_set)
  return [...builtins, ...configured]
})

// 未配置: 第三方且未填 API Key
const unconfiguredProviders = computed(() =>
  providers.value.filter((p: any) => !BUILTIN_SLUGS.includes(p.slug) && !p.api_key_set)
)

// 配额已用百分比
const quotaUsedPercent = computed(() => {
  const pr = copilotUsage.value?.premium_requests
  if (!pr || pr.unlimited || !pr.entitlement) return 0
  return Math.round(((pr.entitlement - pr.remaining) / pr.entitlement) * 1000) / 10
})

function cardStyle(p: any) {
  if (p.slug === 'copilot' && copilotStatus.value.authenticated) return 'background: #212121; border-left: 3px solid #18a058'
  if (p.slug === 'antigravity' && agStatus.value.authenticated) return 'background: #212121; border-left: 3px solid #fbbc04'
  if (p.slug === 'github') return 'background: #212121; border-left: 3px solid #4098fc'
  if (p.enabled && p.api_key_set) return 'background: #212121; border-left: 3px solid #18a058'
  return 'background: #212121; opacity: 0.8'
}

// ==================== 第三方提供商操作 ====================
async function saveApiKey(p: any) {
  const key = editingKeys[p.slug]
  if (!key) return
  saving[p.slug] = true
  try {
    await providerApi.update(p.slug, { api_key: key, enabled: true })
    editingKeys[p.slug] = ''
    message.success(`${p.name} API Key 已保存并启用`)
    await loadProviders()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving[p.slug] = false
  }
}

async function saveBaseUrl(p: any) {
  const url = editingUrls[p.slug]
  if (!url) return
  try {
    await providerApi.update(p.slug, { base_url: url })
    message.success('Base URL 已更新')
    await loadProviders()
  } catch (e: any) {
    message.error('更新失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function saveGithubToken() {
  const token = githubTokenInput.value.trim()
  if (!token) return
  savingGithubToken.value = true
  try {
    await systemApi.setGithubToken(token)
    githubTokenInput.value = ''
    message.success('GitHub Models 全局 Token 已保存')
    await loadProviders()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingGithubToken.value = false
  }
}

async function clearGithubToken() {
  try {
    await systemApi.clearGithubToken()
    message.success('GitHub Models 全局 Token 已清除')
    await loadProviders()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('清除失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function toggleProvider(p: any, enabled: boolean) {
  try {
    await providerApi.update(p.slug, { enabled })
    message.success(`${p.name} ${enabled ? '已启用' : '已禁用'}`)
    await loadProviders()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('操作失败')
  }
}

async function testProvider(p: any) {
  testing[p.slug] = true
  try {
    const { data } = await providerApi.test(p.slug)
    if (data.success) {
      message.success(`✅ ${p.name}: ${data.message}`)
    } else {
      message.error(`❌ ${p.name}: ${data.message}`)
    }
  } catch (e: any) {
    message.error('测试失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    testing[p.slug] = false
  }
}

const showModelsModal = ref(false)
const modelsModalProvider = ref('')
const modelsResult = ref<any>({ models: [] })
const fetchingModels = ref(false)

async function fetchModels(p: any) {
  modelsModalProvider.value = p.name
  fetchingModels.value = true
  showModelsModal.value = true
  try {
    const { data } = await providerApi.fetchModels(p.slug)
    modelsResult.value = data
  } catch (e: any) {
    modelsResult.value = { models: [], message: '获取失败: ' + (e.response?.data?.detail || e.message) }
  } finally {
    fetchingModels.value = false
  }
}

async function deleteProvider(p: any) {
  try {
    await providerApi.delete(p.slug)
    message.success(`已删除 ${p.name}`)
    await loadProviders()
  } catch (e: any) {
    message.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

// ==================== 添加自定义提供商 ====================
const showAddModal = ref(false)
const addingProvider = ref(false)
const newProvider = reactive({
  slug: '',
  name: '',
  base_url: '',
  api_key: '',
  icon: '🔌',
})

async function addProvider() {
  if (!newProvider.slug || !newProvider.name || !newProvider.base_url) {
    message.warning('请填写标识、名称和 Base URL')
    return false
  }
  addingProvider.value = true
  try {
    await providerApi.create({ ...newProvider })
    message.success(`已添加 ${newProvider.name}`)
    newProvider.slug = ''
    newProvider.name = ''
    newProvider.base_url = ''
    newProvider.api_key = ''
    newProvider.icon = '🔌'
    showAddModal.value = false
    await loadProviders()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('添加失败: ' + (e.response?.data?.detail || e.message))
    return false
  } finally {
    addingProvider.value = false
  }
}

// ==================== Copilot OAuth ====================
const copilotStatus = ref<any>({ authenticated: false })
const copilotUsage = ref<any>(null)
const loadingUsage = ref(false)
const authLoading = ref(false)
const testingCopilot = ref(false)

// 多账号
const copilotAccounts = ref<any[]>([])
const copilotActiveAccountIndex = ref<number>(0)
const copilotAccountOptions = computed(() => {
  return copilotAccounts.value.map((a: any) => ({
    label: `${a.label || a.github_user} (${a.token_hint})`,
    value: a.index
  }))
})

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

async function loadCopilotAccounts() {
  try {
    const { data } = await copilotAuthApi.accounts()
    copilotAccounts.value = data || []
  } catch {}
}

async function switchCopilotAccount(index: number) {
  try {
    await copilotAuthApi.switchAccount(index)
    copilotActiveAccountIndex.value = index
    message.success('已切换 Copilot 账号')
    await fetchCopilotStatus()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('切换账号失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function removeCopilotAccount(index: number) {
  try {
    const { data } = await copilotAuthApi.removeAccount(index)
    message.success(`已删除 Copilot 账号 ${data.removed_label}`)
    await fetchCopilotStatus()
    await modelApi.refresh()
  } catch (e: any) {
    message.error('删除账号失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function fetchCopilotStatus() {
  try {
    const { data } = await copilotAuthApi.status()
    copilotStatus.value = data
    if (data.authenticated) {
      if (data.active_account && copilotAccounts.value.length) {
        // 更新 active index based on the returned account index, assume index is matched in the array or it's implicitly index 0 if not
        // A better way is to rely on active_index returned or just reload accounts
      }
      copilotActiveAccountIndex.value = data.account_count > 0 && data.active_account ? copilotAccounts.value.findIndex(a => a.github_user === data.active_account.github_user) : 0
      // if not found, reset to 0
      if (copilotActiveAccountIndex.value < 0) copilotActiveAccountIndex.value = 0

      await loadCopilotAccounts()
      fetchCopilotUsage()
    }
  } catch {}
}

async function fetchCopilotUsage() {
  loadingUsage.value = true
  try {
    const { data } = await copilotAuthApi.usage()
    copilotUsage.value = data
  } catch {} finally {
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
    }
    startPolling()
    const totalMs = (data.expires_in || 900) * 1000
    const startTime = Date.now()
    progressTimer = setInterval(() => {
      deviceFlow.value.progress = Math.min(100, ((Date.now() - startTime) / totalMs) * 100)
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
        message.success('🎉 Copilot 授权成功!')
      } else if (data.status === 'expired') {
        stopPolling()
        deviceFlow.value = { active: false }
        message.warning('授权码已过期，请重新开始')
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
  message.success('已复制')
}

async function testCopilot() {
  testingCopilot.value = true
  try {
    const { data } = await copilotAuthApi.test()
    data.success ? message.success('✅ ' + data.message) : message.error('❌ ' + data.message)
  } catch (e: any) {
    message.error('测试失败')
  } finally {
    testingCopilot.value = false
  }
}

async function logoutCopilot() {
  try {
    await copilotAuthApi.logout()
    copilotStatus.value = { authenticated: false }
    copilotUsage.value = null
    copilotAccounts.value = []
    await modelApi.refresh()
    message.info('已注销 Copilot')
  } catch {}
}

// ==================== Anti-Gravity OAuth ====================
const agStatus = ref<any>({ authenticated: false })
const agUsage = ref<any>(null)
const loadingAgUsage = ref(false)
const agAuthLoading = ref(false)
const testingAg = ref(false)
const agDeviceFlow = ref<any>({
  active: false,
  user_code: '',
  verification_url: '',
  progress: 0,
})
let agPollTimer: any = null
let agProgressTimer: any = null

async function fetchAgStatus() {
  try {
    const { data } = await antigravityAuthApi.status()
    agStatus.value = data
    if (data.authenticated) fetchAgUsage()
  } catch {}
}

async function fetchAgUsage() {
  loadingAgUsage.value = true
  try {
    const { data } = await antigravityAuthApi.usage()
    agUsage.value = data
  } catch {} finally {
    loadingAgUsage.value = false
  }
}

async function startAgAuth() {
  agAuthLoading.value = true
  try {
    const { data } = await antigravityAuthApi.startDeviceFlow()
    agDeviceFlow.value = {
      active: true,
      user_code: data.user_code,
      verification_url: data.verification_url,
      progress: 0,
    }
    startAgPolling()
    const totalMs = (data.expires_in || 1800) * 1000
    const startTime = Date.now()
    agProgressTimer = setInterval(() => {
      agDeviceFlow.value.progress = Math.min(100, ((Date.now() - startTime) / totalMs) * 100)
    }, 1000)
  } catch (e: any) {
    message.error('启动授权失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    agAuthLoading.value = false
  }
}

function startAgPolling() {
  agPollTimer = setInterval(async () => {
    try {
      const { data } = await antigravityAuthApi.pollDeviceFlow()
      if (data.status === 'success') {
        stopAgPolling()
        agDeviceFlow.value = { active: false }
        await fetchAgStatus()
        await modelApi.refresh()
        message.success('🎉 Anti-Gravity 授权成功!')
      } else if (data.status === 'expired') {
        stopAgPolling()
        agDeviceFlow.value = { active: false }
        message.warning('授权码已过期，请重新开始')
      }
    } catch {}
  }, 6000)
}

function stopAgPolling() {
  if (agPollTimer) { clearInterval(agPollTimer); agPollTimer = null }
  if (agProgressTimer) { clearInterval(agProgressTimer); agProgressTimer = null }
}

function copyAgCode() {
  navigator.clipboard.writeText(agDeviceFlow.value.user_code)
  message.success('已复制')
}

async function testAntigravity() {
  testingAg.value = true
  try {
    const { data } = await antigravityAuthApi.test()
    data.success ? message.success('✅ ' + data.message) : message.error('❌ ' + data.message)
  } catch (e: any) {
    message.error('测试失败')
  } finally {
    testingAg.value = false
  }
}

async function logoutAntigravity() {
  try {
    await antigravityAuthApi.logout()
    agStatus.value = { authenticated: false }
    agUsage.value = null
    await modelApi.refresh()
    message.info('已注销 Anti-Gravity')
  } catch {}
}

// ==================== 生命周期 ====================
onMounted(() => {
  loadProviders()
  fetchCopilotStatus()
  fetchAgStatus()
})

onUnmounted(() => {
  stopPolling()
  stopAgPolling()
})
</script>
