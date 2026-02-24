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
    if (studioConfig.customModelsEnabled) {
      base.push({ label: '🧩 补充模型', key: 'custom' })
    }
    return base
  })

  const sourceFilterLabel = computed(() => {
    const opt = sourceFilterOptions.value.find(o => o.key === modelSourceFilter.value)
    return opt?.label || '全部'
  })

  function onSourceFilterChange(key: string) {
    if (key === 'custom' && !studioConfig.customModelsEnabled) {
      modelSourceFilter.value = 'all'
      return
    }
    modelSourceFilter.value = key as any
  }

  // 模型选项 (分组)
  const modelOptions = computed(() => {
    const byCategory = models.value.filter(m => m.category === 'discussion' || m.category === 'both')
    const sourceFiltered = modelSourceFilter.value === 'all'
      ? byCategory
      : modelSourceFilter.value === 'copilot'
        ? byCategory.filter(m => m.provider_slug === 'copilot' || m.api_backend === 'copilot')
        : modelSourceFilter.value === 'custom'
          ? byCategory.filter(m => m.is_custom)
          : modelSourceFilter.value === 'github'
            ? byCategory.filter(m => m.provider_slug === 'github' || (!m.provider_slug && m.api_backend === 'models'))
            : byCategory.filter(m => m.provider_slug === modelSourceFilter.value)

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

    // 如果当前选中的模型不在列表中 (列表尚未加载或被过滤掉)，
    // 注入一个占位选项，避免 n-select 显示空白
    const allValues = result.flatMap(g => g.children.map(c => c.value))
    if (selectedModel.value && !allValues.includes(selectedModel.value)) {
      result.unshift({
        type: 'group', label: '当前', key: '_current', provider_slug: 'github',
        children: [{
          label: selectedModel.value, value: selectedModel.value,
          description: '', supports_vision: false, supports_tools: true,
          is_reasoning: false, api_backend: '', is_custom: false,
          provider_slug: 'github', pricing_tier: '', premium_multiplier: 0,
          is_deprecated: false, pricing_note: '',
          max_input_tokens: 0, max_output_tokens: 0,
        }],
      })
    }

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
    const customStr = model.is_custom ? ' 🧩' : ''
    return `${selectedModel.value}${customStr}`
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
      ? 'color:#18a058;font-size:11px;flex-shrink:0;margin-left:8px;font-weight:600'
      : 'color:#888;font-size:11px;flex-shrink:0;margin-left:8px'
    return h('div', { style: 'display:flex;justify-content:space-between;align-items:center;width:100%' }, [
      h('span', { style: nameStyle }, [selected ? '● ' : '', option.label as string, ' ', iconVNode, customStr, capStr, depStr]),
      h('span', { style: priceStyle }, [
        ctxText ? h('span', { style: 'color:#666;margin-right:6px' }, ctxText) : null,
        priceText,
      ]),
    ])
  }

  async function refreshModels() {
    loadingModels.value = true
    try {
      await modelApi.refresh()
      const { data } = await modelApi.list({ category: 'discussion', custom_models: studioConfig.customModelsEnabled })
      models.value = data
      message.success(`已刷新，共 ${data.length} 个可用模型`)
    } catch (e: any) {
      message.error('刷新模型列表失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      loadingModels.value = false
    }
  }

  async function loadModels() {
    try {
      const { data } = await modelApi.list({ category: 'discussion', custom_models: studioConfig.customModelsEnabled })
      models.value = data
      // 不重置 selectedModel — 保持用户/项目历史选择的模型
      // 即使模型不在列表中也不改动，避免切换项目时闪烁
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
