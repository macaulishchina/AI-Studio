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
      <!-- 侧边栏折叠时的全局恢复按钮 (仅在 Welcome 页显示绝对定位按钮，Chat 页集成在 Header 中) -->
      <div v-if="sidebarCollapsed && !activeConversationId" class="sidebar-toggle-float">
        <n-button quaternary circle @click="sidebarCollapsed = false">
          <template #icon><n-icon :component="MenuOutline" /></template>
        </n-button>
      </div>

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
          <div class="header-left" style="display: flex; align-items: center; gap: 8px;">
            <n-button
              v-if="sidebarCollapsed"
              quaternary
              circle
              size="small"
              @click="sidebarCollapsed = false"
            >
              <template #icon><n-icon :component="MenuOutline" /></template>
            </n-button>
            <h3>{{ activeConversation?.title || '新对话' }}</h3>
          </div>
          <div class="chat-header-actions">
            <n-select
              v-model:value="selectedModel"
              :options="modelOptions"
              :render-label="renderModelLabel"
              size="small"
              filterable
              :consistent-menu-width="false"
              style="min-width: 180px; max-width: 300px"
              @update:value="updateConversationModel($event)"
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
import { useMessage, NButton, NInput, NIcon, NSelect } from 'naive-ui'
import { AddOutline, SendOutline, MenuOutline, CodeSlashOutline, ChatbubblesOutline, BookOutline, ConstructOutline } from '@vicons/ionicons5'
import { conversationApi } from '@/api'
import { useModelSelection } from '@/composables/useModelSelection'
import { marked } from 'marked'

// ========== Model Selection (via composable) ==========
const {
  selectedModel: modelSelectionModel,
  modelOptions,
  renderModelLabel,
  loadModels,
} = useModelSelection('gpt-4o', { useGlobalDefault: true, applySourceFilter: false })

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
const selectedModel = modelSelectionModel  // use shared ref
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

async function updateConversationModel(model: string) {
  selectedModel.value = model
  // 如果有活跃对话, 更新服务端对话模型
  const convId = activeConversationId.value
  if (convId) {
    try {
      await conversationApi.update(convId, { model })
    } catch (e: any) {
      console.warn('更新对话模型失败:', e)
    }
  }
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
  // 后端常返回 UTC 时间但不带时区后缀，前端需按 UTC 解析避免出现“新建即 8 小时前”
  const normalized = (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(dateStr))
    ? `${dateStr}Z`
    : dateStr
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return ''
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
  loadModels()
})

// Watch route changes to load conversation messages
watch(activeConversationId, async (id) => {
  if (id) {
    await loadMessages(id)
    // 同步对话绑定的模型到选择器
    const conv = activeConversation.value
    if (conv?.model) {
      selectedModel.value = conv.model
    }
  } else {
    messages.value = []
  }
}, { immediate: true })
</script>

<style scoped>
.chat-home {
  display: flex;
  height: 100%;
  background: #101014; /* 更深邃的背景 */
  color: #e0e0e0;
}

/* ── Sidebar ── */
.sidebar {
  width: 280px;
  background: #18181c;
  border-right: 1px solid #2d2d30;
  display: flex;
  flex-direction: column;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
}
.sidebar.collapsed {
  width: 0;
  overflow: hidden;
  border-right: none;
  opacity: 0;
}
.sidebar-header {
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  /* border-bottom: 1px solid #2d2d30; */
}
.sidebar-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}
.sidebar-list::-webkit-scrollbar {
  width: 4px;
}
.sidebar-list::-webkit-scrollbar-thumb {
  background: #333;
  border-radius: 2px;
}
.conv-item {
  padding: 12px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 4px;
  border: 1px solid transparent;
}
.conv-item:hover {
  background: #26262a;
}
.conv-item.active {
  background: #2b2b30;
  border-color: #3a3a40;
}
.conv-title {
  font-size: 14px;
  font-weight: 500;
  color: #eee;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-time {
  font-size: 11px;
  color: #777;
  margin-top: 4px;
}
.empty-hint {
  text-align: center;
  color: #555;
  padding: 60px 16px;
  font-size: 13px;
}

/* ── Main Area ── */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  position: relative;
  background-image: radial-gradient(circle at 50% 0%, #1e1e24 0%, #101014 60%);
}

.sidebar-toggle-float {
  position: absolute;
  top: 16px;
  left: 16px;
  z-index: 20;
}

/* ── Welcome ── */
.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  max-width: 800px;
  margin: 0 auto;
  width: 100%;
}
.welcome-logo {
  font-size: 72px;
  margin-bottom: 16px;
  filter: drop-shadow(0 0 20px rgba(124, 108, 255, 0.2));
}
.welcome-title {
  font-size: 32px;
  font-weight: 700;
  color: #fff;
  margin: 0;
  background: linear-gradient(135deg, #fff 0%, #a5a5a5 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.welcome-subtitle {
  color: #888;
  font-size: 16px;
  margin: 12px 0 40px;
}
.quick-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
  margin-bottom: 40px;
}
.quick-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  background: #1e1e22;
  border: 1px solid #333;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 14px;
  color: #ccc;
}
.quick-card:hover {
  border-color: #7c6cff;
  background: #252529;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  color: #fff;
}

/* ── Input Area ── */
.input-area {
  padding: 20px 0;
  /* border-top: 1px solid #2a2a2a; */
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
  position: relative;
}
.welcome-input {
  /* Welcome 页特殊的样式 */
  background: #1e1e22;
  border: 1px solid #333;
  border-radius: 16px;
  padding: 12px 16px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
  transition: all 0.2s;
}
.welcome-input:focus-within {
  border-color: #7c6cff;
  box-shadow: 0 8px 24px rgba(124, 108, 255, 0.15);
}
.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

/* ── Chat View ── */
.chat-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  /* border-bottom: 1px solid #2a2a2a; */
  flex-shrink: 0;
  /* background: rgba(16, 16, 20, 0.8); */
  /* backdrop-filter: blur(10px); */
  z-index: 10;
}
.chat-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #ddd;
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
  padding: 20px 24px;
  scroll-behavior: smooth;
}
.messages-area::-webkit-scrollbar {
  width: 6px;
}
.messages-area::-webkit-scrollbar-thumb {
  background: #333;
  border-radius: 3px;
}
.message {
  display: flex;
  gap: 16px;
  margin-bottom: 32px;
  max-width: 800px;
  margin-left: auto;
  margin-right: auto;
}
.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
  background: #1e1e22;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}
.message.user .message-avatar {
  background: #2a2a30;
}
.message.assistant .message-avatar {
  background: linear-gradient(135deg, #2c2640 0%, #3d3459 100%);
}
.message-content {
  flex: 1;
  min-width: 0;
  padding-top: 6px;
}
.message-sender {
  font-size: 13px;
  font-weight: 600;
  color: #999;
  margin-bottom: 6px;
}
.message-text {
  font-size: 15px;
  line-height: 1.7;
  color: #eee;
  word-break: break-word;
}
.message-text :deep(pre) {
  background: #18181c;
  border: 1px solid #2d2d30;
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
  margin: 12px 0;
}
.message-text :deep(code) {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 13px;
  background: rgba(255, 255, 255, 0.1);
  padding: 2px 4px;
  border-radius: 4px;
}
.message-text :deep(pre) :deep(code) {
  background: transparent;
  padding: 0;
}
.message-text :deep(p) {
  margin: 8px 0;
}
.message-text :deep(ul), .message-text :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}
.message-thinking {
  margin-top: 12px;
  font-size: 13px;
  color: #777;
  background: #18181c;
  padding: 8px 12px;
  border-radius: 8px;
  border-left: 3px solid #444;
}
.message-thinking.active {
  color: #7c6cff;
  border-left-color: #7c6cff;
  background: rgba(124, 108, 255, 0.05);
}
.message-tools {
  margin-top: 12px;
}
.tool-call-item {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  background: #18181c;
  border-radius: 4px;
  border: 1px solid #333;
  font-size: 12px;
  color: #888;
  margin-right: 6px;
  margin-bottom: 4px;
}
.tool-result {
  color: #63e2b7;
  margin-left: 6px;
  font-weight: bold;
}
.message.streaming .message-text::after {
  content: '▊';
  animation: blink 0.7s infinite;
  color: #7c6cff;
  margin-left: 2px;
}

/* ── Chat Input ── */
.chat-view .input-area {
  padding: 0 24px 24px;
  max-width: 800px;
  margin: 0 auto;
  position: relative;
  z-index: 20;
}
.chat-view .input-area .n-input {
  background: #1e1e22;
  border: 1px solid #333;
  border-radius: 16px;
  padding: 12px 90px 12px 16px; /* 右侧预留给按钮 */
  box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.1);
  transition: all 0.2s;
}
.chat-view .input-area .n-input:focus-within {
  border-color: #7c6cff;
  box-shadow: 0 -4px 24px rgba(124, 108, 255, 0.1);
  background: #252529;
}
.chat-view .input-actions {
  position: absolute;
  bottom: 36px;
  right: 36px;
  margin: 0;
  gap: 8px;
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .sidebar {
    display: none;
  }
  .welcome-title {
    font-size: 24px;
  }
  .quick-actions {
    flex-direction: column;
    width: 100%;
    padding: 0 20px;
  }
  .message {
    padding: 0 12px;
  }
  .chat-view .input-area {
    padding: 0 12px 12px;
  }
}
</style>
