/**
 * 聊天工具函数 — 纯函数/无状态的辅助方法
 * 包含: markdown 渲染, 时间格式化, token 格式化, 错误解析, 滚动控制
 */
import { nextTick, type Ref } from 'vue'
import { marked } from 'marked'

// ==================== Markdown ====================

export function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return marked.parse(text, { async: false }) as string
  } catch {
    return text.replace(/\n/g, '<br>')
  }
}

// ==================== 时间格式化 ====================

export function formatTime(d: string): string {
  // 后端存储 UTC 时间, ISO 字符串不含 Z 后缀 → 手动补 Z
  const utcStr = d && !d.endsWith('Z') && !d.includes('+') ? d + 'Z' : d
  return new Date(utcStr).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

// ==================== Token 格式化 ====================

export function formatTokens(n: number): string {
  if (!n) return '0'
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(0)}K`
  return `${n}`
}

// ==================== 滚动控制 ====================

export function useScroll(containerRef: Ref<HTMLElement | undefined>) {
  function scrollToBottom() {
    nextTick(() => {
      if (containerRef.value) {
        containerRef.value.scrollTop = containerRef.value.scrollHeight
      }
    })
  }
  function scrollToTop() {
    nextTick(() => {
      if (containerRef.value) {
        containerRef.value.scrollTop = 0
      }
    })
  }
  return { scrollToBottom, scrollToTop }
}

// ==================== 错误解析 ====================

export function parseErrorMeta(errorText: string, backendMeta?: any): any {
  const meta: any = { ...(backendMeta || {}) }

  const rlMatch = errorText.match(/Rate limit.*?(\d+)\s*per\s*(\d+)s/i)
  if (rlMatch) {
    meta.error_type = meta.error_type || 'rate_limit'
    meta.rate_limit = `${rlMatch[1]} per ${rlMatch[2]}s`
    meta.rate_limit_count = parseInt(rlMatch[1])
    meta.rate_limit_seconds = parseInt(rlMatch[2])
  }
  const waitMatch = errorText.match(/wait\s+(\d+)\s*seconds?/i)
  if (waitMatch) {
    meta.wait_seconds = parseInt(waitMatch[1])
    meta.error_type = meta.error_type || 'rate_limit'
  }

  const ctxMatch = errorText.match(/maximum context length.*?(\d{3,})/i)
  if (ctxMatch) {
    meta.error_type = meta.error_type || 'context_overflow'
    meta.max_context_tokens = parseInt(ctxMatch[1])
  }
  const maxSizeMatch = errorText.match(/Max size:\s*(\d+)\s*tokens/i)
  if (maxSizeMatch) {
    meta.error_type = meta.error_type || 'context_overflow'
    meta.max_context_tokens = parseInt(maxSizeMatch[1])
  }
  const requestedMatch = errorText.match(/requested\s+(\d+)\s*tokens/i)
  if (requestedMatch) {
    meta.requested_tokens = parseInt(requestedMatch[1])
  }

  if (meta.error_type === 'rate_limit') {
    meta.summary = `🚦 速率限制 (${meta.rate_limit || ''}${meta.wait_seconds ? `, 等待 ${meta.wait_seconds}s` : ''})`
  } else if (meta.error_type === 'context_overflow') {
    meta.summary = `📏 上下文超限 (最大 ${formatTokens(meta.max_context_tokens || 0)})`
  } else if (meta.error_type === 'auth_error') {
    meta.summary = '🔒 认证错误，请检查授权状态'
  } else {
    meta.summary = '⚠️ AI 服务错误'
  }

  return meta
}

export function formatErrorAsMessage(error: string, meta: any): string {
  const parts = ['**⚠️ AI 服务错误**\n']

  if (meta.error_type === 'rate_limit') {
    if (meta.rate_limit_count && meta.rate_limit_seconds) {
      parts.push(`> 🚦 **速率限制**: 每 ${meta.rate_limit_seconds}秒 最多 ${meta.rate_limit_count} 次请求`)
    }
    if (meta.wait_seconds) {
      parts.push(`> ⏱️ **等待**: ${meta.wait_seconds} 秒后可重试`)
    }
    parts.push('\n💡 *建议：稍后重新发送消息，或切换到其他模型*')
  } else if (meta.error_type === 'context_overflow') {
    const limit = meta.max_context_tokens
    if (limit) {
      parts.push(`> 📏 **上下文超限**: 模型最大 ${formatTokens(limit)} tokens`)
    }
    if (meta.requested_tokens) {
      parts.push(`> 📊 **实际请求**: ${formatTokens(meta.requested_tokens)} tokens`)
    }
    parts.push('\n💡 *建议：删除部分历史消息，或切换到上下文更大的模型*')
  } else if (meta.error_type === 'auth_error') {
    parts.push('> 🔒 **认证失败**: 请前往设置页面检查 Copilot 授权状态')
  } else {
    const brief = error.length > 300 ? error.slice(0, 300) + '...' : error
    parts.push('```\n' + brief + '\n```')
  }

  return parts.join('\n')
}

// ==================== 用户颜色映射 ====================

const userColorMap: Record<string, string> = {}
const userColors = ['#0ea5e9', '#a855f7', '#22c55e', '#f59e0b', '#ec4899', '#06b6d4', '#84cc16']
let colorIndex = 0

export function getUserColor(senderName: string): string {
  if (!senderName || senderName === 'assistant') return '#e94560'
  if (!userColorMap[senderName]) {
    userColorMap[senderName] = userColors[colorIndex % userColors.length]
    colorIndex++
  }
  return userColorMap[senderName]
}

// ==================== 工具显示 ====================

export const toolNames: Record<string, string> = {
  read_file: '📖 读取文件',
  search_text: '🔍 搜索',
  list_directory: '📂 列目录',
  get_file_tree: '🌳 目录树',
  ask_user: '❓ 提问',
}

export function toolDisplayName(name: string): string {
  return toolNames[name] || name
}

export function formatToolArgs(name: string, args: any): string {
  if (!args) return ''
  if (name === 'read_file') {
    let s = args.path || ''
    if (args.start_line) s += ` L${args.start_line}`
    if (args.end_line) s += `-${args.end_line}`
    return s
  }
  if (name === 'search_text') {
    let s = `"${args.query || ''}"`
    if (args.include_pattern) s += ` in ${args.include_pattern}`
    return s
  }
  if (name === 'list_directory' || name === 'get_file_tree') {
    return args.path || '.'
  }
  if (name === 'ask_user') {
    const qs = parseQuestions(args)
    return `${qs.length} 个问题`
  }
  return JSON.stringify(args)
}

// ==================== ask_user 问题解析 ====================

export interface QuestionOption {
  label: string
  description?: string
  recommended?: boolean
}

export interface ParsedQuestion {
  question: string
  type: 'single' | 'multi'
  options: QuestionOption[]
  context?: string
}

export function parseQuestions(args: any): ParsedQuestion[] {
  // Handle _raw format: when backend json.loads fails, arguments get wrapped as {"_raw": "<json string>"}
  let effectiveArgs = args
  if (args?._raw && !args?.questions) {
    try {
      effectiveArgs = typeof args._raw === 'string' ? JSON.parse(args._raw) : args._raw
    } catch { return [] }
  }
  if (!effectiveArgs?.questions) return []
  try {
    const qs = typeof effectiveArgs.questions === 'string' ? JSON.parse(effectiveArgs.questions) : effectiveArgs.questions
    if (!Array.isArray(qs)) return []
    return qs.map((q: any) => ({
      question: q.question || '',
      type: q.type === 'multi' ? 'multi' : 'single',
      options: (q.options || []).map((opt: any) =>
        typeof opt === 'string' ? { label: opt } : { label: opt.label || '', description: opt.description, recommended: !!opt.recommended }
      ),
      context: q.context,
    }))
  } catch { return [] }
}

export function getRecommendedLabels(q: ParsedQuestion): string {
  const recs = q.options?.filter(o => o.recommended)
  if (recs?.length) return recs.map(o => o.label).join('、')
  return ''
}
