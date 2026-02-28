<template>
  <n-space vertical :size="16">
    <!-- 待审批提醒 -->
    <n-alert v-if="pendingUsers.length > 0" type="warning" :bordered="false">
      有 {{ pendingUsers.length }} 个用户等待审批
    </n-alert>

    <!-- 过滤 & 操作 -->
    <n-space justify="space-between" align="center">
      <n-space :size="8" align="center">
        <n-select
          v-model:value="statusFilter"
          :options="statusOptions"
          size="small"
          style="width: 140px"
          placeholder="全部状态"
          clearable
        />
        <n-text depth="3" style="font-size: 11px">共 {{ filteredUsers.length }} 个用户</n-text>
      </n-space>
      <n-button size="small" secondary @click="loadData">
        🔄 刷新
      </n-button>
    </n-space>

    <!-- 用户列表 -->
    <n-spin :show="loading">
      <n-empty v-if="!loading && filteredUsers.length === 0" description="暂无用户" />

      <div v-else class="user-list">
        <div
          v-for="u in filteredUsers"
          :key="u.id"
          class="user-card"
          :class="{
            'user-card-pending': u.status === 'pending',
            'user-card-disabled': u.status === 'disabled',
          }"
        >
          <!-- 头部: 用户名 + 状态 -->
          <div class="user-card-header">
            <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0">
              <n-text strong style="font-size: 14px">{{ u.nickname || u.username }}</n-text>
              <n-text v-if="u.nickname && u.nickname !== u.username" depth="3" style="font-size: 11px">@{{ u.username }}</n-text>
            </div>
            <n-space :size="4">
              <n-tag :type="statusTagType(u.status)" size="small" :bordered="false" round>
                {{ statusLabel(u.status) }}
              </n-tag>
              <n-tag type="info" size="small" :bordered="false" round>
                {{ roleLabel(u.role) }}
              </n-tag>
            </n-space>
          </div>

          <!-- 信息行 -->
          <div class="user-card-meta">
            <n-text depth="3" style="font-size: 11px">
              注册于 {{ formatDt(u.created_at) }}
              <template v-if="u.approved_by"> · {{ u.approved_by }} 审批于 {{ formatDt(u.approved_at) }}</template>
              <template v-if="u.last_login_at"> · 最后登录 {{ formatDt(u.last_login_at) }}</template>
            </n-text>
          </div>

          <!-- 权限标签 -->
          <div v-if="u.permissions?.length" class="user-card-perms">
            <n-tag
              v-for="p in u.permissions.slice(0, 6)" :key="p"
              size="tiny"
              :bordered="false"
              round
              type="default"
            >
              {{ permLabel(p) }}
            </n-tag>
            <n-text v-if="u.permissions.length > 6" depth="3" style="font-size: 10px">
              +{{ u.permissions.length - 6 }}
            </n-text>
          </div>

          <!-- 操作按钮 -->
          <div class="user-card-actions">
            <template v-if="u.status === 'pending'">
              <n-button type="success" size="small" @click="showApproveModal(u)">
                ✅ 审批
              </n-button>
              <n-button type="error" size="small" secondary @click="rejectUser(u)">
                🚫 拒绝
              </n-button>
            </template>
            <template v-else>
              <n-button size="small" secondary @click="showEditModal(u)">
                ✏️ 编辑
              </n-button>
              <n-dropdown
                trigger="click"
                :options="moreActions(u)"
                @select="(key: string) => handleMoreAction(key, u)"
              >
                <n-button size="small" quaternary>⋯</n-button>
              </n-dropdown>
            </template>
          </div>
        </div>
      </div>
    </n-spin>

    <!-- 审批 / 编辑 Modal -->
    <n-modal v-model:show="showModal" preset="card" :title="modalTitle" style="width: 560px; max-width: 95vw" :bordered="false">
      <n-space vertical :size="16">
        <!-- 用户名 -->
        <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" bordered size="small">
          <n-descriptions-item label="用户名">{{ editingUser?.username }}</n-descriptions-item>
          <n-descriptions-item label="昵称">
            <n-input v-model:value="editForm.nickname" size="small" placeholder="显示昵称" />
          </n-descriptions-item>
        </n-descriptions>

        <!-- 角色选择 -->
        <n-card size="small" style="background: #212121">
          <template #header>
            <n-text style="font-size: 13px">👤 角色</n-text>
          </template>
          <n-radio-group v-model:value="editForm.role" @update:value="onRoleChange">
            <n-space :size="16">
              <n-radio value="admin">
                <span>🛡️ 管理员</span>
                <n-text depth="3" style="font-size: 10px; display: block">全部权限</n-text>
              </n-radio>
              <n-radio value="developer">
                <span>💻 开发者</span>
                <n-text depth="3" style="font-size: 10px; display: block">项目 + AI + 审查</n-text>
              </n-radio>
              <n-radio value="viewer">
                <span>👁️ 观察者</span>
                <n-text depth="3" style="font-size: 10px; display: block">仅查看</n-text>
              </n-radio>
            </n-space>
          </n-radio-group>
        </n-card>

        <!-- 细分权限 -->
        <n-card size="small" style="background: #212121">
          <template #header>
            <n-space align="center" :size="8">
              <n-text style="font-size: 13px">🔑 细分权限</n-text>
              <n-text depth="3" style="font-size: 11px">基于角色预设, 可逐项调整</n-text>
            </n-space>
          </template>
          <n-space vertical :size="12">
            <div v-for="group in permGroups" :key="group.group">
              <n-text depth="2" style="font-size: 12px; font-weight: 600; display: block; margin-bottom: 4px">
                {{ group.group }}
              </n-text>
              <n-checkbox-group v-model:value="editForm.permissions">
                <n-space :size="4" :wrap="true">
                  <n-checkbox
                    v-for="item in group.items"
                    :key="item.key"
                    :value="item.key"
                    :label="item.icon + ' ' + item.label"
                  />
                </n-space>
              </n-checkbox-group>
            </div>
          </n-space>
        </n-card>

        <!-- 状态 (仅编辑模式) -->
        <n-card v-if="modalMode === 'edit'" size="small" style="background: #212121">
          <template #header>
            <n-text style="font-size: 13px">📊 状态</n-text>
          </template>
          <n-radio-group v-model:value="editForm.status">
            <n-space>
              <n-radio value="active">✅ 激活</n-radio>
              <n-radio value="disabled">🚫 禁用</n-radio>
            </n-space>
          </n-radio-group>
        </n-card>
      </n-space>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveUser">
            {{ modalMode === 'approve' ? '✅ 审批并激活' : '💾 保存' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { userApi } from '@/api'

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

interface UserInfo {
  id: number
  username: string
  nickname: string
  role: string
  status: string
  permissions: string[]
  created_at: string
  approved_by?: string | null
  approved_at?: string | null
  last_login_at?: string | null
}

interface PermItem { key: string; label: string; icon: string }
interface PermGroupDef { group: string; items: PermItem[] }

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const saving = ref(false)
const users = ref<UserInfo[]>([])
const statusFilter = ref<string | null>(null)
const permGroups = ref<PermGroupDef[]>([])
const roleDefaults = ref<Record<string, string[]>>({})
const permLabelMap = ref<Record<string, string>>({})

const statusOptions = [
  { label: '⏳ 待审批', value: 'pending' },
  { label: '✅ 已激活', value: 'active' },
  { label: '🚫 已禁用', value: 'disabled' },
]

const pendingUsers = computed(() => users.value.filter(u => u.status === 'pending'))
const filteredUsers = computed(() =>
  statusFilter.value ? users.value.filter(u => u.status === statusFilter.value) : users.value
)

// Modal
const showModal = ref(false)
const modalMode = ref<'approve' | 'edit'>('approve')
const modalTitle = computed(() => modalMode.value === 'approve' ? '审批用户' : '编辑用户')
const editingUser = ref<UserInfo | null>(null)
const editForm = ref({
  nickname: '',
  role: 'viewer',
  permissions: [] as string[],
  status: 'active',
})

function statusTagType(s: string) {
  if (s === 'active') return 'success'
  if (s === 'pending') return 'warning'
  return 'error'
}

function statusLabel(s: string) {
  if (s === 'active') return '已激活'
  if (s === 'pending') return '待审批'
  return '已禁用'
}

function roleLabel(r: string) {
  if (r === 'admin') return '🛡️ 管理员'
  if (r === 'developer') return '💻 开发者'
  return '👁️ 观察者'
}

function permLabel(key: string) {
  return permLabelMap.value[key] || key
}

function formatDt(dt?: string | null) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function moreActions(u: UserInfo) {
  const items: any[] = []
  if (u.status === 'active') items.push({ label: '🚫 禁用', key: 'disable' })
  if (u.status === 'disabled') items.push({ label: '✅ 激活', key: 'enable' })
  items.push({ label: '🔑 重置密码', key: 'reset-pwd' })
  items.push({ label: '🗑️ 删除', key: 'delete' })
  return items
}

// ---- Data Loading ----

async function loadData() {
  loading.value = true
  try {
    const [usersRes, permRes] = await Promise.all([
      userApi.list(),
      userApi.permissionDefs(),
    ])
    users.value = usersRes.data
    permGroups.value = permRes.data.groups
    roleDefaults.value = permRes.data.role_defaults
    // 构建 permLabel 映射
    const map: Record<string, string> = {}
    for (const g of permRes.data.groups) {
      for (const item of g.items) {
        map[item.key] = item.icon + ' ' + item.label
      }
    }
    permLabelMap.value = map
  } catch (e: any) {
    message.error('加载用户列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

// ---- Modal ----

function showApproveModal(u: UserInfo) {
  editingUser.value = u
  modalMode.value = 'approve'
  editForm.value = {
    nickname: u.nickname || u.username,
    role: 'developer',
    permissions: [...(roleDefaults.value['developer'] || [])],
    status: 'active',
  }
  showModal.value = true
}

function showEditModal(u: UserInfo) {
  editingUser.value = u
  modalMode.value = 'edit'
  editForm.value = {
    nickname: u.nickname || u.username,
    role: u.role || 'viewer',
    permissions: [...(u.permissions || [])],
    status: u.status,
  }
  showModal.value = true
}

function onRoleChange(role: string) {
  // 角色切换时重置为角色默认权限
  editForm.value.permissions = [...(roleDefaults.value[role] || [])]
}

async function saveUser() {
  if (!editingUser.value) return
  saving.value = true
  try {
    if (modalMode.value === 'approve') {
      await userApi.approve(editingUser.value.id, {
        role: editForm.value.role,
        permissions: editForm.value.permissions,
      })
      message.success('用户已审批激活')
    } else {
      await userApi.update(editingUser.value.id, {
        nickname: editForm.value.nickname,
        role: editForm.value.role,
        permissions: editForm.value.permissions,
        status: editForm.value.status,
      })
      message.success('用户信息已更新')
    }
    showModal.value = false
    await loadData()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  } finally {
    saving.value = false
  }
}

// ---- Actions ----

async function rejectUser(u: UserInfo) {
  dialog.warning({
    title: '确认拒绝',
    content: `确定拒绝用户「${u.nickname || u.username}」的注册申请？`,
    positiveText: '拒绝',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await userApi.reject(u.id)
        message.success('已拒绝')
        await loadData()
      } catch (e: any) {
        message.error(e.response?.data?.detail || '操作失败')
      }
    },
  })
}

async function handleMoreAction(key: string, u: UserInfo) {
  if (key === 'disable') {
    try {
      await userApi.update(u.id, { status: 'disabled' })
      message.success('已禁用')
      await loadData()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '操作失败')
    }
  } else if (key === 'enable') {
    try {
      await userApi.update(u.id, { status: 'active' })
      message.success('已激活')
      await loadData()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '操作失败')
    }
  } else if (key === 'reset-pwd') {
    dialog.warning({
      title: '重置密码',
      content: `将「${u.nickname || u.username}」的密码重置为 studio123？`,
      positiveText: '确认重置',
      negativeText: '取消',
      onPositiveClick: async () => {
        try {
          await userApi.resetPassword(u.id)
          message.success('密码已重置为 studio123')
        } catch (e: any) {
          message.error(e.response?.data?.detail || '操作失败')
        }
      },
    })
  } else if (key === 'delete') {
    dialog.error({
      title: '确认删除',
      content: `确定永久删除用户「${u.nickname || u.username}」？此操作不可撤销。`,
      positiveText: '删除',
      negativeText: '取消',
      onPositiveClick: async () => {
        try {
          await userApi.delete(u.id)
          message.success('已删除')
          await loadData()
        } catch (e: any) {
          message.error(e.response?.data?.detail || '操作失败')
        }
      },
    })
  }
}
</script>

<style scoped>
.user-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.user-card {
  background: #212121;
  border-radius: 8px;
  padding: 12px 16px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  transition: border-color 0.15s;
}

.user-card:hover {
  border-color: rgba(255, 255, 255, 0.12);
}

.user-card-pending {
  border-left: 3px solid #f0a020;
}

.user-card-disabled {
  opacity: 0.6;
  border-left: 3px solid #7c6cff;
}

.user-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.user-card-meta {
  margin-top: 4px;
}

.user-card-perms {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.user-card-actions {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
</style>
