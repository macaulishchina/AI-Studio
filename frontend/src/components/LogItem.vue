<template>
  <div class="log-item" @click="onClick">
    <!-- 类型色条 -->
    <div class="type-bar" :style="{ background: typeTag.color }" />

    <!-- 主体 -->
    <div class="body">
      <!-- 第一行：图标+技能标签 + 标题 + 右侧元信息 -->
      <div class="title-row">
        <span class="role-icon">{{ roleIcon }}</span>
        <n-tag :color="{ color: typeTag.color + '22', textColor: typeTag.color, borderColor: typeTag.color + '55' }" size="small" class="type-tag">
          {{ typeTag.label }}
        </n-tag>
        <span class="title">{{ item.title || item.name || '未命名' }}</span>
        <div class="spacer" />
        <div class="meta-right">
          <span v-if="(item as any).message_count" class="meta-text">💬 {{ (item as any).message_count }}</span>
          <span v-if="item.updated_at" class="meta-text">{{ formatDate(item.updated_at) }}</span>
          <n-tag v-if="item.status" :type="statusTagType(item.status)" size="small" round>
            {{ statusDisplay(item.status) }}
          </n-tag>
          <n-tag v-if="(item as any).is_archived" type="default" size="small" :bordered="false">已归档</n-tag>
        </div>
      </div>

      <!-- 第二行：描述摘要 -->
      <div v-if="item.description" class="desc">{{ shortContent(item.description) }}</div>

      <!-- 第三行：创建者 + 参与者 -->
      <div v-if="item.created_by || (item.participants && item.participants.length)" class="people-row">
        <span v-if="item.created_by" class="person-badge creator">
          <span class="person-avatar">{{ item.created_by.charAt(0).toUpperCase() }}</span>
          {{ item.created_by }}
        </span>
        <template v-if="item.participants && item.participants.length">
          <span class="people-sep">·</span>
          <span
            v-for="p in item.participants.filter(x => x !== item.created_by).slice(0, 4)"
            :key="p"
            class="person-badge participant"
          >
            <span class="person-avatar">{{ p.charAt(0).toUpperCase() }}</span>
            {{ p }}
          </span>
          <span
            v-if="item.participants.filter(x => x !== item.created_by).length > 4"
            class="meta-text"
          >+{{ item.participants.filter(x => x !== item.created_by).length - 4 }}</span>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'

interface RoleBrief {
  id?: number
  name?: string
  icon?: string
  stages?: { key: string; label: string; status: string }[]
  ui_labels?: Record<string, string>
}

interface LogItemShape {
  id?: number | string
  title?: string
  name?: string
  description?: string
  log_type?: string
  type?: string
  status?: string
  updated_at?: string
  created_by?: string
  participants?: string[]
  role?: RoleBrief | null
}

const props = defineProps<{ item: LogItemShape }>()
const emit = defineEmits<{ (e: 'click'): void }>()

// ── 无 role 时的兆底映射 ─────────────────────────────────────────
const FALLBACK: Record<string, { label: string; color: string; emoji: string }> = {
  bug:     { label: '缺陷', color: '#d03050', emoji: '🐞' },
  feature: { label: '需求', color: '#2080f0', emoji: '✨' },
  task:    { label: '任务', color: '#18a058', emoji: '📝' },
  note:    { label: '记录', color: '#888',    emoji: '📌' },
  project: { label: '项目', color: '#63e2b7', emoji: '📋' },
}

// ── role.name 到颜色的映射（按关键词匹配） ──────────────────────
const NAME_COLOR: Array<{ keys: string[]; color: string }> = [
  { keys: ['bug', '缺陷', '问诊', 'fix', '修复'], color: '#d03050' },
  { keys: ['需求', 'feature', '功能', '分析'],    color: '#2080f0' },
  { keys: ['任务', 'task'],                        color: '#18a058' },
  { keys: ['审查', '评审', 'review'],              color: '#f0a020' },
  { keys: ['部署', 'deploy', '发布'],              color: '#8a2be2' },
]

function roleColor(name = ''): string {
  const n = name.toLowerCase()
  for (const { keys, color } of NAME_COLOR) {
    if (keys.some(k => n.includes(k))) return color
  }
  return '#63e2b7'
}

// ── 左侧图标 ─────────────────────────────────────────────────────
const roleIcon = computed(() =>
  props.item.role?.icon ||
  FALLBACK[props.item.log_type || props.item.type || 'project']?.emoji ||
  '📄'
)

// ── 类型标签：优先 role.name，确保每种角色有独立颜色和名称 ──────
const typeTag = computed(() => {
  const role = props.item.role
  if (role?.name) {
    return { label: role.name, color: roleColor(role.name) }
  }
  const fb = FALLBACK[props.item.log_type || props.item.type || 'project']
  return fb
    ? { label: fb.label, color: fb.color }
    : { label: '日志', color: '#888' }
})

// ── 状态标签 ─────────────────────────────────────────────────────
const STATUS_TYPE: Record<string, 'error' | 'warning' | 'info' | 'success' | 'default'> = {
  draft: 'default', discussing: 'info', planned: 'warning',
  implementing: 'warning', reviewing: 'info', deploying: 'warning',
  deployed: 'success', rolled_back: 'error', closed: 'default',
}
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿', discussing: '讨论中', planned: '已定稿',
  implementing: '实施中', reviewing: '审核中', deploying: '部署中',
  deployed: '已部署', rolled_back: '已回滚', closed: '已关闭',
}

function statusDisplay(s = '') {
  const stages = props.item.role?.stages
  if (stages) {
    const st = stages.find(x => x.status === s)
    if (st) return st.label
  }
  return STATUS_LABEL[s] || s
}

function statusTagType(s = '') {
  const stages = props.item.role?.stages
  if (stages?.length && stages[stages.length - 1].status === s) return 'success'
  return STATUS_TYPE[s] ?? 'default'
}

function shortContent(s = '') {
  return s.length > 100 ? s.slice(0, 97) + '...' : s
}

function formatDate(d = '') {
  if (!d) return ''
  try {
    // 后端存储 UTC 时间 (datetime.utcnow)，ISO 字符串不含 Z 后缀
    // 需要手动补 Z 让浏览器正确转为本地时区
    const utcStr = d && !d.endsWith('Z') && !d.includes('+') ? d + 'Z' : d
    return new Date(utcStr).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch { return d }
}

function onClick() { emit('click') }
</script>

<style scoped>
.log-item {
  display: flex;
  align-items: stretch;
  cursor: pointer;
  transition: background 0.15s;
  border-radius: 4px;
  overflow: hidden;
}
.log-item:hover { background: rgba(255, 255, 255, 0.04) }

/* 左侧竖色条 */
.type-bar {
  width: 3px;
  flex-shrink: 0;
  border-radius: 2px 0 0 2px;
  opacity: 0.85;
}

.body {
  flex: 1;
  min-width: 0;
  padding: 10px 14px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: nowrap;
}

.role-icon {
  font-size: 15px;
  flex-shrink: 0;
  line-height: 1;
}

.type-tag {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.title {
  font-weight: 600;
  font-size: 14px;
  color: var(--c-foreground, #e6eef8);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.spacer { flex: 1 }

.meta-right {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}

.meta-text { font-size: 12px; color: var(--c-muted, #9aa6b2) }

.desc {
  margin-top: 4px;
  font-size: 13px;
  color: var(--c-muted, #9aa6b2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 创建者 & 参与者行 */
.people-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  flex-wrap: wrap;
}

.person-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: var(--c-muted, #9aa6b2);
  padding: 1px 6px 1px 2px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
}

.person-badge.creator {
  color: #63e2b7;
  background: rgba(99, 226, 183, 0.08);
}

.person-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  font-size: 9px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.1);
  color: inherit;
  flex-shrink: 0;
}

.person-badge.creator .person-avatar {
  background: rgba(99, 226, 183, 0.2);
}

.people-sep {
  color: rgba(255, 255, 255, 0.15);
  font-size: 10px;
}

/* ── 移动端适配 ── */
@media (max-width: 767px) {
  .body {
    padding: 8px 10px;
  }
  .title-row {
    gap: 4px;
    flex-wrap: wrap;
  }
  .title {
    font-size: 13px;
    flex: 1;
    min-width: 0;
  }
  .meta-right {
    gap: 4px;
    flex-wrap: wrap;
  }
  .meta-text {
    font-size: 11px;
  }
  .desc {
    font-size: 12px;
  }
  .people-row {
    gap: 4px;
  }
  .person-badge {
    font-size: 10px;
    padding: 1px 4px 1px 2px;
  }
}
</style>
