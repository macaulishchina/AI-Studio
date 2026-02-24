/**
 * SSE 敲定方案流式处理 — handleFinalizePlan + handleSSEResponse
 * 管理: streaming, streamContent, streamThinking, streamToolCalls, streamSegments
 */
import { ref, type Ref } from 'vue'
import { useMessage } from 'naive-ui'
import { discussionApi, tasksApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { parseErrorMeta, formatErrorAsMessage, formatTokens } from './useChatUtils'

interface FinalizeOptions {
  projectId: () => number
  selectedModel: Ref<string>
  messages: Ref<any[]>
  persistentContextInfo: Ref<any>
  scrollToBottom: () => void
  onPlanFinalized: () => void
}

export function useSSEFinalize(options: FinalizeOptions) {
  const message = useMessage()
  const authStore = useAuthStore()
  const studioConfig = useStudioConfigStore()

  const streaming = ref(false)
  const streamContent = ref('')
  const streamThinking = ref('')
  const streamToolCalls = ref<Array<{
    id: string; name: string; arguments: any
    status: 'calling' | 'done' | 'error' | 'preparing'
    result?: string; duration_ms?: number
  }>>([])
  const streamSegments = ref<Array<{
    type: 'content' | 'tool'
    text?: string
    toolCall?: { id: string; name: string; arguments: any; status: string; result?: string; duration_ms?: number }
  }>>([])

  const finalizingPlan = ref(false)
  const abortController = ref<AbortController | null>(null)
  const currentTaskId = ref<number | null>(null)
  const tokenUsage = ref<any>(null)
  const lastTokenUsage = ref<any>(null)
  const summaryNotice = ref<string>('')
  let sseContentSaved = false

  function appendStreamContent(text: string) {
    streamContent.value += text
    const segs = streamSegments.value
    const last = segs[segs.length - 1]
    if (last && last.type === 'content') {
      last.text = (last.text || '') + text
    } else {
      segs.push({ type: 'content', text })
    }
  }

  async function handleSSEResponse(response: Response) {
    const reader = response.body?.getReader()
    const decoder = new TextDecoder()
    if (!reader) throw new Error('No response body')

    let savedThinking = ''
    let savedToolCalls: any[] = []
    sseContentSaved = false
    let streamTruncated = false
    streamToolCalls.value = []
    streamSegments.value = []
    let totalEventsProcessed = 0

    let sseBuffer = ''  // SSE 行缓冲: 跨 chunk 拼接不完整的行
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      sseBuffer += decoder.decode(value, { stream: true })
      const parts = sseBuffer.split('\n')
      // 最后一段可能是不完整的行, 留到下一次 read 拼接
      sseBuffer = parts.pop() || ''

      for (const line of parts) {
        if (!line.startsWith('data: ')) continue
        totalEventsProcessed++
        try {
          const data = JSON.parse(line.slice(6))
          if (data.type === 'content') {
            appendStreamContent(data.content)
          } else if (data.type === 'thinking') {
            streamThinking.value += data.content
            savedThinking += data.content
          } else if (data.type === 'context') {
            options.persistentContextInfo.value = data.context
          } else if (data.type === 'summary') {
            summaryNotice.value = data.summary
          } else if (data.type === 'tool_call_start') {
            const tc_data = data.tool_call || data
            const toolCall = { id: tc_data.id || '', name: tc_data.name || '', arguments: null as any, status: 'preparing' as const }
            streamToolCalls.value.push(toolCall)
            streamSegments.value.push({ type: 'tool', toolCall })
          } else if (data.type === 'tool_call') {
            const tc_data = data.tool_call || data
            const tcId = tc_data.id || data.tool_call_id || ''
            const existingTc = streamToolCalls.value.find(t => t.id === tcId)
            if (existingTc) {
              existingTc.arguments = tc_data.arguments || data.arguments || ''
              existingTc.status = 'calling'
            } else {
              const toolCall = { id: tcId, name: tc_data.name || data.name || '', arguments: tc_data.arguments || data.arguments || '', status: 'calling' as const }
              streamToolCalls.value.push(toolCall)
              streamSegments.value.push({ type: 'tool', toolCall })
            }
          } else if (data.type === 'tool_result') {
            const tc = streamToolCalls.value.find(t => t.id === data.tool_call_id)
            if (tc) { tc.status = 'done'; tc.result = data.result; tc.duration_ms = data.duration_ms }
            savedToolCalls = [...streamToolCalls.value]
          } else if (data.type === 'tool_error') {
            const tc = streamToolCalls.value.find(t => t.id === data.tool_call_id)
            if (tc) { tc.status = 'error'; tc.result = data.error; tc.duration_ms = data.duration_ms }
            savedToolCalls = [...streamToolCalls.value]
          } else if (data.type === 'truncated') {
            streamTruncated = true
          } else if (data.type === 'usage') {
            tokenUsage.value = data.usage
            lastTokenUsage.value = data.usage
          } else if (data.type === 'heartbeat' || data.type === 'ask_user_pending' || data.type === 'command_approval_request') {
            // ignore (command_approval_request handled by project event bus)
          } else if (data.type === 'cancelled') {
            break
          } else if (data.type === 'done') {
            if (streamContent.value || savedToolCalls.length) {
              options.messages.value.push({
                id: data.message_id || Date.now(),
                role: 'assistant',
                sender_name: options.selectedModel.value,
                content: streamContent.value || '',
                model_used: options.selectedModel.value,
                thinking_content: savedThinking || null,
                tool_calls: savedToolCalls.length ? savedToolCalls : null,
                token_usage: tokenUsage.value || null,
                created_at: new Date().toISOString(),
              })
              sseContentSaved = true
            }
          } else if (data.type === 'error') {
            const errorMeta = parseErrorMeta(data.error, data.error_meta)
            if (!streamContent.value && !sseContentSaved) {
              options.messages.value.push({
                id: Date.now(), role: 'assistant', sender_name: options.selectedModel.value,
                content: formatErrorAsMessage(data.error, errorMeta),
                model_used: options.selectedModel.value,
                thinking_content: savedThinking || null,
                tool_calls: savedToolCalls.length ? savedToolCalls : null,
                token_usage: tokenUsage.value || null,
                created_at: new Date().toISOString(),
              })
              sseContentSaved = true
              if (errorMeta.max_context_tokens || errorMeta.rate_limit) {
                studioConfig.updateModelCapability(options.selectedModel.value, errorMeta)
              }
            } else if (streamContent.value && !sseContentSaved) {
              options.messages.value.push({
                id: Date.now(), role: 'assistant', sender_name: options.selectedModel.value,
                content: streamContent.value + '\n\n---\n' + formatErrorAsMessage(data.error, errorMeta),
                model_used: options.selectedModel.value,
                thinking_content: savedThinking || null,
                tool_calls: savedToolCalls.length ? savedToolCalls : null,
                token_usage: tokenUsage.value || null,
                created_at: new Date().toISOString(),
              })
              sseContentSaved = true
            }
            message.warning(errorMeta.summary || '⚠️ AI 服务错误', { duration: 10000 })
          }
        } catch {}

        if (totalEventsProcessed % 5 === 0) {
          await new Promise(r => setTimeout(r, 0))
          options.scrollToBottom()
        }
      }
      options.scrollToBottom()
    }

    // 兜底保存
    if ((streamContent.value || savedToolCalls.length) && !sseContentSaved) {
      options.messages.value.push({
        id: Date.now(), role: 'assistant', sender_name: options.selectedModel.value,
        content: streamContent.value || '',
        model_used: options.selectedModel.value,
        thinking_content: savedThinking || null,
        tool_calls: savedToolCalls.length ? savedToolCalls : null,
        token_usage: tokenUsage.value || null,
        created_at: new Date().toISOString(),
      })
    }

    return { truncated: streamTruncated }
  }

  async function handleFinalizePlan() {
    finalizingPlan.value = true
    streaming.value = true
    streamContent.value = ''
    streamThinking.value = ''
    streamSegments.value = []
    abortController.value = new AbortController()

    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (authStore.token) headers['Authorization'] = `Bearer ${authStore.token}`

      const response = await fetch(discussionApi.finalizePlanUrl(options.projectId()), {
        method: 'POST',
        headers,
        signal: abortController.value.signal,
      })

      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      if (!reader) throw new Error('No response body')

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const text = decoder.decode(value, { stream: true })
        for (const line of text.split('\n')) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'content') {
              appendStreamContent(data.content)
              options.scrollToBottom()
            } else if (data.type === 'thinking') {
              streamThinking.value += data.content
              options.scrollToBottom()
            } else if (data.type === 'done') {
              message.success(`设计稿已生成 (v${data.plan_version})`)
              options.onPlanFinalized()
            } else if (data.type === 'error') {
              message.error(data.error)
            }
          } catch {}
        }
      }

      if (streamContent.value) {
        options.messages.value.push({
          id: Date.now(),
          role: 'assistant',
          sender_name: `Plan Generator (${options.selectedModel.value})`,
          content: streamContent.value,
          message_type: 'plan_final',
          created_at: new Date().toISOString(),
        })
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        message.error('Plan 生成异常: ' + (e.message || ''))
      }
    } finally {
      finalizingPlan.value = false
      streaming.value = false
      streamContent.value = ''
      streamThinking.value = ''
      streamSegments.value = []
      abortController.value = null
      options.scrollToBottom()
    }
  }

  function stopFinalizeStreaming() {
    abortController.value?.abort()
    if (currentTaskId.value) {
      tasksApi.cancel(currentTaskId.value).catch(() => {})
      currentTaskId.value = null
    }
    if (streamContent.value) {
      options.messages.value.push({
        id: Date.now(),
        role: 'assistant',
        sender_name: options.selectedModel.value,
        content: streamContent.value + '\n\n---\n*⏹ 已手动停止*',
        model_used: options.selectedModel.value,
        thinking_content: streamThinking.value || null,
        tool_calls: streamToolCalls.value.length ? [...streamToolCalls.value] : null,
        created_at: new Date().toISOString(),
      })
    }
    streaming.value = false
    streamContent.value = ''
    streamThinking.value = ''
    streamToolCalls.value = []
    streamSegments.value = []
    abortController.value = null
    options.scrollToBottom()
  }

  return {
    streaming,
    streamContent,
    streamThinking,
    streamToolCalls,
    streamSegments,
    finalizingPlan,
    lastTokenUsage,
    summaryNotice,
    appendStreamContent,
    handleSSEResponse,
    handleFinalizePlan,
    stopFinalizeStreaming,
  }
}
