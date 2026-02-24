/**
 * 上下文信息管理 — 上下文使用率显示, 总结, 清空, 模型切换检查
 */
import { ref, reactive, computed, type Ref } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { discussionApi, projectApi } from '@/api'
import { formatTokens } from './useChatUtils'

interface ContextInfoOptions {
  projectId: () => number
  selectedModel: Ref<string>
  selectedModelMaxTokens: Ref<number>
  messages: Ref<any[]>
  scrollToBottom: () => void
}

export function useContextInfo(options: ContextInfoOptions) {
  const message = useMessage()
  const dialog = useDialog()

  const persistentContextInfo = ref<any>(null)
  const contextCompressing = ref(false)
  const summarizing = ref(false)
  let contextCheckVersion = 0

  // 内容查看器状态
  const ctxContentModal = ref(false)
  const ctxContentTitle = ref('')
  const ctxContentText = ref('')
  const ctxExpanded = reactive<Record<string, boolean>>({})

  function refreshContextInfo() {
    const model = options.selectedModel.value
    if (!model || !options.projectId()) return
    discussionApi.checkContext(options.projectId(), model).then(({ data: ctx }) => {
      if (ctx?.context) persistentContextInfo.value = ctx.context
    }).catch(() => {})
  }

  // 始终显示的上下文信息 (分母跟随活跃模型)
  const displayContextInfo = computed(() => {
    const total = options.selectedModelMaxTokens.value
    if (persistentContextInfo.value) {
      const used = persistentContextInfo.value.used || 0
      const effectiveTotal = total || persistentContextInfo.value.total || 1
      const percentage = Math.min(100, Math.round(used * 100 / Math.max(effectiveTotal, 1)))
      return { used, total: effectiveTotal, percentage }
    }
    return { used: 0, total: total || 0, percentage: 0 }
  })

  const ctxBreakdown = computed(() => {
    const bd = persistentContextInfo.value?.breakdown
    return { system: bd?.system || 0, tools: bd?.tools || 0, history: bd?.history || 0 }
  })

  const ctxMessages = computed(() => {
    const m = persistentContextInfo.value?.messages
    return { kept: m?.kept || 0, dropped: m?.dropped || 0, total: m?.total || 0 }
  })

  const ctxBreakdownPercents = computed(() => {
    const total = displayContextInfo.value.total || 1
    return {
      system: Math.round(ctxBreakdown.value.system * 100 / total),
      tools: Math.round(ctxBreakdown.value.tools * 100 / total),
      history: Math.round(ctxBreakdown.value.history * 100 / total),
    }
  })

  const ctxSystemSections = computed(() => {
    return persistentContextInfo.value?.system_sections || []
  })

  const ctxHistoryDetail = computed(() => {
    return persistentContextInfo.value?.history_detail || []
  })

  function openCtxContent(name: string, content?: string) {
    if (!content) return
    ctxContentTitle.value = name
    ctxContentText.value = content
    ctxContentModal.value = true
  }

  async function handleSummarize() {
    summarizing.value = true
    try {
      const { data } = await discussionApi.summarizeContext(options.projectId())
      message.success(`已总结 ${data.summarized_count} 条旧消息 → 1 条摘要`)
      const { data: msgs } = await discussionApi.getMessages(options.projectId())
      options.messages.value = msgs
      options.scrollToBottom()
      refreshContextInfo()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '总结失败')
    } finally {
      summarizing.value = false
    }
  }

  function handleClearContext() {
    dialog.warning({
      title: '确认清空',
      content: '将删除所有讨论消息，此操作不可撤销。确定清空？',
      positiveText: '清空',
      negativeText: '取消',
      onPositiveClick: async () => {
        try {
          await discussionApi.clearContext(options.projectId())
          options.messages.value = []
          persistentContextInfo.value = null
          message.success('已清空所有讨论消息')
        } catch (e: any) {
          message.error(e.response?.data?.detail || '清空失败')
        }
      },
    })
  }

  async function handleModelChange(val: string) {
    try {
      await projectApi.update(options.projectId(), { discussion_model: val })
    } catch {}

    const myVersion = ++contextCheckVersion
    contextCompressing.value = true
    try {
      const { data } = await discussionApi.checkContext(options.projectId(), val)
      if (myVersion !== contextCheckVersion) return
      if (data.context) {
        persistentContextInfo.value = data.context
      }
      if (data.summarized && data.summary_text) {
        message.info('上下文已自动压缩以适应新模型窗口')
      }
    } catch {} finally {
      if (myVersion === contextCheckVersion) {
        contextCompressing.value = false
      }
    }
  }

  return {
    persistentContextInfo,
    contextCompressing,
    summarizing,
    ctxContentModal,
    ctxContentTitle,
    ctxContentText,
    ctxExpanded,
    displayContextInfo,
    ctxBreakdown,
    ctxMessages,
    ctxBreakdownPercents,
    ctxSystemSections,
    ctxHistoryDetail,
    refreshContextInfo,
    openCtxContent,
    handleSummarize,
    handleClearContext,
    handleModelChange,
  }
}
