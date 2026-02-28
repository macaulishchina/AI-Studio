<template>
  <n-space vertical :size="16">
    <!-- 模型能力管理 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>📊 模型能力管理</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-space :size="6">
          <n-popconfirm v-if="editMode" @positive-click="resetAllCapabilities">
            <template #trigger>
              <n-button size="tiny" type="warning" ghost>🔄 清除能力覆盖</n-button>
            </template>
            确定要清除所有手动覆盖的能力设置吗？将恢复为自动检测值。
          </n-popconfirm>
          <n-popconfirm @positive-click="() => { void restoreAllOverrides() }">
            <template #trigger>
              <n-button size="tiny" type="error" ghost :loading="restoringOverrides" :disabled="restoringOverrides">♻ 恢复接口值</n-button>
            </template>
            将清空所有能力/定价覆盖并恢复为接口与内置默认值，确认继续？
          </n-popconfirm>
        </n-space>
      </template>

      <n-text depth="3" style="font-size: 11px; display: block; margin-bottom: 8px">
        模型的上下文窗口和能力数据。
        <template v-if="!editMode">点击筛选栏「✏️ 编辑」进入编辑模式。</template>
        <template v-else>点击数值可编辑, 点击能力开关可切换。修改会即时持久化到数据库。</template>
      </n-text>

      <!-- 数据维护操作区 -->
      <div style="display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap">
        <!-- Copilot 定价刷新 -->
        <div :style="isMobile
          ? 'flex: 1; min-width: 0; padding: 8px 12px; background: rgba(64, 152, 252, 0.06); border: 1px solid rgba(64, 152, 252, 0.15); border-radius: 6px'
          : 'flex: 1; min-width: 280px; padding: 8px 12px; background: rgba(64, 152, 252, 0.06); border: 1px solid rgba(64, 152, 252, 0.15); border-radius: 6px'">
          <div :style="isMobile
            ? 'display:flex;flex-direction:column;gap:8px;align-items:flex-start'
            : 'display:flex;align-items:center;justify-content:space-between;gap:8px'">
            <n-space vertical :size="2">
              <n-text style="font-size: 12px; font-weight: 500">💰 Copilot 定价</n-text>
              <n-text depth="3" style="font-size: 10px">从
                <n-button text tag="a" href="https://docs.github.com/en/copilot/concepts/billing/copilot-requests#model-multipliers" target="_blank" size="tiny" type="info" style="font-size: 10px">
                  官方文档
                </n-button>
                同步倍率，仅影响 Copilot 来源模型
              </n-text>
              <n-text v-if="studioConfig.pricingSyncedAt" depth="3" style="font-size: 10px">
                最近刷新：{{ formatSyncTime(studioConfig.pricingSyncedAt) }}
              </n-text>
            </n-space>
            <n-button size="tiny" type="primary" ghost @click="handleRefreshPricing" :loading="loadingPricing" :block="isMobile">
              🔄 刷新定价
            </n-button>
          </div>
        </div>
        <!-- 全局 Token 上限校准 -->
        <div :style="isMobile
          ? 'flex: 1; min-width: 0; padding: 8px 12px; background: rgba(24, 160, 88, 0.06); border: 1px solid rgba(24, 160, 88, 0.15); border-radius: 6px'
          : 'flex: 1; min-width: 280px; padding: 8px 12px; background: rgba(24, 160, 88, 0.06); border: 1px solid rgba(24, 160, 88, 0.15); border-radius: 6px'">
          <div :style="isMobile
            ? 'display:flex;flex-direction:column;gap:8px;align-items:flex-start'
            : 'display:flex;align-items:center;justify-content:space-between;gap:8px'">
            <n-space vertical :size="2">
              <n-text style="font-size: 12px; font-weight: 500">🧠 模型能力校准</n-text>
              <n-space align="center" :size="6" :wrap="true">
                <n-text depth="3" style="font-size: 10px">联网校准 Token 上限 + 内置知识库校准视觉/工具/推理能力</n-text>
                <n-button
                  text
                  tag="a"
                  href="https://docs.github.com/en/rest/models?apiVersion=2022-11-28"
                  target="_blank"
                  size="tiny"
                  type="info"
                  style="font-size: 10px"
                >
                  GitHub Models API
                </n-button>
                <n-button
                  text
                  tag="a"
                  href="https://docs.github.com/en/copilot/concepts/billing/copilot-requests#model-multipliers"
                  target="_blank"
                  size="tiny"
                  type="info"
                  style="font-size: 10px"
                >
                  Copilot 模型文档
                </n-button>
                <n-popover trigger="click" placement="bottom-start" style="max-width: 520px">
                  <template #trigger>
                    <n-tag size="tiny" type="info" :bordered="false" round style="cursor: help">依据与方法</n-tag>
                  </template>
                  <n-space vertical :size="6">
                    <n-text strong style="font-size: 12px">校准依据</n-text>
                    <n-ul style="margin: 0; padding-left: 16px; font-size: 12px">
                      <n-li>Token 上限：GitHub Models 官方 /models + Copilot 官方 /models 元数据（在线抓取）</n-li>
                      <n-li>视觉/工具/推理：内置能力映射知识库（可人工覆盖）</n-li>
                    </n-ul>
                    <n-text strong style="font-size: 12px">校准方法</n-text>
                    <n-ul style="margin: 0; padding-left: 16px; font-size: 12px">
                      <n-li>按模型 ID 精确匹配，失败时使用前缀匹配（兼容日期后缀）</n-li>
                      <n-li>仅在检测到差异时写入覆盖，避免无意义更新</n-li>
                      <n-li>覆盖值立即生效，可在编辑模式中单模型回滚</n-li>
                    </n-ul>
                  </n-space>
                </n-popover>
              </n-space>
              <n-text v-if="studioConfig.capabilityCalibratedAt" depth="3" style="font-size: 10px">
                最近校准：{{ formatSyncTime(studioConfig.capabilityCalibratedAt) }}
              </n-text>
            </n-space>
            <n-button size="tiny" type="info" ghost @click="handleRefreshTokenLimits" :loading="loadingTokenLimits" :block="isMobile">
              🧠 校准
            </n-button>
          </div>
        </div>
      </div>

      <template v-if="isMobile">
        <div style="margin-bottom: 10px; padding: 10px; border-radius: 8px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06)">
          <n-space vertical :size="8">
            <n-input
              v-model:value="capSearch"
              placeholder="搜索模型名..."
              size="small"
              clearable
            />

            <n-grid :cols="2" :x-gap="8" :y-gap="8">
              <n-gi>
                <n-select
                  v-model:value="capSourceFilter"
                  :options="sourceFilterOptions"
                  :render-label="renderSourceLabel"
                  size="small"
                  placeholder="来源"
                />
              </n-gi>
              <n-gi>
                <n-select
                  v-model:value="capCompanyFilter"
                  :options="companyFilterOptions"
                  :render-label="renderCompanyLabel"
                  size="small"
                  placeholder="厂商"
                />
              </n-gi>
              <n-gi :span="2">
                <n-select
                  v-model:value="capPricingFilter"
                  :options="pricingFilterOptions"
                  size="small"
                  placeholder="定价"
                />
              </n-gi>
            </n-grid>

            <n-space :size="8" :wrap="false">
              <n-button size="small" secondary block @click="fetchMergedCapabilities" :loading="loadingMerged">
                刷新
              </n-button>
              <n-button size="small" :type="editMode ? 'primary' : 'default'" block @click="editMode = !editMode">
                {{ editMode ? '完成' : '编辑' }}
              </n-button>
            </n-space>
          </n-space>
        </div>
      </template>
      <template v-else>
        <n-space align="center" style="margin-bottom: 8px" :size="8" :wrap="true">
          <n-input
            v-model:value="capSearch"
            placeholder="搜索模型名..."
            size="small" :style="controlStyle(200)" clearable
          />
          <n-select
            v-model:value="capSourceFilter"
            :options="sourceFilterOptions"
            :render-label="renderSourceLabel"
            size="small" :style="controlStyle(160)" placeholder="来源"
          />
          <n-select
            v-model:value="capCompanyFilter"
            :options="companyFilterOptions"
            :render-label="renderCompanyLabel"
            size="small" :style="controlStyle(140)" placeholder="厂商"
          />
          <n-select
            v-model:value="capPricingFilter"
            :options="pricingFilterOptions"
            size="small" :style="controlStyle(130)" placeholder="定价"
          />
          <n-button size="small" @click="fetchMergedCapabilities" :loading="loadingMerged">
            🔄 刷新
          </n-button>
          <n-button size="small" :type="editMode ? 'primary' : 'default'" @click="editMode = !editMode">
            {{ editMode ? '✅ 完成' : '✏️ 编辑' }}
          </n-button>
        </n-space>
      </template>

      <n-spin :show="loadingMerged">
        <n-data-table
          v-if="filteredMerged.length"
          :columns="mergedColumns"
          :data="filteredMerged"
          size="small"
          :max-height="500"
          :scroll-x="editMode ? 1050 : 950"

        />
        <n-empty v-else description="加载中..." />
      </n-spin>
    </n-card>

    <!-- 定价变化确认对话框 -->
    <n-modal v-model:show="showPricingDiffModal" preset="card" title="📊 定价变化确认" style="width: 850px; max-width: 95vw">
      <n-alert v-if="pricingDiff.length === 0" type="success" :bordered="false">
        ✅ 定价已是最新，与 GitHub 官方文档一致，无需更新。
      </n-alert>
      <template v-else>
        <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 12px">
          从 GitHub 官方文档检测到以下定价变化，确认后将更新运行时定价表 (重启后恢复为代码默认值)：
        </n-text>
        <n-data-table
          :columns="pricingDiffColumns"
          :data="pricingDiff"
          size="small"
          :max-height="400"
        />
        <n-space justify="end" style="margin-top: 12px">
          <n-button size="small" @click="showPricingDiffModal = false">取消</n-button>
          <n-button size="small" type="primary" @click="applyPricingChanges" :loading="applyingPricing">
            确认应用 ({{ pricingDiff.length }} 项变更)
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, h, onMounted, onUnmounted } from 'vue'
import { useMessage, NInputNumber, NTag, NText, NButton, NSwitch, NSpace, NTooltip } from 'naive-ui'
import { modelApi, modelConfigApi } from '@/api'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { getProviderIcon } from '@/utils/providerIcons'

const message = useMessage()
const studioConfig = useStudioConfigStore()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

function controlStyle(pcWidth: number) {
  if (isMobile.value) return { width: '100%' }
  return { width: `${pcWidth}px` }
}

// ==================== 编辑模式 ====================
const editMode = ref(false)

// ==================== 模型能力管理 ====================
const mergedData = ref<any[]>([])
const loadingMerged = ref(false)
const capSearch = ref('')
const capSourceFilter = ref('')
const capCompanyFilter = ref('')
const capPricingFilter = ref('')
const docModelSet = ref<Set<string>>(new Set())

// === Token 格式化 ===
function fmtTokens(n: number | null | undefined): string {
  if (!n) return '-'
  if (n >= 1000000) {
    const v = n / 1000000
    return v === Math.floor(v) ? `${v}M` : `${v.toFixed(1)}M`
  }
  if (n >= 1000) {
    const v = n / 1000
    return v === Math.floor(v) ? `${v}K` : `${v.toFixed(1)}K`
  }
  return `${n}`
}

// === 来源 (provider_slug) ===
function getModelSlug(m: any): string {
  return m.provider_slug || (m.api_backend === 'copilot' ? 'copilot' : 'github')
}

function providerDisplayName(slug: string): string {
  if (slug === 'copilot') return 'Copilot'
  if (slug === 'github') return 'GitHub'
  return slug.charAt(0).toUpperCase() + slug.slice(1)
}

const sourceFilterOptions = computed(() => {
  const slugMap = new Map<string, string>()
  for (const m of mergedData.value) {
    const slug = getModelSlug(m)
    if (!slugMap.has(slug)) {
      slugMap.set(slug, providerDisplayName(slug))
    }
  }
  const opts: { label: string; value: string; slug?: string }[] = [{ label: '全部来源', value: '' }]
  const builtinOrder = ['copilot', 'github']
  for (const s of builtinOrder) {
    if (slugMap.has(s)) {
      opts.push({ label: slugMap.get(s)!, value: s, slug: s })
      slugMap.delete(s)
    }
  }
  for (const [slug, label] of [...slugMap.entries()].sort((a, b) => a[1].localeCompare(b[1]))) {
    opts.push({ label, value: slug, slug })
  }
  return opts
})

function renderSourceLabel(option: any, selected: boolean) {
  if (!option.slug) {
    return h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [option.label as string])
  }
  const iconHtml = getProviderIcon(option.slug, option.label, 14)
  return h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [
    h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
    option.label as string,
  ])
}

// === 厂商 (publisher) ===
const COMPANY_ICON_SLUG: Record<string, string> = {
  'OpenAI': 'openai',
  'Anthropic': 'anthropic',
  'Google': 'google',
  'DeepSeek': 'deepseek',
  'Mistral AI': 'mistralai',
  'Meta': 'meta',
  'Microsoft': 'microsoft',
  'xAI': 'xai',
  'Cohere': 'cohere',
  'AI21 Labs': 'ai21',
  'Qwen': 'qwen',
}

const companyFilterOptions = computed(() => {
  const companies = new Set<string>()
  for (const m of mergedData.value) {
    if (m.publisher) companies.add(m.publisher)
  }
  const opts: { label: string; value: string }[] = [{ label: '全部厂商', value: '' }]
  for (const c of [...companies].sort()) {
    opts.push({ label: c, value: c })
  }
  return opts
})

function renderCompanyLabel(option: any, selected: boolean) {
  if (!option.value) {
    return h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [option.label as string])
  }
  const slug = COMPANY_ICON_SLUG[option.label] || option.label.toLowerCase().replace(/\s+/g, '')
  const iconHtml = getProviderIcon(slug, option.label, 14)
  return h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [
    h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
    option.label as string,
  ])
}

function formatSyncTime(iso: string): string {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

// === 定价 (动态) ===
const pricingFilterOptions = computed(() => {
  const multipliers = new Set<number>()
  for (const m of mergedData.value) {
    if (m.premium_multiplier != null) multipliers.add(m.premium_multiplier)
  }
  const opts: { label: string; value: string }[] = [{ label: '全部定价', value: '' }]
  if (multipliers.has(0)) {
    opts.push({ label: '🆓 x0 (免费)', value: '0' })
    multipliers.delete(0)
  }
  const sorted = [...multipliers].sort((a, b) => a - b)
  for (const v of sorted) {
    opts.push({ label: `💰 x${v}`, value: String(v) })
  }
  return opts
})

// === 过滤 ===
const filteredMerged = computed(() => {
  let list = mergedData.value

  if (studioConfig.docModelsOnly && docModelSet.value.size > 0) {
    list = list.filter((m: any) => {
      const slug = getModelSlug(m)
      return slug !== 'copilot' ||
        docModelSet.value.has(String(m.id || m.name).replace(/^copilot:/, '').toLowerCase())
    })
  }

  if (capSourceFilter.value) {
    list = list.filter((m: any) => getModelSlug(m) === capSourceFilter.value)
  }

  if (capCompanyFilter.value) {
    list = list.filter((m: any) => m.publisher === capCompanyFilter.value)
  }

  if (capPricingFilter.value) {
    const target = parseFloat(capPricingFilter.value)
    list = list.filter((m: any) => m.premium_multiplier === target)
  }

  if (capSearch.value) {
    const q = capSearch.value.toLowerCase()
    list = list.filter((m: any) =>
      m.name.toLowerCase().includes(q) ||
      m.id.toLowerCase().includes(q) ||
      (m.publisher || '').toLowerCase().includes(q)
    )
  }
  return list
})

async function fetchMergedCapabilities() {
  loadingMerged.value = true
  try {
    const { data } = await modelConfigApi.getMerged()
    mergedData.value = data
  } catch {}
  finally { loadingMerged.value = false }
}

async function updateCapOverride(row: any, field: string, val: any) {
  try {
    await modelConfigApi.upsertCapability(row.id, { [field]: val })
    await fetchMergedCapabilities()
  } catch (e: any) {
    message.error('更新失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function resetSingleCapability(row: any) {
  const clean = row.id.replace(/^copilot:/, '').toLowerCase()
  try {
    await modelConfigApi.deleteCapability(clean)
    await fetchMergedCapabilities()
    message.success(`已重置 ${row.name}`)
  } catch (e: any) {
    if (e.response?.status === 404) {
      message.info('该模型没有覆盖记录')
    } else {
      message.error('重置失败')
    }
  }
}

async function resetAllCapabilities() {
  try {
    await modelConfigApi.resetAllCapabilities()
    await fetchMergedCapabilities()
    message.success('所有能力覆盖已清除')
  } catch (e: any) {
    message.error('重置失败')
  }
}

async function restoreAllOverrides() {
  if (restoringOverrides.value) return
  restoringOverrides.value = true
  try {
    await modelApi.resetAllOverrides()
    studioConfig.clearModelSyncMarks()
    await fetchMergedCapabilities()
    message.success('已恢复默认接口值（覆盖已清空）')
  } catch (e: any) {
    message.error('恢复失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    restoringOverrides.value = false
  }
}



// ==================== 定价刷新 ====================
const loadingPricing = ref(false)
const applyingPricing = ref(false)
const loadingTokenLimits = ref(false)
const restoringOverrides = ref(false)
const showPricingDiffModal = ref(false)
const pricingDiff = ref<any[]>([])
const scrapedPricing = ref<Record<string, any>>({})

async function handleRefreshTokenLimits() {
  loadingTokenLimits.value = true
  try {
    const { data } = await modelApi.refreshTokenLimits()
    const parts: string[] = []
    if (data.updated_count > 0) parts.push(`Token 上限 ${data.updated_count} 个`)
    if (data.cap_updated > 0) parts.push(`能力 ${data.cap_updated} 个`)
    if (parts.length > 0) {
      message.success(`已校准: ${parts.join(', ')}`)
    } else {
      message.success('模型能力已是最新')
    }
    studioConfig.setCapabilityCalibration((data.matched_models || []).map((m: string) => m.toLowerCase()))
    await fetchMergedCapabilities()
  } catch (e: any) {
    message.error('校准失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loadingTokenLimits.value = false
  }
}

async function handleRefreshPricing() {
  loadingPricing.value = true
  try {
    const { data } = await modelApi.refreshPricing()
    pricingDiff.value = data.changes || []
    scrapedPricing.value = data.scraped || {}
    const docIds = Object.keys(scrapedPricing.value || {}).map((k: string) => k.toLowerCase())
    docModelSet.value = new Set(docIds)
    studioConfig.setDocModels(docIds)
    studioConfig.setPricingSync(docIds)
    showPricingDiffModal.value = true
    if (pricingDiff.value.length === 0) {
      message.success(`定价已是最新 (共 ${data.scraped_count} 个模型)`)
    } else {
      message.info(`检测到 ${pricingDiff.value.length} 项定价变化`)
    }
  } catch (e: any) {
    message.error('刷新定价失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loadingPricing.value = false
  }
}

async function applyPricingChanges() {
  applyingPricing.value = true
  try {
    await modelApi.applyPricing(scrapedPricing.value)
    showPricingDiffModal.value = false
    message.success('定价表已更新，正在刷新模型数据...')
    await fetchMergedCapabilities()
  } catch (e: any) {
    message.error('应用定价失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    applyingPricing.value = false
  }
}

const pricingDiffColumns = [
  {
    title: '模型',
    key: 'model',
    width: 180,
    ellipsis: { tooltip: true },
    render(row: any) {
      return h(NText, { style: 'font-size:12px;font-family:monospace' }, () => row.model)
    },
  },
  {
    title: '类型',
    key: 'type',
    width: 70,
    render(row: any) {
      const map: Record<string, { type: string; label: string }> = {
        changed: { type: 'warning', label: '变更' },
        added: { type: 'success', label: '新增' },
        removed: { type: 'error', label: '移除' },
      }
      const m = map[row.type] || { type: 'default', label: row.type }
      return h(NTag, { size: 'tiny', type: m.type as any, bordered: false }, () => m.label)
    },
  },
  {
    title: '付费(旧)',
    key: 'old_paid',
    width: 75,
    render(row: any) {
      if (row.old_paid == null) return h(NText, { depth: 3 }, () => '-')
      const color = row.old_paid === 0 ? '#18a058' : '#f0a020'
      return h(NText, { style: `color:${color};font-weight:bold` }, () => `x${row.old_paid}`)
    },
  },
  {
    title: '→',
    key: 'arrow1',
    width: 25,
    render() { return h(NText, { depth: 3 }, () => '→') },
  },
  {
    title: '付费(新)',
    key: 'new_paid',
    width: 75,
    render(row: any) {
      if (row.new_paid == null) return h(NText, { depth: 3 }, () => '-')
      const color = row.new_paid === 0 ? '#18a058' : '#f0a020'
      return h(NText, { style: `color:${color};font-weight:bold` }, () => `x${row.new_paid}`)
    },
  },
  {
    title: '免费(旧)',
    key: 'old_free',
    width: 75,
    render(row: any) {
      if (row.old_free == null) return h(NTag, { size: 'tiny', type: 'error', bordered: false }, () => '需订阅')
      return h(NTag, { size: 'tiny', type: 'success', bordered: false }, () => `x${row.old_free}`)
    },
  },
  {
    title: '→',
    key: 'arrow2',
    width: 25,
    render() { return h(NText, { depth: 3 }, () => '→') },
  },
  {
    title: '免费(新)',
    key: 'new_free',
    width: 75,
    render(row: any) {
      if (row.new_free == null) return h(NTag, { size: 'tiny', type: 'error', bordered: false }, () => '需订阅')
      return h(NTag, { size: 'tiny', type: 'success', bordered: false }, () => `x${row.new_free}`)
    },
  },
  {
    title: '说明',
    key: 'note',
    ellipsis: { tooltip: true },
    render(row: any) {
      return h(NText, { depth: 3, style: 'font-size:11px' }, () => row.note)
    },
  },
]

// ==================== 表格列定义 (响应编辑模式) ====================
const mergedColumns = computed(() => {
  const modelKey = (row: any) => String(row.id || '').replace(/^copilot:/, '').toLowerCase()
  const toneByState = (state: 'default' | 'pricing' | 'capability' | 'override') => {
    if (state === 'override') return '#f0a020'
    if (state === 'capability') return '#2080f0'
    if (state === 'pricing') return '#36ad6a'
    return '#8a93a6'
  }
  const labelByState = (state: 'default' | 'pricing' | 'capability' | 'override') => {
    if (state === 'override') return '手动覆盖'
    if (state === 'capability') return '能力校准'
    if (state === 'pricing') return '定价刷新'
    return ''
  }
  const stateForField = (row: any, overrideField?: string, preferPricing = false): 'default' | 'pricing' | 'capability' | 'override' => {
    if (overrideField && row[overrideField] !== null && row[overrideField] !== undefined) return 'override'
    const key = modelKey(row)
    if (preferPricing && studioConfig.isPricingSyncedModel(key)) return 'pricing'
    if (studioConfig.isCapabilityCalibratedModel(key)) return 'capability'
    return 'default'
  }
  const renderStateDot = (state: 'default' | 'pricing' | 'capability' | 'override') => {
    if (state === 'default') return null
    const color = toneByState(state)
    const label = labelByState(state)
    return h(NTooltip, { trigger: 'hover' }, {
      trigger: () => h('span', {
        style: `display:inline-block;width:6px;height:6px;border-radius:999px;background:${color};margin-left:6px;vertical-align:middle;opacity:.95`,
      }),
      default: () => label,
    })
  }

  const cols: any[] = [
    {
      title: '模型',
      key: 'name',
      width: 180,
      fixed: 'left' as const,
      ellipsis: { tooltip: true },
      render(row: any) {
        const slug = getModelSlug(row)
        const iconHtml = getProviderIcon(slug, '', 12)
        return h('span', { style: 'display:inline-flex;align-items:center;gap:3px;font-size:12px' }, [
          h(NText, null, () => row.name),
          h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
        ])
      },
    },
    {
      title: '来源',
      key: 'provider_slug',
      width: 100,
      render(row: any) {
        const slug = getModelSlug(row)
        const label = providerDisplayName(slug)
        const iconHtml = getProviderIcon(slug, '', 12)
        return h('span', { style: 'display:inline-flex;align-items:center;gap:3px;font-size:11px' }, [
          h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
          h(NText, { depth: 3 }, () => label),
        ])
      },
    },
    {
      title: '厂商',
      key: 'publisher',
      width: 90,
      render(row: any) {
        if (!row.publisher) return h(NText, { depth: 3, style: 'font-size:11px' }, () => '-')
        const slug = COMPANY_ICON_SLUG[row.publisher] || row.publisher.toLowerCase().replace(/\s+/g, '')
        const iconHtml = getProviderIcon(slug, row.publisher, 12)
        return h('span', { style: 'display:inline-flex;align-items:center;gap:3px;font-size:11px' }, [
          h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
          h(NText, { depth: 3 }, () => row.publisher),
        ])
      },
    },
  ]

  // 输入窗口
  if (editMode.value) {
    cols.push({
      title: '输入窗口',
      key: 'eff_max_input',
      width: 120,
      sorter: (a: any, b: any) => (a.eff_max_input || 0) - (b.eff_max_input || 0),
      render(row: any) {
        return h(NInputNumber, {
          value: row.eff_max_input,
          size: 'tiny',
          min: 0,
          step: 1000,
          style: 'width:100px',
          'onUpdate:value': (val: number | null) => {
            if (val != null) updateCapOverride(row, 'max_input_tokens', val)
          },
        })
      },
    })
  } else {
    cols.push({
      title: '输入窗口',
      key: 'eff_max_input',
      width: 80,
      sorter: (a: any, b: any) => (a.eff_max_input || 0) - (b.eff_max_input || 0),
      render(row: any) {
        const state = stateForField(row, 'override_max_input', false)
        return h('span', { style: 'display:inline-flex;align-items:center' }, [
          h(NText, { style: 'font-size:12px;font-variant-numeric:tabular-nums' }, () => fmtTokens(row.eff_max_input)),
          renderStateDot(state),
        ])
      },
    })
  }

  // 输出
  if (editMode.value) {
    cols.push({
      title: '输出',
      key: 'eff_max_output',
      width: 120,
      sorter: (a: any, b: any) => (a.eff_max_output || 0) - (b.eff_max_output || 0),
      render(row: any) {
        return h(NInputNumber, {
          value: row.eff_max_output,
          size: 'tiny',
          min: 0,
          step: 100,
          style: 'width:100px',
          'onUpdate:value': (val: number | null) => {
            if (val != null) updateCapOverride(row, 'max_output_tokens', val)
          },
        })
      },
    })
  } else {
    cols.push({
      title: '输出',
      key: 'eff_max_output',
      width: 70,
      sorter: (a: any, b: any) => (a.eff_max_output || 0) - (b.eff_max_output || 0),
      render(row: any) {
        const state = stateForField(row, 'override_max_output', false)
        return h('span', { style: 'display:inline-flex;align-items:center' }, [
          h(NText, { style: 'font-size:12px;font-variant-numeric:tabular-nums' }, () => fmtTokens(row.eff_max_output)),
          renderStateDot(state),
        ])
      },
    })
  }

  // 定价
  cols.push({
    title: '定价',
    key: 'premium_multiplier',
    width: 80,
    sorter: (a: any, b: any) => (a.premium_multiplier ?? 0) - (b.premium_multiplier ?? 0),
    render(row: any) {
      const state = stateForField(row, undefined, true)
      const color = row.premium_multiplier === 0 ? '#18a058' : '#f0a020'
      const text = row.premium_multiplier === 0 ? 'x0' : `x${row.premium_multiplier}`
      return h('span', { style: 'display:inline-flex;align-items:center' }, [
        h(NTag, { size: 'tiny', bordered: false, style: `color:${color};font-weight:600` }, () => text),
        renderStateDot(state),
      ])
    },
  })

  // 视觉
  cols.push({
    title: '👁️ 视觉',
    key: 'eff_supports_vision',
    width: 65,
    render(row: any) {
      if (editMode.value) {
        return h(NSwitch, {
          size: 'small',
          value: row.eff_supports_vision,
          'onUpdate:value': (val: boolean) => updateCapOverride(row, 'supports_vision', val),
        })
      }
      const state = stateForField(row, 'override_supports_vision', false)
      return h('span', { style: 'display:inline-flex;align-items:center' }, [
        h(NText, { style: 'font-size:14px;color:#cfd6e4' }, () => row.eff_supports_vision ? '✅' : '—'),
        renderStateDot(state),
      ])
    },
  })

  // 工具
  cols.push({
    title: '🔧 工具',
    key: 'eff_supports_tools',
    width: 65,
    render(row: any) {
      if (editMode.value) {
        return h(NSwitch, {
          size: 'small',
          value: row.eff_supports_tools,
          'onUpdate:value': (val: boolean) => updateCapOverride(row, 'supports_tools', val),
        })
      }
      const state = stateForField(row, 'override_supports_tools', false)
      return h('span', { style: 'display:inline-flex;align-items:center' }, [
        h(NText, { style: 'font-size:14px;color:#cfd6e4' }, () => row.eff_supports_tools ? '✅' : '—'),
        renderStateDot(state),
      ])
    },
  })

  // 推理
  cols.push({
    title: '🧠 推理',
    key: 'eff_is_reasoning',
    width: 65,
    render(row: any) {
      if (editMode.value) {
        return h(NSwitch, {
          size: 'small',
          value: row.eff_is_reasoning,
          'onUpdate:value': (val: boolean) => updateCapOverride(row, 'is_reasoning', val),
        })
      }
      const state = stateForField(row, 'override_is_reasoning', false)
      return h('span', { style: 'display:inline-flex;align-items:center' }, [
        h(NText, { style: 'font-size:14px;color:#cfd6e4' }, () => row.eff_is_reasoning ? '✅' : '—'),
        renderStateDot(state),
      ])
    },
  })

  // 重置列 (仅编辑模式)
  if (editMode.value) {
    cols.push({
      title: '↩️',
      key: 'actions',
      width: 50,
      renderTitle() {
        return h(NTooltip, { trigger: 'hover' }, {
          trigger: () => h('span', { style: 'cursor: help' }, '↩️'),
          default: () => '重置为自动检测值 (仅对有手动覆盖的行可用)',
        })
      },
      render(row: any) {
        if (!row.has_override) return null
        return h(NTooltip, { trigger: 'hover' }, {
          trigger: () => h(NButton, {
            size: 'tiny',
            quaternary: true,
            onClick: () => resetSingleCapability(row),
          }, () => '↩️'),
          default: () => `重置 ${row.name} 的覆盖`,
        })
      },
    })
  }

  return cols
})

onMounted(() => {
  docModelSet.value = new Set((studioConfig.docModelIds || []).map((k: string) => k.toLowerCase()))
  fetchMergedCapabilities()
})
</script>


