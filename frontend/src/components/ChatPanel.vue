<template>
  <div style="display: flex; flex-direction: column; height: 100%; min-height: 400px">
    <!-- 项目信息栏 -->
    <div class="project-info-bar" @click="showProjectEdit = true">
      <n-ellipsis :line-clamp="1" :tooltip="false" style="flex: 1; min-width: 0">
        <span class="project-info-title">{{ props.project.title }}</span>
        <span v-if="props.project.description" class="project-info-sep">—</span>
        <span v-if="props.project.description" class="project-info-desc">{{ props.project.description }}</span>
        <span v-else class="project-info-desc" style="opacity: 0.35">点击添加需求描述...</span>
      </n-ellipsis>
      <span class="project-info-edit-icon">✏️</span>
    </div>

    <!-- 项目信息编辑弹窗 -->
    <n-modal v-model:show="showProjectEdit" preset="card" title="编辑项目信息" style="width: 520px; max-width: 95vw" :mask-closable="true">
      <n-form label-placement="top" :show-feedback="false">
        <n-form-item label="项目名称">
          <n-input v-model:value="editProjectTitle" placeholder="项目名称" />
        </n-form-item>
        <n-form-item label="需求描述" style="margin-top: 12px">
          <n-input v-model:value="editProjectDesc" type="textarea" :autosize="{ minRows: 3, maxRows: 10 }" placeholder="详细描述你的需求..." />
        </n-form-item>
      </n-form>
      <n-text depth="3" style="font-size: 11px; display: block; margin-top: 8px">需求描述会注入到每次 AI 对话的上下文中</n-text>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <n-button @click="showProjectEdit = false">取消</n-button>
          <n-button type="primary" @click="saveProjectInfo" :loading="savingProject">保存</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 中间区域: 消息 + 右侧 slot (设计稿) 并排 -->
    <div style="flex: 1; display: flex; overflow: hidden; min-height: 0">
      <!-- 消息列表 -->
      <div ref="messageListRef" style="flex: 1; overflow-y: auto; padding: 4px 0; min-width: 0">

      <!-- 空对话欢迎状态 -->
      <div v-if="!messages.length && !streaming" class="empty-chat-welcome">
        <div class="empty-chat-icon">{{ props.project.type_info?.icon || '💬' }}</div>
        <div class="empty-chat-title">{{ props.project.type_info?.name || '讨论' }}</div>
        <div class="empty-chat-desc">{{ props.project.title }}</div>
        <n-button
          type="primary"
          size="large"
          :loading="startingChat"
          :disabled="aiMuted || props.readonly"
          style="margin-top: 20px; border-radius: 20px; padding: 0 32px"
          @click="handleStartChat"
        >
          <template #icon><span style="font-size: 16px">✨</span></template>
          开始对话
        </n-button>
        <n-text v-if="props.readonly" depth="3" style="font-size: 12px; margin-top: 8px">此阶段已完成，当前为只读模式</n-text>
        <n-text v-else-if="aiMuted" depth="3" style="font-size: 12px; margin-top: 8px">AI 已禁言，请先解除禁言</n-text>
      </div>

      <div v-for="msg in messages" :key="msg.id" style="margin-bottom: 6px">
        <!-- 系统消息 (上下文总结) -->
        <div v-if="msg.role === 'system'" style="display: flex; justify-content: center">
          <n-card size="small" style="max-width: 90%; background: #1a2a3e; border: 1px dashed #f0a020; border-radius: 6px; --n-padding-top: 4px; --n-padding-bottom: 4px">
            <n-collapse>
              <n-collapse-item name="summary">
                <template #header>
                  <n-space align="center" :size="4">
                    <span style="font-size: 14px">📝</span>
                    <n-text style="color: #f0a020; font-size: 11px; font-weight: 500">
                      上下文自动总结
                    </n-text>
                    <n-text depth="3" style="font-size: 10px">{{ formatTime(msg.created_at) }}</n-text>
                  </n-space>
                </template>
                <div class="thinking-block" v-html="renderMarkdown(msg.content)" />
              </n-collapse-item>
            </n-collapse>
          </n-card>
        </div>

        <!-- ask_user 回答: 紧凑指示器 (不重复显示已在卡片中展示的内容) -->
        <div
          v-else-if="msg.role === 'user' && msg.content?.startsWith('<!-- ask_user_response -->')"
          :style="{ display: 'flex', justifyContent: isMyMessage(msg) ? 'flex-end' : 'flex-start' }"
          @mouseenter="hoveredMessageId = msg.id"
          @mouseleave="hoveredMessageId = null"
        >
          <div class="ask-user-reply-indicator">
            <span style="opacity: 0.5">💬</span>
            <n-text v-if="!isMyMessage(msg)" :style="{ color: getUserColor(msg.sender_name), fontSize: '12px', marginRight: '4px' }">{{ msg.sender_name }}</n-text>
            <span>已提交回答</span>
            <n-popover trigger="click" placement="bottom" style="max-width: 400px">
              <template #trigger>
                <span class="ask-reply-detail-link">查看</span>
              </template>
              <div class="markdown-body" v-html="renderMarkdown(msg.content.replace('<!-- ask_user_response -->\n', ''))" />
            </n-popover>
            <n-text depth="3" style="font-size: 10px; margin-left: 4px">{{ formatTime(msg.created_at) }}</n-text>
          </div>
        </div>

        <!-- 用户/AI 消息 (跳过完全空的 assistant 消息) -->
        <div
          v-else-if="!isEmptyAssistantMessage(msg)"
          :style="{ display: 'flex', justifyContent: (msg.role === 'user' && isMyMessage(msg)) ? 'flex-end' : 'flex-start' }"
          @mouseenter="hoveredMessageId = msg.id"
          @mouseleave="hoveredMessageId = null"
        >
          <div style="max-width: 85%; position: relative">
            <n-card
              size="small"
              :style="{
                background: (msg.role === 'user' && isMyMessage(msg)) ? '#1a3a5c' : '#1a2a3e',
                borderLeft: (msg.role === 'assistant' || (msg.role === 'user' && !isMyMessage(msg))) ? '2px solid ' + (msg.role === 'assistant' ? '#e94560' : '#f0a020') : 'none',
                borderRight: (msg.role === 'user' && isMyMessage(msg)) ? '2px solid #0ea5e9' : 'none',
                '--n-padding-top': '6px',
                '--n-padding-bottom': '6px',
                '--n-padding-left': '10px',
                '--n-padding-right': '10px',
              }"
            >
            <template #header>
              <div style="display: flex; align-items: center; justify-content: space-between; gap: 4px">
                <n-space align="center" :size="6" style="flex: 1; min-width: 0">
                  <n-text :style="{ color: msg.role === 'assistant' ? '#e94560' : getUserColor(msg.sender_name), fontSize: '12px' }">
                    {{ msg.sender_name || msg.role }}
                  </n-text>
                  <n-tag v-if="msg.model_used" size="tiny" :bordered="false" round>
                    {{ msg.model_used }}
                  </n-tag>
                  <n-text depth="3" style="font-size: 10px">
                    {{ formatTime(msg.created_at) }}
                  </n-text>
                </n-space>
                <!-- 操作按钮 (常驻显示在 header 右侧) -->
                <n-button-group size="tiny" class="msg-actions" :class="{ 'msg-actions-visible': hoveredMessageId === msg.id }">
                  <n-button quaternary @click.stop="copyMessage(msg)" title="复制">
                    <template #icon><span style="font-size: 11px">📋</span></template>
                  </n-button>
                  <n-button quaternary @click.stop="msg.role === 'user' ? retryMessage(msg) : regenerateMessage(msg)" :title="msg.role === 'user' ? '重新发送' : '重新生成'">
                    <template #icon><span style="font-size: 11px">🔄</span></template>
                  </n-button>
                </n-button-group>
              </div>
            </template>

            <!-- 图片附件 -->
            <n-space v-if="msg.attachments?.length" style="margin-bottom: 6px">
              <n-image
                v-for="(att, i) in msg.attachments.filter((a: any) => a.type === 'image')"
                :key="i"
                :src="att.url"
                width="180"
                style="border-radius: 6px"
              />
            </n-space>

            <!-- 思考过程 (已保存的消息) -->
            <n-collapse v-if="msg.thinking_content" style="margin-bottom: 6px">
              <n-collapse-item title="💭 思考过程" name="thinking">
                <div class="thinking-block" v-html="renderMarkdown(msg.thinking_content)" />
              </n-collapse-item>
            </n-collapse>

            <!-- 工具调用记录 (已保存消息, 折叠显示, 不含 ask_user) -->
            <div v-if="getRegularToolCalls(msg.tool_calls).length" style="margin-bottom: 4px">
              <div class="tool-group-header" @click="toggleToolGroup(msg.id)">
                <span class="tool-group-arrow" :class="{ open: expandedToolGroups[msg.id] }">▶</span>
                <span class="tool-group-icon">🛠️</span>
                <span class="tool-group-count">{{ getRegularToolCalls(msg.tool_calls).length }} 轮工具调用</span>
              </div>
              <div v-if="expandedToolGroups[msg.id]" class="tool-group-body">
                <template v-for="tc in getRegularToolCalls(msg.tool_calls)" :key="tc.id">
                  <div class="tool-inline">
                    <span :class="tc.result?.startsWith('ERROR:') ? 'tool-icon-error' : 'tool-icon-ok'">
                      {{ tc.result?.startsWith('ERROR:') ? '❌' : '✅' }}
                    </span>
                    <span class="tool-inline-name">{{ toolDisplayName(tc.name) }}</span>
                    <code v-if="tc.arguments" class="tool-inline-args">{{ formatToolArgs(tc.name, tc.arguments) }}</code>
                    <span v-if="tc.duration_ms" class="tool-inline-time">({{ tc.duration_ms }}ms)</span>
                    <n-popover trigger="click" placement="bottom" style="max-width: 500px; max-height: 300px; overflow: auto">
                      <template #trigger>
                        <span class="tool-inline-view">查看</span>
                      </template>
                      <div class="tool-result-content" v-html="renderMarkdown(tc.result || '(无结果)')" />
                    </n-popover>
                  </div>
                </template>
              </div>
            </div>

            <!-- 消息内容 (Markdown) -->
            <div class="markdown-body" v-html="renderMarkdown(msg.content)" />

            <!-- ask_user 问题卡片 (渲染在文本内容之后, 符合对话直觉) -->
            <template v-for="tc in (msg.tool_calls || []).filter((t: any) => t.name === 'ask_user' && parseQuestions(t.arguments).length > 0)" :key="tc.id">
              <div class="question-card" style="margin-top: 6px">
                <template v-if="getCardState(tc.id).submitted || isAskUserAnswered(msg, tc)">
                  <!-- 已提交/已回答: 紧凑回显 -->
                  <div class="question-card-header question-card-header-done">
                    <span class="question-card-icon">{{ isAskUserAutoDecided(msg, tc) ? '🤖' : '✅' }}</span>
                    <span class="question-card-title" style="color: #8a8a8a">{{ isAskUserAutoDecided(msg, tc) ? 'AI 自行决定' : '已回答' }}</span>
                  </div>
                  <!-- 逐题回显 (本地提交 或 DB 历史统一逻辑, 含 AI 推荐回显) -->
                  <div v-for="(q, qi) in parseQuestions(tc.arguments)" :key="qi" class="question-summary-row">
                    <span class="question-summary-q">{{ q.question }}</span>
                    <!-- 本地 cardState: 优先用本地选择 -->
                    <span v-if="getCardState(tc.id).submitted && (getCardState(tc.id).answers[qi]?.length || getCardState(tc.id).customTexts[qi]?.trim())" class="question-summary-a">
                      {{ getCardState(tc.id).customTexts[qi]?.trim() || getCardState(tc.id).answers[qi]?.join('、') }}
                    </span>
                    <!-- DB 历史: 从回答文本解析 -->
                    <span v-else-if="!getCardState(tc.id).submitted && getDbAnswerForQuestion(msg, q.question)" class="question-summary-a">
                      {{ getDbAnswerForQuestion(msg, q.question) }}
                    </span>
                    <!-- 未回答: 显示 AI 推荐 -->
                    <span v-else-if="getRecommendedLabels(q)" class="question-summary-a question-summary-a-auto">
                      🤖 {{ getRecommendedLabels(q) }}
                    </span>
                  </div>
                </template>
                <template v-else>
                  <!-- 未提交: 交互选择 -->
                  <div class="question-card-header">
                    <span class="question-card-icon">💬</span>
                    <span class="question-card-title">AI 想了解以下问题</span>
                    <span class="question-card-hint">选择后点击提交，未回答的问题由 AI 决定</span>
                  </div>
                  <div v-for="(q, qi) in parseQuestions(tc.arguments)" :key="qi" class="question-item">
                    <div class="question-text">
                      {{ qi + 1 }}. {{ q.question }}
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
                    <input v-if="!q.options?.length || getCardState(tc.id).answers[qi]?.some(a => a.includes('其他'))"
                      class="question-custom-input"
                      :placeholder="q.options?.length ? '请补充说明...' : '请输入你的回答...'"
                      :value="getCardState(tc.id).customTexts[qi] || ''"
                      @input="(e: any) => getCardState(tc.id).customTexts[qi] = e.target.value" />
                  </div>
                  <div class="question-submit-row">
                    <n-button size="small" type="primary" @click="submitQuestionCard(tc.id, parseQuestions(tc.arguments))">
                      提交回答
                    </n-button>
                    <n-button size="tiny" quaternary @click="submitQuestionCard(tc.id, parseQuestions(tc.arguments))">
                      跳过全部，AI 自行决定
                    </n-button>
                  </div>
                </template>
              </div>
            </template>

            <!-- 工具调用统计 -->
            <div v-if="msg.token_usage?.tool_rounds" style="margin-top: 4px; padding-top: 3px; border-top: 1px solid #333">
              <n-text depth="3" style="font-size: 10px; color: #63e2b7">
                🛠️ {{ msg.token_usage.tool_rounds }} 轮工具调用
              </n-text>
            </div>
          </n-card>
          </div>
        </div>
      </div>

      <!-- 上下文总结通知 -->
      <div v-if="summaryNotice" style="display: flex; justify-content: center; margin-bottom: 6px">
        <n-card size="small" style="max-width: 90%; background: #1a2a3e; border: 1px dashed #f0a020; border-radius: 6px">
          <n-collapse>
            <n-collapse-item name="summary">
              <template #header>
                <n-space align="center" :size="6">
                  <span style="font-size: 16px">📝</span>
                  <n-text style="color: #f0a020; font-size: 12px; font-weight: 500">
                    上下文已接近上限，自动总结了早期对话
                  </n-text>
                </n-space>
              </template>
              <div class="thinking-block" v-html="renderMarkdown(summaryNotice)" />
            </n-collapse-item>
          </n-collapse>
        </n-card>
      </div>

      <!-- AI 正在回复 (多任务 + 敲定方案统一渲染) -->
      <div v-for="card in activeStreamCards" :key="card.key" style="display: flex; justify-content: flex-start; margin-bottom: 6px">
        <n-card size="small" style="max-width: 85%; background: #1a2a3e; border-left: 2px solid #e94560; --n-padding-top: 6px; --n-padding-bottom: 6px">
          <template #header>
            <n-space align="center" :size="6">
              <n-text style="color: #e94560; font-size: 12px">{{ card.model }}</n-text>
              <n-text v-if="card.senderName" depth="3" style="font-size: 10px">by {{ card.senderName }}</n-text>
              <n-spin size="small" />
              <n-button v-if="card.isMine && card.taskId" size="tiny" type="error" quaternary @click="cancelTask(card.taskId)" style="padding: 0 4px; font-size: 11px">⏹</n-button>
            </n-space>
          </template>

          <!-- 思考过程 (折叠) -->
          <n-collapse v-if="card.thinking" :default-expanded-names="['thinking']" style="margin-bottom: 8px">
            <n-collapse-item title="💭 思考过程" name="thinking">
              <div class="thinking-block" v-html="renderMarkdown(card.thinking)" />
            </n-collapse-item>
          </n-collapse>

          <!-- 流式内容段 (工具调用内联显示) -->
          <template v-for="(seg, segIdx) in card.segments" :key="segIdx">
            <div v-if="seg.type === 'content'" class="markdown-body"
              v-html="renderMarkdown((seg.text || '') + (segIdx === card.segments.length - 1 ? '▍' : ''))" />
            <!-- ask_user: 交互式问题卡片 (preparing 状态也显示) -->
            <div v-else-if="seg.type === 'tool' && seg.toolCall?.name === 'ask_user' && (seg.toolCall.status === 'preparing' || parseQuestions(seg.toolCall.arguments).length > 0)" class="question-card">
              <!-- 准备中: 参数还在流式传输 -->
              <template v-if="seg.toolCall.status === 'preparing'">
                <div class="question-card-header">
                  <span class="question-card-icon">💬</span>
                  <span class="question-card-title">AI 正在组织问题…</span>
                  <n-spin :size="12" style="margin-left: 6px" />
                </div>
                <div class="question-preparing-body">
                  <div class="question-preparing-skeleton">
                    <div class="skeleton-line" style="width: 70%"></div>
                    <div class="skeleton-options">
                      <div class="skeleton-pill"></div>
                      <div class="skeleton-pill" style="width: 80px"></div>
                      <div class="skeleton-pill" style="width: 100px"></div>
                    </div>
                    <div class="skeleton-line" style="width: 55%; margin-top: 10px"></div>
                    <div class="skeleton-options">
                      <div class="skeleton-pill" style="width: 90px"></div>
                      <div class="skeleton-pill" style="width: 70px"></div>
                    </div>
                  </div>
                </div>
              </template>
              <template v-else-if="getCardState(seg.toolCall.id).submitted">
                <!-- 已提交: 紧凑回显 (含 AI 推荐回显) -->
                <div class="question-card-header question-card-header-done">
                  <span class="question-card-icon">{{ parseQuestions(seg.toolCall.arguments).every((_: any, qi: number) => !getCardState(seg.toolCall.id).answers[qi]?.length && !getCardState(seg.toolCall.id).customTexts[qi]?.trim()) ? '🤖' : '✅' }}</span>
                  <span class="question-card-title" style="color: #8a8a8a">{{ parseQuestions(seg.toolCall.arguments).every((_: any, qi: number) => !getCardState(seg.toolCall.id).answers[qi]?.length && !getCardState(seg.toolCall.id).customTexts[qi]?.trim()) ? 'AI 自行决定' : '已回答' }}</span>
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
                <!-- 未提交: 交互选择 -->
                <div class="question-card-header">
                  <span class="question-card-icon">💬</span>
                  <span class="question-card-title">AI 想了解以下问题</span>
                  <n-spin v-if="seg.toolCall.status === 'calling'" :size="12" style="margin-left: 6px" />
                  <span v-else class="question-card-hint">选择后点击提交，未回答的问题由 AI 决定</span>
                </div>
                <div v-for="(q, qi) in parseQuestions(seg.toolCall.arguments)" :key="qi" class="question-item">
                  <div class="question-text">
                    {{ qi + 1 }}. {{ q.question }}
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
                  <input v-if="!q.options?.length || getCardState(seg.toolCall.id).answers[qi]?.some(a => a.includes('其他'))"
                    class="question-custom-input"
                    :placeholder="q.options?.length ? '请补充说明...' : '请输入你的回答...'"
                    :value="getCardState(seg.toolCall.id).customTexts[qi] || ''"
                    @input="(e: any) => getCardState(seg.toolCall.id).customTexts[qi] = e.target.value" />
                </div>
                <div v-if="seg.toolCall.status !== 'calling'" class="question-submit-row">
                  <n-button size="small" type="primary" @click="submitQuestionCard(seg.toolCall.id, parseQuestions(seg.toolCall.arguments))">
                    提交回答
                  </n-button>
                  <n-button size="tiny" quaternary @click="submitQuestionCard(seg.toolCall.id, parseQuestions(seg.toolCall.arguments))">
                    跳过全部，AI 自行决定
                  </n-button>
                </div>
              </template>
            </div>
            <!-- 普通工具: 单行内联 -->
            <div v-else-if="seg.type === 'tool' && seg.toolCall" class="tool-inline">
              <span v-if="seg.toolCall.status === 'calling' || seg.toolCall.status === 'preparing'" class="tool-icon-pending">⏳</span>
              <span v-else-if="seg.toolCall.status === 'error'" class="tool-icon-error">❌</span>
              <span v-else class="tool-icon-ok">✅</span>
              <span class="tool-inline-name">{{ toolDisplayName(seg.toolCall.name) }}</span>
              <code v-if="seg.toolCall.arguments" class="tool-inline-args">{{ formatToolArgs(seg.toolCall.name, seg.toolCall.arguments) }}</code>
              <span v-if="seg.toolCall.duration_ms" class="tool-inline-time">({{ seg.toolCall.duration_ms }}ms)</span>
              <n-spin v-if="seg.toolCall.status === 'calling' || seg.toolCall.status === 'preparing'" :size="10" style="margin-left: 2px" />
              <n-popover v-if="seg.toolCall.result" trigger="click" placement="bottom" style="max-width: 500px; max-height: 300px; overflow: auto">
                <template #trigger>
                  <span class="tool-inline-view">查看</span>
                </template>
                <div class="tool-result-content" v-html="renderMarkdown(seg.toolCall.result)" />
              </n-popover>
            </div>
          </template>
          <div v-if="!card.segments.length" class="markdown-body" v-html="renderMarkdown('▍')" />
        </n-card>
      </div>
    </div>
      <!-- 右侧插槽 (设计稿面板在此渲染) -->
      <slot name="aside" />
    </div>

    <!-- 图片预览区 -->
    <div v-if="pendingImages.length" style="padding: 6px 8px; background: #16213e; border-radius: 6px; margin-bottom: 4px">
      <n-space :size="6">
        <div v-for="(img, i) in pendingImages" :key="i" style="position: relative">
          <n-image :src="img.preview" width="64" height="64" style="border-radius: 6px; object-fit: cover" />
          <n-button circle size="tiny" type="error" style="position: absolute; top: -4px; right: -4px" @click="pendingImages.splice(i, 1)">✕</n-button>
        </div>
      </n-space>
    </div>

    <!-- 隐藏的文件选择器 (绕过 n-upload 的 DOM 问题) -->
    <input ref="fileInputRef" type="file" accept="image/*" style="display: none" @change="onFileInputChange" />

    <!-- ========== 输入区 ========== -->
    <!-- 只读模式: 锁定提示 -->
    <div v-if="props.readonly" class="input-area" style="justify-content: center; align-items: center; min-height: 48px; padding: 12px">
      <n-text depth="3" style="font-size: 13px">🔒 此阶段已完成，当前为只读模式</n-text>
    </div>
    <!-- 正常输入区 -->
    <div v-else class="input-area">
      <!-- 第 1 行: 工具栏 -->
      <div class="toolbar-row">
        <n-dropdown :options="sourceFilterOptions" @select="onSourceFilterChange" trigger="click" size="small">
          <n-button size="small" quaternary style="padding: 0 6px">
            {{ sourceFilterLabel }} <span style="font-size: 10px; margin-left: 2px; opacity: 0.6">▾</span>
          </n-button>
        </n-dropdown>
        <div class="model-select-group">
          <n-select
            v-model:value="selectedModel"
            :options="modelOptions"
            :render-label="renderModelLabel"
            size="small"
            style="width: 100%"
            filterable
            :consistent-menu-width="false"
            @update:value="handleModelChange"
          />
          <button class="model-refresh-btn" @click="refreshModels" :disabled="loadingModels" :title="loadingModels ? '刷新中...' : '刷新模型列表'">
            <span :class="{ 'spin-icon': loadingModels }">⟲</span>
          </button>
        </div>
        <n-button v-if="currentModelCaps.supports_vision" size="small" quaternary :disabled="finalizingPlan" @click="fileInputRef?.click()">📷 图片</n-button>
        <n-popover v-if="currentModelCaps.supports_tools" trigger="click" placement="bottom" style="max-width: 360px" @update:show="onToolPopoverShow">
          <template #trigger>
            <n-button size="small" quaternary :type="toolCheckboxValues.length ? 'info' : 'default'">🛠️ 工具</n-button>
          </template>
          <div style="padding: 4px 0">
            <n-text strong style="font-size: 13px">AI 工具权限</n-text>
            <n-text depth="3" style="font-size: 11px; display: block; margin: 4px 0 8px">
              控制 AI 在本项目中可使用的工具，可在设置页工具管理中配置命令授权规则
            </n-text>
            <n-checkbox-group :value="toolCheckboxValues" @update:value="onToolPermChange">
              <n-space vertical :size="2">
                <template v-for="perm in permDefs" :key="perm.key">
                  <!-- 顶级权限 (无 parent) -->
                  <n-checkbox v-if="!perm.parent" :value="perm.key">
                    <template #default>
                      <n-tooltip trigger="hover" :delay="500">
                        <template #trigger>
                          <span>{{ perm.icon }} {{ perm.label }}</span>
                        </template>
                        {{ perm.tip }}
                      </n-tooltip>
                    </template>
                  </n-checkbox>
                  <!-- 子权限 (有 parent, 仅当父权限开启时显示) -->
                  <div v-else-if="toolPermissions.includes(perm.parent)" style="padding-left: 22px; border-left: 2px solid #333; margin-left: 8px">
                    <n-checkbox :value="perm.key">
                      <template #default>
                        <n-tooltip trigger="hover" :delay="500">
                          <template #trigger>
                            <span>{{ perm.icon }} {{ perm.label }}</span>
                          </template>
                          {{ perm.tip }}
                        </n-tooltip>
                      </template>
                    </n-checkbox>
                  </div>
                </template>
              </n-space>
            </n-checkbox-group>
            <!-- 写命令已启用时的状态提示 -->
            <template v-if="toolPermissions.includes('execute_command')">
              <div v-if="toolPermissions.includes('auto_approve_commands')" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #333; display: flex; align-items: center; gap: 6px">
                <n-tag size="tiny" type="warning" :bordered="false" round>自动批准</n-tag>
                <n-text depth="2" style="font-size: 11px; flex: 1">写命令已设为自动批准</n-text>
                <n-button size="tiny" quaternary type="error" @click="revokeAutoApprove">撤销</n-button>
              </div>
              <n-text v-else depth="3" style="font-size: 11px; display: block; margin-top: 6px; padding-top: 6px; border-top: 1px solid #333">
                💡 写命令默认每次需审批确认，可在「设置 → AI 工作流 → 工具管理」中预设自动放行/拦截规则
              </n-text>
            </template>
          </div>
        </n-popover>
        <n-tag v-if="streamingTasks.size > 0 && !streaming" type="warning" size="small" :bordered="false" round>⏳ AI 回复中 ({{ streamingTasks.size }})</n-tag>
      </div>

      <!-- 第 2 行: 文本输入框 -->
      <n-input
        ref="inputRef"
        v-model:value="inputText"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 5 }"
        :placeholder="aiMuted ? '人工讨论模式 (Enter 发送)' : '描述你的需求... (Enter 发送, Shift+Enter 换行)'"
        :disabled="finalizingPlan"
        @keydown="handleKeydown"
        style="margin: 4px 0"
      />

      <!-- 第 3 行: 操作栏 -->
      <div class="action-bar">
        <n-popover trigger="click" placement="top-start" style="padding: 0">
          <template #trigger>
            <div class="action-bar-item" style="cursor: pointer">
              <n-progress
                type="line"
                :percentage="displayContextInfo.percentage"
                :show-indicator="false"
                :height="3"
                style="width: 48px"
                :color="displayContextInfo.percentage > 80 ? '#e94560' : displayContextInfo.percentage > 50 ? '#f0a020' : '#18a058'"
              />
              <span class="action-bar-stat">
                {{ formatTokens(displayContextInfo.used) }}/{{ formatTokens(displayContextInfo.total) }} · {{ displayContextInfo.percentage }}%
              </span>
              <n-spin v-if="contextCompressing" :size="12" style="margin-left: 4px" />
            </div>
          </template>
          <!-- 上下文占用明细气泡 (树形检查器) -->
          <div class="ctx-breakdown">
            <div class="ctx-breakdown-title">📊 上下文占用明细</div>
            <div class="ctx-breakdown-bar">
              <div class="ctx-bar-seg ctx-bar-system" :style="{ width: ctxBreakdownPercents.system + '%' }" />
              <div class="ctx-bar-seg ctx-bar-tools" :style="{ width: ctxBreakdownPercents.tools + '%' }" />
              <div class="ctx-bar-seg ctx-bar-history" :style="{ width: ctxBreakdownPercents.history + '%' }" />
            </div>

            <!-- 可展开的树形明细 -->
            <div class="ctx-tree">
              <!-- System Prompt (可展开子节点) -->
              <div class="ctx-tree-node">
                <div class="ctx-tree-row" @click="ctxExpanded.system = !ctxExpanded.system">
                  <span class="ctx-tree-arrow" :class="{ open: ctxExpanded.system }">▶</span>
                  <span class="ctx-dot" style="background:#a855f7" />
                  <span class="ctx-tree-label">System Prompt</span>
                  <span class="ctx-val">{{ formatTokens(ctxBreakdown.system) }}</span>
                </div>
                <div v-if="ctxExpanded.system && ctxSystemSections.length" class="ctx-tree-children">
                  <div v-for="(sec, si) in ctxSystemSections" :key="si" class="ctx-tree-node">
                    <div class="ctx-tree-row ctx-tree-row-child"
                         @click="sec.children ? (ctxExpanded['sys_' + si] = !ctxExpanded['sys_' + si]) : openCtxContent(sec.name, sec.content)">
                      <span v-if="sec.children" class="ctx-tree-arrow" :class="{ open: ctxExpanded['sys_' + si] }">▶</span>
                      <span v-else class="ctx-tree-arrow ctx-tree-leaf">·</span>
                      <span class="ctx-tree-label" :class="{ 'ctx-clickable': !sec.children }">{{ sec.name }}</span>
                      <span class="ctx-val">{{ formatTokens(sec.tokens) }}</span>
                    </div>
                    <!-- 子节点的 children (如关键文件) -->
                    <div v-if="sec.children && ctxExpanded['sys_' + si]" class="ctx-tree-children">
                      <div v-for="(child, ci) in sec.children" :key="ci" class="ctx-tree-row ctx-tree-row-leaf ctx-clickable-row"
                           @click="openCtxContent(child.name, child.content)">
                        <span class="ctx-tree-arrow ctx-tree-leaf">·</span>
                        <span class="ctx-tree-label ctx-clickable">{{ child.name }}</span>
                        <span class="ctx-val">{{ formatTokens(child.tokens) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 工具定义 -->
              <div class="ctx-tree-node">
                <div class="ctx-tree-row">
                  <span class="ctx-tree-arrow ctx-tree-leaf">·</span>
                  <span class="ctx-dot" style="background:#0ea5e9" />
                  <span class="ctx-tree-label">工具定义</span>
                  <span class="ctx-val">{{ formatTokens(ctxBreakdown.tools) }}</span>
                </div>
              </div>

              <!-- 对话历史 (可展开每条消息) -->
              <div class="ctx-tree-node">
                <div class="ctx-tree-row" @click="ctxExpanded.history = !ctxExpanded.history">
                  <span class="ctx-tree-arrow" :class="{ open: ctxExpanded.history }">▶</span>
                  <span class="ctx-dot" style="background:#f59e0b" />
                  <span class="ctx-tree-label">对话历史</span>
                  <span class="ctx-val">{{ formatTokens(ctxBreakdown.history) }}</span>
                </div>
                <div v-if="ctxExpanded.history && ctxHistoryDetail.length" class="ctx-tree-children">
                  <div v-for="(hm, hi) in ctxHistoryDetail" :key="hi" class="ctx-tree-row ctx-tree-row-leaf">
                    <span class="ctx-tree-arrow ctx-tree-leaf">·</span>
                    <span class="ctx-tree-label ctx-tree-msg-label" :class="'ctx-role-' + hm.role">
                      {{ hm.role === 'user' ? '👤' : hm.role === 'assistant' ? '🤖' : '📋' }}
                      <span class="ctx-msg-preview">{{ hm.preview || '(空)' }}</span>
                    </span>
                    <span class="ctx-val">{{ formatTokens(hm.tokens) }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 汇总 -->
            <div class="ctx-tree-summary">
              <span>总计 / 上限</span>
              <span class="ctx-val">{{ formatTokens(displayContextInfo.used) }} / {{ formatTokens(displayContextInfo.total) }}</span>
            </div>
            <div v-if="ctxMessages.total" class="ctx-breakdown-msgs">
              💬 消息: 保留 {{ ctxMessages.kept }} / 共 {{ ctxMessages.total }}
              <span v-if="ctxMessages.dropped"> · 丢弃 {{ ctxMessages.dropped }}</span>
            </div>
          </div>
        </n-popover>
        <!-- 上下文内容查看气泡 -->
        <n-modal v-model:show="ctxContentModal" preset="card" :title="ctxContentTitle"
                 style="width: min(620px, 95vw); max-height: 70vh;"
                 :bordered="false" size="small"
                 :segmented="{ content: true }">
          <n-scrollbar style="max-height: calc(70vh - 80px)">
            <pre class="ctx-content-pre">{{ ctxContentText }}</pre>
          </n-scrollbar>
        </n-modal>
        <n-button-group size="tiny">
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button size="tiny" quaternary :loading="summarizing" :disabled="streaming || messages.length < 4" @click="handleSummarize">
                📝
              </n-button>
            </template>
            总结上下文：将旧消息压缩为摘要，释放上下文空间
          </n-tooltip>
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button size="tiny" quaternary :disabled="streaming || !messages.length" @click="handleClearContext">
                🗑️
              </n-button>
            </template>
            清空上下文：删除所有讨论消息，重新开始
          </n-tooltip>
        </n-button-group>
        <span class="action-bar-spring" />
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button size="small" :type="aiMuted ? 'error' : 'default'" quaternary :loading="muteLoading" @click="toggleAiMute">
              {{ aiMuted ? '🔇 AI已禁言' : '🔊 禁言AI' }}
            </n-button>
          </template>
          {{ aiMuted ? '解除禁言后，AI 会阅读所有新消息并回复' : '禁言后仅人工讨论，AI 不参与回复' }}
        </n-tooltip>
        <n-button size="small" type="warning" quaternary @click="handleFinalizePlan" :loading="finalizingPlan" :disabled="messages.length < 2 || anyStreaming">
          📋 {{ props.project.type_info?.ui_labels?.finalize_action || '敲定' }}
        </n-button>
        <n-button v-if="anyStreaming" size="small" type="error" @click="stopAllMyStreaming">⏹ 停止</n-button>
        <n-button size="small" type="primary" @click="sendMessage()" :disabled="finalizingPlan || anyStreaming || (!inputText.trim() && !pendingImages.length)">发送</n-button>
      </div>
    </div>
  </div>

  <!-- 命令审批对话框 -->
  <n-modal v-model:show="commandApproval.show" preset="card" title="⚠️ AI 请求执行写命令" style="max-width: 540px; width: 95vw" :mask-closable="false" :closable="false">
    <n-alert type="warning" :bordered="false" style="margin-bottom: 12px">
      AI 正在尝试执行以下写入命令，需要您的授权才能继续。
    </n-alert>
    <div style="background: #0d1b2a; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-family: monospace; font-size: 13px; color: #e0e0e0; word-break: break-all; white-space: pre-wrap">$ {{ commandApproval.command }}</div>
    <n-space vertical :size="8" style="margin-bottom: 16px">
      <n-text depth="2" style="font-size: 12px">授权范围：</n-text>
      <n-radio-group v-model:value="commandApproval.scope" size="small">
        <n-space :size="12" :wrap="true">
          <n-radio value="once">仅本次</n-radio>
          <n-radio value="session">
            <n-tooltip trigger="hover">
              <template #trigger>本次回答</template>
              本次 AI 回复中的同类命令自动批准
            </n-tooltip>
          </n-radio>
          <n-radio value="project">
            <n-tooltip trigger="hover">
              <template #trigger>本项目</template>
              为此项目创建授权规则（可在设置中管理）
            </n-tooltip>
          </n-radio>
          <n-radio value="permanent">
            <n-tooltip trigger="hover">
              <template #trigger>永久</template>
              所有项目中的同类命令永久自动批准（可在设置 → 工具管理中管理）
            </n-tooltip>
          </n-radio>
        </n-space>
      </n-radio-group>
      <n-checkbox
        v-if="commandApproval.scope === 'project' || commandApproval.scope === 'permanent'"
        v-model:checked="commandApproval.allCommands"
        style="margin-top: 4px"
      >
        <n-tooltip trigger="hover">
          <template #trigger>所有命令</template>
          授权所有写入命令，而不仅是当前命令类型
        </n-tooltip>
      </n-checkbox>
    </n-space>
    <template #action>
      <n-space justify="end">
        <n-button @click="handleCommandApproval(false)" :loading="commandApproval.loading" :disabled="commandApproval.loading">
          拒绝
        </n-button>
        <n-button type="warning" @click="handleCommandApproval(true)" :loading="commandApproval.loading" :disabled="commandApproval.loading">
          授权执行
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
/**
 * ChatPanel — 聊天面板编排器
 *
 * 核心逻辑拆分到 6 个 composable:
 *   useChatUtils       — 纯函数 (markdown / 时间 / 错误 / 滚动 / 工具显示)
 *   useModelSelection   — 模型列表, 过滤, 分组, 渲染
 *   useContextInfo      — 上下文占用率, 总结, 清空, 模型切换检查
 *   useProjectEventBus  — 项目事件总线 SSE (多人实时同步)
 *   useSSEFinalize      — 敲定方案流式处理
 *   useAskUser          — ask_user 问题卡片状态
 */
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { discussionApi, projectApi, tasksApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStudioConfigStore } from '@/stores/studioConfig'
import { useToolStore, type PermissionInfo } from '@/stores/tool'
import type { Project } from '@/stores/project'

// ---- Composables ----
import {
  renderMarkdown, formatTime, formatTokens,
  useScroll, parseErrorMeta, formatErrorAsMessage,
  getUserColor, toolDisplayName, formatToolArgs,
  parseQuestions, getRecommendedLabels,
  type ParsedQuestion,
} from '@/composables/useChatUtils'
import { useModelSelection } from '@/composables/useModelSelection'
import { useContextInfo } from '@/composables/useContextInfo'
import { useProjectEventBus } from '@/composables/useProjectEventBus'
import { useSSEFinalize } from '@/composables/useSSEFinalize'
import { useAskUser } from '@/composables/useAskUser'

// ==================== Props / Emit ====================

const props = defineProps<{ project: Project; readonly?: boolean }>()
const emit = defineEmits(['plan-finalized'])
const authStore = useAuthStore()
const studioConfig = useStudioConfigStore()
const message = useMessage()
const dialog = useDialog()

// ==================== Core Refs ====================

const messages = ref<any[]>([])
const inputText = ref('')
const inputHistory = ref<string[]>([])
const historyIndex = ref(-1)
const historySavedInput = ref('')
const INPUT_HISTORY_MAX = 50
const INPUT_HISTORY_KEY_PREFIX = 'studio_input_history_'

function loadInputHistory(projectId: number) {
  try {
    const raw = localStorage.getItem(INPUT_HISTORY_KEY_PREFIX + projectId)
    inputHistory.value = raw ? JSON.parse(raw) : []
  } catch {
    inputHistory.value = []
  }
  historyIndex.value = -1
  historySavedInput.value = ''
}

function saveInputHistory(projectId: number) {
  try {
    // 仅保留最近 N 条
    const trimmed = inputHistory.value.slice(-INPUT_HISTORY_MAX)
    localStorage.setItem(INPUT_HISTORY_KEY_PREFIX + projectId, JSON.stringify(trimmed))
  } catch { /* quota exceeded — ignore */ }
}
const startingChat = ref(false)
const messageListRef = ref<HTMLElement>()
const inputRef = ref()
const fileInputRef = ref<HTMLInputElement>()
const hoveredMessageId = ref<number | null>(null)
const aiMuted = ref(false)
const muteLoading = ref(false)
const expandedToolGroups = reactive<Record<number, boolean>>({})

// ==================== Scroll ====================

const { scrollToBottom, scrollToTop } = useScroll(messageListRef)

// ==================== Model Selection ====================

const {
  models, selectedModel, loadingModels,
  modelSourceFilter, sourceFilterOptions, sourceFilterLabel,
  modelOptions, currentModelCaps,
  selectedModelDisplay, selectedModelProviderIcon,
  selectedModelMaxTokens, currentModelToolRounds,
  onSourceFilterChange, renderModelLabel,
  refreshModels, loadModels,
} = useModelSelection(props.project.discussion_model || 'gpt-4o')

// ==================== Context Info ====================

const {
  persistentContextInfo, contextCompressing, summarizing,
  ctxContentModal, ctxContentTitle, ctxContentText, ctxExpanded,
  displayContextInfo, ctxBreakdown, ctxMessages,
  ctxBreakdownPercents, ctxSystemSections, ctxHistoryDetail,
  refreshContextInfo, openCtxContent,
  handleSummarize, handleClearContext, handleModelChange,
} = useContextInfo({
  projectId: () => props.project.id,
  selectedModel,
  selectedModelMaxTokens,
  messages,
  scrollToBottom,
})

// ==================== SSE Finalize ====================

const {
  streaming, streamContent, streamThinking, streamToolCalls, streamSegments,
  finalizingPlan, lastTokenUsage, summaryNotice,
  handleFinalizePlan: _handleFinalizePlan,
  stopFinalizeStreaming,
} = useSSEFinalize({
  projectId: () => props.project.id,
  selectedModel,
  messages,
  persistentContextInfo,
  scrollToBottom,
  onPlanFinalized: () => emit('plan-finalized'),
})

// ==================== 命令审批对话框 ====================

const commandApproval = ref<{
  show: boolean
  taskId: number
  command: string
  toolCallId: string
  scope: string
  allCommands: boolean
  loading: boolean
}>({ show: false, taskId: 0, command: '', toolCallId: '', scope: 'once', allCommands: false, loading: false })

function onCommandApprovalRequest(taskId: number, command: string, toolCallId: string) {
  commandApproval.value = { show: true, taskId, command, toolCallId, scope: 'once', allCommands: false, loading: false }
}

async function handleCommandApproval(approved: boolean) {
  const { taskId, scope, allCommands } = commandApproval.value
  commandApproval.value.loading = true
  try {
    await tasksApi.approveCommand(taskId, { approved, scope, all_commands: allCommands })
  } catch (e: any) {
    message.error(e.response?.data?.detail || '审批请求失败')
  } finally {
    commandApproval.value.show = false
    commandApproval.value.loading = false
  }
}

// ==================== Project Event Bus ====================

const {
  streamingTasks, myTaskIds,
  anyStreamingWith,
  subscribe: subscribeBus,
  unsubscribe: unsubscribeBus,
  cancelTask,
} = useProjectEventBus({
  projectId: () => props.project.id,
  messages,
  persistentContextInfo,
  lastTokenUsage,
  scrollToBottom,
  refreshContextInfo,
  sendMessage: (content?: string) => sendMessage(content),
  onCommandApprovalRequest,
})

const anyStreaming = anyStreamingWith(streaming)

// 多任务 + 敲定方案流式卡片统一入口
const activeStreamCards = computed(() => {
  const cards: Array<{key: string; taskId: number; model: string; senderName: string; thinking: string; segments: any[]; toolCalls: any[]; isMine: boolean}> = []
  for (const [taskId, ts] of streamingTasks.value) {
    cards.push({
      key: 'task-' + taskId,
      taskId,
      model: ts.model || '模型',
      senderName: ts.senderName || '',
      thinking: ts.thinking,
      segments: ts.segments,
      toolCalls: ts.toolCalls,
      isMine: myTaskIds.value.has(taskId),
    })
  }
  if (finalizingPlan.value && streaming.value) {
    cards.push({
      key: 'finalize',
      taskId: 0,
      model: selectedModel.value,
      senderName: '',
      thinking: streamThinking.value,
      segments: streamSegments.value,
      toolCalls: streamToolCalls.value,
      isMine: true,
    })
  }
  return cards
})

// ==================== Ask User ====================

const {
  getCardState, toggleOption, submitQuestionCard,
  isAskUserAnswered, getAskUserAnswer, isAskUserAutoDecided,
  getDbAnswerForQuestion, getRegularToolCalls,
} = useAskUser(messages, (content: string) => sendMessage(content))

const currentUserName = computed(() => authStore.user?.nickname || authStore.user?.username || 'user')
function isMyMessage(msg: any): boolean {
  return msg.sender_name === currentUserName.value
}

/** 检测完全空的 assistant 消息 (内容为空且没有可渲染的工具调用) */
function isEmptyAssistantMessage(msg: any): boolean {
  if (msg.role !== 'assistant') return false
  if (msg.content?.trim()) return false
  if (msg.thinking_content?.trim()) return false
  // 有可渲染的 ask_user 卡片?
  const askUserTcs = (msg.tool_calls || []).filter((t: any) => t.name === 'ask_user')
  if (askUserTcs.some((tc: any) => parseQuestions(tc.arguments).length > 0)) return false
  // 有常规工具调用?
  if (getRegularToolCalls(msg.tool_calls).length > 0) return false
  return true
}

// ==================== Project Info Edit ====================

const showProjectEdit = ref(false)
const editProjectTitle = ref('')
const editProjectDesc = ref('')
const savingProject = ref(false)

watch(showProjectEdit, (val) => {
  if (val) {
    editProjectTitle.value = props.project.title || ''
    editProjectDesc.value = props.project.description || ''
  }
})

async function saveProjectInfo() {
  savingProject.value = true
  try {
    await projectApi.update(props.project.id, {
      title: editProjectTitle.value,
      description: editProjectDesc.value,
    })
    ;(props.project as any).title = editProjectTitle.value
    ;(props.project as any).description = editProjectDesc.value
    showProjectEdit.value = false
    message.success('项目信息已更新')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    savingProject.value = false
  }
}

// ==================== Tool Permissions ====================

const toolStore = useToolStore()

// 从 store 加载权限定义 (启动时加载一次)
if (!toolStore.permissions.length) {
  toolStore.fetchPermissions().catch(() => {/* fallback 到空列表 */})
}

// 弹出工具气泡时刷新权限定义
function onToolPopoverShow(show: boolean) {
  if (show) {
    toolStore.fetchPermissions().catch(() => {})
  }
}

// 动态权限定义: 非元权限 (用于 checkbox 渲染)
const permDefs = computed(() => toolStore.permissions.filter(p => !p.is_meta))
// 元标志列表 (不在 checkbox 里显示, 通过审批流写入)
const metaPermKeys = computed(() => toolStore.permissions.filter(p => p.is_meta).map(p => p.key))

// 默认权限: 所有非元权限中排除 execute_command
const ALL_DEFAULT_PERMS_COMPUTED = computed(() =>
  permDefs.value.length
    ? permDefs.value.map(p => p.key).filter(k => k !== 'execute_command')
    : ['ask_user', 'read_source', 'read_config', 'search', 'tree', 'execute_readonly_command']  // fallback
)

const toolPermissions = ref<string[]>(
  props.project.tool_permissions?.length ? props.project.tool_permissions : []
)
// 确保默认值在权限加载后同步
watch(ALL_DEFAULT_PERMS_COMPUTED, (defaults) => {
  if (!props.project.tool_permissions?.length && toolPermissions.value.length === 0) {
    toolPermissions.value = [...defaults]
  }
}, { immediate: true })

// checkbox 绑定值: 过滤掉元标志
const toolCheckboxValues = computed(() =>
  toolPermissions.value.filter(p => !metaPermKeys.value.includes(p))
)
// 获取指定 key 的所有子权限 key (递归)
function getChildPermKeys(parentKey: string): string[] {
  const children: string[] = []
  for (const p of toolStore.permissions) {
    if (p.parent === parentKey) {
      children.push(p.key)
      children.push(...getChildPermKeys(p.key))
    }
  }
  return children
}
function onToolPermChange(val: string[]) {
  // 保留元标志
  let meta = toolPermissions.value.filter(p => metaPermKeys.value.includes(p))

  // 检查被取消勾选的权限，级联移除其子权限
  const removed = toolCheckboxValues.value.filter(k => !val.includes(k))
  const cascadeRemove = new Set<string>()
  for (const r of removed) {
    for (const child of getChildPermKeys(r)) {
      cascadeRemove.add(child)
    }
  }
  const finalVal = val.filter(k => !cascadeRemove.has(k))
  meta = meta.filter(m => !cascadeRemove.has(m))

  // 如果关闭了 execute_command, 也移除 auto_approve_commands
  const finalMeta = finalVal.includes('execute_command') ? meta : meta.filter(m => m !== 'auto_approve_commands')
  const newPerms = [...finalVal, ...finalMeta]
  toolPermissions.value = newPerms
  saveToolPermissions(newPerms)
}
function revokeAutoApprove() {
  const newPerms = toolPermissions.value.filter(p => p !== 'auto_approve_commands')
  toolPermissions.value = newPerms
  saveToolPermissions(newPerms)
  message.success('已撤销写命令自动批准')
}
async function saveToolPermissions(perms: string[]) {
  try {
    await projectApi.update(props.project.id, { tool_permissions: perms })
  } catch {
    message.error('保存工具权限失败')
  }
}

// ==================== Image Upload ====================

const pendingImages = ref<Array<{ file: File; preview: string; uploaded?: any }>>([])

async function onFileInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''
  try {
    const preview = URL.createObjectURL(file)
    const { data } = await discussionApi.uploadImage(props.project.id, file)
    pendingImages.value.push({ file, preview, uploaded: data })
  } catch (e: any) {
    message.error(e.response?.data?.detail || '图片上传失败')
  }
}

// ==================== Message Actions ====================

function toggleToolGroup(msgId: number) {
  expandedToolGroups[msgId] = !expandedToolGroups[msgId]
}

async function copyMessage(msg: any) {
  try {
    await navigator.clipboard.writeText(msg.content)
    message.success('已复制到剪贴板')
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = msg.content
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    message.success('已复制到剪贴板')
  }
}

async function retryMessage(msg: any) {
  const retryContent = msg.content
  const retryAttachments = msg.attachments || []
  try {
    if (msg.id && msg.id < 1e12) {
      await discussionApi.deleteMessageAndAfter(props.project.id, msg.id)
    }
    const idx = messages.value.findIndex(m => m.id === msg.id)
    if (idx >= 0) messages.value = messages.value.slice(0, idx)
    await sendMessage(retryContent, retryAttachments)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重试失败')
  }
}

async function regenerateMessage(msg: any) {
  try {
    if (msg.id && msg.id < 1e12) {
      await discussionApi.deleteMessage(props.project.id, msg.id)
    }
    messages.value = messages.value.filter(m => m.id !== msg.id)
    await sendMessage('', [], true)
  } catch (e: any) {
    if (e.name !== 'AbortError') {
      message.error('重新生成失败: ' + (e.message || ''))
    }
  }
}

// ==================== Stop Generation ====================

function stopAllMyStreaming() {
  for (const taskId of myTaskIds.value) {
    cancelTask(taskId)
  }
  if (streaming.value) {
    stopFinalizeStreaming()
  }
}

// ==================== Send Message ====================

async function handleStartChat() {
  startingChat.value = true
  try {
    await sendMessage('', [], true)
  } finally {
    startingChat.value = false
  }
}

async function sendMessage(overrideContent?: string, overrideAttachments?: any[], regenerate = false) {
  const text = overrideContent ?? inputText.value.trim()
  const isOverride = overrideContent !== undefined
  if (!text && !pendingImages.value.length && !isOverride) return

  const attachments = isOverride
    ? (overrideAttachments || [])
    : pendingImages.value
        .filter(img => img.uploaded)
        .map(img => ({
          type: 'image',
          url: img.uploaded.url,
          base64: img.uploaded.base64,
          mime_type: img.uploaded.mime_type,
          name: img.file.name,
        }))

  const senderName = authStore.user?.nickname || authStore.user?.username || 'user'
  if (!isOverride) {
    // 保存到输入历史 (去重连续相同输入)
    if (text.trim()) {
      const last = inputHistory.value[inputHistory.value.length - 1]
      if (last !== text.trim()) {
        inputHistory.value.push(text.trim())
      }
      saveInputHistory(props.project.id)
    }
    historyIndex.value = -1
    historySavedInput.value = ''
    inputText.value = ''
    pendingImages.value = []
  }

  const tempMsgId = Date.now()
  if (!regenerate) {
    messages.value.push({
      id: tempMsgId,
      role: 'user',
      sender_name: senderName,
      content: text,
      attachments,
      created_at: new Date().toISOString(),
      _pending: true,  // 标记为待确认的本地消息, SSE 去重用
    })
    scrollToBottom()
  }

  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (authStore.token) headers['Authorization'] = `Bearer ${authStore.token}`

    const response = await fetch(discussionApi.discussUrl(props.project.id), {
      method: 'POST',
      headers,
      body: JSON.stringify({ message: text, sender_name: senderName, attachments, max_tool_rounds: currentModelToolRounds.value, regenerate }),
    })

    const result = await response.json()

    if (result.status === 'muted') {
      message.info('AI 已禁言，消息已保存')
      if (result.user_message_id && !regenerate) {
        const tmpMsg = messages.value.find(m => m.id === tempMsgId)
        if (tmpMsg) tmpMsg.id = result.user_message_id
      }
      return
    }

    if (result.task_id) {
      myTaskIds.value.add(result.task_id)
      if (result.user_message_id && !regenerate) {
        const tmpMsg = messages.value.find(m => m.id === tempMsgId)
        if (tmpMsg) tmpMsg.id = result.user_message_id
      }
    }
  } catch (e: any) {
    if (e.name !== 'AbortError') {
      message.error('AI 通信异常: ' + (e.message || ''))
    }
  }
}

// ==================== Finalize Plan ====================

async function handleFinalizePlan() {
  await _handleFinalizePlan()
}

// ==================== AI Mute ====================

async function toggleAiMute() {
  muteLoading.value = true
  try {
    const { data } = await discussionApi.toggleAiMute(props.project.id)
    aiMuted.value = data.ai_muted
    if (data.ai_muted) {
      message.warning('AI 已禁言 · 仅人工讨论模式')
    } else {
      message.success('AI 已解除禁言 · 发送消息将触发 AI 回复')
    }
  } catch (e: any) {
    if (e.response?.status === 401) {
      message.error('Token 已过期，请刷新页面重新登录')
    } else {
      message.error(e.response?.data?.detail || '操作失败')
    }
  } finally {
    muteLoading.value = false
  }
}

// ==================== Keyboard ====================

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault()
    // AI 回复中时禁止发送
    if (anyStreaming.value) return
    sendMessage()
    return
  }
  // 上下箭头切换历史输入
  const isMultiLine = inputText.value.includes('\n')
  if (!isMultiLine && e.key === 'ArrowUp' && inputHistory.value.length > 0) {
    e.preventDefault()
    if (historyIndex.value === -1) {
      // 第一次进入历史模式，保存当前输入
      historySavedInput.value = inputText.value
    }
    if (historyIndex.value < inputHistory.value.length - 1) {
      historyIndex.value++
      inputText.value = inputHistory.value[inputHistory.value.length - 1 - historyIndex.value]
    }
    return
  }
  if (!isMultiLine && e.key === 'ArrowDown' && historyIndex.value >= 0) {
    e.preventDefault()
    historyIndex.value--
    if (historyIndex.value < 0) {
      // 回到底部，恢复原始输入
      inputText.value = historySavedInput.value
    } else {
      inputText.value = inputHistory.value[inputHistory.value.length - 1 - historyIndex.value]
    }
    return
  }
}

// ==================== Lifecycle ====================

// 当项目 ID 变化时重新加载消息 (修复新建项目后显示旧聊天上下文)
// immediate: true 确保首次挂载时也触发，防止复用组件时使用旧消息
watch(() => props.project.id, async (newId, oldId) => {
  if (newId === oldId && oldId !== undefined) return
  // 重置状态
  messages.value = []
  streaming.value = false
  streamContent.value = ''
  streamThinking.value = ''
  streamSegments.value = []
  streamingTasks.value.clear()
  myTaskIds.value.clear()
  // 加载新项目的消息
  try {
    const { data } = await discussionApi.getMessages(newId)
    messages.value = data
    await nextTick()
    scrollToBottom()
  } catch {}
  try {
    const { data } = await discussionApi.getAiMuteStatus(newId)
    aiMuted.value = data.ai_muted
  } catch {}
  // 加载该项目的输入历史
  loadInputHistory(newId)
  // 恢复该项目的模型选择
  selectedModel.value = props.project.discussion_model || 'gpt-4o'
  // 刷新工具权限
  toolPermissions.value = props.project.tool_permissions?.length
    ? props.project.tool_permissions
    : [...ALL_DEFAULT_PERMS_COMPUTED.value]
  refreshContextInfo()
}, { immediate: true })

onMounted(async () => {
  // watch immediate: true 已在挂载时加载消息，这里主要是初始化 event bus 和模型列表
  subscribeBus()
  loadModels().then(() => refreshContextInfo())

  setTimeout(() => {
    if (!persistentContextInfo.value) refreshContextInfo()
  }, 3000)
})

onUnmounted(() => {
  unsubscribeBus()
})
</script>

<style>
.markdown-body {
  color: #e0e0e0;
  line-height: 1.5;
  font-size: 13px;
}
.markdown-body pre {
  background: #0d1b2a;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
}
.markdown-body code {
  background: #0d1b2a;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
.markdown-body pre code {
  background: none;
  padding: 0;
}
.markdown-body p { margin: 0.4em 0; }
.markdown-body h1, .markdown-body h2, .markdown-body h3 { color: #e94560; margin: 0.6em 0 0.3em; }
.markdown-body ul, .markdown-body ol { padding-left: 1.5em; }
.markdown-body blockquote {
  border-left: 3px solid #e94560;
  margin: 0.4em 0;
  padding: 0.3em 0.8em;
  background: rgba(233, 69, 96, 0.1);
}
.markdown-body table { border-collapse: collapse; width: 100%; }
.markdown-body th, .markdown-body td { border: 1px solid #333; padding: 4px 10px; }
.markdown-body th { background: #0d1b2a; }
.markdown-body img { max-width: 100%; border-radius: 6px; }
.thinking-block {
  color: #999;
  font-size: 12px;
  line-height: 1.4;
  font-style: italic;
  border-left: 2px solid #555;
  padding-left: 8px;
  margin: 3px 0;
}
.thinking-block p { margin: 0.2em 0; }

/* 消息操作按钮 (header 内联, 默认半透明) */
.msg-actions {
  opacity: 0.2;
  transition: opacity 0.15s ease;
  flex-shrink: 0;
}
.msg-actions:hover, .msg-actions-visible {
  opacity: 0.8;
}
.msg-actions .n-button {
  padding: 0 3px !important;
}

/* Tool call visualization */
.tool-group-header {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 6px;
  font-size: 11px;
  color: #63e2b7;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s;
  user-select: none;
}
.tool-group-header:hover {
  background: rgba(99, 226, 183, 0.06);
}
.tool-group-arrow {
  font-size: 8px;
  transition: transform 0.15s;
  color: #666;
}
.tool-group-arrow.open {
  transform: rotate(90deg);
}
.tool-group-icon {
  font-size: 12px;
}
.tool-group-count {
  font-size: 11px;
  color: #888;
}
.tool-group-body {
  margin-left: 4px;
}
.question-result-text {
  padding: 4px 0;
  font-size: 12px;
  color: #aaa;
}
.tool-inline {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  margin: 2px 0;
  font-size: 11px;
  color: #aaa;
  background: rgba(99, 226, 183, 0.04);
  border-left: 2px solid rgba(99, 226, 183, 0.4);
  border-radius: 0 4px 4px 0;
  line-height: 1.6;
  flex-wrap: wrap;
}
.tool-inline-name {
  color: #e0e0e0;
  font-weight: 500;
  white-space: nowrap;
}
.tool-inline-args {
  color: #888;
  font-size: 10px;
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  background: none;
  padding: 0;
  word-break: break-all;
}
.tool-inline-time {
  color: #666;
  font-size: 10px;
  white-space: nowrap;
}
.tool-inline-view {
  color: #63e2b7;
  font-size: 10px;
  cursor: pointer;
  white-space: nowrap;
  margin-left: 2px;
  text-decoration: underline;
  text-decoration-style: dotted;
}
.tool-inline-view:hover {
  color: #7eebca;
}
.tool-call-item {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 5px;
  padding: 4px 8px;
  margin: 3px 0;
  font-size: 11px;
}
.tool-call-header {
  display: flex;
  align-items: center;
  gap: 5px;
  font-weight: 500;
  color: #ccc;
}
.tool-result-content {
  color: #999;
  font-size: 10px;
  margin-top: 3px;
  max-height: 160px;
  overflow-y: auto;
  white-space: pre-wrap;
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  background: rgba(0, 0, 0, 0.2);
  border-radius: 4px;
  padding: 4px 6px;
}
.tool-icon-ok { color: #63e2b7; }
.tool-icon-error { color: #e88080; }
.tool-icon-pending { color: #f2c97d; }

/* ============ ask_user 问题卡片 ============ */
.question-card {
  background: linear-gradient(135deg, rgba(99, 226, 183, 0.06), rgba(14, 165, 233, 0.06));
  border: 1px solid rgba(99, 226, 183, 0.2);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 6px 0;
}
.question-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.question-card-header-done {
  margin-bottom: 4px;
  padding-bottom: 4px;
}
.question-card-icon {
  font-size: 14px;
  flex-shrink: 0;
}
.question-card-title {
  color: #63e2b7;
  font-size: 12px;
  font-weight: 600;
}
.question-card-hint {
  color: #666;
  font-size: 10px;
  margin-left: auto;
}
.question-type-tag {
  display: inline-block;
  font-size: 10px;
  color: #0ea5e9;
  background: rgba(14, 165, 233, 0.12);
  border: 1px solid rgba(14, 165, 233, 0.3);
  border-radius: 3px;
  padding: 0 4px;
  margin-left: 6px;
  vertical-align: middle;
  font-weight: 400;
}
.question-item {
  margin-bottom: 10px;
}
.question-item:last-child {
  margin-bottom: 0;
}
.question-text {
  color: #e0e0e0;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
  margin-bottom: 3px;
}
.question-context {
  color: #777;
  font-size: 11px;
  line-height: 1.3;
  margin-bottom: 5px;
  padding-left: 14px;
  font-style: italic;
}
.question-options {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-left: 14px;
}
.question-option-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  font-size: 12px;
  color: #b0b0b0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 14px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
  line-height: 1.4;
}
.question-option-btn:hover {
  color: #e0e0e0;
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.25);
}
/* 推荐标记 (未选中时显示) */
.question-option-recommended {
  color: #63e2b7;
  border-color: rgba(99, 226, 183, 0.3);
  background: rgba(99, 226, 183, 0.06);
}
.question-option-recommended:hover {
  border-color: rgba(99, 226, 183, 0.5);
  background: rgba(99, 226, 183, 0.12);
}
.rec-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  background: #63e2b7;
  border-radius: 50%;
  flex-shrink: 0;
}
/* 选中状态 */
.question-option-selected {
  color: #fff !important;
  background: rgba(99, 226, 183, 0.25) !important;
  border-color: #63e2b7 !important;
}
.option-desc {
  color: #777;
  font-size: 10px;
  margin-left: 2px;
}
.question-option-selected .option-desc {
  color: rgba(255,255,255,0.6);
}
.question-custom-input {
  display: block;
  width: calc(100% - 14px);
  margin: 6px 0 0 14px;
  padding: 4px 8px;
  font-size: 12px;
  color: #e0e0e0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  outline: none;
  transition: border-color 0.15s;
}
.question-custom-input:focus {
  border-color: rgba(99, 226, 183, 0.5);
}
.question-custom-input::placeholder {
  color: #555;
}
.question-submit-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
/* 已回答的紧凑摘要 */
.question-summary-row {
  display: flex;
  gap: 6px;
  font-size: 11px;
  line-height: 1.5;
  padding: 1px 0;
}
.question-summary-q {
  color: #888;
  flex-shrink: 0;
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.question-summary-a {
  color: #63e2b7;
  font-weight: 500;
}
/* AI 自行决定的推荐答案 (区别于用户选择的绿色) */
.question-summary-a-auto {
  color: #8a8a8a;
  font-weight: 400;
  font-style: italic;
}
/* 问题准备中骨架屏 */
.question-preparing-body {
  padding: 4px 0;
}
.question-preparing-skeleton {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.skeleton-line {
  height: 12px;
  background: linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0.04) 75%);
  background-size: 200% 100%;
  border-radius: 6px;
  animation: skeleton-shimmer 1.5s infinite;
}
.skeleton-options {
  display: flex;
  gap: 6px;
  padding-left: 14px;
}
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

/* ============ 项目信息栏 ============ */
.project-info-bar {
  display: flex;
  align-items: center;
  padding: 3px 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  background: rgba(255,255,255,0.02);
  cursor: pointer;
  gap: 6px;
  flex-shrink: 0;
  transition: background 0.15s;
}
.project-info-bar:hover {
  background: rgba(255,255,255,0.05);
}
.project-info-title {
  font-weight: 600;
  font-size: 12px;
  color: rgba(255,255,255,0.8);
}
.project-info-sep {
  margin: 0 4px;
  opacity: 0.25;
  font-size: 11px;
}
.project-info-desc {
  font-size: 11px;
  color: rgba(255,255,255,0.4);
}
.project-info-edit-icon {
  font-size: 11px;
  opacity: 0;
  transition: opacity 0.15s;
  flex-shrink: 0;
}
.project-info-bar:hover .project-info-edit-icon {
  opacity: 0.5;
}

/* ============ 上下文明细气泡 (树形检查器) ============ */
.ctx-breakdown {
  padding: 10px 12px;
  min-width: 240px;
  max-width: 360px;
  max-height: 420px;
  overflow-y: auto;
  font-size: 12px;
}
.ctx-breakdown-title {
  font-weight: 600;
  color: #e0e0e0;
  margin-bottom: 8px;
}
.ctx-breakdown-bar {
  display: flex;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: rgba(255,255,255,0.06);
  margin-bottom: 10px;
}
.ctx-bar-seg {
  height: 100%;
  min-width: 1px;
  transition: width 0.3s;
}
.ctx-bar-system { background: #a855f7; }
.ctx-bar-tools { background: #0ea5e9; }
.ctx-bar-history { background: #f59e0b; }

/* 树形节点 */
.ctx-tree { display: flex; flex-direction: column; gap: 1px; }
.ctx-tree-node { display: flex; flex-direction: column; }
.ctx-tree-row {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 0;
  color: #bbb;
  font-size: 11px;
  cursor: pointer;
  border-radius: 3px;
  transition: background 0.1s;
}
.ctx-tree-row:hover { background: rgba(255,255,255,0.04); }
.ctx-tree-row-child { padding-left: 16px; }
.ctx-tree-row-leaf { padding-left: 32px; cursor: default; }
.ctx-clickable-row { cursor: pointer !important; }
.ctx-clickable { text-decoration-style: dotted; text-decoration-line: underline; text-underline-offset: 2px; text-decoration-color: #555; }
.ctx-clickable:hover { color: #e0e0e0; text-decoration-color: #999; }
.ctx-tree-arrow {
  font-size: 8px;
  width: 10px;
  text-align: center;
  flex-shrink: 0;
  transition: transform 0.15s;
  color: #666;
}
.ctx-tree-arrow.open { transform: rotate(90deg); }
.ctx-tree-leaf { font-size: 9px; color: #444; }
.ctx-tree-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-tree-children {
  display: flex;
  flex-direction: column;
}
.ctx-tree-msg-label {
  display: flex;
  align-items: center;
  gap: 4px;
}
.ctx-msg-preview {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  color: #777;
}
.ctx-role-user { color: #0ea5e9; }
.ctx-role-assistant { color: #e94560; }
.ctx-role-system { color: #63e2b7; }

.ctx-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.ctx-val {
  margin-left: auto;
  color: #ddd;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.ctx-tree-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(255,255,255,0.08);
  color: #aaa;
  font-size: 11px;
}
.ctx-breakdown-msgs {
  margin-top: 4px;
  color: #888;
  font-size: 10px;
}

/* ask_user 回答紧凑指示器 */
.ask-user-reply-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 10px;
  background: rgba(14, 165, 233, 0.08);
  color: #8aa;
  font-size: 11px;
}
.ask-reply-detail-link {
  cursor: pointer;
  color: #0ea5e9;
  font-size: 10px;
  opacity: 0.7;
  transition: opacity 0.15s;
}
.ask-reply-detail-link:hover { opacity: 1; }

/* ============ 输入区布局 ============ */
.input-area {
  background: #16213e;
  border-radius: 8px;
  padding: 5px 8px;
  flex-shrink: 0;
}

/* 第 1 行工具栏: flexbox + nowrap + 模型选择器自动缩小 */
.toolbar-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
}
.toolbar-row > * {
  flex-shrink: 0;
}
/* 模型选择器适应内容宽度，空间不足时可缩小 */
.model-select-group {
  display: flex;
  align-items: center;
  flex: 0 1 auto;
  min-width: 100px;
  overflow: hidden;
}
.model-select-group .n-select {
  min-width: 0;
}
.model-select-group .n-base-selection {
  border-top-right-radius: 0 !important;
  border-bottom-right-radius: 0 !important;
}
.model-refresh-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 28px;
  border: 1px solid rgba(255,255,255,0.15);
  border-left: none;
  border-radius: 0 4px 4px 0;
  background: rgba(255,255,255,0.04);
  color: #aaa;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}
.model-refresh-btn:hover:not(:disabled) {
  background: rgba(255,255,255,0.1);
  color: #e0e0e0;
}
.model-refresh-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 第 3 行操作栏 */
.action-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-top: 2px;
}
.action-bar-item {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.action-bar-stat {
  font-size: 10px;
  color: rgba(255,255,255,0.35);
  white-space: nowrap;
  flex-shrink: 0;
}
.action-bar-spring {
  flex: 1;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.spin-icon {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

/* 上下文内容气泡 */
.ctx-content-pre {
  margin: 0;
  padding: 0;
  font-family: 'Menlo', 'Monaco', 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #ccc;
  white-space: pre-wrap;
  word-break: break-all;
  background: transparent;
}

/* 空对话欢迎状态 */
.empty-chat-welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 280px;
  padding: 40px 20px;
  opacity: 0;
  animation: fadeInUp 0.5s ease forwards;
}
.empty-chat-icon {
  font-size: 56px;
  margin-bottom: 12px;
  filter: drop-shadow(0 4px 12px rgba(0,0,0,0.3));
  animation: gentleBounce 2s ease-in-out infinite;
}
.empty-chat-title {
  font-size: 18px;
  font-weight: 600;
  color: rgba(255,255,255,0.85);
  margin-bottom: 6px;
}
.empty-chat-desc {
  font-size: 13px;
  color: rgba(255,255,255,0.4);
  max-width: 320px;
  text-align: center;
  line-height: 1.5;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes gentleBounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}
</style>
