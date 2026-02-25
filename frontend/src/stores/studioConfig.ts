/**
 * 设计院全局配置 store (localStorage 持久化)
 *
 * 包含:
 * - freeModelsOnly: 仅显示免费模型 (pricing_note 为 "x0" 的)
 * - modelBlacklist: 模型黑名单 (不显示在选择列表中)
 * - freeToolRounds: 免费模型工具调用轮次上限 (默认 50)
 * - paidToolRounds: 付费模型工具调用轮次上限 (默认 3)
 * - modelCapabilities: 模型能力覆盖/学习记录 (从错误和手动编辑中积累)
 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const STORAGE_KEY = 'studio_config'

interface ModelCapabilityOverride {
  max_input_tokens?: number
  max_output_tokens?: number
  rate_limit_info?: string
  rate_limit_seconds?: number
  last_error?: string
  last_error_at?: string
}

interface StudioConfig {
  freeModelsOnly: boolean
  customModelsEnabled: boolean
  docModelsOnly: boolean
  docModelIds: string[]
  modelBlacklist: string[]
  freeToolRounds: number
  paidToolRounds: number
  maxAutoContinues: number
  chatModelSourceFilter: string
  modelCapabilities: Record<string, ModelCapabilityOverride>
  pricingSyncedAt?: string
  pricingModelIds?: string[]
  capabilityCalibratedAt?: string
  capabilityModelIds?: string[]
}

function loadConfig(): StudioConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        freeModelsOnly: parsed.freeModelsOnly ?? false,
        customModelsEnabled: parsed.customModelsEnabled ?? true,
        docModelsOnly: parsed.docModelsOnly ?? false,
        docModelIds: parsed.docModelIds ?? [],
        modelBlacklist: parsed.modelBlacklist ?? [],
        freeToolRounds: parsed.freeToolRounds ?? 50,
        paidToolRounds: parsed.paidToolRounds ?? 3,
        maxAutoContinues: parsed.maxAutoContinues ?? 3,
        chatModelSourceFilter: (parsed.chatModelSourceFilter === 'custom' ? 'all' : (parsed.chatModelSourceFilter ?? 'all')),
        modelCapabilities: parsed.modelCapabilities ?? {},
        pricingSyncedAt: parsed.pricingSyncedAt ?? '',
        pricingModelIds: parsed.pricingModelIds ?? [],
        capabilityCalibratedAt: parsed.capabilityCalibratedAt ?? '',
        capabilityModelIds: parsed.capabilityModelIds ?? [],
      }
    }
  } catch {}
  return {
    freeModelsOnly: false,
    customModelsEnabled: true,
    docModelsOnly: false,
    docModelIds: [],
    modelBlacklist: [],
    freeToolRounds: 50,
    paidToolRounds: 3,
    maxAutoContinues: 3,
    chatModelSourceFilter: 'all',
    modelCapabilities: {},
    pricingSyncedAt: '',
    pricingModelIds: [],
    capabilityCalibratedAt: '',
    capabilityModelIds: [],
  }
}

function saveConfig(config: StudioConfig) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
}

export const useStudioConfigStore = defineStore('studioConfig', () => {
  const initial = loadConfig()

  const freeModelsOnly = ref(initial.freeModelsOnly)
  const customModelsEnabled = ref(initial.customModelsEnabled)
  const docModelsOnly = ref(initial.docModelsOnly)
  const docModelIds = ref<string[]>(initial.docModelIds)
  const modelBlacklist = ref<string[]>(initial.modelBlacklist)
  const freeToolRounds = ref(initial.freeToolRounds)
  const paidToolRounds = ref(initial.paidToolRounds)
  const maxAutoContinues = ref(initial.maxAutoContinues)
  const chatModelSourceFilter = ref(initial.chatModelSourceFilter)
  const modelCapabilities = ref<Record<string, ModelCapabilityOverride>>(initial.modelCapabilities)
  const pricingSyncedAt = ref(initial.pricingSyncedAt || '')
  const pricingModelIds = ref<string[]>(initial.pricingModelIds || [])
  const capabilityCalibratedAt = ref(initial.capabilityCalibratedAt || '')
  const capabilityModelIds = ref<string[]>(initial.capabilityModelIds || [])

  // 自动持久化
  function persist() {
    saveConfig({
      freeModelsOnly: freeModelsOnly.value,
      customModelsEnabled: customModelsEnabled.value,
      docModelsOnly: docModelsOnly.value,
      docModelIds: docModelIds.value,
      modelBlacklist: modelBlacklist.value,
      freeToolRounds: freeToolRounds.value,
      paidToolRounds: paidToolRounds.value,
      maxAutoContinues: maxAutoContinues.value,
      chatModelSourceFilter: chatModelSourceFilter.value,
      modelCapabilities: modelCapabilities.value,
      pricingSyncedAt: pricingSyncedAt.value,
      pricingModelIds: pricingModelIds.value,
      capabilityCalibratedAt: capabilityCalibratedAt.value,
      capabilityModelIds: capabilityModelIds.value,
    })
  }

  watch(freeModelsOnly, persist)
  watch(customModelsEnabled, persist)
  watch(docModelsOnly, persist)
  watch(docModelIds, persist, { deep: true })
  watch(modelBlacklist, persist, { deep: true })
  watch(freeToolRounds, persist)
  watch(paidToolRounds, persist)
  watch(maxAutoContinues, persist)
  watch(chatModelSourceFilter, persist)
  watch(modelCapabilities, persist, { deep: true })
  watch(pricingSyncedAt, persist)
  watch(pricingModelIds, persist, { deep: true })
  watch(capabilityCalibratedAt, persist)
  watch(capabilityModelIds, persist, { deep: true })

  function setPricingSync(modelIds: string[], syncedAt?: string) {
    pricingModelIds.value = Array.from(new Set((modelIds || []).map(i => i.toLowerCase())))
    pricingSyncedAt.value = syncedAt || new Date().toISOString()
  }

  function setCapabilityCalibration(modelIds: string[], calibratedAt?: string) {
    capabilityModelIds.value = Array.from(new Set((modelIds || []).map(i => i.toLowerCase())))
    capabilityCalibratedAt.value = calibratedAt || new Date().toISOString()
  }

  function clearModelSyncMarks() {
    pricingModelIds.value = []
    capabilityModelIds.value = []
    pricingSyncedAt.value = ''
    capabilityCalibratedAt.value = ''
  }

  function isPricingSyncedModel(modelId: string): boolean {
    const clean = (modelId || '').replace(/^copilot:/, '').toLowerCase()
    return pricingModelIds.value.includes(clean)
  }

  function isCapabilityCalibratedModel(modelId: string): boolean {
    const clean = (modelId || '').replace(/^copilot:/, '').toLowerCase()
    return capabilityModelIds.value.includes(clean)
  }

  /**
   * 判断模型是否应该在列表中显示
   */
  function isModelVisible(model: {
    id: string
    name: string
    api_backend?: string
    pricing_note?: string
    premium_multiplier?: number
  }): boolean {
    // 黑名单过滤
    const nameLC = (model.name || model.id).toLowerCase()
    if (modelBlacklist.value.some(b => nameLC.includes(b.toLowerCase()))) {
      return false
    }
    // 仅免费模型过滤
    if (freeModelsOnly.value) {
      const note = (model.pricing_note || '').toLowerCase()
      const isFree = note === 'x0' || note.startsWith('x0 ') || (model.premium_multiplier ?? 0) === 0
      if (!isFree) return false
    }
    // 官方推荐模型过滤（仅 Copilot 模型生效）
    if (docModelsOnly.value && docModelIds.value.length > 0 && model.api_backend === 'copilot') {
      const cleanId = (model.id || model.name || '').replace(/^copilot:/, '').toLowerCase()
      if (!docModelIds.value.includes(cleanId)) return false
    }
    return true
  }

  function setDocModels(ids: string[]) {
    docModelIds.value = Array.from(new Set((ids || []).map(i => i.toLowerCase())))
  }

  /**
   * 根据模型是否付费返回工具轮次上限
   */
  function getToolRounds(model: { pricing_note?: string; premium_multiplier?: number }): number {
    const note = (model.pricing_note || '').toLowerCase()
    const isFree = note === 'x0' || note.startsWith('x0 ') || (model.premium_multiplier ?? 0) === 0
    return isFree ? freeToolRounds.value : paidToolRounds.value
  }

  function addToBlacklist(name: string) {
    const trimmed = name.trim()
    if (trimmed && !modelBlacklist.value.includes(trimmed)) {
      modelBlacklist.value.push(trimmed)
    }
  }

  function removeFromBlacklist(name: string) {
    modelBlacklist.value = modelBlacklist.value.filter(b => b !== name)
  }

  /**
   * 从 AI 错误元数据中更新模型能力
   */
  function updateModelCapability(modelId: string, errorMeta: any) {
    const existing = modelCapabilities.value[modelId] || {}
    const update: ModelCapabilityOverride = { ...existing }
    if (errorMeta.max_context_tokens) update.max_input_tokens = errorMeta.max_context_tokens
    if (errorMeta.rate_limit) update.rate_limit_info = errorMeta.rate_limit
    if (errorMeta.rate_limit_seconds) update.rate_limit_seconds = errorMeta.rate_limit_seconds
    update.last_error = errorMeta.summary || errorMeta.error_type || ''
    update.last_error_at = new Date().toISOString()
    modelCapabilities.value[modelId] = update
  }

  /**
   * 获取模型能力覆盖
   */
  function getModelCapability(modelId: string): ModelCapabilityOverride {
    return modelCapabilities.value[modelId] || {}
  }

  /**
   * 手动设置模型能力字段
   */
  function setModelCapability(modelId: string, field: string, value: any) {
    if (!modelCapabilities.value[modelId]) {
      modelCapabilities.value[modelId] = {}
    }
    ;(modelCapabilities.value[modelId] as any)[field] = value
  }

  /**
   * 获取模型有效的 max_input (覆盖 > API 原始值)
   */
  function getEffectiveMaxInput(modelId: string, apiMaxInput: number): number {
    const override = modelCapabilities.value[modelId]?.max_input_tokens
    return override || apiMaxInput
  }

  return {
    freeModelsOnly,
    customModelsEnabled,
    docModelsOnly,
    docModelIds,
    modelBlacklist,
    freeToolRounds,
    paidToolRounds,
    maxAutoContinues,
    chatModelSourceFilter,
    modelCapabilities,
    pricingSyncedAt,
    pricingModelIds,
    capabilityCalibratedAt,
    capabilityModelIds,
    isModelVisible,
    getToolRounds,
    addToBlacklist,
    removeFromBlacklist,
    updateModelCapability,
    getModelCapability,
    setModelCapability,
    getEffectiveMaxInput,
    setPricingSync,
    setCapabilityCalibration,
    clearModelSyncMarks,
    isPricingSyncedModel,
    isCapabilityCalibratedModel,
    setDocModels,
  }
})
