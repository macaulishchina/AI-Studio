<template>
  <div class="chat-home">
    <!-- 侧边栏: 历史对话列表 -->
    <div class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="sidebar-header">
        <n-button quaternary size="small" @click="createConversation" style="flex: 1;">
          <template #icon><n-icon :component="AddOutline" /></template>
          新对话
        </n-button>
        <n-button quaternary size="small" circle @click="sidebarCollapsed = !sidebarCollapsed">
          <template #icon><n-icon :component="MenuOutline" /></template>
        </n-button>
      </div>
      <div class="sidebar-list">
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: conv.id === activeConversationId }"
          @click="openConversation(conv.id)"
        >
          <div class="conv-title">{{ conv.title || '新对话' }}</div>
          <div class="conv-time">{{ formatTime(conv.updated_at) }}</div>
        </div>
        <div v-if="conversations.length === 0" class="empty-hint">
          还没有对话，开始吧~
        </div>
      </div>
    </div>

    <!-- 主区域 -->
    <div class="main-area">
      <!-- 无对话选中: 欢迎页 -->
      <div v-if="!activeConversationId" class="welcome">
        <div class="welcome-logo">🐕</div>
        <h1 class="welcome-title">Dogi</h1>
        <p class="welcome-subtitle">有什么我可以帮你的？</p>

        <div class="quick-actions">
          <div class="quick-card" v-for="item in quickActions" :key="item.label" @click="sendQuick(item.prompt)">
            <n-icon :component="item.icon" :size="20" color="#7c6cff" />
            <span>{{ item.label }}</span>
          </div>
        </div>

        <div class="input-area welcome-input">
          <n-input
            v-model:value="inputText"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
            placeholder="输入消息，开始新对话..."
            @keydown="handleKeydown"
            :disabled="sending"
          />
          <div class="input-actions">
            <ModelSelector
              :model="selectedModel"
              @update:model="selectedModel = $event"
            />
            <n-button
              type="primary"
              :loading="sending"
              :disabled="!inputText.trim()"
              @click="sendNewMessage"
              circle
              size="small"
            >
              <template #icon><n-icon :component="SendOutline" /></template>
            </n-button>
          </div>
        </div>
      </div>

      <!-- 有对话选中: 聊天视图 -->
      <div v-else class="chat-view">
        <div class="chat-header">
          <h3>{{ activeConversation?.title || '新对话' }}</h3>
          <div class="chat-header-actions">
            <ModelSelector
              :model="activeConversation?.model || selectedModel"
              @update:model="updateConversationModel($event)"
              size="small"
            />
          </div>
        </div>

        <div class="messages-area" ref="messagesRef">
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="message"
            :class="msg.role"
          >
            <div class="message-avatar">
              {{ msg.role === 'user' ? '👤' : '🐕' }}
            </div>
            <div class="message-content">
              <div class="message-sender">{{ msg.role === 'user' ? msg.sender_name : 'Dogi' }}</div>
              <div class="message-text" v-html="renderMarkdown(msg.content)"></div>
              <div v-if="msg.thinking_content" class="message-thinking">
                <details>
                  <summary>💭 思考过程</summary>
                  <div v-html="renderMarkdown(msg.thinking_content)"></div>
                </details>
              </div>
              <div v-if="msg.tool_calls?.length" class="message-tools">
                <details>
                  <summary>🔧 调用了 {{ msg.tool_calls.length }} 个工具</summary>
                  <div v-for="tc in msg.tool_calls" :key="tc.id" class="tool-call-item">
                    <code>{{ tc.name }}</code>
                    <span v-if="tc.result" class="tool-result">✓</span>
                  </div>
                </details>
              </div>
            </div>
          </div>

          <!-- AI 正在生成 -->
          <div v-if="streaming" class="message assistant streaming">
            <div class="message-avatar">🐕</div>
            <div class="message-content">
              <div class="message-sender">Dogi</div>
              <div class="message-text" v-html="renderMarkdown(streamContent)"></div>
              <div v-if="streamThinking" class="message-thinking active">
                💭 正在思考...
              </div>
            </div>
          </div>
        </div>

        <div class="input-area">
          <n-input
            v-model:value="inputText"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
            placeholder="输入消息..."
            @keydown="handleKeydown"
            :disabled="sending"
          />
          <div class="input-actions">
            <n-button
              v-if="streaming"
              type="error"
              size="small"
              @click="cancelStreaming"
              quaternary
            >
              停止
            </n-button>
            <n-button
              type="primary"
              :loading="sending"
              :disabled="!inputText.trim() || streaming"
              @click="sendMessage"
              circle
              size="small"
            >
              <template #icon><n-icon :component="SendOutline" /></template>
            </n-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick, defineComponent, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage, NButton, NInput, NIcon } from 'naive-ui'
import { AddOutline, SendOutline, MenuOutline, CodeSlashOutline, ChatbubblesOutline, BookOutline, ConstructOutline } from '@vicons/ionicons5'
import { conversationApi } from '@/api'
import { marked } from 'marked'

// ========== Model Selector (inline mini component) ==========
const ModelSelector = defineComponent({
  name: 'ModelSelector',
  props: {
    model: { type: String, default: 'gpt-4o' },
    size: { type: String, default: 'small' },
  },
  emits: ['update:model'],
  setup(props, { emit }) {
    // Simplified model display; full model selection can be added later
    return () => h('span', {
      class: 'model-badge',
      title: props.model,
      onClick: () => {
        // TODO: open model picker popover
      }
    }, props.model.replace('copilot:', '').split('/').pop())
  }
})

// ========== State ==========
const route = useRoute()
const router = useRouter()
const message = useMessage()

const sidebarCollapsed = ref(false)
const inputText = ref('')
const sending = ref(false)
const streaming = ref(false)
const streamContent = ref('')
const streamThinking = ref('')
const selectedModel = ref('gpt-4o')
const messagesRef = ref<HTMLElement>()

// Conversations list
const conversations = ref<any[]>([])
const activeConversationId = computed(() => {
  const id = route.params.id
  return id ? Number(id) : null
})
const activeConversation = computed(() =>
  conversations.value.find(c => c.id === activeConversationId.value)
)

// Messages for active conversation
const messages = ref<any[]>([])

// Quick actions for welcome page
const quickActions = [
  { label: '写代码', prompt: '帮我写一个', icon: CodeSlashOutline },
  { label: '聊一聊', prompt: '', icon: ChatbubblesOutline },
  { label: '学知识', prompt: '给我讲讲', icon: BookOutline },
  { label: '用工具', prompt: '帮我用工具', icon: ConstructOutline },
]

// ========== API Methods ==========
async function loadConversations() {
  try {
    const res = await conversationApi.list()
    conversations.value = res.data
  } catch (e: any) {
    // API not ready yet — silent
    console.warn('conversations API not ready:', e.message)
    conversations.value = []
  }
}

async function loadMessages(convId: number) {
  try {
    const res = await conversationApi.getMessages(convId)
    messages.value = res.data
    await nextTick()
    scrollToBottom()
  } catch (e: any) {
    console.warn('load messages failed:', e.message)
    messages.value = []
  }
}

async function createConversation() {
  try {
    const res = await conversationApi.create({ model: selectedModel.value })
    conversations.value.unshift(res.data)
    router.push(`/c/${res.data.id}`)
  } catch (e: any) {
    message.error('创建对话失败: ' + e.message)
  }
}

async function openConversation(id: number) {
  router.push(`/c/${id}`)
}

function updateConversationModel(model: string) {
  selectedModel.value = model
  // TODO: update server-side conversation model
}

// ========== Chat Logic ==========
async function sendNewMessage() {
  if (!inputText.value.trim()) return
  const text = inputText.value.trim()
  inputText.value = ''
  sending.value = true

  try {
    // Create conversation first
    const res = await conversationApi.create({
      model: selectedModel.value,
      title: text.slice(0, 50),
    })
    const conv = res.data
    conversations.value.unshift(conv)

    // Navigate to it
    await router.push(`/c/${conv.id}`)

    // Wait for route to settle, then send message
    await nextTick()
    await doSendMessage(conv.id, text)
  } catch (e: any) {
    message.error('发送失败: ' + e.message)
  } finally {
    sending.value = false
  }
}

async function sendMessage() {
  if (!inputText.value.trim() || !activeConversationId.value) return
  const text = inputText.value.trim()
  inputText.value = ''
  await doSendMessage(activeConversationId.value, text)
}

async function doSendMessage(convId: number, text: string) {
  sending.value = true
  streaming.value = true
  streamContent.value = ''
  streamThinking.value = ''

  // Optimistic: add user message to display
  messages.value.push({
    id: Date.now(),
    role: 'user',
    sender_name: 'user',
    content: text,
    created_at: new Date().toISOString(),
  })
  await nextTick()
  scrollToBottom()

  try {
    const res = await conversationApi.discuss(convId, {
      message: text,
      sender_name: 'user',
    })
    const taskId = res.data.task_id
    if (taskId) {
      await subscribeToTask(taskId, convId)
    }
  } catch (e: any) {
    message.error('对话失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    sending.value = false
    streaming.value = false
    // Reload messages to get canonical data
    if (convId) await loadMessages(convId)
    // Reload conversation list to get updated title
    await loadConversations()
  }
}

async function subscribeToTask(taskId: number, convId: number) {
  const url = `/studio-api/tasks/${taskId}/stream`
  const eventSource = new EventSource(url)

  return new Promise<void>((resolve) => {
    eventSource.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        switch (data.type) {
          case 'content':
          case 'content_delta':
            streamContent.value += data.content || ''
            scrollToBottom()
            break
          case 'thinking':
          case 'thinking_delta':
            streamThinking.value += data.content || ''
            break
          case 'done':
            eventSource.close()
            resolve()
            break
          case 'error':
            message.error('AI 错误: ' + (data.error || '未知'))
            eventSource.close()
            resolve()
            break
        }
      } catch (err) {
        // ignore parse errors
      }
    }
    eventSource.onerror = () => {
      eventSource.close()
      resolve()
    }
  })
}

function cancelStreaming() {
  streaming.value = false
  // TODO: call cancel task API
}

function sendQuick(prompt: string) {
  inputText.value = prompt
}

// ========== Helpers ==========
function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (activeConversationId.value) {
      sendMessage()
    } else {
      sendNewMessage()
    }
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return marked.parse(text, { breaks: true }) as string
  } catch {
    return text
  }
}

function formatTime(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

// ========== Lifecycle ==========
onMounted(() => {
  loadConversations()
})

// Watch route changes to load conversation messages
watch(activeConversationId, async (id) => {
  if (id) {
    await loadMessages(id)
  } else {
    messages.value = []
  }
}, { immediate: true })
</script>

<style scoped>
.chat-home {
  display: flex;
  height: 100%;
  background: #1a1a1a;
  color: #e0e0e0;
}

/* ── Sidebar ── */
.sidebar {
  width: 260px;
  background: #171717;
  border-right: 1px solid #2a2a2a;
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;
  flex-shrink: 0;
}
.sidebar.collapsed {
  width: 0;
  overflow: hidden;
  border-right: none;
}
.sidebar-header {
  padding: 12px;
  display: flex;
  align-items: center;
  gap: 4px;
  border-bottom: 1px solid #2a2a2a;
}
.sidebar-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.conv-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 2px;
}
.conv-item:hover {
  background: #252525;
}
.conv-item.active {
  background: #2a2a2a;
}
.conv-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-time {
  font-size: 11px;
  color: #666;
  margin-top: 2px;
}
.empty-hint {
  text-align: center;
  color: #555;
  padding: 40px 16px;
  font-size: 13px;
}

/* ── Main Area ── */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* ── Welcome ── */
.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  gap: 8px;
}
.welcome-logo {
  font-size: 64px;
  line-height: 1;
}
.welcome-title {
  font-size: 28px;
  font-weight: 600;
  color: #fff;
  margin: 8px 0 0;
}
.welcome-subtitle {
  color: #888;
  font-size: 16px;
  margin: 0 0 24px;
}
.quick-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
  margin-bottom: 32px;
}
.quick-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  background: #212121;
  border: 1px solid #333;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.15s;
  font-size: 13px;
}
.quick-card:hover {
  border-color: #7c6cff;
  background: #252525;
}

/* ── Input Area ── */
.input-area {
  padding: 12px 16px;
  border-top: 1px solid #2a2a2a;
}
.welcome-input {
  width: 100%;
  max-width: 640px;
  border-top: none;
  padding: 0;
}
.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

/* ── Chat View ── */
.chat-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #2a2a2a;
  flex-shrink: 0;
}
.chat-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
}
.chat-header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* ── Messages ── */
.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  max-width: 800px;
  margin-left: auto;
  margin-right: auto;
}
.message-avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
  background: #252525;
}
.message.user .message-avatar {
  background: #2a2a2a;
}
.message.assistant .message-avatar {
  background: #2c2640;
}
.message-content {
  flex: 1;
  min-width: 0;
}
.message-sender {
  font-size: 12px;
  color: #888;
  margin-bottom: 4px;
}
.message-text {
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.message-text :deep(pre) {
  background: #141414;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  margin: 8px 0;
}
.message-text :deep(code) {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 13px;
}
.message-text :deep(p) {
  margin: 4px 0;
}
.message-thinking {
  margin-top: 8px;
  font-size: 12px;
  color: #666;
}
.message-thinking.active {
  color: #7c6cff;
}
.message-tools {
  margin-top: 8px;
  font-size: 12px;
  color: #666;
}
.tool-call-item {
  padding: 2px 0;
}
.tool-result {
  color: #4caf50;
  margin-left: 4px;
}
.message.streaming .message-text::after {
  content: '▊';
  animation: blink 0.7s infinite;
  color: #7c6cff;
}
@keyframes blink {
  50% { opacity: 0; }
}

/* ── Model badge ── */
.model-badge {
  display: inline-flex;
  padding: 2px 8px;
  background: #252525;
  border: 1px solid #333;
  border-radius: 6px;
  font-size: 11px;
  color: #aaa;
  cursor: pointer;
  white-space: nowrap;
}
.model-badge:hover {
  border-color: #7c6cff;
  color: #ccc;
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .sidebar {
    display: none;
  }
  .welcome-title {
    font-size: 22px;
  }
  .quick-actions {
    flex-direction: column;
  }
}
</style>
