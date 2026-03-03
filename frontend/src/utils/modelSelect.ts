import { h } from 'vue'
import { getProviderIcon } from '@/utils/providerIcons'
import { formatTokens } from '@/composables/useChatUtils'

export type ModelLike = Record<string, any>

type BuildOptions = {
  getEffectiveMaxInput?: (modelId: string, rawMaxInput: number) => number
}

type RenderOptions = {
  isPricingSyncedModel?: (modelId: string) => boolean
  isCapabilityCalibratedModel?: (modelId: string) => boolean
}

export function buildGroupedModelOptions(modelList: ModelLike[], options?: BuildOptions) {
  const mapOption = (model: ModelLike) => {
    const modelId = String(model.id || model.name || '')
    const rawMaxInput = Number(model.max_input_tokens || 0)
    const maxInput = options?.getEffectiveMaxInput
      ? Number(options.getEffectiveMaxInput(modelId, rawMaxInput) || 0)
      : rawMaxInput

    return {
      label: model.friendly_name || model.name || model.id,
      value: modelId,
      supports_vision: !!model.supports_vision,
      supports_tools: !!model.supports_tools,
      is_reasoning: !!model.is_reasoning,
      api_backend: model.api_backend,
      provider_slug: model.provider_slug || (model.api_backend === 'copilot' ? 'copilot' : 'github'),
      pricing_tier: model.pricing_tier,
      premium_multiplier: model.premium_multiplier,
      is_deprecated: !!model.is_deprecated,
      pricing_note: model.pricing_note,
      max_input_tokens: maxInput,
      max_output_tokens: Number(model.max_output_tokens || 0),
    }
  }

  const groups: Array<{ key: string; label: string; slug: string; items: ModelLike[] }> = []
  const groupMap: Record<string, { key: string; label: string; slug: string; items: ModelLike[] }> = {}

  for (const model of modelList || []) {
    const family = model.model_family || model.publisher || model.provider_slug || 'Other'
    const slug = model.provider_slug || (model.api_backend === 'copilot' ? 'copilot' : 'github')
    const groupKey = `${slug}:${family}`
    if (!groupMap[groupKey]) {
      const group = { key: groupKey, label: family, slug, items: [] as ModelLike[] }
      groups.push(group)
      groupMap[groupKey] = group
    }
    groupMap[groupKey].items.push(model)
  }

  return groups.map((group) => ({
    type: 'group' as const,
    label: group.label,
    key: group.key,
    provider_slug: group.slug,
    children: group.items.map(mapOption),
  }))
}

export function createModelLabelRenderer(options?: RenderOptions) {
  return (option: any, selected: boolean) => {
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
    const contextText = option.max_input_tokens ? formatTokens(option.max_input_tokens) : ''
    const cleanId = String(option.value || option.label || '').replace(/^copilot:/, '').toLowerCase()
    const pricingConfirmed = options?.isPricingSyncedModel ? options.isPricingSyncedModel(cleanId) : false
    const capabilityConfirmed = options?.isCapabilityCalibratedModel ? options.isCapabilityCalibratedModel(cleanId) : false
    const priceColor = pricingConfirmed
      ? (String(priceText).startsWith('x0') ? '#36ad6a' : '#f0a020')
      : '#8a93a6'
    const priceBg = pricingConfirmed
      ? (String(priceText).startsWith('x0') ? 'rgba(24,160,88,.14)' : 'rgba(240,160,32,.14)')
      : 'rgba(138,147,166,.16)'

    const caps: string[] = []
    if (option.is_reasoning) caps.push('推理')
    if (option.supports_vision) caps.push('视觉')
    if (option.supports_tools) caps.push('工具')
    const capsShort = caps.length
      ? caps.map((value) => (value === '推理' ? '推' : value === '视觉' ? '视' : '工')).join('/')
      : '未标'
    const capsText = caps.length ? caps.join(' / ') : '未标注'

    const subParts: string[] = []
    if (contextText) subParts.push(`${contextText} 上下文`)
    subParts.push(`能力：${capsText}`)
    if (option.is_deprecated) subParts.push('即将弃用')
    const subText = subParts.join(' · ')
    const selectedMeta = `${contextText ? `${contextText} · ` : ''}能力:${capsShort} · ${priceText}`

    const priceChip = h(
      'span',
      {
        style: `font-size:10px;line-height:16px;padding:0 6px;border-radius:10px;background:${priceBg};color:${priceColor};font-weight:600;`,
      },
      priceText,
    )

    if (selected) {
      return h('div', { style: 'display:flex;align-items:center;width:100%;min-width:0;overflow:hidden' }, [
        iconVNode,
        h('span', { style: 'margin-left:2px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' }, [
          h('span', { style: 'font-weight:600' }, option.label as string),
          h(
            'span',
            { style: `margin-left:6px;font-size:10px;color:${capabilityConfirmed ? '#2b7fd9' : '#8a93a6'}` },
            selectedMeta,
          ),
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
}
