<template>
  <n-space vertical :size="16">
    <n-alert type="info" :bordered="false">
      配置 AI 长期记忆系统。记忆让 AI 能记住用户偏好、已知事实和历史决策，提供更个性化的服务。
    </n-alert>

    <!-- 全局开关 + 统计 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🧠 记忆总控</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-space :size="8" align="center">
          <n-switch
            :value="config.memory_enabled"
            @update:value="(v: boolean) => updateConfig('memory_enabled', v)"
            :loading="saving"
          />
          <n-text depth="3" style="font-size: 11px">{{ config.memory_enabled ? '已启用' : '已关闭' }}</n-text>
        </n-space>
      </template>

      <!-- 统计卡片 -->
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 8px; margin-top: 4px">
        <div v-for="s in statItems" :key="s.label"
          style="padding: 8px 12px; background: rgba(255,255,255,0.03); border-radius: 6px; text-align: center">
          <div style="font-size: 20px; font-weight: 600">{{ s.value }}</div>
          <n-text depth="3" style="font-size: 11px">{{ s.label }}</n-text>
        </div>
      </div>
    </n-card>

    <!-- 提取配置 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🔍 记忆提取</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-space align="center">
          <n-switch
            :value="config.memory_auto_extract"
            @update:value="(v: boolean) => updateConfig('memory_auto_extract', v)"
          />
          <n-text>对话后自动提取记忆</n-text>
          <n-text depth="3" style="font-size: 11px">每次对话结束后自动提取事实、决策、偏好等信息</n-text>
        </n-space>

        <n-space align="center">
          <n-switch
            :value="config.memory_extract_assistant"
            @update:value="(v: boolean) => updateConfig('memory_extract_assistant', v)"
          />
          <n-text>从 AI 回复中提取</n-text>
          <n-text depth="3" style="font-size: 11px">AI 回复中常包含总结性事实和决策</n-text>
        </n-space>

        <n-descriptions :column="1" label-placement="left" bordered size="small">
          <n-descriptions-item label="提取模型">
            <n-select
              :value="config.memory_extraction_model || null"
              :options="modelOptions"
              :render-label="renderModelLabel"
              :loading="loading"
              filterable
              placeholder="跟随聊天默认模型"
              clearable
              size="small"
              style="width: 100%; min-width: 200px"
              @update:value="(v: string | null) => updateConfig('memory_extraction_model', v || '')"
            />
          </n-descriptions-item>
          <n-descriptions-item label="合并模型">
            <n-select
              :value="config.memory_consolidation_model || null"
              :options="modelOptions"
              :render-label="renderModelLabel"
              :loading="loading"
              filterable
              placeholder="跟随聊天默认模型"
              clearable
              size="small"
              style="width: 100%; min-width: 200px"
              @update:value="(v: string | null) => updateConfig('memory_consolidation_model', v || '')"
            />
          </n-descriptions-item>
        </n-descriptions>
      </n-space>
    </n-card>

    <!-- 维护配置 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>🔧 记忆维护</span>
        </n-space>
      </template>
      <n-descriptions :column="1" label-placement="left" bordered size="small">
        <n-descriptions-item label="每用户记忆上限">
          <n-space align="center" :size="4">
            <n-input-number
              :value="config.memory_max_per_user"
              :min="50" :max="5000" :step="50" size="small" style="width: 120px"
              @update:value="(v: number | null) => updateConfig('memory_max_per_user', v ?? 500)"
            />
            <n-text depth="3" style="font-size: 10px">超出时自动裁剪低重要性记忆</n-text>
          </n-space>
        </n-descriptions-item>
        <n-descriptions-item label="未访问衰减天数">
          <n-space align="center" :size="4">
            <n-input-number
              :value="config.memory_decay_days"
              :min="7" :max="365" size="small" style="width: 120px"
              @update:value="(v: number | null) => updateConfig('memory_decay_days', v ?? 30)"
            />
            <n-text depth="3" style="font-size: 10px">超过此天数的记忆重要性自动衰减</n-text>
          </n-space>
        </n-descriptions-item>
        <n-descriptions-item label="自动合并周期 (小时)">
          <n-space align="center" :size="4">
            <n-input-number
              :value="config.memory_auto_consolidate_hours"
              :min="0" :max="168" size="small" style="width: 120px"
              @update:value="(v: number | null) => updateConfig('memory_auto_consolidate_hours', v ?? 24)"
            />
            <n-text depth="3" style="font-size: 10px">0 = 关闭自动合并</n-text>
          </n-space>
        </n-descriptions-item>
      </n-descriptions>
    </n-card>

    <!-- 操作按钮 -->
    <n-card size="small" style="background: #212121">
      <template #header>
        <n-space align="center" :size="8">
          <span>⚡ 手动操作</span>
        </n-space>
      </template>
      <n-space :size="12">
        <n-button type="primary" ghost size="small" :loading="consolidating" @click="handleConsolidate">
          🔄 合并重复记忆
        </n-button>
        <n-popconfirm @positive-click="handleClear">
          <template #trigger>
            <n-button type="error" ghost size="small" :loading="clearing">
              🗑️ 清空所有记忆
            </n-button>
          </template>
          确定要清空当前用户的所有记忆吗？此操作不可撤销。
        </n-popconfirm>
      </n-space>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useMessage } from 'naive-ui'
import { memoryApi, modelApi } from '@/api'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { buildGroupedModelOptions, createModelLabelRenderer } from '@/utils/modelSelect'

const message = useMessage()
const studioConfig = useStudioConfigStore()
const loading = ref(false)
const saving = ref(false)
const consolidating = ref(false)
const clearing = ref(false)

const config = reactive({
  memory_enabled: true,
  memory_extraction_model: '',
  memory_consolidation_model: '',
  memory_auto_extract: true,
  memory_extract_assistant: true,
  memory_max_per_user: 500,
  memory_decay_days: 30,
  memory_auto_consolidate_hours: 24,
})

const stats = reactive({
  total: 0,
  facts: 0,
  decisions: 0,
  preferences: 0,
  episodes: 0,
  profiles: 0,
})

const statItems = computed(() => [
  { label: '总计', value: stats.total },
  { label: '事实', value: stats.facts },
  { label: '决策', value: stats.decisions },
  { label: '偏好', value: stats.preferences },
  { label: '事件', value: stats.episodes },
  { label: '画像', value: stats.profiles },
])

const allModels = ref<any[]>([])

const modelOptions = computed(() => {
  const pool = allModels.value.filter((m: any) => !m.category || m.category === 'discussion' || m.category === 'both')
  return buildGroupedModelOptions(pool, {
    getEffectiveMaxInput: (modelId: string, rawMaxInput: number) =>
      studioConfig.getEffectiveMaxInput(modelId, rawMaxInput),
  })
})

const renderModelLabel = createModelLabelRenderer({
  isPricingSyncedModel: (modelId: string) => studioConfig.isPricingSyncedModel(modelId),
  isCapabilityCalibratedModel: (modelId: string) => studioConfig.isCapabilityCalibratedModel(modelId),
})

async function loadConfig() {
  try {
    const { data } = await memoryApi.getConfig()
    Object.assign(config, data)
  } catch (e: any) {
    console.warn('加载记忆配置失败:', e)
  }
}

async function loadStats() {
  try {
    const { data } = await memoryApi.getStats()
    Object.assign(stats, data)
  } catch (e: any) {
    console.warn('加载记忆统计失败:', e)
  }
}

async function loadModels() {
  loading.value = true
  try {
    const { data } = await modelApi.list({ category: 'discussion', custom_models: false })
    if (Array.isArray(data)) {
      allModels.value = data
    }
  } catch (e: any) {
    console.warn('加载模型列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function updateConfig(key: string, value: any) {
  saving.value = true
  try {
    const { data } = await memoryApi.updateConfig({ [key]: value })
    Object.assign(config, data)
  } catch (e: any) {
    message.error('保存失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function handleConsolidate() {
  consolidating.value = true
  try {
    const { data } = await memoryApi.consolidate()
    message.success(`合并完成, 移除了 ${data.removed} 条重复记忆`)
    if (data.stats) Object.assign(stats, data.stats)
    else await loadStats()
  } catch (e: any) {
    message.error('合并失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    consolidating.value = false
  }
}

async function handleClear() {
  clearing.value = true
  try {
    const { data } = await memoryApi.clear()
    message.success(`已清空 ${data.removed} 条记忆`)
    await loadStats()
  } catch (e: any) {
    message.error('清空失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    clearing.value = false
  }
}

onMounted(() => {
  loadConfig()
  loadStats()
  loadModels()
})
</script>
