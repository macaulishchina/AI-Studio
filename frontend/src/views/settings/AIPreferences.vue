<template>
  <n-space vertical :size="16">
    <n-alert type="info" :bordered="false">
      配置 AI 模型的全局使用偏好，影响所有讨论和实施面板中的模型选择和行为。
    </n-alert>

    <!-- 模型筛选 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🎯 模型筛选</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-space align="center">
          <n-switch v-model:value="studioConfig.freeModelsOnly" />
          <n-text>仅使用免费模型</n-text>
          <n-text depth="3" style="font-size: 11px">开启后只显示 x0 的免费模型，不消耗高级请求额度</n-text>
        </n-space>

        <n-space align="center">
          <n-switch v-model:value="studioConfig.docModelsOnly" />
          <n-text>只用官方推荐模型</n-text>
          <n-text depth="3" style="font-size: 11px">仅影响 Copilot 来源的模型过滤，开启后只显示 GitHub 官方文档中列出的 Copilot 模型，不影响其他来源</n-text>
        </n-space>
      </n-space>
    </n-card>

    <!-- 工具调用轮次 & AI 行为 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🔧 工具调用 & AI 行为</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-text depth="3" style="font-size: 11px">
          工具轮次 = AI 可查看代码的次数。免费模型多次调用不影响额度，付费模型每次都消耗高级请求。
        </n-text>
        <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" bordered size="small">
          <n-descriptions-item label="免费模型工具轮次">
            <n-input-number
              v-model:value="studioConfig.freeToolRounds"
              :min="1" :max="100" size="small" style="width: 100px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="付费模型工具轮次">
            <n-input-number
              v-model:value="studioConfig.paidToolRounds"
              :min="1" :max="50" size="small" style="width: 100px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="截断自动继续次数">
            <n-space align="center" :size="4">
              <n-input-number
                v-model:value="studioConfig.maxAutoContinues"
                :min="0" :max="10" size="small" style="width: 100px"
              />
              <n-text depth="3" style="font-size: 10px">AI 输出因 token 截断时自动继续的最大次数 (0=关闭)</n-text>
            </n-space>
          </n-descriptions-item>
        </n-descriptions>
      </n-space>
    </n-card>

    <!-- 聊天默认模型 & 白名单 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>💬 聊天模型默认值</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-button size="tiny" type="primary" ghost :loading="savingChatSettings" @click="saveChatSettings">
          💾 保存
        </n-button>
      </template>
      <n-space vertical :size="12">
        <n-space align="center" :size="8">
          <n-text depth="3" style="font-size: 11px">模型来源：</n-text>
          <n-dropdown
            :options="sourceFilterDropdownOptions"
            trigger="click"
            @select="onSourceFilterChange"
          >
            <n-button size="tiny" quaternary>
              {{ sourceFilterLabel }} <n-icon size="12" style="margin-left:2px"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg></n-icon>
            </n-button>
          </n-dropdown>
        </n-space>
        <n-text depth="3" style="font-size: 11px">
          聊天模型白名单：限制在聊天面板中可选的模型 (留空 = 不限制, 显示全部)。
        </n-text>
        <n-select
          v-model:value="chatAllowlist"
          multiple
          filterable
          clearable
          :options="allChatModelOptions"
          :render-label="renderChatModelLabel"
          :loading="loadingModels"
          placeholder="留空 = 不限制，显示全部可用模型"
          size="small"
          :max-tag-count="6"
          style="width: 100%"
        />
        <n-text depth="3" style="font-size: 11px">
          全局默认聊天模型：项目/对话未指定模型时使用此默认值。从白名单中选择 (留空 = gpt-4o)。
        </n-text>
        <n-select
          v-model:value="chatDefaultModel"
          filterable
          clearable
          :options="chatDefaultOptions"
          :render-label="renderChatModelLabel"
          :loading="loadingModels"
          placeholder="留空 = gpt-4o"
          size="small"
          style="width: 100%"
        />
      </n-space>
    </n-card>

    <!-- STT 配置 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🎤 语音转文字 (STT)</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-space :size="6">
          <n-tag v-if="sttConfigured" size="small" type="success" :bordered="false">已配置</n-tag>
          <n-tag v-else size="small" type="warning" :bordered="false">未配置</n-tag>
          <n-button size="tiny" type="primary" ghost :loading="savingSttSettings" @click="saveSttSettings">
            💾 保存
          </n-button>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-text depth="3" style="font-size: 11px">
          配置服务端 STT (语音转文字) 服务。支持 OpenAI-compatible API (Whisper / Groq / faster-whisper-server 等)。
          浏览器端 STT 不需要配置。
        </n-text>
        <n-descriptions :column="1" label-placement="left" bordered size="small">
          <n-descriptions-item label="STT API 地址">
            <n-input
              v-model:value="sttApiBase"
              placeholder="如 http://localhost:8080 或 https://api.openai.com"
              size="small" style="width: 320px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="STT API Key">
            <n-input
              v-model:value="sttApiKey"
              type="password"
              show-password-on="click"
              :placeholder="sttApiKeyConfigured ? '已配置 (留空保持不变)' : '留空=无认证'"
              size="small" style="width: 320px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="默认 STT 模型">
            <n-select
              v-model:value="sttDefaultModel"
              filterable
              clearable
              :options="sttDefaultOptions"
              :loading="loadingSttModels"
              placeholder="如 whisper-1, whisper-large-v3-turbo"
              size="small" style="width: 320px"
            />
          </n-descriptions-item>
        </n-descriptions>
        <n-text depth="3" style="font-size: 11px">
          STT 模型白名单 (留空 = 使用默认列表)。从可用模型中选择。
        </n-text>
        <n-select
          v-model:value="sttAllowlist"
          multiple
          filterable
          clearable
          :options="allSttModelOptions"
          :loading="loadingSttModels"
          placeholder="留空 = 使用默认列表"
          size="small"
          :max-tag-count="6"
          style="width: 100%"
        />
      </n-space>
    </n-card>

    <!-- RAG 索引配置 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>📚 RAG 索引</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-button size="tiny" type="primary" ghost :loading="savingRagConfig" @click="saveRagConfig">
          💾 保存
        </n-button>
      </template>
      <n-space vertical :size="12">
        <n-text depth="3" style="font-size: 11px">
          RAG 后台索引器会定期扫描工作区文件并生成向量索引，用于 AI 对话中的代码检索。
          间隔越长越节约 API 调用额度，但索引更新越慢。
        </n-text>
        <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" bordered size="small">
          <n-descriptions-item label="索引间隔 (秒)">
            <n-input-number
              v-model:value="ragConfig.rag_index_interval_seconds"
              :min="60" :max="86400" :step="300" size="small" style="width: 140px"
            />
            <n-text depth="3" style="font-size: 10px; margin-left: 4px">默认 3600 (1小时)</n-text>
          </n-descriptions-item>
          <n-descriptions-item label="Embedding 批大小">
            <n-input-number
              v-model:value="ragConfig.rag_embedding_batch_size"
              :min="1" :max="64" size="small" style="width: 100px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="批次间延迟 (秒)">
            <n-input-number
              v-model:value="ragConfig.rag_batch_delay_seconds"
              :min="0" :max="30" :step="0.5" size="small" style="width: 100px"
            />
            <n-text depth="3" style="font-size: 10px; margin-left: 4px">降低限流风险</n-text>
          </n-descriptions-item>
        </n-descriptions>
      </n-space>
    </n-card>

    <!-- 模型黑名单 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🚫 模型黑名单</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-text depth="3" style="font-size: 11px">
          匹配到关键词的模型不会出现在选择列表中 (模糊匹配，不区分大小写)
        </n-text>
        <n-space :size="4" :wrap="true">
          <n-tag
            v-for="item in studioConfig.modelBlacklist" :key="item"
            closable size="small" type="error"
            @close="studioConfig.removeFromBlacklist(item)"
          >
            {{ item }}
          </n-tag>
          <n-text v-if="!studioConfig.modelBlacklist.length" depth="3" style="font-size: 12px">
            暂无黑名单规则
          </n-text>
        </n-space>
        <n-space>
          <n-input
            v-model:value="blacklistInput"
            placeholder="输入模型名关键词..."
            size="small" style="width: 200px"
            @keydown.enter="addBlacklist"
          />
          <n-button size="small" @click="addBlacklist">添加</n-button>
        </n-space>
      </n-space>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { systemApi, modelApi, sttApi } from '@/api'
import { buildGroupedModelOptions, createModelLabelRenderer } from '@/utils/modelSelect'

const studioConfig = useStudioConfigStore()
const message = useMessage()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

const blacklistInput = ref('')
function addBlacklist() {
  if (blacklistInput.value.trim()) {
    studioConfig.addToBlacklist(blacklistInput.value)
    blacklistInput.value = ''
  }
}

// ── 模型设置 (服务端持久化) ──
const chatDefaultModel = ref<string | null>(null)
const chatAllowlist = ref<string[]>([])
const sttApiBase = ref('')
const sttApiKey = ref('')
const sttApiKeyConfigured = ref(false)
const sttDefaultModel = ref<string | null>(null)
const sttAllowlist = ref<string[]>([])
const savingChatSettings = ref(false)
const savingSttSettings = ref(false)
const savingRagConfig = ref(false)
const ragConfig = ref({ rag_index_interval_seconds: 3600, rag_embedding_batch_size: 8, rag_batch_delay_seconds: 2.0 })

// ── 模型列表 ──
const allModels = ref<any[]>([])
const sttModels = ref<any[]>([])
const loadingModels = ref(false)
const loadingSttModels = ref(false)

const sttConfigured = computed(() => !!sttApiBase.value)

// ── 模型来源过滤 ──
const chatSourceFilter = ref('all')

const sourceFilterOptions = computed(() => {
  const base: Array<{ label: string; key: string }> = [
    { label: '全部', key: 'all' },
    { label: 'GitHub (免费)', key: 'github' },
  ]
  if (allModels.value.some(m => m.api_backend === 'copilot')) {
    base.push({ label: 'Copilot (付费)', key: 'copilot' })
  }
  const seen = new Set<string>()
  for (const m of allModels.value) {
    const slug = m.provider_slug || ''
    if (slug && slug !== 'github' && slug !== 'copilot' && !seen.has(slug)) {
      seen.add(slug)
      base.push({ label: m.publisher || slug, key: slug })
    }
  }
  return base
})

const sourceFilterDropdownOptions = computed(() =>
  sourceFilterOptions.value.map(o => ({ label: o.label, key: o.key }))
)

const sourceFilterLabel = computed(() => {
  const opt = sourceFilterOptions.value.find(o => o.key === chatSourceFilter.value)
  return opt?.label || '全部'
})

function onSourceFilterChange(key: string) {
  chatSourceFilter.value = key
}

// ── 按来源过滤模型 ──
function filterBySource(models: any[]) {
  const source = chatSourceFilter.value
  if (source === 'all') return models
  if (source === 'copilot') return models.filter(m => m.provider_slug === 'copilot' || m.api_backend === 'copilot')
  if (source === 'github') return models.filter(m => m.provider_slug === 'github' || (!m.provider_slug && m.api_backend === 'models'))
  return models.filter(m => m.provider_slug === source)
}

// ── 聊天模型选项 (用于白名单多选：按来源过滤后的模型分组) ──
const allChatModelOptions = computed(() => {
  const byCategory = allModels.value.filter(m => m.category === 'discussion' || m.category === 'both')
  return buildGroupedOptions(filterBySource(byCategory))
})

// ── 聊天默认模型选项 (从白名单中选择；白名单空则从全部中选) ──
const chatDefaultOptions = computed(() => {
  const byCategory = allModels.value.filter(m => m.category === 'discussion' || m.category === 'both')
  const pool = chatAllowlist.value.length > 0
    ? byCategory.filter(m => chatAllowlist.value.includes(m.id))
    : byCategory
  return buildGroupedOptions(pool)
})

// ── STT 模型选项 ──
const allSttModelOptions = computed(() =>
  sttModels.value.map(m => ({ label: m.name || m.id, value: m.id }))
)

const sttDefaultOptions = computed(() => {
  if (sttAllowlist.value.length > 0) {
    return sttModels.value
      .filter(m => sttAllowlist.value.includes(m.id))
      .map(m => ({ label: m.name || m.id, value: m.id }))
  }
  return allSttModelOptions.value
})

const renderChatModelLabel = createModelLabelRenderer({
  isPricingSyncedModel: (modelId: string) => studioConfig.isPricingSyncedModel(modelId),
  isCapabilityCalibratedModel: (modelId: string) => studioConfig.isCapabilityCalibratedModel(modelId),
})

// ── 构建分组选项 ──
function buildGroupedOptions(modelList: any[]) {
  return buildGroupedModelOptions(modelList, {
    getEffectiveMaxInput: (modelId: string, rawMaxInput: number) =>
      studioConfig.getEffectiveMaxInput(modelId, rawMaxInput),
  })
}

onMounted(async () => {
  // 加载模型设置
  try {
    const { data } = await systemApi.getModelSettings()
    chatDefaultModel.value = data.chat_default_model || null
    chatAllowlist.value = data.chat_model_allowlist || []
    sttApiBase.value = data.stt_api_base || ''
    sttApiKeyConfigured.value = !!data.stt_api_key_configured
    sttDefaultModel.value = data.stt_default_model || null
    sttAllowlist.value = data.stt_model_allowlist || []
  } catch (e: any) {
    console.warn('加载模型设置失败:', e)
  }

  // 加载 RAG 配置
  try {
    const { data: ragData } = await systemApi.getRagConfig()
    ragConfig.value = { ...ragConfig.value, ...ragData }
  } catch (e: any) {
    console.warn('加载 RAG 配置失败:', e)
  }

  // 加载聊天模型列表
  loadingModels.value = true
  try {
    const { data } = await modelApi.list({ category: 'discussion', custom_models: false })
    allModels.value = data
  } catch (e: any) {
    console.warn('加载模型列表失败:', e)
  } finally {
    loadingModels.value = false
  }

  // 加载 STT 模型列表
  loadingSttModels.value = true
  try {
    const { data } = await sttApi.models()
    sttModels.value = data || []
  } catch (e: any) {
    console.warn('加载 STT 模型列表失败:', e)
  } finally {
    loadingSttModels.value = false
  }
})

async function saveChatSettings() {
  savingChatSettings.value = true
  try {
    const updates: Record<string, any> = {
      chat_default_model: chatDefaultModel.value || '',
      chat_model_allowlist: chatAllowlist.value,
    }
    await systemApi.setModelSettings(updates)
    message.success('聊天模型设置已保存')
  } catch (e: any) {
    message.error('保存失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    savingChatSettings.value = false
  }
}

async function saveRagConfig() {
  savingRagConfig.value = true
  try {
    const { data } = await systemApi.setRagConfig(ragConfig.value)
    ragConfig.value = { ...ragConfig.value, ...data }
    message.success('RAG 索引配置已保存 (下次索引周期生效)')
  } catch (e: any) {
    message.error('保存失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    savingRagConfig.value = false
  }
}

async function saveSttSettings() {
  savingSttSettings.value = true
  try {
    const updates: Record<string, any> = {
      stt_api_base: sttApiBase.value.trim(),
      stt_default_model: sttDefaultModel.value || '',
      stt_model_allowlist: sttAllowlist.value,
    }
    if (sttApiKey.value) {
      updates.stt_api_key = sttApiKey.value
    }
    await systemApi.setModelSettings(updates)
    message.success('STT 设置已保存')
    sttApiKey.value = ''
    if (updates.stt_api_key || sttApiBase.value) {
      sttApiKeyConfigured.value = true
    }
  } catch (e: any) {
    message.error('保存失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    savingSttSettings.value = false
  }
}
</script>
