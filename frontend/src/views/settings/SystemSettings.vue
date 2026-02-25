<template>
  <n-space vertical :size="16">
    <!-- GitHub 连接 -->
    <n-card title="🔗 GitHub 连接" size="small" style="background: #16213e">
      <n-spin :show="checkingGithub">
        <n-descriptions :column="1" label-placement="left" bordered>
          <!-- Token 状态 -->
          <n-descriptions-item label="Token">
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <n-tag :type="githubStatus.masked_token ? 'success' : 'warning'" size="small">
                {{ githubStatus.masked_token ? '已配置' : '未配置' }}
              </n-tag>
              <n-text v-if="githubStatus.masked_token" code style="font-size: 12px; letter-spacing: 0.5px">
                {{ githubStatus.masked_token }}
              </n-text>
            </div>
          </n-descriptions-item>
          <!-- 仓库绑定 -->
          <n-descriptions-item label="仓库">
            <div style="display: flex; align-items: center; gap: 8px">
              <n-tag :type="githubStatus.repo_configured ? 'success' : 'warning'" size="small">
                {{ githubStatus.repo_configured ? '已绑定' : '未绑定' }}
              </n-tag>
              <n-text v-if="githubStatus.repo" code style="font-size: 12px">{{ githubStatus.repo }}</n-text>
            </div>
          </n-descriptions-item>
          <!-- 连接状态 -->
          <n-descriptions-item label="状态">
            <n-tag :type="githubStatus.connected ? 'success' : 'error'" size="small">
              {{ githubStatus.connected ? '已连接' : '未连接' }}
            </n-tag>
          </n-descriptions-item>
          <!-- 分支 (连接成功时显示) -->
          <n-descriptions-item label="默认分支" v-if="githubStatus.default_branch">
            {{ githubStatus.default_branch }}
          </n-descriptions-item>
          <!-- 错误提示 -->
          <n-descriptions-item label="提示" v-if="githubStatus.error">
            <n-text type="warning" style="font-size: 12px">{{ githubStatus.error }}</n-text>
          </n-descriptions-item>
        </n-descriptions>
      </n-spin>

      <!-- 操作区 -->
      <n-space style="margin-top: 10px" :size="8" :wrap="true">
        <n-button size="small" @click="checkGithub" :loading="checkingGithub">🔄 重新检测</n-button>
        <n-button size="small" type="primary" ghost @click="showTokenInput = !showTokenInput">
          {{ githubStatus.masked_token ? '🔑 更换 Token' : '🔑 设置 Token' }}
        </n-button>
        <n-button v-if="githubStatus.masked_token" size="small" type="error" ghost @click="handleClearToken">
          清除 Token
        </n-button>
        <n-button size="small" ghost @click="showRepoInput = !showRepoInput">
          {{ githubStatus.repo_configured ? '📦 更换仓库' : '📦 绑定仓库' }}
        </n-button>
      </n-space>

      <!-- Token 输入区 -->
      <div v-if="showTokenInput" style="margin-top: 10px">
        <n-input-group>
          <n-input
            v-model:value="tokenInput"
            type="password"
            show-password-on="click"
            placeholder="输入 GitHub Token (ghp_... / github_pat_...)"
            clearable
            style="flex: 1"
          />
          <n-button type="primary" :loading="savingToken" :disabled="!tokenInput.trim()" @click="handleSaveToken">
            保存
          </n-button>
        </n-input-group>
        <n-text depth="3" style="font-size: 11px; margin-top: 4px; display: block">
          Token 仅运行时生效，重启后需在 .env 中配置 GITHUB_TOKEN 持久化
        </n-text>
      </div>

      <!-- Repo 输入区 -->
      <div v-if="showRepoInput" style="margin-top: 10px">
        <n-input-group>
          <n-input
            v-model:value="repoInput"
            placeholder="owner/repo 格式, 如 myorg/myproject"
            clearable
            style="flex: 1"
          />
          <n-button type="primary" :loading="savingRepo" :disabled="!repoInput.trim()" @click="handleSaveRepo">
            保存
          </n-button>
        </n-input-group>
        <n-text depth="3" style="font-size: 11px; margin-top: 4px; display: block">
          仓库绑定仅运行时生效，重启后需在 .env 中配置 GITHUB_REPO 持久化
        </n-text>
      </div>
    </n-card>

    <!-- 工作目录管理 -->
    <n-card title="📁 工作目录管理" size="small" style="background: #16213e">
      <template #header-extra>
        <n-space :size="8">
          <n-button size="small" type="primary" @click="showAddDir = true">
            ＋ 添加目录
          </n-button>
          <n-button size="small" @click="fetchWorkspaceDirs" :loading="loadingDirs">
            🔄
          </n-button>
        </n-space>
      </template>

      <!-- 添加目录弹窗 -->
      <n-modal v-model:show="showAddDir" preset="dialog" title="添加工作目录" positive-text="添加" negative-text="取消"
        @positive-click="handleAddDir" :loading="addingDir">
        <n-space vertical :size="12">
          <n-input v-model:value="newDirPath" placeholder="输入工作目录绝对路径, 如 D:\projects\myapp" clearable />
          <n-input v-model:value="newDirLabel" placeholder="标签 (可选, 如: UE5引擎)" clearable />
        </n-space>
      </n-modal>

      <!-- 目录列表 -->
      <n-spin :show="loadingDirs">
        <n-space vertical :size="8" v-if="workspaceDirs.length">
          <div
            v-for="dir in workspaceDirs"
            :key="dir.id"
            :style="{
              padding: '10px 14px',
              borderRadius: '6px',
              border: dir.is_active ? '1.5px solid #63e2b7' : '1px solid rgba(255,255,255,0.08)',
              background: dir.is_active ? 'rgba(99,226,183,0.06)' : 'rgba(255,255,255,0.02)',
              transition: 'all 0.2s',
            }"
          >
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <!-- 活跃标记 -->
              <n-tag v-if="dir.is_active" type="success" size="small" :bordered="false" round>当前</n-tag>
              <!-- VCS 类型 -->
              <n-tag :type="dir.vcs_type === 'none' ? 'default' : 'info'" size="small" :bordered="false">
                {{ ({git: 'Git', svn: 'SVN', none: '—'} as Record<string, string>)[dir.vcs_type] || dir.vcs_type }}
              </n-tag>
              <!-- 标签 -->
              <n-text strong style="font-size: 13px">{{ dir.label || dir.path.split(/[\\/]/).pop() }}</n-text>
              <!-- 目录不存在警告 -->
              <n-tag v-if="!dir.exists" type="error" size="small">目录不存在</n-tag>

              <!-- 操作按钮 (右对齐) -->
              <div style="margin-left: auto; display: flex; gap: 4px; flex-shrink: 0">
                <n-button v-if="!dir.is_active" size="tiny" type="primary" ghost @click="handleActivate(dir)"
                  :loading="dir._switching">
                  切换
                </n-button>
                <n-popconfirm @positive-click="handleRemoveDir(dir)">
                  <template #trigger>
                    <n-button size="tiny" type="error" ghost :disabled="dir.is_active && workspaceDirs.length === 1">
                      移除
                    </n-button>
                  </template>
                  确定移除工作目录「{{ dir.label || dir.path }}」？<br>（不会删除实际文件）
                </n-popconfirm>
              </div>
            </div>
            <!-- 路径 -->
            <n-text code depth="3" style="font-size: 11px; margin-top: 4px; display: block; word-break: break-all">
              {{ dir.path }}
            </n-text>
          </div>
        </n-space>
        <n-empty v-else description="尚未配置工作目录，点击上方「添加目录」开始" />
      </n-spin>
    </n-card>

    <!-- 工作区概览 -->
    <n-card title="🔍 工作区概览" size="small" style="background: #16213e">
      <template #header-extra>
        <n-button size="small" @click="fetchWorkspaceOverview(true)" :loading="loadingWorkspace">
          🔄 刷新
        </n-button>
      </template>
      <n-spin :show="loadingWorkspace">
        <template v-if="workspaceOverview">
          <!-- 工作区路径 -->
          <n-alert type="info" :bordered="false" style="margin-bottom: 12px; background: rgba(32,128,240,.08)">
            <template #icon><span>📂</span></template>
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <n-text depth="3" style="font-size: 12px; white-space: nowrap">当前路径:</n-text>
              <n-text code style="font-size: 12px; word-break: break-all">{{ workspaceOverview.workspace_path }}</n-text>
              <n-tag v-if="!workspaceOverview.workspace_exists" type="error" size="small">目录不存在</n-tag>
            </div>
          </n-alert>

          <!-- VCS + 基本信息 -->
          <n-descriptions :column="2" label-placement="left" bordered size="small" style="margin-bottom: 12px">
            <n-descriptions-item label="版本控制">
              <n-tag :type="workspaceOverview.vcs_type === 'none' ? 'default' : 'info'" size="small">
                {{ ({'git': 'Git', 'svn': 'SVN', 'none': '无'} as Record<string, string>)[workspaceOverview.vcs_type] || workspaceOverview.vcs_type }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="分支 / 路径" v-if="workspaceOverview.vcs?.branch">
              <n-text code>{{ workspaceOverview.vcs.branch }}</n-text>
            </n-descriptions-item>
            <n-descriptions-item label="最新提交" v-if="workspaceOverview.vcs?.last_commit_hash">
              <n-text code style="font-size: 12px">
                {{ workspaceOverview.vcs.last_commit_hash.slice(0, 8) }}
              </n-text>
              <n-text depth="3" style="margin-left: 6px; font-size: 12px">
                {{ workspaceOverview.vcs.last_commit_message }}
              </n-text>
            </n-descriptions-item>
            <n-descriptions-item label="未提交变更">
              <n-tag :type="workspaceOverview.uncommitted_count > 0 ? 'warning' : 'success'" size="small">
                {{ workspaceOverview.uncommitted_count }} 个文件
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="文件总数">
              {{ workspaceOverview.total_files }}
            </n-descriptions-item>
          </n-descriptions>

          <!-- 语言统计 -->
          <div v-if="workspaceOverview.language_stats?.length" style="margin-bottom: 12px">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">语言分布</n-text>
            <n-space :size="4" :wrap="true">
              <n-tag
                v-for="lang in workspaceOverview.language_stats"
                :key="lang.language"
                size="small"
                :bordered="false"
                :style="{ background: langColor(lang.language) + '25', color: langColor(lang.language) }"
              >
                {{ lang.language }}
                <template #avatar>
                  <span style="font-size: 10px; opacity: 0.7">{{ lang.percentage }}%</span>
                </template>
              </n-tag>
            </n-space>
          </div>

          <!-- 关键文件 -->
          <div v-if="workspaceOverview.key_files?.length" style="margin-bottom: 12px">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">关键文件</n-text>
            <n-space :size="4" :wrap="true">
              <n-tag v-for="f in workspaceOverview.key_files" :key="f" size="small" :bordered="false" type="success">
                {{ f }}
              </n-tag>
            </n-space>
          </div>

          <!-- 贡献者 -->
          <div v-if="workspaceOverview.contributors?.length" style="margin-bottom: 12px">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">贡献者 Top {{ workspaceOverview.contributors.length }}</n-text>
            <n-space :size="6" :wrap="true">
              <n-tag
                v-for="c in workspaceOverview.contributors"
                :key="c.name"
                size="small"
                round
              >
                {{ c.name }}
                <template #avatar>
                  <span style="font-size: 10px; opacity: 0.7">{{ c.commits }}</span>
                </template>
              </n-tag>
            </n-space>
          </div>

          <!-- 近期提交 -->
          <div v-if="workspaceOverview.recent_commits?.length">
            <n-text depth="3" style="font-size: 12px; margin-bottom: 6px; display: block">近期提交</n-text>
            <n-space vertical :size="2">
              <div v-for="(cm, idx) in workspaceOverview.recent_commits.slice(0, 8)" :key="idx" style="font-size: 12px; line-height: 1.6">
                <n-text code style="font-size: 11px; margin-right: 6px">{{ (cm.hash || '').slice(0, 7) }}</n-text>
                <n-text>{{ cm.message }}</n-text>
                <n-text depth="3" style="margin-left: 6px; font-size: 11px">{{ cm.author }} · {{ cm.time }}</n-text>
              </div>
            </n-space>
          </div>
        </template>
        <n-empty v-else description="加载中…" />
      </n-spin>
    </n-card>

    <!-- 系统状态 -->
    <n-card title="🖥️ 系统状态" size="small" style="background: #16213e">
      <n-spin :show="loadingStatus">
        <n-descriptions :column="1" label-placement="left" bordered v-if="systemStatus">
          <n-descriptions-item :label="vcsLabel + ' 分支'">
            {{ systemStatus.vcs?.branch || systemStatus.git?.branch || '—' }}
          </n-descriptions-item>
          <n-descriptions-item label="最近提交">
            <n-space vertical :size="2">
              <n-text v-for="(c, i) in recentCommitLines" :key="i" code style="font-size: 12px">
                {{ c }}
              </n-text>
              <n-text v-if="!recentCommitLines.length" depth="3">暂无提交记录</n-text>
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
import { ref, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { systemApi, endpointProbeApi, workspaceDirApi } from '@/api'

const message = useMessage()

const githubStatus = ref<any>({})
const systemStatus = ref<any>(null)
const checkingGithub = ref(false)
const loadingStatus = ref(false)

// GitHub Token / Repo 管理
const showTokenInput = ref(false)
const showRepoInput = ref(false)
const tokenInput = ref('')
const repoInput = ref('')
const savingToken = ref(false)
const savingRepo = ref(false)

// 工作目录管理
const workspaceDirs = ref<any[]>([])
const loadingDirs = ref(false)
const showAddDir = ref(false)
const addingDir = ref(false)
const newDirPath = ref('')
const newDirLabel = ref('')

// 工作区概览
const workspaceOverview = ref<any>(null)
const loadingWorkspace = ref(false)

// 端点探测
const probeEndpoints = ref<any[]>([])
const probingAll = ref(false)
const probeResult = ref<any>(null)

// ── VCS 兼容 ──────────────────────────────
const vcsLabel = computed(() => {
  const t = systemStatus.value?.vcs?.type || workspaceOverview.value?.vcs_type
  if (t === 'svn') return 'SVN'
  if (t === 'git') return 'Git'
  return 'VCS'
})

const recentCommitLines = computed(() => {
  // 优先使用新格式（向后兼容旧 git 字段）
  return systemStatus.value?.git?.recent_commits || []
})

// ── 语言颜色 ──────────────────────────────
const LANG_COLORS: Record<string, string> = {
  Python: '#3572A5', TypeScript: '#3178C6', JavaScript: '#F7DF1E', Vue: '#42b883',
  Java: '#B07219', Go: '#00ADD8', Rust: '#DEA584', 'C++': '#F34B7D', 'C#': '#178600',
  Ruby: '#CC342D', PHP: '#4F5D95', Swift: '#F05138', Kotlin: '#A97BFF', Dart: '#00B4AB',
  HTML: '#E34F26', CSS: '#1572B6', SCSS: '#C6538C', Shell: '#89E051', SQL: '#E38C00',
  Markdown: '#083FA1', YAML: '#CB171E', JSON: '#A0A0A0', XML: '#0060AC', Docker: '#2496ED',
}
function langColor(lang: string): string {
  return LANG_COLORS[lang] || '#63e2b7'
}

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

async function handleSaveToken() {
  savingToken.value = true
  try {
    await systemApi.setGithubToken(tokenInput.value.trim())
    message.success('GitHub Token 已保存')
    tokenInput.value = ''
    showTokenInput.value = false
    // 重新检测连接状态
    await checkGithub()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingToken.value = false
  }
}

async function handleClearToken() {
  try {
    await systemApi.clearGithubToken()
    message.success('GitHub Token 已清除')
    await checkGithub()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '清除失败')
  }
}

async function handleSaveRepo() {
  savingRepo.value = true
  try {
    await systemApi.setGithubRepo(repoInput.value.trim())
    message.success('GitHub 仓库已绑定')
    repoInput.value = ''
    showRepoInput.value = false
    await checkGithub()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingRepo.value = false
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

async function fetchWorkspaceOverview(forceRefresh = false) {
  loadingWorkspace.value = true
  try {
    const { data } = await systemApi.workspaceOverview(forceRefresh)
    workspaceOverview.value = data
  } catch (e: any) {
    console.warn('工作区概览加载失败', e)
  } finally {
    loadingWorkspace.value = false
  }
}

// ── 工作目录管理 ─────────────────────────
async function fetchWorkspaceDirs() {
  loadingDirs.value = true
  try {
    const { data } = await workspaceDirApi.list()
    workspaceDirs.value = data.map((d: any) => ({ ...d, _switching: false }))
  } catch (e: any) {
    console.warn('加载工作目录列表失败', e)
  } finally {
    loadingDirs.value = false
  }
}

async function handleAddDir() {
  if (!newDirPath.value.trim()) {
    message.warning('请输入工作目录路径')
    return false
  }
  addingDir.value = true
  try {
    await workspaceDirApi.add({ path: newDirPath.value.trim(), label: newDirLabel.value.trim() })
    message.success('工作目录已添加')
    newDirPath.value = ''
    newDirLabel.value = ''
    showAddDir.value = false
    await fetchWorkspaceDirs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '添加失败')
    return false
  } finally {
    addingDir.value = false
  }
}

async function handleActivate(dir: any) {
  dir._switching = true
  try {
    await workspaceDirApi.activate(dir.id)
    message.success(`已切换到: ${dir.label || dir.path}`)
    await fetchWorkspaceDirs()
    // 切换后重新加载工作区概览
    fetchWorkspaceOverview(true)
    // 重新加载系统状态
    fetchStatus()
    // 通知其他组件（头部、Dashboard）同步更新
    window.dispatchEvent(new CustomEvent('workspace-switched', { detail: { id: dir.id, path: dir.path } }))
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换失败')
  } finally {
    dir._switching = false
  }
}

async function handleRemoveDir(dir: any) {
  try {
    await workspaceDirApi.remove(dir.id)
    message.success('工作目录已移除')
    await fetchWorkspaceDirs()
    fetchWorkspaceOverview(true)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '移除失败')
  }
}

onMounted(() => {
  fetchStatus()
  fetchProbeEndpoints()
  fetchWorkspaceDirs()
  // 工作区概览独立加载，不阻塞页面
  fetchWorkspaceOverview()
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
