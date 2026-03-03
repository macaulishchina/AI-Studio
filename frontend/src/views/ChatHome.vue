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
            <n-button
              quaternary circle size="small"
              @click="showConfigModal = true"
              title="对话配置"
            >
              <template #icon><n-icon :component="SettingsOutline" /></template>
            </n-button>
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
            :class="[msg.role, { 'message-right': msg.role === 'user' }]"
          >
            <!-- AI 消息: 头像在左 -->
            <template v-if="msg.role !== 'user'">
              <div class="message-avatar assistant-avatar">🐕</div>
              <div class="message-content">
                <div class="message-sender">Dogi</div>

                <!-- 工具调用记录 (折叠, 不含 ask_user) -->
                <div v-if="getRegularToolCalls(msg.tool_calls).length" class="tool-group">
                  <div class="tool-group-header" @click="msg._toolsOpen = !msg._toolsOpen">
                    <span class="tool-group-arrow" :class="{ open: msg._toolsOpen }">▶</span>
                    <span>🛠️</span>
                    <span class="tool-group-count">{{ getRegularToolCalls(msg.tool_calls).length }} 轮工具调用</span>
                  </div>
                  <div v-if="msg._toolsOpen" class="tool-group-body">
                    <div v-for="tc in getRegularToolCalls(msg.tool_calls)" :key="tc.id" class="tool-inline">
                      <span :class="tc.result?.startsWith?.('ERROR:') ? 'tool-icon-error' : 'tool-icon-ok'">
                        {{ tc.result?.startsWith?.('ERROR:') ? '❌' : '✅' }}
                      </span>
                      <span class="tool-inline-name">{{ tc.name }}</span>
                      <code v-if="tc.arguments" class="tool-inline-args">{{ formatToolArgs(tc.name, tc.arguments) }}</code>
                      <span v-if="tc.duration_ms" class="tool-inline-time">({{ tc.duration_ms }}ms)</span>
                    </div>
                  </div>
                </div>

                <div class="message-text" v-html="renderMarkdown(msg.content)"></div>

                <!-- ask_user 问题卡片 (已保存消息) -->
                <template v-for="tc in (msg.tool_calls || []).filter((t: any) => t.name === 'ask_user' && parseQuestions(t.arguments).length > 0)" :key="tc.id">
                  <div class="question-card">
                    <template v-if="getCardState(tc.id).submitted || isAskUserAnswered(msg, tc)">
                      <div class="question-card-header question-card-header-done">
                        <span class="question-card-icon">{{ isAskUserAutoDecided(msg, tc) ? '🤖' : '✅' }}</span>
                        <span class="question-card-title" style="color: #8a8a8a">{{ isAskUserAutoDecided(msg, tc) ? 'AI 自行决定' : '已回答' }}</span>
                      </div>
                      <div v-for="(q, qi) in parseQuestions(tc.arguments)" :key="qi" class="question-summary-row">
                        <span class="question-summary-q">{{ q.question }}</span>
                        <span v-if="getCardState(tc.id).submitted && (getCardState(tc.id).answers[qi]?.length || getCardState(tc.id).customTexts[qi]?.trim())" class="question-summary-a">
                          {{ getCardState(tc.id).customTexts[qi]?.trim() || getCardState(tc.id).answers[qi]?.join('、') }}
                        </span>
                        <span v-else-if="!getCardState(tc.id).submitted && getDbAnswerForQuestion(msg, q.question)" class="question-summary-a">
                          {{ getDbAnswerForQuestion(msg, q.question) }}
                        </span>
                        <span v-else-if="getRecommendedLabels(q)" class="question-summary-a question-summary-a-auto">
                          🤖 {{ getRecommendedLabels(q) }}
                        </span>
                      </div>
                    </template>
                    <template v-else>
                      <div class="question-card-header">
                        <span class="question-card-icon">💬</span>
                        <span class="question-card-title">Dogi 想了解以下问题</span>
                        <span class="question-card-hint">选择后点击提交，未回答的问题由 AI 决定</span>
                      </div>
                      <div v-for="(q, qi) in parseQuestions(tc.arguments)" :key="qi" class="question-item">
                        <div class="question-text">{{ qi + 1 }}. {{ q.question }}
                          <span v-if="q.type === 'multi'" class="question-type-tag">多选</span>
                        </div>
                        <div v-if="q.context" class="question-context">{{ q.context }}</div>
                        <div v-if="q.options?.length" class="question-options">
                          <span v-for="(opt, oi) in q.options" :key="oi"
                            class="question-option-btn"
                            :class="{
                              'question-option-selected': getCardState(tc.id).answers[qi]?.includes(opt.label),
                              'question-option-recommended': opt.recommended && !getCardState(tc.id).answers[qi]?.includes(opt.label),
                            }"
                            @click="toggleOption(tc.id, qi, opt.label, q.type)">
                            <span v-if="opt.recommended" class="rec-dot" />
                            {{ opt.label }}
                            <span v-if="opt.description" class="option-desc">{{ opt.description }}</span>
                          </span>
                        </div>
                        <input v-if="!q.options?.length || getCardState(tc.id).answers[qi]?.some((a: string) => a.includes('其他'))"
                          class="question-custom-input"
                          :placeholder="q.options?.length ? '请补充说明...' : '请输入你的回答...'"
                          :value="getCardState(tc.id).customTexts[qi] || ''"
                          @input="(e: any) => getCardState(tc.id).customTexts[qi] = e.target.value" />
                      </div>
                      <div class="question-submit-row">
                        <n-button size="small" type="primary" @click="submitQuestionCard(tc.id, parseQuestions(tc.arguments))">提交回答</n-button>
                        <n-button size="tiny" quaternary @click="submitQuestionCard(tc.id, parseQuestions(tc.arguments))">跳过全部，AI 自行决定</n-button>
                      </div>
                    </template>
                  </div>
                </template>

                <div v-if="msg.thinking_content" class="message-thinking">
                  <details>
                    <summary>💭 思考过程</summary>
                    <div v-html="renderMarkdown(msg.thinking_content)"></div>
                  </details>
                </div>
              </div>
            </template>

            <!-- ask_user 回答: 紧凑气泡 -->
            <template v-else-if="msg.role === 'user' && msg.content?.startsWith('<!-- ask_user_response -->')">
              <div class="message-content user-content">
                <div class="message-text user-bubble user-bubble-reply">
                  <div class="reply-indicator">📝 回答了问题</div>
                  <div v-html="renderMarkdown(msg.content.replace('<!-- ask_user_response -->\n', ''))" />
                </div>
              </div>
              <div class="message-avatar user-avatar">👤</div>
            </template>

            <!-- 用户消息: 气泡在右 -->
            <template v-else>
              <div class="message-content user-content">
                <div class="message-text user-bubble" v-html="renderMarkdown(msg.content)"></div>
              </div>
              <div class="message-avatar user-avatar">👤</div>
            </template>
          </div>

          <!-- AI 正在生成 -->
          <div v-if="streaming" class="message assistant streaming">
            <div class="message-avatar assistant-avatar">🐕</div>
            <div class="message-content">
              <div class="message-sender">Dogi</div>
              <div v-if="streamThinking" class="message-thinking active">
                💭 正在思考...
              </div>

              <!-- 流式内容段 (文本 + 工具调用内联) -->
              <template v-for="(seg, segIdx) in streamSegments" :key="segIdx">
                <div v-if="seg.type === 'content'" class="message-text"
                  v-html="renderMarkdown((seg.text || '') + (segIdx === streamSegments.length - 1 ? '▍' : ''))"></div>

                <!-- ask_user: 交互式问题卡片 -->
                <div v-else-if="seg.type === 'tool' && seg.toolCall?.name === 'ask_user' && (seg.toolCall.status === 'preparing' || parseQuestions(seg.toolCall.arguments).length > 0)" class="question-card">
                  <template v-if="seg.toolCall.status === 'preparing'">
                    <div class="question-card-header">
                      <span class="question-card-icon">💬</span>
                      <span class="question-card-title">Dogi 正在组织问题…</span>
                    </div>
                    <div class="question-preparing-body">
                      <div class="question-preparing-skeleton">
                        <div class="skeleton-line" style="width: 70%"></div>
                        <div class="skeleton-options">
                          <div class="skeleton-pill"></div>
                          <div class="skeleton-pill" style="width: 80px"></div>
                          <div class="skeleton-pill" style="width: 100px"></div>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template v-else-if="getCardState(seg.toolCall.id).submitted">
                    <div class="question-card-header question-card-header-done">
                      <span class="question-card-icon">✅</span>
                      <span class="question-card-title" style="color: #8a8a8a">已回答</span>
                    </div>
                    <div v-for="(q, qi) in parseQuestions(seg.toolCall.arguments)" :key="qi" class="question-summary-row">
                      <span class="question-summary-q">{{ q.question }}</span>
                      <span v-if="getCardState(seg.toolCall.id).answers[qi]?.length || getCardState(seg.toolCall.id).customTexts[qi]?.trim()" class="question-summary-a">
                        {{ getCardState(seg.toolCall.id).customTexts[qi]?.trim() || getCardState(seg.toolCall.id).answers[qi]?.join('、') }}
                      </span>
                      <span v-else-if="getRecommendedLabels(q)" class="question-summary-a question-summary-a-auto">
                        🤖 {{ getRecommendedLabels(q) }}
                      </span>
                    </div>
                  </template>
                  <template v-else>
                    <div class="question-card-header">
                      <span class="question-card-icon">💬</span>
                      <span class="question-card-title">Dogi 想了解以下问题</span>
                      <span v-if="seg.toolCall.status !== 'calling'" class="question-card-hint">选择后点击提交</span>
                    </div>
                    <div v-for="(q, qi) in parseQuestions(seg.toolCall.arguments)" :key="qi" class="question-item">
                      <div class="question-text">{{ qi + 1 }}. {{ q.question }}
                        <span v-if="q.type === 'multi'" class="question-type-tag">多选</span>
                      </div>
                      <div v-if="q.context" class="question-context">{{ q.context }}</div>
                      <div v-if="q.options?.length" class="question-options">
                        <span v-for="(opt, oi) in q.options" :key="oi"
                          class="question-option-btn"
                          :class="{
                            'question-option-selected': getCardState(seg.toolCall.id).answers[qi]?.includes(opt.label),
                            'question-option-recommended': opt.recommended && !getCardState(seg.toolCall.id).answers[qi]?.includes(opt.label),
                          }"
                          @click="toggleOption(seg.toolCall.id, qi, opt.label, q.type)">
                          <span v-if="opt.recommended" class="rec-dot" />
                          {{ opt.label }}
                          <span v-if="opt.description" class="option-desc">{{ opt.description }}</span>
                        </span>
                      </div>
                      <input v-if="!q.options?.length || getCardState(seg.toolCall.id).answers[qi]?.some((a: string) => a.includes('其他'))"
                        class="question-custom-input"
                        :placeholder="q.options?.length ? '请补充说明...' : '请输入你的回答...'"
                        :value="getCardState(seg.toolCall.id).customTexts[qi] || ''"
                        @input="(e: any) => getCardState(seg.toolCall.id).customTexts[qi] = e.target.value" />
                    </div>
                    <div v-if="seg.toolCall.status !== 'calling'" class="question-submit-row">
                      <n-button size="small" type="primary" @click="submitQuestionCard(seg.toolCall.id, parseQuestions(seg.toolCall.arguments))">提交回答</n-button>
                      <n-button size="tiny" quaternary @click="submitQuestionCard(seg.toolCall.id, parseQuestions(seg.toolCall.arguments))">跳过全部，AI 自行决定</n-button>
                    </div>
                  </template>
                </div>

                <!-- 普通工具: 单行内联 -->
                <div v-else-if="seg.type === 'tool' && seg.toolCall" class="tool-inline">
                  <span v-if="seg.toolCall.status === 'calling' || seg.toolCall.status === 'preparing'" class="tool-icon-pending">⏳</span>
                  <span v-else-if="seg.toolCall.status === 'error'" class="tool-icon-error">❌</span>
                  <span v-else class="tool-icon-ok">✅</span>
                  <span class="tool-inline-name">{{ seg.toolCall.name }}</span>
                  <code v-if="seg.toolCall.arguments" class="tool-inline-args">{{ formatToolArgs(seg.toolCall.name, seg.toolCall.arguments) }}</code>
                  <span v-if="seg.toolCall.duration_ms" class="tool-inline-time">({{ seg.toolCall.duration_ms }}ms)</span>
                </div>
              </template>

              <div v-if="!streamSegments.length" class="message-text" v-html="renderMarkdown('▍')"></div>
            </div>
          </div>

          <!-- 记忆提取提示 -->
          <div v-if="memoryToast" class="memory-toast">
            <span>🧠 已从本次对话提取 {{ memoryToastCount }} 条记忆</span>
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

    <!-- ========== 对话配置弹窗 ========== -->
    <n-modal
      v-model:show="showConfigModal"
      preset="card"
      title="对话配置"
      :style="{ width: '520px' }"
      :bordered="false"
      :segmented="{ content: true }"
      size="small"
    >
      <n-tabs type="line" animated>
        <!-- 工具权限 -->
        <n-tab-pane name="tools" tab="🔧 工具权限">
          <div class="config-section">
            <p class="config-hint">选择 Dogi 在此对话中可使用的工具权限</p>
            <n-checkbox-group v-model:value="configToolPermissions">
              <n-space vertical :size="8">
                <n-checkbox
                  v-for="perm in allToolPermissions"
                  :key="perm.key"
                  :value="perm.key"
                  :label="perm.label"
                />
              </n-space>
            </n-checkbox-group>
          </div>
        </n-tab-pane>

        <!-- 技能 -->
        <n-tab-pane name="skills" tab="⚡ 技能">
          <div class="config-section">
            <p class="config-hint">为 Dogi 选择可用技能</p>
            <n-spin :show="loadingSkills">
              <div class="config-list" v-if="availableSkills.length">
                <div
                  v-for="skill in availableSkills"
                  :key="skill.id"
                  class="config-item"
                  :class="{ selected: selectedSkillNames.includes(skill.name) }"
                  @click="toggleSkill(skill.name)"
                >
                  <span class="config-item-icon">{{ skill.icon || '⚡' }}</span>
                  <div class="config-item-info">
                    <div class="config-item-name">{{ skill.name }}</div>
                    <div class="config-item-desc">{{ skill.description || '' }}</div>
                  </div>
                  <n-icon v-if="selectedSkillNames.includes(skill.name)" :component="CheckmarkOutline" color="#63e2b7" />
                </div>
              </div>
              <div v-else class="config-empty">暂无可用技能</div>
            </n-spin>
          </div>
        </n-tab-pane>

        <!-- MCP 服务 -->
        <n-tab-pane name="mcp" tab="🔌 MCP">
          <div class="config-section">
            <p class="config-hint">管理 MCP 服务连接</p>
            <n-spin :show="loadingMcp">
              <div class="config-list" v-if="mcpServers.length">
                <div
                  v-for="srv in mcpServers"
                  :key="srv.slug"
                  class="config-item"
                  :class="{ selected: srv.enabled }"
                >
                  <span class="config-item-icon">{{ srv.icon || '🔌' }}</span>
                  <div class="config-item-info">
                    <div class="config-item-name">{{ srv.name }}</div>
                    <div class="config-item-desc">{{ srv.description || srv.slug }}</div>
                  </div>
                  <n-switch
                    :value="srv.enabled"
                    size="small"
                    @update:value="toggleMcp(srv.slug, $event)"
                  />
                </div>
              </div>
              <div v-else class="config-empty">暂无 MCP 服务</div>
            </n-spin>
          </div>
        </n-tab-pane>

        <!-- 记忆 -->
        <n-tab-pane name="memory" tab="🧠 记忆">
          <div class="config-section">
            <div class="memory-header">
              <p class="config-hint">Dogi 记住的关于你的信息</p>
              <n-button size="tiny" quaternary @click="loadMemories" :loading="loadingMemories">
                刷新
              </n-button>
            </div>
            <p class="config-hint" style="margin: 0 0 8px; font-size: 11px; opacity: .6;">
              L1 情景记忆 — 每次对话结束自动提取的事实/决策/偏好，跨对话持久化
            </p>
            <n-spin :show="loadingMemories">
              <div class="config-list" v-if="memories.length">
                <div v-for="mem in memories" :key="mem.id" class="memory-item">
                  <n-tag :type="memoryTypeColor(mem.memory_type)" size="small" round>
                    {{ memoryTypeLabel(mem.memory_type) }}
                  </n-tag>
                  <span class="memory-content">{{ mem.content }}</span>
                  <n-button
                    quaternary circle size="tiny"
                    @click="deleteMemory(mem.id)"
                    class="memory-delete"
                  >
                    <template #icon><n-icon :component="CloseOutline" :size="14" /></template>
                  </n-button>
                </div>
              </div>
              <div v-else class="config-empty">暂无记忆数据</div>
            </n-spin>
            <div class="memory-actions" v-if="memories.length > 2">
              <n-button size="small" quaternary @click="consolidateMemories" :loading="consolidating">
                🔄 合并去重
              </n-button>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>

      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <n-button size="small" @click="showConfigModal = false">取消</n-button>
          <n-button type="primary" size="small" @click="saveConfig" :loading="savingConfig">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  useMessage, NButton, NInput, NIcon, NSelect, NModal, NTabs, NTabPane,
  NCheckboxGroup, NCheckbox, NSpace, NSwitch, NTag, NSpin,
} from 'naive-ui'
import {
  AddOutline, SendOutline, MenuOutline, CodeSlashOutline,
  ChatbubblesOutline, BookOutline, ConstructOutline,
  SettingsOutline, CheckmarkOutline, CloseOutline,
} from '@vicons/ionicons5'
import { conversationApi, skillApi, mcpApi, memoryApi } from '@/api'
import { useModelSelection } from '@/composables/useModelSelection'
import { useAskUser } from '@/composables/useAskUser'
import { parseQuestions, getRecommendedLabels, formatToolArgs } from '@/composables/useChatUtils'
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

// Memory toast
const memoryToast = ref(false)
const memoryToastCount = ref(0)

// Streaming tool state
const streamSegments = ref<Array<{
  type: 'content' | 'tool'
  text?: string
  toolCall?: { id: string; name: string; arguments: any; status: string; result?: string; duration_ms?: number }
}>>([])
const streamToolCalls = ref<any[]>([])

// Conversations list
const conversations = ref<any[]>([])
const activeConversationId = computed(() => {
  const id = route.params.id
  return id ? Number(id) : null
})
const activeConversation = computed(() =>
  conversations.value.find(c => c.id === activeConversationId.value)
)

function syncSelectedModelFromActiveConversation() {
  const conv = activeConversation.value
  if (conv?.model) {
    selectedModel.value = conv.model
  }
}

// Messages for active conversation
const messages = ref<any[]>([])

// Quick actions for welcome page
const quickActions = [
  { label: '写代码', prompt: '帮我写一个', icon: CodeSlashOutline },
  { label: '聊一聊', prompt: '', icon: ChatbubblesOutline },
  { label: '学知识', prompt: '给我讲讲', icon: BookOutline },
  { label: '用工具', prompt: '帮我用工具', icon: ConstructOutline },
]

// ========== ask_user 问题卡片 ==========
function _sendAskUserResponse(content: string) {
  const convId = activeConversationId.value
  if (!convId) return
  doSendMessage(convId, content)
}

const {
  getCardState,
  toggleOption,
  submitQuestionCard,
  isAskUserAnswered,
  isAskUserAutoDecided,
  getDbAnswerForQuestion,
  getRegularToolCalls,
} = useAskUser(messages, _sendAskUserResponse)

// ========== Config Modal State ==========
const showConfigModal = ref(false)
const savingConfig = ref(false)

const allToolPermissions = [
  { key: 'ask_user', label: '💬 ask_user — 主动提问' },
  { key: 'read_source', label: '📖 read_source — 读取源码' },
  { key: 'read_config', label: '⚙️ read_config — 读取配置' },
  { key: 'search', label: '🔍 search — 搜索文件' },
  { key: 'tree', label: '🌳 tree — 目录树' },
  { key: 'execute_readonly_command', label: '▶️ execute_readonly_command — 只读命令' },
  { key: 'execute_command', label: '⚠️ execute_command — 写入命令' },
]
const configToolPermissions = ref<string[]>([])

const loadingSkills = ref(false)
const availableSkills = ref<any[]>([])
const selectedSkillNames = ref<string[]>([])

const loadingMcp = ref(false)
const mcpServers = ref<any[]>([])

const loadingMemories = ref(false)
const memories = ref<any[]>([])
const consolidating = ref(false)

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
  streamSegments.value = []
  streamToolCalls.value = []
  memoryToast.value = false

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
          case 'content_delta': {
            const text = data.content || ''
            streamContent.value += text
            // 追加到 segments
            const segs = streamSegments.value
            const last = segs[segs.length - 1]
            if (last && last.type === 'content') {
              last.text = (last.text || '') + text
            } else {
              segs.push({ type: 'content', text })
            }
            scrollToBottom()
            break
          }
          case 'thinking':
          case 'thinking_delta':
            streamThinking.value += data.content || ''
            break
          case 'tool_call_start': {
            const tc_data = data.tool_call || data
            const toolCall = { id: tc_data.id || '', name: tc_data.name || '', arguments: null as any, status: 'preparing' }
            streamToolCalls.value.push(toolCall)
            streamSegments.value.push({ type: 'tool', toolCall })
            scrollToBottom()
            break
          }
          case 'tool_call': {
            const tc_data = data.tool_call || data
            const tcId = tc_data.id || data.tool_call_id || ''
            const existing = streamToolCalls.value.find((t: any) => t.id === tcId)
            if (existing) {
              existing.arguments = tc_data.arguments || data.arguments || ''
              existing.status = 'calling'
            } else {
              const toolCall = { id: tcId, name: tc_data.name || data.name || '', arguments: tc_data.arguments || data.arguments || '', status: 'calling' }
              streamToolCalls.value.push(toolCall)
              streamSegments.value.push({ type: 'tool', toolCall })
            }
            scrollToBottom()
            break
          }
          case 'tool_result': {
            const tc = streamToolCalls.value.find((t: any) => t.id === data.tool_call_id)
            if (tc) { tc.status = 'done'; tc.result = data.result; tc.duration_ms = data.duration_ms }
            break
          }
          case 'tool_error': {
            const tc = streamToolCalls.value.find((t: any) => t.id === data.tool_call_id)
            if (tc) { tc.status = 'error'; tc.result = data.error; tc.duration_ms = data.duration_ms }
            break
          }
          case 'ask_user_pending':
            // ask_user tool is waiting — scroll to bottom so card is visible
            scrollToBottom()
            break
          case 'memory_updated':
            memoryToastCount.value = data.count || 0
            memoryToast.value = true
            setTimeout(() => { memoryToast.value = false }, 4000)
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

// ========== Config Modal Methods ==========
async function loadConfigData() {
  const conv = activeConversation.value
  if (conv) {
    configToolPermissions.value = conv.tool_permissions || []
  }

  loadingSkills.value = true
  loadingMcp.value = true
  loadingMemories.value = true

  try {
    const [skillsRes, mcpRes] = await Promise.all([
      skillApi.list({ enabled_only: true }).catch(() => ({ data: [] })),
      mcpApi.listServers().catch(() => ({ data: [] })),
    ])
    availableSkills.value = skillsRes.data || []
    mcpServers.value = (mcpRes.data || []).map((s: any) => ({
      ...s,
      enabled: s.enabled ?? false,
    }))
  } catch (e: any) {
    console.warn('加载配置数据失败:', e)
  } finally {
    loadingSkills.value = false
    loadingMcp.value = false
  }

  await loadMemories()
}

async function loadMemories() {
  loadingMemories.value = true
  try {
    const res = await memoryApi.list({ limit: 50 })
    memories.value = res.data || []
  } catch (e: any) {
    console.warn('加载记忆失败:', e)
    memories.value = []
  } finally {
    loadingMemories.value = false
  }
}

function toggleSkill(name: string) {
  const idx = selectedSkillNames.value.indexOf(name)
  if (idx >= 0) {
    selectedSkillNames.value.splice(idx, 1)
  } else {
    selectedSkillNames.value.push(name)
  }
}

async function toggleMcp(slug: string, enabled: boolean) {
  const srv = mcpServers.value.find((s: any) => s.slug === slug)
  if (srv) {
    srv.enabled = enabled
    try {
      await mcpApi.updateServer(slug, { enabled })
    } catch (e: any) {
      console.warn('MCP toggle failed:', e)
    }
  }
}

async function deleteMemory(id: string) {
  try {
    await memoryApi.delete(id)
    memories.value = memories.value.filter((m: any) => m.id !== id)
  } catch (e: any) {
    message.error('删除失败')
  }
}

async function consolidateMemories() {
  consolidating.value = true
  try {
    const res = await memoryApi.consolidate()
    message.success(`合并完成，移除 ${res.data.removed || 0} 条重复`)
    await loadMemories()
  } catch (e: any) {
    message.error('合并失败')
  } finally {
    consolidating.value = false
  }
}

async function saveConfig() {
  savingConfig.value = true
  try {
    const convId = activeConversationId.value
    if (convId) {
      await conversationApi.update(convId, {
        tool_permissions: configToolPermissions.value,
      })
      const conv = activeConversation.value
      if (conv) conv.tool_permissions = [...configToolPermissions.value]
    }
    showConfigModal.value = false
    message.success('配置已保存')
  } catch (e: any) {
    message.error('保存失败')
  } finally {
    savingConfig.value = false
  }
}

function memoryTypeLabel(type: string): string {
  const map: Record<string, string> = {
    fact: '事实', decision: '决策', preference: '偏好',
    context: '上下文', episode: '事件', profile: '画像',
  }
  return map[type] || type
}

function memoryTypeColor(type: string): 'info' | 'success' | 'warning' | 'error' | 'default' {
  const map: Record<string, any> = {
    fact: 'info', decision: 'success', preference: 'warning', profile: 'error',
  }
  return map[type] || 'default'
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
onMounted(async () => {
  await Promise.all([
    loadConversations(),
    loadModels(),
  ])
  syncSelectedModelFromActiveConversation()
})

// Watch route changes to load conversation messages
watch(activeConversationId, async (id) => {
  if (id) {
    await loadMessages(id)
    syncSelectedModelFromActiveConversation()
  } else {
    messages.value = []
  }
}, { immediate: true })

watch(activeConversation, () => {
  syncSelectedModelFromActiveConversation()
})

// Watch config modal open → load data
watch(showConfigModal, (show) => {
  if (show) loadConfigData()
})
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
/* 用户消息右对齐 */
.message.message-right {
  justify-content: flex-end;
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
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}
.assistant-avatar {
  background: linear-gradient(135deg, #2c2640 0%, #3d3459 100%);
}
.user-avatar {
  background: #2a2a30;
}
.message-content {
  flex: 1;
  min-width: 0;
  padding-top: 6px;
  max-width: calc(100% - 52px);
}
.user-content {
  flex: initial;
  max-width: 75%;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
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
/* 用户消息气泡 */
.user-bubble {
  background: linear-gradient(135deg, #4a3c8a 0%, #5b4fa0 100%);
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  display: inline-block;
  color: #f0f0f0;
}
.user-bubble :deep(p) {
  margin: 4px 0;
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

/* ── Memory Toast ── */
.memory-toast {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px 16px;
  margin: 8px auto;
  max-width: 400px;
  background: rgba(124, 108, 255, 0.1);
  border: 1px solid rgba(124, 108, 255, 0.2);
  border-radius: 20px;
  font-size: 13px;
  color: #a89cff;
  animation: fadeInUp 0.3s ease;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ── Tool Inline ── */
.tool-group { margin-bottom: 8px; }
.tool-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #999;
  cursor: pointer;
  padding: 2px 0;
}
.tool-group-header:hover { color: #ccc; }
.tool-group-arrow {
  display: inline-block;
  transition: transform 0.15s;
  font-size: 10px;
}
.tool-group-arrow.open { transform: rotate(90deg); }
.tool-group-count { color: #63e2b7; font-size: 11px; }
.tool-group-body { padding: 4px 0 4px 16px; }
.tool-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  font-size: 12px;
  color: #999;
}
.tool-inline-name { color: #b8b8b8; font-weight: 500; }
.tool-inline-args {
  color: #666;
  font-size: 11px;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-inline-time { color: #555; font-size: 10px; }
.tool-icon-ok { color: #63e2b7; }
.tool-icon-error { color: #e88080; }
.tool-icon-pending { color: #f2c97d; }

/* ── ask_user 问题卡片 ── */
.question-card {
  background: linear-gradient(135deg, rgba(124, 108, 255, 0.06), rgba(99, 226, 183, 0.04));
  border: 1px solid rgba(124, 108, 255, 0.2);
  border-radius: 12px;
  padding: 12px 14px;
  margin: 8px 0;
}
.question-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.question-card-header-done { margin-bottom: 6px; padding-bottom: 4px; }
.question-card-icon { font-size: 14px; flex-shrink: 0; }
.question-card-title { color: #7c6cff; font-size: 12px; font-weight: 600; }
.question-card-hint { color: #666; font-size: 10px; margin-left: auto; }
.question-type-tag {
  display: inline-block;
  font-size: 10px;
  color: #7c6cff;
  background: rgba(124, 108, 255, 0.12);
  border: 1px solid rgba(124, 108, 255, 0.3);
  border-radius: 3px;
  padding: 0 4px;
  margin-left: 6px;
  vertical-align: middle;
}
.question-item { margin-bottom: 10px; }
.question-item:last-child { margin-bottom: 0; }
.question-text { color: #e0e0e0; font-size: 13px; font-weight: 500; line-height: 1.5; margin-bottom: 4px; }
.question-context { color: #777; font-size: 11px; line-height: 1.3; margin-bottom: 5px; padding-left: 14px; font-style: italic; }
.question-options { display: flex; flex-wrap: wrap; gap: 6px; padding-left: 14px; }
.question-option-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  font-size: 12px;
  color: #b0b0b0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}
.question-option-btn:hover { color: #e0e0e0; background: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.25); }
.question-option-recommended { color: #7c6cff; border-color: rgba(124, 108, 255, 0.3); background: rgba(124, 108, 255, 0.06); }
.question-option-recommended:hover { border-color: rgba(124, 108, 255, 0.5); background: rgba(124, 108, 255, 0.12); }
.rec-dot { display: inline-block; width: 5px; height: 5px; background: #7c6cff; border-radius: 50%; flex-shrink: 0; }
.question-option-selected { color: #fff !important; background: rgba(124, 108, 255, 0.25) !important; border-color: #7c6cff !important; }
.option-desc { color: #777; font-size: 10px; margin-left: 2px; }
.question-option-selected .option-desc { color: rgba(255,255,255,0.6); }
.question-custom-input {
  display: block;
  width: calc(100% - 14px);
  margin: 6px 0 0 14px;
  padding: 5px 10px;
  font-size: 12px;
  color: #e0e0e0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  outline: none;
  transition: border-color 0.15s;
}
.question-custom-input:focus { border-color: rgba(124, 108, 255, 0.5); }
.question-custom-input::placeholder { color: #555; }
.question-submit-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.question-summary-row { display: flex; gap: 6px; font-size: 11px; line-height: 1.5; padding: 1px 0; }
.question-summary-q { color: #888; flex-shrink: 0; max-width: 50%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.question-summary-a { color: #7c6cff; font-weight: 500; }
.question-summary-a-auto { color: #8a8a8a; font-weight: 400; font-style: italic; }

/* 问题卡片骨架屏 */
.question-preparing-body { padding: 4px 0; }
.question-preparing-skeleton { display: flex; flex-direction: column; gap: 6px; }
.skeleton-line {
  height: 12px;
  background: linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0.04) 75%);
  background-size: 200% 100%;
  border-radius: 6px;
  animation: skeleton-shimmer 1.5s infinite;
}
.skeleton-options { display: flex; gap: 6px; padding-left: 14px; }
.skeleton-pill {
  height: 24px;
  width: 60px;
  background: linear-gradient(90deg, rgba(255,255,255,0.03) 25%, rgba(255,255,255,0.06) 50%, rgba(255,255,255,0.03) 75%);
  background-size: 200% 100%;
  border-radius: 12px;
  animation: skeleton-shimmer 1.5s infinite;
}
@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ask_user 回答紧凑气泡 */
.user-bubble-reply { background: linear-gradient(135deg, #3d3570 0%, #4a3f7a 100%); }
.reply-indicator { font-size: 11px; color: #a89cff; margin-bottom: 4px; }

/* ── Config Modal ── */
.config-section { min-height: 120px; }
.config-hint { font-size: 13px; color: #888; margin: 0 0 12px; }
.config-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 320px;
  overflow-y: auto;
}
.config-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: #1e1e22;
  border: 1px solid #333;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.config-item:hover { border-color: #555; background: #252529; }
.config-item.selected { border-color: #7c6cff; background: rgba(124, 108, 255, 0.06); }
.config-item-icon { font-size: 20px; width: 28px; text-align: center; flex-shrink: 0; }
.config-item-info { flex: 1; min-width: 0; }
.config-item-main {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.config-item-name { font-size: 14px; font-weight: 500; color: #eee; }
.config-item-desc { font-size: 12px; color: #777; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.config-empty { text-align: center; color: #555; padding: 40px 0; font-size: 13px; }

/* ── Detail Toggle / Panel ── */
.detail-toggle {
  font-size: 11px;
  color: #7c6cff;
  cursor: pointer;
  user-select: none;
  padding: 2px 6px;
  border-radius: 4px;
  margin-top: 4px;
  align-self: flex-end;
  flex-shrink: 0;
}
.detail-toggle:hover { background: rgba(124, 108, 255, 0.12); }
.detail-panel {
  margin-top: 8px;
  padding: 8px 10px;
  background: #18181c;
  border-radius: 6px;
  border: 1px solid #2a2a30;
  font-size: 12px;
  color: #aaa;
  line-height: 1.6;
}
.detail-panel p { margin: 0; }
.detail-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.detail-label {
  color: #777;
  font-size: 11px;
  min-width: 70px;
  flex-shrink: 0;
}
.detail-panel code {
  font-family: 'Fira Code', monospace;
  font-size: 11px;
  color: #b0b0b0;
  background: #222;
  padding: 1px 4px;
  border-radius: 3px;
  word-break: break-all;
}
.detail-code {
  margin: 4px 0 0;
  padding: 8px;
  background: #111;
  border-radius: 4px;
  font-size: 11px;
  color: #999;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
  font-family: 'Fira Code', monospace;
  line-height: 1.5;
}

/* ── Tool Permission Items ── */
.tool-perm-item { flex-direction: column; align-items: stretch; }
.tool-perm-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tool-perm-label { flex: 1; font-size: 13px; color: #ddd; }

/* ── MCP Tools List ── */
.mcp-tools-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
}
.mcp-tool-item {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 3px 6px;
  background: #111;
  border-radius: 4px;
}
.mcp-tool-name {
  font-size: 12px;
  font-weight: 500;
  color: #b0b0ff;
  font-family: 'Fira Code', monospace;
  flex-shrink: 0;
}
.mcp-tool-desc {
  font-size: 11px;
  color: #666;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.memory-header .config-hint { margin: 0; }
.memory-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #1e1e22;
  border-radius: 6px;
  font-size: 13px;
}
.memory-content { flex: 1; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.memory-delete { opacity: 0; transition: opacity 0.15s; flex-shrink: 0; }
.memory-item:hover .memory-delete { opacity: 1; }
.memory-actions { display: flex; justify-content: center; margin-top: 8px; }

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
  .user-content {
    max-width: 85%;
  }
}
</style>
