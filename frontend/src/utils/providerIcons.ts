/**
 * AI 服务提供商图标工具
 *
 * 已知提供商使用 SVG 品牌图标, 未知提供商生成彩色字母徽章。
 * SVG 内容为硬编码可信数据, 可安全用于 v-html / innerHTML。
 */

// SVG 内容 (viewBox="0 0 16 16")
const PROVIDER_SVG_CONTENT: Record<string, string> = {
  // GitHub Invertocat mark (official octicon)
  github:
    '<path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>',

  // Copilot — sparkle / 4-point star
  copilot:
    '<path fill="currentColor" d="M8 0l2.09 5.67L16 8l-5.91 2.33L8 16l-2.09-5.67L0 8l5.91-2.33z"/>',

  // OpenAI — brand green/dark badge
  openai:
    '<rect rx="3" width="16" height="16" fill="#10A37F"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="9" font-weight="700" font-family="system-ui,-apple-system,sans-serif">AI</text>',

  // Anthropic — brand orange/tan badge
  anthropic:
    '<rect rx="3" width="16" height="16" fill="#D4A574"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">A</text>',

  // Google — brand blue badge
  google:
    '<rect rx="3" width="16" height="16" fill="#4285F4"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">G</text>',

  // DeepSeek — brand blue badge "D"
  deepseek:
    '<rect rx="3" width="16" height="16" fill="#4D6BFE"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">D</text>',

  // Mistral AI — brand orange badge
  mistralai:
    '<rect rx="3" width="16" height="16" fill="#F7D046"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#333" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">M</text>',

  // Meta — brand blue badge
  meta:
    '<rect rx="3" width="16" height="16" fill="#0668E1"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">M</text>',

  // Microsoft — brand badge
  microsoft:
    '<rect rx="3" width="16" height="16" fill="#00A4EF"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="9" font-weight="700" font-family="system-ui,-apple-system,sans-serif">MS</text>',

  // xAI — brand badge
  xai:
    '<rect rx="3" width="16" height="16" fill="#1DA1F2"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">X</text>',

  // Cohere — brand green badge
  cohere:
    '<rect rx="3" width="16" height="16" fill="#39594D"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">C</text>',

  // AI21 Labs — brand badge
  ai21:
    '<rect rx="3" width="16" height="16" fill="#6C63FF"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="8" font-weight="700" font-family="system-ui,-apple-system,sans-serif">21</text>',

  // 通义千问 (Qwen) — brand purple badge "Q"
  qwen:
    '<rect rx="3" width="16" height="16" fill="#615DEA"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">Q</text>',

  // 智谱 (Zhipu GLM) — brand blue badge "Z"
  zhipu:
    '<rect rx="3" width="16" height="16" fill="#3B82F6"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">Z</text>',

  // 月之暗面 Kimi — brand purple badge "K"
  kimi:
    '<rect rx="3" width="16" height="16" fill="#7C3AED"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">K</text>',

  // SiliconFlow — brand badge
  siliconflow:
    '<rect rx="3" width="16" height="16" fill="#6366F1"/>' +
    '<text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="9" font-weight="700" font-family="system-ui,-apple-system,sans-serif">SF</text>',
}

/**
 * 获取已知提供商的 SVG 图标 HTML
 */
export function getProviderIconSvg(slug: string, size: number = 14): string {
  const content = PROVIDER_SVG_CONTENT[slug]
  if (!content) return ''
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="${size}" height="${size}" style="vertical-align:middle">${content}</svg>`
}

/**
 * 为未知提供商生成彩色字母徽章 SVG
 */
export function generateProviderBadge(initial: string, color: string = '#666', size: number = 14): string {
  const ch = initial.charAt(0).toUpperCase()
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="${size}" height="${size}" style="vertical-align:middle"><rect rx="3" width="16" height="16" fill="${color}"/><text x="8" y="11.5" text-anchor="middle" fill="#fff" font-size="10" font-weight="700" font-family="system-ui,-apple-system,sans-serif">${ch}</text></svg>`
}

/**
 * 获取提供商图标 (已知 → SVG, 未知 → 字母徽章)
 */
export function getProviderIcon(slug: string, fallbackName?: string, size: number = 14): string {
  const svg = getProviderIconSvg(slug, size)
  if (svg) return svg
  const initial = (fallbackName || slug || '?').charAt(0)
  return generateProviderBadge(initial, '#666', size)
}
