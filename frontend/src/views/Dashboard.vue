<template>
  <div>
    <n-space vertical :size="24">
      <!-- 欢迎区 -->
      <n-card style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)">
        <div class="welcome-area">
          <div>
            <n-h2 :style="{ margin: 0, color: '#e94560', fontSize: isMobile ? '18px' : undefined }">🤖 AI设计院</n-h2>
            <n-text depth="3" :style="{ fontSize: isMobile ? '12px' : undefined }">AI 驱动的需求迭代平台 — 让想法变成现实</n-text>
          </div>
          <n-button type="primary" @click="showCreate = true" :size="isMobile ? 'medium' : 'large'">
            <template #icon><n-icon :component="AddOutline" /></template>
            新建项目
          </n-button>
        </div>
      </n-card>

      <!-- 统计卡片 -->
      <n-grid :cols="isMobile ? 2 : 4" :x-gap="isMobile ? 8 : 16" :y-gap="isMobile ? 8 : 16">
        <n-gi>
          <n-card size="small" style="background: #16213e">
            <n-statistic label="进行中" :value="activeCount" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small" style="background: #16213e">
            <n-statistic label="已部署" :value="deployedCount" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small" style="background: #16213e">
            <n-statistic label="快照数" :value="snapshotCount" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small" style="background: #16213e">
            <n-statistic label="总项目" :value="projects.length" />
          </n-card>
        </n-gi>
      </n-grid>

      <!-- 最近动态：标签分组筛选 -->
      <div v-if="projects.length">
        <n-card size="small" style="background: #16213e">
          <template #header>
            <div class="filter-section">
              <!-- 类型筛选 -->
              <div class="filter-row">
                <span class="filter-label">类型</span>
                <div class="tag-filter-bar">
                  <span
                    class="filter-chip"
                    :class="{ active: activeTypeFilter === null }"
                    @click="activeTypeFilter = null"
                  >
                    全部
                    <span class="chip-count">{{ projects.length }}</span>
                  </span>
                  <span
                    v-for="group in allTypeGroups"
                    :key="group.typeKey"
                    class="filter-chip"
                    :class="{ active: activeTypeFilter === group.typeKey }"
                    :style="{
                      '--chip-color': group.color,
                      '--chip-bg': group.color + '18',
                      '--chip-active-bg': group.color + '30',
                    }"
                    @click="activeTypeFilter = activeTypeFilter === group.typeKey ? null : group.typeKey"
                  >
                    {{ group.icon }} {{ group.name }}
                    <span class="chip-count">{{ group.total }}</span>
                  </span>
                </div>
              </div>
              <!-- 状态筛选 -->
              <div class="filter-row">
                <span class="filter-label">状态</span>
                <div class="tag-filter-bar">
                  <span
                    class="filter-chip filter-chip-sm"
                    :class="{ active: activeStatusFilter === null }"
                    @click="activeStatusFilter = null"
                  >全部</span>
                  <span
                    v-for="st in allStatusGroups"
                    :key="st.key"
                    class="filter-chip filter-chip-sm"
                    :class="{ active: activeStatusFilter === st.key }"
                    :style="{
                      '--chip-color': st.color,
                      '--chip-active-bg': st.color + '25',
                    }"
                    @click="activeStatusFilter = activeStatusFilter === st.key ? null : st.key"
                  >
                    {{ st.label }}
                    <span class="chip-count">{{ st.total }}</span>
                  </span>
                </div>
              </div>
              <!-- 人员筛选 -->
              <div v-if="allUserGroups.length > 1" class="filter-row">
                <span class="filter-label">人员</span>
                <div class="tag-filter-bar">
                  <span
                    class="filter-chip filter-chip-sm"
                    :class="{ active: activeUserFilter === null }"
                    @click="activeUserFilter = null"
                  >全部</span>
                  <span
                    v-for="u in allUserGroups"
                    :key="u.name"
                    class="filter-chip filter-chip-sm filter-chip-user"
                    :class="{ active: activeUserFilter === u.name }"
                    @click="activeUserFilter = activeUserFilter === u.name ? null : u.name"
                  >
                    <span class="chip-avatar">{{ u.name.charAt(0).toUpperCase() }}</span>
                    {{ u.name }}
                    <span class="chip-count">{{ u.total }}</span>
                  </span>
                </div>
              </div>
            </div>
          </template>
          <n-list bordered style="background: transparent">
            <TransitionGroup name="list">
              <n-list-item
                v-for="p in filteredProjects"
                :key="p.id"
                style="padding: 0"
              >
                <LogItem :item="p" @click="() => router.push(`/projects/${p.id}`)" />
              </n-list-item>
            </TransitionGroup>
          </n-list>
          <n-empty v-if="filteredProjects.length === 0" description="该分类暂无项目" style="padding: 24px 0" />
        </n-card>
      </div>
      <n-empty v-else description="还没有项目，点击「新建项目」开始" />
    </n-space>

    <!-- 新建项目对话框 -->
    <n-modal v-model:show="showCreate" preset="dialog" :title="createDialogTitle" style="width: 600px; max-width: 95vw">
      <n-form :model="newProject" label-placement="left" label-width="80">
        <n-form-item label="类型">
          <div class="type-card-grid">
            <div
              v-for="pt in projectTypes"
              :key="pt.key"
              class="type-card"
              :class="{ 'type-card-active': newProject.project_type === pt.key }"
              @click="newProject.project_type = pt.key"
            >
              <span class="type-icon">{{ pt.icon || '📋' }}</span>
              <span class="type-name">{{ pt.name }}</span>
            </div>
          </div>
        </n-form-item>
        <n-form-item :label="selectedTypeLabels.project_noun + '标题'">
          <n-input v-model:value="newProject.title" :placeholder="selectedTypeUiLabels.create_placeholder || ('简明描述' + selectedTypeLabels.project_noun + '目标')" />
        </n-form-item>
        <n-form-item :label="selectedTypeLabels.project_noun + '描述'">
          <n-input
            v-model:value="newProject.description"
            type="textarea"
            :rows="4"
            :placeholder="selectedTypeUiLabels.description_placeholder || ('详细描述' + selectedTypeLabels.project_noun + '背景和期望效果...')"
          />
        </n-form-item>
        <n-form-item label="讨论模型">
          <n-space vertical :size="8">
            <n-radio-group v-model:value="discussFilter" size="small">
              <n-radio-button v-for="f in providerFilters" :key="f.value" :value="f.value">
                <span style="display:inline-flex;align-items:center;gap:3px">
                  <span v-if="f.icon" v-html="f.icon"></span>
                  <span>{{ f.label }}</span>
                </span>
              </n-radio-button>
            </n-radio-group>
            <n-select v-model:value="newProject.discussion_model" :options="modelOptions" filterable :render-label="renderModelLabel" />
          </n-space>
        </n-form-item>
      </n-form>
      <template #action>
        <n-button @click="showCreate = false">取消</n-button>
        <n-button type="primary" @click="handleCreate" :loading="creating">创建并进入讨论</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { AddOutline } from '@vicons/ionicons5'
import LogItem from '@/components/LogItem.vue'
import { useProjectStore } from '@/stores/project'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { snapshotApi, modelApi, projectApi } from '@/api'
import { getProviderIcon } from '@/utils/providerIcons'

const router = useRouter()

// ── 响应式检测 ──────────────────────────────────────────
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
const isMobile = computed(() => windowWidth.value < 768)
function _onResize() { windowWidth.value = window.innerWidth }
const message = useMessage()
const store = useProjectStore()
const studioConfig = useStudioConfigStore()

const showCreate = ref(false)
const creating = ref(false)
const snapshotCount = ref(0)
const models = ref<any[]>([])
const discussFilter = ref('all')
const projectTypes = ref<any[]>([])

const providerFilters = computed(() => {
  const filters: Array<{value: string; label: string; icon: string}> = [
    { value: 'all', label: '全部', icon: '' },
    { value: 'github', label: 'GitHub', icon: getProviderIcon('github', 'G', 12) },
  ]
  if (models.value.some(m => m.api_backend === 'copilot')) {
    filters.push({ value: 'copilot', label: 'Copilot', icon: getProviderIcon('copilot', 'C', 12) })
  }
  const seen = new Set<string>()
  for (const m of models.value) {
    const slug = m.provider_slug || ''
    if (slug && slug !== 'github' && slug !== 'copilot' && !seen.has(slug)) {
      seen.add(slug)
      filters.push({ value: slug, label: m.publisher || slug, icon: getProviderIcon(slug, m.publisher || slug, 12) })
    }
  }
  if (studioConfig.customModelsEnabled) {
    filters.push({ value: 'custom', label: '补充', icon: '' })
  }
  return filters
})

const newProject = ref({
  title: '',
  description: '',
  discussion_model: 'gpt-4o',
  project_type: 'requirement',
})

const projects = computed(() => store.projects)
const activeCount = computed(() =>
  projects.value.filter(p => !['deployed', 'closed', 'rolled_back'].includes(p.status)).length
)
const deployedCount = computed(() =>
  projects.value.filter(p => p.status === 'deployed').length
)

// ── 按角色分组，标签筛选 ─────────────────────────────────────────
const ROLE_COLORS: Record<string, string> = {
  bug: '#d03050', fix: '#d03050', 缺陷: '#d03050', 问诊: '#d03050',
  需求: '#2080f0', feature: '#2080f0', 分析: '#2080f0',
  任务: '#18a058', task: '#18a058',
  审查: '#f0a020', review: '#f0a020', 评审: '#f0a020',
}
function roleGroupColor(name = '') {
  const n = name.toLowerCase()
  for (const [key, color] of Object.entries(ROLE_COLORS)) {
    if (n.includes(key)) return color
  }
  return '#63e2b7'
}

const activeTypeFilter = ref<string | null>(null)
const activeStatusFilter = ref<string | null>(null)
const activeUserFilter = ref<string | null>(null)

const STATUS_META: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: '#888' },
  discussing: { label: '讨论中', color: '#2080f0' },
  planned: { label: '已定稿', color: '#f0a020' },
  implementing: { label: '实施中', color: '#f0a020' },
  reviewing: { label: '审核中', color: '#2080f0' },
  deploying: { label: '部署中', color: '#f0a020' },
  deployed: { label: '已部署', color: '#18a058' },
  rolled_back: { label: '已回滚', color: '#d03050' },
  closed: { label: '已关闭', color: '#888' },
}

const allTypeGroups = computed(() => {
  const map = new Map<string, { typeKey: string; name: string; icon: string; color: string; total: number }>()
  for (const p of projects.value) {
    const tk = p.project_type || p.type_info?.key || 'unknown'
    if (!map.has(tk)) {
      const name = p.type_info?.name || '项目'
      map.set(tk, { typeKey: tk, name, icon: p.type_info?.icon || '📋', color: roleGroupColor(name), total: 0 })
    }
    map.get(tk)!.total++
  }
  return [...map.values()]
    .sort((a, b) => b.total - a.total)
})

const allStatusGroups = computed(() => {
  const map = new Map<string, number>()
  for (const p of projects.value) {
    map.set(p.status, (map.get(p.status) || 0) + 1)
  }
  return [...map.entries()]
    .map(([key, total]) => ({
      key,
      label: STATUS_META[key]?.label || key,
      color: STATUS_META[key]?.color || '#888',
      total,
    }))
    .sort((a, b) => b.total - a.total)
})

const allUserGroups = computed(() => {
  const map = new Map<string, number>()
  for (const p of projects.value) {
    // 创建者
    if (p.created_by) map.set(p.created_by, (map.get(p.created_by) || 0) + 1)
    // 参与者
    if (p.participants) {
      for (const u of p.participants) {
        if (u !== p.created_by) map.set(u, (map.get(u) || 0) + 1)
      }
    }
  }
  return [...map.entries()]
    .map(([name, total]) => ({ name, total }))
    .sort((a, b) => b.total - a.total)
})

const filteredProjects = computed(() => {
  let list = projects.value

  if (activeTypeFilter.value) {
    list = list.filter(p => (p.project_type || p.type_info?.key || 'unknown') === activeTypeFilter.value)
  }
  if (activeStatusFilter.value) {
    list = list.filter(p => p.status === activeStatusFilter.value)
  }
  if (activeUserFilter.value) {
    const u = activeUserFilter.value
    list = list.filter(p =>
      p.created_by === u || (p.participants && p.participants.includes(u))
    )
  }

  return list.slice(0, 30)
})

const selectedTypeLabels = computed(() => {
  const pt = projectTypes.value.find(t => t.key === newProject.value.project_type)
  if (pt?.ui_labels) return { project_noun: pt.ui_labels.project_noun || '需求', output_noun: pt.ui_labels.output_noun || '设计稿' }
  return { project_noun: '需求', output_noun: '设计稿' }
})

const selectedTypeUiLabels = computed(() => {
  const pt = projectTypes.value.find(t => t.key === newProject.value.project_type)
  return pt?.ui_labels || {} as Record<string, string>
})

const createDialogTitle = computed(() => {
  const pt = projectTypes.value.find(t => t.key === newProject.value.project_type)
  if (pt?.ui_labels?.create_title) return pt.ui_labels.create_title
  return '🆕 新建项目'
})

// 空操作: project_type 已有默认值 'requirement'

function filterBySource(list: any[], source: string) {
  if (source === 'all') return list
  if (source === 'custom') return list.filter(m => m.is_custom)
  if (source === 'github') return list.filter(m => m.provider_slug === 'github' || (!m.provider_slug && m.api_backend === 'models'))
  if (source === 'copilot') return list.filter(m => m.provider_slug === 'copilot' || m.api_backend === 'copilot')
  return list.filter(m => m.provider_slug === source)
}

function buildGroupedOptions(list: any[]) {
  // 保留 API 返回顺序, 按 model_family 分组 (后端已提供)
  const mapOpt = (m: any) => ({
    label: m.name || m.id, value: m.id,
    supports_vision: m.supports_vision, supports_tools: m.supports_tools,
    is_reasoning: m.is_reasoning, api_backend: m.api_backend,
    is_custom: m.is_custom,
    provider_slug: m.provider_slug || (m.api_backend === 'copilot' ? 'copilot' : 'github'),
    max_input_tokens: studioConfig.getEffectiveMaxInput(m.id, m.max_input_tokens || 0),
    pricing_tier: m.pricing_tier, premium_multiplier: m.premium_multiplier,
    is_deprecated: m.is_deprecated, pricing_note: m.pricing_note,
  })
  // 按 model_family 保序分组
  const groups: Array<{ key: string; label: string; slug: string; items: any[] }> = []
  const groupMap: Record<string, typeof groups[0]> = {}
  for (const m of list) {
    const family = m.model_family || m.publisher || m.provider_slug || 'Other'
    const slug = m.provider_slug || (m.api_backend === 'copilot' ? 'copilot' : 'github')
    const gKey = slug + ':' + family
    if (!groupMap[gKey]) {
      const g = { key: gKey, label: family, slug, items: [] as any[] }
      groups.push(g)
      groupMap[gKey] = g
    }
    groupMap[gKey].items.push(m)
  }
  return groups.map(g => ({
    type: 'group', label: g.label, key: g.key, provider_slug: g.slug,
    children: g.items.map(mapOpt),
  }))
}

function renderModelLabel(option: any, selected: boolean) {
  if (option.type === 'group') {
    const iconHtml = getProviderIcon(option.provider_slug || 'github', option.label, 14)
    return h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [
      h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
      option.label,
    ])
  }
  const caps: string[] = []
  if (option.is_reasoning) caps.push('🧠')
  if (option.supports_vision) caps.push('👁️')
  if (option.supports_tools) caps.push('🔧')
  const depStr = option.is_deprecated ? ' ⚠️' : ''
  const capStr = caps.length ? ` ${caps.join('')}` : ''
  const iconHtml = getProviderIcon(option.provider_slug || 'github', '', 12)
  const iconVNode = h('span', { innerHTML: iconHtml, style: 'display:inline-flex;vertical-align:middle;margin:0 2px' })
  const customStr = option.is_custom ? ' 🧩' : ''
  const priceText = option.pricing_note || 'x0'
  const ctxText = option.max_input_tokens ? formatTokens(option.max_input_tokens) : ''
  const nameStyle = selected ? 'font-weight:600' : ''
  const priceStyle = selected
    ? 'color:#18a058;font-size:11px;flex-shrink:0;margin-left:12px;font-weight:600'
    : 'color:#888;font-size:11px;flex-shrink:0;margin-left:12px'
  return h('div', { style: 'display:flex;justify-content:space-between;align-items:center;width:100%' }, [
    h('span', { style: nameStyle }, [selected ? '● ' : '', option.label as string, ' ', iconVNode, customStr, capStr, depStr]),
    h('span', { style: priceStyle }, [
      ctxText ? h('span', { style: 'color:#666;margin-right:6px' }, ctxText) : null,
      priceText,
    ]),
  ])
}

function formatTokens(n: number): string {
  if (!n) return '0'
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(0)}K`
  return `${n}`
}

const modelOptions = computed(() => {
  const byCategory = models.value.filter(m => m.category === 'discussion' || m.category === 'both')
  return buildGroupedOptions(filterBySource(byCategory, discussFilter.value))
})

function statusType(status: string) {
  const map: Record<string, any> = {
    draft: 'default', discussing: 'info', planned: 'warning',
    implementing: 'warning', reviewing: 'info', deploying: 'warning',
    deployed: 'success', rolled_back: 'error', closed: 'default',
  }
  return map[status] || 'default'
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    draft: '草稿', discussing: '讨论中', planned: '已定稿',
    implementing: '实施中', reviewing: '审核中', deploying: '部署中',
    deployed: '已部署', rolled_back: '已回滚', closed: '已关闭',
  }
  return map[status] || status
}

function formatDate(dateStr: string) {
  // 后端存储 UTC 时间，ISO 字符串不含 Z 后缀，手动补 Z 转本地时区
  const utcStr = dateStr && !dateStr.endsWith('Z') && !dateStr.includes('+') ? dateStr + 'Z' : dateStr
  return new Date(utcStr).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

async function handleCreate() {
  if (!newProject.value.title.trim()) {
    message.warning('请输入需求标题')
    return
  }
  creating.value = true
  try {
    const project = await store.createProject(newProject.value)
    showCreate.value = false
    message.success('项目已创建')
    router.push(`/projects/${project.id}`)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  window.addEventListener('resize', _onResize)
  store.fetchProjects()
  try {
    const { data } = await projectApi.listTypes()
    projectTypes.value = data
  } catch {}
  try {
    const { data } = await snapshotApi.list()
    snapshotCount.value = data.length
  } catch {}
  try {
    const { data } = await modelApi.list({ custom_models: studioConfig.customModelsEnabled })
    models.value = data
  } catch {}
})

onUnmounted(() => {
  window.removeEventListener('resize', _onResize)
})
</script>

<style scoped>
.welcome-area {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
@media (max-width: 767px) {
  .welcome-area {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
}
.type-card {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 14px; border-radius: 8px;
  border: 1.5px solid #333; cursor: pointer;
  transition: all .15s; user-select: none;
}
.type-card:hover { border-color: #63e2b7; }
.type-card-active { border-color: #63e2b7; background: rgba(99,226,183,.12); }
.type-icon { font-size: 18px; }
.type-name { font-size: 13px; }
.type-card-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* ── 过滤器区域 ──────────────────── */
.filter-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.filter-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
@media (max-width: 767px) {
  .filter-row {
    gap: 6px;
  }
  .filter-chip {
    padding: 3px 8px;
    font-size: 12px;
  }
  .filter-chip-sm {
    padding: 2px 6px;
    font-size: 11px;
  }
}
@media (max-width: 767px) {
  .tag-filter-bar {
    gap: 4px;
  }
}

.filter-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.35);
  flex-shrink: 0;
  width: 28px;
  text-align: right;
}

/* ── 标签筛选栏 ──────────────────── */
.tag-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border-radius: 14px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.08);
  transition: all 0.2s ease;
}

.filter-chip:hover {
  background: var(--chip-active-bg, rgba(255, 255, 255, 0.1));
  color: var(--chip-color, rgba(255, 255, 255, 0.85));
  border-color: var(--chip-color, rgba(255, 255, 255, 0.2));
}

.filter-chip.active {
  background: var(--chip-active-bg, rgba(99, 226, 183, 0.2));
  color: var(--chip-color, #63e2b7);
  border-color: var(--chip-color, #63e2b7);
  font-weight: 600;
}

.chip-count {
  font-size: 11px;
  min-width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.08);
  padding: 0 5px;
}

.filter-chip.active .chip-count {
  background: var(--chip-color, #63e2b7);
  color: #16213e;
  font-weight: 700;
}

.filter-chip-sm {
  padding: 2px 10px;
  font-size: 12px;
  border-radius: 12px;
}

.filter-chip-user {
  gap: 4px;
}

.chip-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  font-size: 9px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.12);
  flex-shrink: 0;
}

.filter-chip-user.active .chip-avatar {
  background: var(--chip-color, #63e2b7);
  color: #16213e;
}

/* ── 列表过渡动画 ──────────────── */
.list-move,
.list-enter-active,
.list-leave-active {
  transition: all 0.25s ease;
}
.list-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}
.list-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
.list-leave-active {
  position: absolute;
  width: 100%;
}
</style>
