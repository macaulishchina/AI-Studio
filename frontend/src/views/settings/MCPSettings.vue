<template>
  <div>
    <!-- 顶部说明 + 操作 -->
    <n-space justify="space-between" align="center" style="margin-bottom: 16px">
      <n-text depth="3">
        管理 MCP (Model Context Protocol) 服务 — 通过标准协议接入外部 AI 工具服务。
      </n-text>
      <n-space :size="8">
        <n-button size="small" :loading="loadingStatus" @click="refreshAll">
          🔄 刷新状态
        </n-button>
        <n-button type="primary" size="small" @click="showCreateModal = true">
          ＋ 添加 MCP 服务
        </n-button>
      </n-space>
    </n-space>

    <!-- 全局状态摘要 -->
    <n-card size="small" style="background: #212121; margin-bottom: 16px" v-if="globalStatus">
      <n-space :size="16" align="center">
        <n-statistic label="已注册" :value="globalStatus.registered" />
        <n-statistic label="已启用" :value="globalStatus.enabled" />
        <n-statistic label="已连接">
          <template #default>
            <n-text :type="globalStatus.connected > 0 ? 'success' : 'default'">
              {{ globalStatus.connected }}
            </n-text>
          </template>
        </n-statistic>
        <n-statistic label="可用工具" :value="globalStatus.total_tools" />
      </n-space>
    </n-card>

    <!-- GitHub MCP 系统级凭据配置 -->
    <n-card title="🔐 GitHub MCP 凭据" size="small" style="background: #212121; margin-bottom: 16px">
      <template #header-extra>
        <n-space :size="8" align="center">
          <n-button size="tiny" quaternary :loading="loadingGithubPanel" @click="loadActiveGithubConfig">
            同步状态
          </n-button>
          <n-button
            v-if="connectionStatus('github') === 'connected'"
            size="tiny"
            quaternary
            type="warning"
            :loading="reconnectingGithub"
            @click="reconnectGithubServer"
          >
            重连 GitHub MCP
          </n-button>
        </n-space>
      </template>

      <div class="cred-panel">
        <n-text depth="3" class="cred-hint">
          系统级 GitHub Token 配置，初始值来自 .env 的 GITHUB_TOKEN。
        </n-text>

        <n-space class="cred-badges" :size="8" align="center" :wrap="true">
          <n-tag :type="githubTokenConfigured ? 'success' : 'warning'" size="small" :bordered="false" round>
            {{ githubTokenConfigured ? '✅ Token 已配置' : '⚠️ Token 未配置' }}
          </n-tag>
          <n-tag v-if="githubMaskedToken" size="small" :bordered="false" type="success" round>
            {{ githubMaskedToken }}
          </n-tag>
        </n-space>

        <div class="cred-form-grid">
          <n-input
            v-model:value="githubTokenInput"
            class="cred-token"
            type="password"
            show-password-on="click"
            clearable
            placeholder="输入新的 GitHub Token（留空则不改）"
            autocomplete="new-password"
            :input-props="{ autocomplete: 'new-password', name: 'mcp-token-' + Date.now() }"
            @keyup.enter="saveGithubOverride"
          />
          <n-space class="cred-actions" :size="8" align="center">
            <n-button
              type="primary"
              :loading="savingGithubCred"
              :disabled="!canSaveGithubCred"
              @click="saveGithubOverride"
            >
              💾 保存配置
            </n-button>
            <n-popconfirm @positive-click="clearGithubTokenOverride">
              <template #trigger>
                <n-button size="small" tertiary type="error" :loading="savingGithubCred" :disabled="!githubTokenConfigured">
                  🧹 清空 Token
                </n-button>
              </template>
              确认清空系统 GitHub Token 配置？
            </n-popconfirm>
          </n-space>
        </div>
      </div>
    </n-card>

    <!-- 服务列表 -->
    <n-spin :show="loading">
      <n-space vertical :size="12" v-if="servers.length">
        <n-card
          v-for="server in servers"
          :key="server.slug"
          size="small"
          style="background: #1a1a1a"
          hoverable
        >
          <!-- 服务头部 -->
          <n-space justify="space-between" align="center">
            <n-space align="center" :size="12">
              <span style="font-size: 24px">{{ server.icon || '🔌' }}</span>
              <div>
                <n-space align="center" :size="6">
                  <n-text strong>{{ server.name }}</n-text>
                  <n-tag size="tiny" :bordered="false" round>
                    <code style="font-size: 11px">{{ server.slug }}</code>
                  </n-tag>
                  <n-tag v-if="server.is_builtin" size="tiny" type="info" round>内置</n-tag>
                  <n-tag size="tiny" :bordered="false" :type="transportTagType(server.transport)">
                    {{ server.transport }}
                  </n-tag>
                  <n-tag
                    size="tiny"
                    :bordered="false"
                    :type="connectionStatus(server.slug) === 'connected' ? 'success' : 'default'"
                    round
                  >
                    {{ connectionLabel(server.slug) }}
                  </n-tag>
                </n-space>
                <n-text depth="3" style="font-size: 12px; display: block; margin-top: 2px">
                  {{ server.description || '无描述' }}
                </n-text>
              </div>
            </n-space>

            <!-- 操作按钮 -->
            <n-space :size="8" align="center">
              <n-switch
                :value="server.enabled"
                size="small"
                @update:value="(v: boolean) => toggleEnabled(server, v)"
              />
              <n-button
                v-if="server.enabled && connectionStatus(server.slug) !== 'connected'"
                size="tiny"
                type="primary"
                :loading="connecting[server.slug]"
                @click="handleConnect(server.slug)"
              >
                连接
              </n-button>
              <n-button
                v-if="connectionStatus(server.slug) === 'connected'"
                size="tiny"
                type="warning"
                :loading="connecting[server.slug]"
                @click="handleDisconnect(server.slug)"
              >
                断开
              </n-button>
              <n-button size="tiny" quaternary @click="openDetail(server)">
                详情
              </n-button>
              <n-popconfirm
                v-if="!server.is_builtin"
                @positive-click="handleDelete(server.slug)"
              >
                <template #trigger>
                  <n-button size="tiny" quaternary type="error">删除</n-button>
                </template>
                确定删除 MCP 服务「{{ server.name }}」？
              </n-popconfirm>
            </n-space>
          </n-space>

          <!-- 工具列表 (可展开) -->
          <div v-if="serverTools[server.slug]?.length" style="margin-top: 10px">
            <n-collapse :default-expanded-names="[]">
              <n-collapse-item :title="`🛠️ 已发现 ${serverTools[server.slug].length} 个工具`" :name="server.slug">
                <n-space :size="6" :wrap="true">
                  <n-tag
                    v-for="tool in serverTools[server.slug]"
                    :key="tool.name"
                    size="small"
                    :bordered="false"
                    round
                  >
                    {{ tool.name }}
                    <template #avatar>
                      <span style="font-size: 12px">🔧</span>
                    </template>
                  </n-tag>
                </n-space>
              </n-collapse-item>
            </n-collapse>
          </div>
        </n-card>
      </n-space>

      <div v-else-if="!loading" style="text-align: center; padding: 40px 0">
        <n-empty description="暂无 MCP 服务">
          <template #extra>
            <n-button size="small" @click="showCreateModal = true">添加第一个 MCP 服务</n-button>
          </template>
        </n-empty>
      </div>
    </n-spin>

    <!-- 审计日志 -->
    <n-card title="📋 MCP 调用日志" size="small" style="background: #212121; margin-top: 24px">
      <template #header-extra>
        <n-space :size="8">
          <n-button size="small" @click="loadAuditLog" :loading="loadingAudit">🔄</n-button>
          <n-button size="small" @click="loadAuditStats" :loading="loadingStats">📊 统计</n-button>
        </n-space>
      </template>

      <!-- 统计信息 -->
      <div v-if="auditStats" style="margin-bottom: 12px">
        <n-space :size="16">
          <n-statistic label="总调用" :value="auditStats.total_calls" />
          <n-statistic label="成功率">
            <template #default>
              <n-text :type="auditStats.success_rate >= 90 ? 'success' : 'warning'">
                {{ auditStats.success_rate?.toFixed(1) }}%
              </n-text>
            </template>
          </n-statistic>
          <n-statistic label="平均耗时">
            <template #default>{{ auditStats.avg_duration_ms?.toFixed(0) }}ms</template>
          </n-statistic>
        </n-space>
      </div>

      <!-- 日志列表 -->
      <n-data-table
        :columns="auditColumns"
        :data="auditLogs"
        :loading="loadingAudit"
        size="small"
        :max-height="300"
        :pagination="{ pageSize: 10 }"
        :row-key="(row: any) => row.id"
      />
    </n-card>

    <!-- 创建/编辑弹窗 -->
    <n-modal
      v-model:show="showCreateModal"
      preset="dialog"
      :title="editingServer ? '编辑 MCP 服务' : '添加 MCP 服务'"
      style="width: 600px"
      :positive-text="editingServer ? '保存' : '创建'"
      negative-text="取消"
      @positive-click="handleSave"
    >
      <n-form ref="formRef" :model="formData" label-placement="left" label-width="100">
        <n-form-item label="标识 (slug)" path="slug">
          <n-input
            v-model:value="formData.slug"
            placeholder="唯一标识, 如: github"
            :disabled="!!editingServer"
          />
        </n-form-item>
        <n-form-item label="名称" path="name">
          <n-input v-model:value="formData.name" placeholder="显示名称, 如: GitHub MCP Server" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input v-model:value="formData.description" type="textarea" :rows="2" placeholder="服务描述" />
        </n-form-item>
        <n-form-item label="图标">
          <n-input v-model:value="formData.icon" placeholder="Emoji 图标" style="width: 80px" />
        </n-form-item>
        <n-form-item label="传输协议">
          <n-select
            v-model:value="formData.transport"
            :options="transportOptions"
            style="width: 200px"
          />
        </n-form-item>
        <template v-if="formData.transport === 'stdio'">
          <n-form-item label="启动命令">
            <n-input v-model:value="formData.command" placeholder="如: npx" />
          </n-form-item>
          <n-form-item label="命令参数">
            <n-dynamic-tags v-model:value="formArgs" />
            <n-text depth="3" style="font-size: 11px; margin-left: 8px">
              回车添加参数
            </n-text>
          </n-form-item>
        </template>
        <template v-if="formData.transport !== 'stdio'">
          <n-form-item label="远程 URL">
            <n-input v-model:value="formData.url" placeholder="如: http://localhost:3000/sse" />
          </n-form-item>
        </template>
        <n-form-item label="环境变量">
          <div style="width: 100%">
            <n-text depth="3" style="font-size: 11px; display: block; margin-bottom: 8px">
              支持占位符: {github_token}, {github_repo}, {gitlab_token} 等 — 运行时从工作目录配置自动替换
            </n-text>
            <div v-for="(_, key) in formEnvTemplate" :key="key" style="display: flex; gap: 8px; margin-bottom: 6px">
              <n-input :value="String(key)" disabled style="width: 40%" />
              <n-input v-model:value="formEnvTemplate[key]" style="width: 50%" />
              <n-button size="small" quaternary type="error" @click="removeEnvVar(String(key))">✕</n-button>
            </div>
            <n-space :size="8" style="margin-top: 4px">
              <n-input v-model:value="newEnvKey" placeholder="变量名" style="width: 180px" size="small" />
              <n-input v-model:value="newEnvValue" placeholder="值 / 占位符" style="width: 200px" size="small" />
              <n-button size="small" @click="addEnvVar">＋</n-button>
            </n-space>
          </div>
        </n-form-item>
      </n-form>
    </n-modal>

    <!-- 详情弹窗 -->
    <n-modal
      v-model:show="showDetailModal"
      preset="card"
      :title="`${detailServer?.icon || '🔌'} ${detailServer?.name || ''} — 详情`"
      style="width: 700px"
    >
      <n-tabs type="segment" animated size="small">
        <n-tab-pane name="info" tab="📋 基本信息">
          <n-descriptions :column="1" label-placement="left" bordered size="small" v-if="detailServer">
            <n-descriptions-item label="标识">{{ detailServer.slug }}</n-descriptions-item>
            <n-descriptions-item label="名称">{{ detailServer.name }}</n-descriptions-item>
            <n-descriptions-item label="传输协议">{{ detailServer.transport }}</n-descriptions-item>
            <n-descriptions-item v-if="detailServer.command" label="启动命令">
              <code>{{ detailServer.command }} {{ (detailServer.args || []).join(' ') }}</code>
            </n-descriptions-item>
            <n-descriptions-item v-if="detailServer.url" label="远程 URL">{{ detailServer.url }}</n-descriptions-item>
            <n-descriptions-item label="环境变量模板">
              <n-tag
                v-for="(v, k) in (detailServer.env_template || {})"
                :key="k"
                size="small"
                :bordered="false"
                style="margin: 2px"
              >
                {{ k }}={{ v }}
              </n-tag>
              <n-text v-if="!Object.keys(detailServer.env_template || {}).length" depth="3">无</n-text>
            </n-descriptions-item>
            <n-descriptions-item label="状态">
              <n-tag :type="detailServer.enabled ? 'success' : 'default'" size="small">
                {{ detailServer.enabled ? '已启用' : '已禁用' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="连接状态">
              <n-tag :type="connectionStatus(detailServer.slug) === 'connected' ? 'success' : 'default'" size="small">
                {{ connectionLabel(detailServer.slug) }}
              </n-tag>
            </n-descriptions-item>
          </n-descriptions>
        </n-tab-pane>

        <n-tab-pane name="tools" tab="🛠️ 工具列表">
          <n-space vertical :size="8" v-if="detailTools.length">
            <n-card
              v-for="tool in detailTools"
              :key="tool.name"
              size="small"
              style="background: rgba(255,255,255,0.03)"
            >
              <n-space align="center" :size="8">
                <n-text strong>{{ tool.name }}</n-text>
              </n-space>
              <n-text depth="3" style="font-size: 12px; display: block; margin-top: 4px">
                {{ tool.description || '无描述' }}
              </n-text>
              <div v-if="tool.inputSchema?.properties" style="margin-top: 6px">
                <n-text depth="3" style="font-size: 11px">参数: </n-text>
                <n-tag
                  v-for="(_, pname) in tool.inputSchema.properties"
                  :key="pname"
                  size="tiny"
                  :bordered="false"
                  style="margin: 1px"
                  :type="(tool.inputSchema.required || []).includes(pname) ? 'primary' : 'default'"
                >
                  {{ pname }}{{ (tool.inputSchema.required || []).includes(pname) ? '*' : '' }}
                </n-tag>
              </div>
            </n-card>
          </n-space>
          <n-empty v-else description="未发现工具 — 请先连接服务" />
        </n-tab-pane>

        <n-tab-pane name="secrets" tab="🔑 密钥验证">
          <n-space vertical :size="12">
            <n-text depth="3">
              验证当前工作目录的凭证是否满足该 MCP 服务的环境变量需求。
            </n-text>
            <n-button
              type="primary"
              size="small"
              :loading="validatingSecrets"
              @click="handleValidateSecrets"
            >
              🔍 验证密钥
            </n-button>
            <n-alert
              v-if="secretsResult"
              :type="secretsResult.complete ? 'success' : 'warning'"
              :title="secretsResult.complete ? '✅ 密钥验证通过' : '⚠️ 缺少必要凭证'"
            >
              <div v-if="secretsResult.missing?.length">
                <n-text>缺少的变量: </n-text>
                <n-tag v-for="m in secretsResult.missing" :key="m" size="small" type="error" style="margin: 2px">
                  {{ m }}
                </n-tag>
                <n-text depth="3" style="display:block; margin-top: 6px; font-size: 12px">
                  请在上方「GitHub MCP 凭据」中配置 Token。GitHub MCP 默认需要 `GITHUB_PERSONAL_ACCESS_TOKEN`。
                </n-text>
              </div>
              <div v-if="secretsResult.resolved_keys?.length" style="margin-top: 4px">
                <n-text>已解析变量: </n-text>
                <n-tag v-for="r in secretsResult.resolved_keys" :key="r" size="small" type="success" style="margin: 2px">
                  {{ r }}
                </n-tag>
              </div>
            </n-alert>
          </n-space>
        </n-tab-pane>
      </n-tabs>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, h, computed } from 'vue'
import { useMessage, NTag, NText } from 'naive-ui'
import { mcpApi, systemApi } from '@/api'

const message = useMessage()

// ─── State ───────────────────────────────────────────
const loading = ref(false)
const loadingStatus = ref(false)
const servers = ref<any[]>([])
const globalStatus = ref<any>(null)
const healthMap = ref<Record<string, string>>({})
const serverTools = ref<Record<string, any[]>>({})
const connecting = ref<Record<string, boolean>>({})

// GitHub MCP 系统级凭据
const githubTokenInput = ref('')
const githubTokenConfigured = ref(false)
const githubMaskedToken = ref('')
const savingGithubCred = ref(false)
const loadingGithubPanel = ref(false)
const reconnectingGithub = ref(false)
const canSaveGithubCred = computed(() => !!githubTokenInput.value.trim())

// 审计日志
const loadingAudit = ref(false)
const loadingStats = ref(false)
const auditLogs = ref<any[]>([])
const auditStats = ref<any>(null)

// 创建/编辑
const showCreateModal = ref(false)
const editingServer = ref<any>(null)
const formData = reactive({
  slug: '',
  name: '',
  description: '',
  icon: '🔌',
  transport: 'stdio',
  command: '',
  url: '',
})
const formArgs = ref<string[]>([])
const formEnvTemplate = reactive<Record<string, string>>({})
const newEnvKey = ref('')
const newEnvValue = ref('')

// 详情
const showDetailModal = ref(false)
const detailServer = ref<any>(null)
const detailTools = ref<any[]>([])
const validatingSecrets = ref(false)
const secretsResult = ref<any>(null)

// ─── Options ─────────────────────────────────────────
const transportOptions = [
  { label: 'stdio (本地进程)', value: 'stdio' },
  { label: 'SSE (远程流)', value: 'sse' },
  { label: 'Streamable HTTP', value: 'streamable_http' },
]

const auditColumns = [
  {
    title: '时间',
    key: 'created_at',
    width: 150,
    render: (row: any) => h(NText, { depth: 3, style: 'font-size:12px' }, () =>
      row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '—'
    ),
  },
  { title: '服务', key: 'server_slug', width: 80 },
  { title: '工具', key: 'tool_name', width: 160 },
  {
    title: '状态',
    key: 'success',
    width: 70,
    render: (row: any) => h(NTag, {
      size: 'tiny',
      type: row.success ? 'success' : 'error',
      bordered: false,
    }, () => row.success ? '成功' : '失败'),
  },
  {
    title: '耗时',
    key: 'duration_ms',
    width: 80,
    render: (row: any) => h(NText, { depth: 3, style: 'font-size:12px' }, () => `${row.duration_ms}ms`),
  },
  {
    title: '结果/错误',
    key: 'result_preview',
    ellipsis: { tooltip: true },
    render: (row: any) => h(NText, {
      depth: 3,
      style: 'font-size:12px',
      type: row.success ? undefined : 'error',
    }, () => row.success ? (row.result_preview || '—') : (row.error_message || '—')),
  },
]

// ─── Data Loading ────────────────────────────────────
async function loadServers() {
  loading.value = true
  try {
    const { data } = await mcpApi.listServers()
    servers.value = data
  } catch (e: any) {
    message.error('加载 MCP 服务列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadStatus() {
  loadingStatus.value = true
  try {
    const { data } = await mcpApi.status()
    const servers = Array.isArray(data?.servers) ? data.servers : []
    const toolsFromServers = servers.reduce((sum: number, server: any) => sum + (Number(server?.tools_count) || 0), 0)

    globalStatus.value = {
      registered: Number(data?.registered ?? data?.total_servers ?? 0),
      enabled: Number(data?.enabled ?? data?.enabled_servers ?? 0),
      connected: Number(data?.connected ?? data?.connected_servers ?? 0),
      total_tools: Number(data?.total_tools ?? toolsFromServers),
    }
  } catch { /* ignore */ } finally {
    loadingStatus.value = false
  }
}

async function loadHealth() {
  try {
    const { data } = await mcpApi.health()
    const normalized: Record<string, string> = {}
    for (const [slug, state] of Object.entries(data || {})) {
      if (typeof state === 'string') {
        normalized[slug] = state
        continue
      }

      const s = state as any
      if (s?.connected) {
        normalized[slug] = s?.healthy === false ? 'error' : 'connected'
      } else {
        normalized[slug] = 'disconnected'
      }
    }
    healthMap.value = normalized
  } catch { /* ignore */ }
}

async function loadServerTools(slug: string) {
  try {
    const { data } = await mcpApi.getTools(slug)
    serverTools.value[slug] = data.tools || []
  } catch { /* ignore */ }
}

async function loadAuditLog() {
  loadingAudit.value = true
  try {
    const { data } = await mcpApi.auditLog({ limit: 50 })
    auditLogs.value = data
  } catch (e: any) {
    message.error('加载审计日志失败')
  } finally {
    loadingAudit.value = false
  }
}

async function loadAuditStats() {
  loadingStats.value = true
  try {
    const { data } = await mcpApi.auditLogStats()
    auditStats.value = data
  } catch { /* ignore */ } finally {
    loadingStats.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadServers(), loadStatus(), loadHealth(), loadActiveGithubConfig()])
  await autoConnectEnabledServers()

  // 加载每个已启用服务的工具
  for (const s of servers.value) {
    if (s.enabled) {
      await loadServerTools(s.slug)
    }
  }
}

onMounted(refreshAll)

// ─── Helpers ─────────────────────────────────────────
function transportTagType(t: string) {
  return t === 'stdio' ? 'info' : t === 'sse' ? 'warning' : 'default'
}

function connectionStatus(slug: string): string {
  return healthMap.value[slug] || 'disconnected'
}

function connectionLabel(slug: string): string {
  const s = connectionStatus(slug)
  return s === 'connected' ? '🟢 已连接' : s === 'error' ? '🔴 错误' : '⚪ 未连接'
}

async function loadActiveGithubConfig() {
  loadingGithubPanel.value = true
  githubMaskedToken.value = ''
  try {
    const { data } = await systemApi.getGithubTokenStatus()
    githubMaskedToken.value = data?.masked_token || ''
    githubTokenConfigured.value = !!data?.configured
  } catch {
    // ignore
  } finally {
    loadingGithubPanel.value = false
  }
}

async function saveGithubOverride() {
  if (!githubTokenInput.value.trim()) {
    message.warning('请输入 Token')
    return
  }
  savingGithubCred.value = true
  try {
    await systemApi.setGithubToken(githubTokenInput.value.trim())
    githubTokenInput.value = ''
    message.success('GitHub 凭据已保存')
    await loadActiveGithubConfig()
  } catch (e: any) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingGithubCred.value = false
  }
}

async function clearGithubTokenOverride() {
  savingGithubCred.value = true
  try {
    await systemApi.clearGithubToken()
    githubTokenInput.value = ''
    message.success('已清空系统 GitHub Token')
    await loadActiveGithubConfig()
  } catch (e: any) {
    message.error('清空失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingGithubCred.value = false
  }
}

// ─── Actions ─────────────────────────────────────────
async function toggleEnabled(server: any, enabled: boolean) {
  try {
    await mcpApi.updateServer(server.slug, { enabled })
    server.enabled = enabled
    message.success(enabled ? '已启用' : '已禁用')
    if (!enabled) {
      // 禁用时也断开连接
      try { await mcpApi.disconnect(server.slug) } catch { /* ignore */ }
      healthMap.value[server.slug] = 'disconnected'
    }
    await loadStatus()
  } catch (e: any) {
    message.error('操作失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function autoConnectEnabledServers() {
  const toConnect = servers.value.filter(
    (server: any) => server.enabled && connectionStatus(server.slug) !== 'connected'
  )
  if (!toConnect.length) return

  for (const server of toConnect) {
    connecting.value[server.slug] = true
    try {
      await mcpApi.connect(server.slug)
      healthMap.value[server.slug] = 'connected'
      await loadServerTools(server.slug)
    } catch {
      // 自动重连失败时静默，保持手动连接入口可用
    } finally {
      connecting.value[server.slug] = false
    }
  }

  await Promise.all([loadStatus(), loadHealth()])
}

async function handleConnect(slug: string) {
  connecting.value[slug] = true
  try {
    const { data } = await mcpApi.connect(slug)
    message.success(`连接成功 — 发现 ${data.tools_discovered || 0} 个工具`)
    healthMap.value[slug] = 'connected'
    await loadServerTools(slug)
    await loadStatus()
  } catch (e: any) {
    message.error('连接失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    connecting.value[slug] = false
  }
}

async function handleDisconnect(slug: string) {
  connecting.value[slug] = true
  try {
    await mcpApi.disconnect(slug)
    healthMap.value[slug] = 'disconnected'
    serverTools.value[slug] = []
    message.success('已断开')
    await loadStatus()
  } catch (e: any) {
    message.error('断开失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    connecting.value[slug] = false
  }
}

async function handleDelete(slug: string) {
  try {
    await mcpApi.deleteServer(slug)
    message.success('已删除')
    await refreshAll()
  } catch (e: any) {
    message.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

// ─── Create / Edit ───────────────────────────────────
function resetForm() {
  formData.slug = ''
  formData.name = ''
  formData.description = ''
  formData.icon = '🔌'
  formData.transport = 'stdio'
  formData.command = ''
  formData.url = ''
  formArgs.value = []
  // 清空 env template
  Object.keys(formEnvTemplate).forEach(k => delete formEnvTemplate[k])
  newEnvKey.value = ''
  newEnvValue.value = ''
  editingServer.value = null
}

function openDetail(server: any) {
  detailServer.value = server
  detailTools.value = serverTools.value[server.slug] || []
  secretsResult.value = null
  showDetailModal.value = true
  // 如果已连接, 刷新工具列表
  if (connectionStatus(server.slug) === 'connected') {
    loadServerTools(server.slug).then(() => {
      detailTools.value = serverTools.value[server.slug] || []
    })
  }
}

function addEnvVar() {
  if (newEnvKey.value.trim()) {
    formEnvTemplate[newEnvKey.value.trim()] = newEnvValue.value
    newEnvKey.value = ''
    newEnvValue.value = ''
  }
}

function removeEnvVar(key: string) {
  delete formEnvTemplate[key]
}

async function handleSave() {
  const payload: any = {
    slug: formData.slug,
    name: formData.name,
    description: formData.description,
    icon: formData.icon,
    transport: formData.transport,
    command: formData.transport === 'stdio' ? formData.command : '',
    args: formData.transport === 'stdio' ? formArgs.value : [],
    url: formData.transport !== 'stdio' ? formData.url : '',
    env_template: { ...formEnvTemplate },
  }

  try {
    if (editingServer.value) {
      await mcpApi.updateServer(editingServer.value.slug, payload)
      message.success('已更新')
    } else {
      await mcpApi.createServer(payload)
      message.success('已创建')
    }
    showCreateModal.value = false
    resetForm()
    await refreshAll()
  } catch (e: any) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message))
    return false // 不关闭弹窗
  }
}

async function handleValidateSecrets() {
  if (!detailServer.value) return
  validatingSecrets.value = true
  try {
    const { data } = await mcpApi.validateSecrets(detailServer.value.slug)
    secretsResult.value = {
      ...data,
      complete: !!(data?.complete ?? data?.valid),
      resolved_keys: data?.resolved_keys || data?.resolved || [],
      missing: data?.missing || [],
    }
  } catch (e: any) {
    message.error('验证失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    validatingSecrets.value = false
  }
}

async function reconnectGithubServer() {
  reconnectingGithub.value = true
  try {
    await mcpApi.disconnect('github')
    await mcpApi.connect('github')
    message.success('GitHub MCP 已重连')
    await refreshAll()
  } catch (e: any) {
    message.error('重连失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    reconnectingGithub.value = false
  }
}
</script>

<style scoped>
.cred-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cred-hint {
  font-size: 12px;
}

.cred-badges {
  margin-bottom: 2px;
}

.cred-form-grid {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.cred-actions {
  justify-content: flex-start;
}

@media (max-width: 980px) {
  .cred-form-grid {
    grid-template-columns: 1fr;
  }

  .cred-actions {
    justify-content: flex-start;
  }
}
</style>
