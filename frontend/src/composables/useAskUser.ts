/**
 * ask_user 问题卡片状态管理 — 选项选择, 提交, 回答检测
 */
import { ref, type Ref } from 'vue'
import { parseQuestions, type ParsedQuestion } from './useChatUtils'

export interface QuestionCardState {
  answers: Record<number, string[]>  // questionIndex → selected labels
  customTexts: Record<number, string> // questionIndex → custom input text
  submitted: boolean
}

export function useAskUser(
  messages: Ref<any[]>,
  sendMessage: (content: string) => void,
) {
  const questionCardStates = ref<Record<string, QuestionCardState>>({})

  function getCardState(tcId: string): QuestionCardState {
    if (!questionCardStates.value[tcId]) {
      questionCardStates.value[tcId] = { answers: {}, customTexts: {}, submitted: false }
    }
    return questionCardStates.value[tcId]
  }

  function toggleOption(tcId: string, qi: number, label: string, type: 'single' | 'multi') {
    const state = getCardState(tcId)
    if (!state.answers[qi]) state.answers[qi] = []
    if (type === 'single') {
      state.answers[qi] = state.answers[qi][0] === label ? [] : [label]
    } else {
      const idx = state.answers[qi].indexOf(label)
      if (idx >= 0) state.answers[qi].splice(idx, 1)
      else state.answers[qi].push(label)
    }
  }

  function submitQuestionCard(tcId: string, questions: ParsedQuestion[]) {
    const state = getCardState(tcId)
    state.submitted = true

    const parts: string[] = []
    questions.forEach((q, qi) => {
      const selected = state.answers[qi] || []
      const custom = state.customTexts[qi]?.trim()
      if (selected.length || custom) {
        const answer = custom || selected.join('、')
        parts.push(`**${q.question}**\n${answer}`)
      }
    })

    if (parts.length === 0) {
      parts.push('以上问题由你来决定，请继续。')
    }

    const content = `<!-- ask_user_response -->\n${parts.join('\n\n')}`
    sendMessage(content)
  }

  // 判断 ask_user 是否已被回答
  function isAskUserAnswered(currentMsg: any, _tc: any): boolean {
    const idx = messages.value.findIndex((m: any) => m.id === currentMsg.id)
    if (idx < 0) return false
    for (let i = idx + 1; i < messages.value.length; i++) {
      const m = messages.value[i]
      if (m.role === 'user' && m.content?.startsWith('<!-- ask_user_response -->')) return true
      if (m.role === 'assistant') break
    }
    return false
  }

  function getAskUserAnswer(currentMsg: any): string {
    const idx = messages.value.findIndex((m: any) => m.id === currentMsg.id)
    if (idx < 0) return ''
    for (let i = idx + 1; i < messages.value.length; i++) {
      const m = messages.value[i]
      if (m.role === 'user' && m.content?.startsWith('<!-- ask_user_response -->')) {
        return m.content.replace('<!-- ask_user_response -->\n', '').replace('<!-- ask_user_response -->', '')
      }
      if (m.role === 'assistant') break
    }
    return ''
  }

  function isAskUserAutoDecided(msg: any, tc: any): boolean {
    const state = getCardState(tc.id)
    if (state.submitted) {
      const questions = parseQuestions(tc.arguments)
      return questions.every((_: any, qi: number) => !state.answers[qi]?.length && !state.customTexts[qi]?.trim())
    }
    const answer = getAskUserAnswer(msg)
    return answer.includes('以上问题由你来决定')
  }

  /**
   * 解析 DB 中存储的回答文本, 返回 { questionText → answerText } 映射
   * 格式: **问题文本**\n回答内容
   */
  function parseAnswerTextMap(answerText: string): Record<string, string> {
    const map: Record<string, string> = {}
    if (!answerText) return map
    // 按 **问题** 分段
    const blocks = answerText.split(/\n\n/).filter(Boolean)
    for (const block of blocks) {
      const match = block.match(/^\*\*(.+?)\*\*\n(.+)$/s)
      if (match) {
        map[match[1].trim()] = match[2].trim()
      }
    }
    return map
  }

  /**
   * 获取 DB 中回答里某个问题的答案 (用于逐题回显)
   */
  function getDbAnswerForQuestion(currentMsg: any, questionText: string): string {
    const raw = getAskUserAnswer(currentMsg)
    const map = parseAnswerTextMap(raw)
    return map[questionText] || ''
  }

  function getRegularToolCalls(toolCalls: any[] | undefined) {
    return (toolCalls || []).filter((tc: any) => tc.name !== 'ask_user')
  }

  return {
    questionCardStates,
    getCardState,
    toggleOption,
    submitQuestionCard,
    isAskUserAnswered,
    getAskUserAnswer,
    isAskUserAutoDecided,
    getDbAnswerForQuestion,
    getRegularToolCalls,
  }
}
