/**
 * 模型选择管理 — 模型列表, 过滤, 分组, 渲染, 能力查询
 */
import { ref, computed, h } from 'vue'
import { useMessage } from 'naive-ui'
import { modelApi } from '@/api'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { getProviderIcon } from '@/utils/providerIcons'
import { formatTokens } from './useChatUtils'

export function useModelSelection(initialModel: string) {
  const message = useMessage()
  const studioConfig = useStudioConfigStore()

  const models = ref<any[]>([])
  const selectedModel = ref(initialModel)
  const loadingModels = ref(false)

  const modelSourceFilter = computed({
    get: () => studioConfig.chatModelSourceFilter,
    set: (v: string) => { studioConfig.chatModelSourceFilter = v }
  })

  // 来源过滤选项
  const sourceFilterOptions = computed(() => {
    const base: Array<{label: string; key: string}> = [
      { label: '全部', key: 'all' },
      { label: 'GitHub (免费)', key: 'github' },
    ]
    if (models.value.some(m => m.api_backend === 'copilot')) {
      base.push({ label: 'Copilot (付费)', key: 'copilot' })
    }
    const seen = new Set<string>()
    for (const m of models.value) {
      const slug = m.provider_slug || ''
      if (slug && slug !== 'github' && slug !== 'copilot' && !seen.has(slug)) {
        seen.add(slug)
        base.push({ label: m.publisher || slug, key: slug })
      }
    }
    return base
  })

  const sourceFilterLabel = computed(() => {
    const opt = sourceFilterOptions.value.find(o => o.key === modelSourceFilter.value)
    return opt?.label || '全部'
  })

  function onSourceFilterChange(key: string) {
    if (key === 'custom') {
      modelSourceFilter.value = 'all'
      return
    }
    modelSourceFilter.value = key as any
  }

  function normalizeSourceFilter() {
    if (modelSourceFilter.value === 'custom') {
      modelSourceFilter.value = 'all'
    }
  }

  function ensureSelectedModelValid() {
    if (!selectedModel.value) return
    const exists = models.value.some((m: any) => m.id === selectedModel.value)
    if (exists) return
    const fallback = models.value.find((m: any) => m.id === 'gpt-4o') || models.value[0]
    if (fallback?.id) {
      selectedModel.value = fallback.id
    }
  }

  // 模型选项 (分组)
  const modelOptions = computed(() => {
    const byCategory = models.value.filter(m => m.category === 'discussion' || m.category === 'both')
    const source = modelSourceFilter.value === 'custom' ? 'all' : modelSourceFilter.value
    const sourceFiltered = source === 'all'
      ? byCategory
      : source === 'copilot'
        ? byCategory.filter(m => m.provider_slug === 'copilot' || m.api_backend === 'copilot')
        : source === 'github'
            ? byCategory.filter(m => m.provider_slug === 'github' || (!m.provider_slug && m.api_backend === 'models'))
            : byCategory.filter(m => m.provider_slug === source)

    const filtered = sourceFiltered.filter(m => studioConfig.isModelVisible(m))

    const mapOpt = (m: any) => ({
      label: m.name, value: m.id,
      description: m.summary || m.description || '',
      supports_vision: m.supports_vision, supports_tools: m.supports_tools,
      is_reasoning: m.is_reasoning, api_backend: m.api_backend,
      is_custom: m.is_custom,
      provider_slug: m.provider_slug || (m.api_backend === 'copilot' ? 'copilot' : 'github'),
      pricing_tier: m.pricing_tier, premium_multiplier: m.premium_multiplier,
      is_deprecated: m.is_deprecated, pricing_note: m.pricing_note,
      max_input_tokens: studioConfig.getEffectiveMaxInput(m.id, m.max_input_tokens || 0),
      max_output_tokens: m.max_output_tokens || 0,
    })

    const groups: Array<{ key: string; label: string; slug: string; items: any[] }> = []
    const groupMap: Record<string, typeof groups[0]> = {}
    for (const m of filtered) {
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
    const result = groups.map(g => ({
      type: 'group', label: g.label, key: g.key, provider_slug: g.slug,
      children: g.items.map(mapOpt),
    }))

    return result
  })

  // 当前模型能力
  const currentModelCaps = computed(() => {
    const model = models.value.find((m: any) => m.id === selectedModel.value)
    if (!model) return { supports_vision: false, supports_tools: false }
    return { supports_vision: !!model.supports_vision, supports_tools: !!model.supports_tools }
  })

  const selectedModelDisplay = computed(() => {
    const model = models.value.find((m: any) => m.id === selectedModel.value)
    if (!model) return selectedModel.value
    return `${selectedModel.value}`
  })

  const selectedModelProviderIcon = computed(() => {
    const model = models.value.find((m: any) => m.id === selectedModel.value)
    if (!model) return ''
    const slug = model.provider_slug || (model.api_backend === 'copilot' ? 'copilot' : 'github')
    return getProviderIcon(slug, '', 12)
  })

  const selectedModelMaxTokens = computed(() => {
    const model = models.value.find((m: any) => m.id === selectedModel.value)
    if (!model) return 0
    return studioConfig.getEffectiveMaxInput(model.id, model.max_input_tokens || 0) || model.max_input_tokens || 0
  })

  const currentModelToolRounds = computed(() => {
    const model = models.value.find(m => m.id === selectedModel.value)
    if (!model) return studioConfig.freeToolRounds
    return studioConfig.getToolRounds(model)
  })

  // 自定义渲染
  function renderModelLabel(option: any, selected: boolean) {
    if (option.type === 'group') {
      const iconHtml = getProviderIcon(option.provider_slug || 'github', option.label, 14)
      return h('span', { style: 'display:inline-flex;align-items:center;gap:4px' }, [
        h('span', { innerHTML: iconHtml, style: 'display:inline-flex' }),
        option.label,
      ])
    }
    const iconHtml = getProviderIcon(option.provider_slug || 'github', '', 12)
    const iconVNode = h('span', { innerHTML: iconHtml, style: 'display:inline-flex;vertical-align:middle;margin:0 2px' })
    const priceText = option.pricing_note || 'x0'
    const ctxText = option.max_input_tokens ? formatTokens(option.max_input_tokens) : ''
    const cleanId = String(option.value || option.label || '').replace(/^copilot:/, '').toLowerCase()
    const pricingConfirmed = studioConfig.isPricingSyncedModel(cleanId)
    const capabilityConfirmed = studioConfig.isCapabilityCalibratedModel(cleanId)
    const priceColor = pricingConfirmed
      ? (String(priceText).startsWith('x0') ? '#36ad6a' : '#f0a020')
      : '#8a93a6'
    const priceBg = pricingConfirmed
      ? (String(priceText).startsWith('x0') ? 'rgba(24,160,88,.14)' : 'rgba(240,160,32,.14)')
      : 'rgba(138,147,166,.16)'
    const chip = (text: string, style: string) => h('span', { style }, text)
    const caps: string[] = []
    if (option.is_reasoning) caps.push('推理')
    if (option.supports_vision) caps.push('视觉')
    if (option.supports_tools) caps.push('工具')
    const capsText = caps.length ? caps.join(' / ') : '未标注'
    const capsShort = caps.length
      ? caps.map(c => (c === '推理' ? '推' : c === '视觉' ? '视' : '工')).join('/')
      : '未标'
    const subParts: string[] = []
    if (ctxText) subParts.push(`${ctxText} 上下文`)
    subParts.push(`能力：${capsText}`)
    if (option.is_deprecated) subParts.push('即将弃用')
    const subText = subParts.join(' · ')
    const selectedMeta = `${ctxText ? `${ctxText} · ` : ''}能力:${capsShort} · ${priceText}`

    const priceChip = chip(
      priceText,
      `font-size:10px;line-height:16px;padding:0 6px;border-radius:10px;background:${priceBg};color:${priceColor};font-weight:600;`
    )

    if (selected) {
      return h('div', { style: 'display:flex;align-items:center;width:100%;min-width:0;overflow:hidden' }, [
        iconVNode,
        h('span', { style: 'margin-left:2px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' }, [
          h('span', { style: 'font-weight:600' }, option.label as string),
          h('span', {
            style: `margin-left:6px;font-size:10px;color:${capabilityConfirmed ? '#2b7fd9' : '#8a93a6'}`
          }, selectedMeta),
        ]),
      ])
    }

    return h('div', { style: 'display:flex;flex-direction:column;gap:2px;width:100%;padding:1px 0' }, [
      h('div', { style: 'display:flex;align-items:center;justify-content:space-between;gap:8px' }, [
        h('span', { style: `display:inline-flex;align-items:center;min-width:0;font-weight:${selected ? 600 : 500}` }, [
          iconVNode,
          h('span', { style: 'margin-left:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' }, option.label as string),
        ]),
        priceChip,
      ]),
      h('div', { style: `font-size:10px;color:${capabilityConfirmed ? '#2b7fd9' : '#8a93a6'};white-space:normal;line-height:1.35` }, subText),
    ])
  }

  async function refreshModels() {
    loadingModels.value = true
    try {
      await modelApi.refresh()
      const { data } = await modelApi.list({ category: 'discussion', custom_models: false })
      models.value = data
      normalizeSourceFilter()
      ensureSelectedModelValid()
      message.success(`已刷新，共 ${data.length} 个可用模型`)
    } catch (e: any) {
      message.error('刷新模型列表失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      loadingModels.value = false
    }
  }

  async function loadModels() {
    try {
      const { data } = await modelApi.list({ category: 'discussion', custom_models: false })
      models.value = data
      normalizeSourceFilter()
      ensureSelectedModelValid()
    } catch {}
  }

  return {
    models,
    selectedModel,
    loadingModels,
    modelSourceFilter,
    sourceFilterOptions,
    sourceFilterLabel,
    modelOptions,
    currentModelCaps,
    selectedModelDisplay,
    selectedModelProviderIcon,
    selectedModelMaxTokens,
    currentModelToolRounds,
    onSourceFilterChange,
    renderModelLabel,
    refreshModels,
    loadModels,
  }
}
