<template>
  <n-space vertical :size="16">

    <!-- 批量操作 + 过滤 -->
    <n-space justify="space-between" align="center" :wrap="false">
      <n-space :size="8" align="center">
        <n-dropdown
          trigger="click"
          :options="batchOptions"
          @select="handleBatchAction"
        >
          <n-button size="small" secondary>
            ⚡ 批量操作
            <template #icon><n-icon><CaretDown /></n-icon></template>
          </n-button>
        </n-dropdown>
        <n-text depth="3" style="font-size: 11px">共 {{ projects.length }} 个项目</n-text>
      </n-space>
      <n-space :size="8" align="center">
        <n-switch v-model:value="showArchived" size="small" />
        <n-text depth="3" style="font-size: 12px">显示已归档</n-text>
      </n-space>
    </n-space>

    <!-- 权限矩阵 -->
    <n-spin :show="loading">
      <div v-if="projects.length === 0 && !loading" style="text-align: center; padding: 40px 0">
        <n-empty description="暂无项目" />
      </div>
      <div v-else class="perm-grid">
        <!-- 表头 -->
        <div class="perm-grid-header">
          <div class="perm-col-project">项目</div>
          <div
            v-for="perm in PERM_DEFS"
            :key="perm.key"
            class="perm-col-toggle"
            :title="perm.tip"
          >
            <span class="perm-header-icon">{{ perm.icon }}</span>
            <span class="perm-header-label">{{ perm.shortLabel }}</span>
          </div>
          <div class="perm-col-auto" title="写入命令自动批准：开启后跳过逐次确认">
            <span class="perm-header-icon">🔓</span>
            <span class="perm-header-label">自动批准</span>
          </div>
        </div>

        <!-- 行 -->
        <div
          v-for="proj in projects"
          :key="proj.id"
          class="perm-grid-row"
          :class="{ 'perm-row-archived': proj.is_archived }"
        >
          <!-- 项目名 -->
          <div class="perm-col-project">
            <n-ellipsis :line-clamp="1" :tooltip="{ width: 260 }" style="max-width: 180px">
              <span v-if="proj.type_info?.icon" style="margin-right: 4px">{{ proj.type_info.icon }}</span>
              <span :style="proj.is_archived ? 'opacity: 0.5' : ''">{{ proj.title }}</span>
            </n-ellipsis>
            <n-tag v-if="proj.is_archived" size="tiny" :bordered="false" type="default" round style="margin-left: 4px">归档</n-tag>
          </div>

          <!-- 工具权限开关 -->
          <div v-for="perm in PERM_DEFS" :key="perm.key" class="perm-col-toggle">
            <n-switch
              :value="hasPerm(proj, perm.key)"
              size="small"
              :disabled="saving[proj.id]"
              :rail-style="perm.key === 'execute_command' ? dangerRailStyle : undefined"
              @update:value="(val: boolean) => togglePerm(proj, perm.key, val)"
            />
          </div>

          <!-- 自动批准 -->
          <div class="perm-col-auto">
            <template v-if="hasPerm(proj, 'execute_command')">
              <n-switch
                :value="hasPerm(proj, 'auto_approve_commands')"
                size="small"
                :disabled="saving[proj.id]"
                :rail-style="dangerRailStyle"
                @update:value="(val: boolean) => togglePerm(proj, 'auto_approve_commands', val)"
              />
            </template>
            <n-text v-else depth="3" style="font-size: 11px">—</n-text>
          </div>
        </div>
      </div>
    </n-spin>

    <!-- 权限说明 -->
    <n-collapse :default-expanded-names="[]">
      <n-collapse-item title="📖 权限说明" name="help">
        <n-descriptions :column="1" label-placement="left" bordered size="small">
          <n-descriptions-item v-for="perm in PERM_DEFS" :key="perm.key" :label="perm.icon + ' ' + perm.label">
            {{ perm.tip }}
          </n-descriptions-item>
          <n-descriptions-item label="🔓 自动批准">
            开启「写入命令」后，每次执行仍需用户确认。如启用「自动批准」则跳过确认，AI 可直接执行任意命令，请谨慎开启。
          </n-descriptions-item>
        </n-descriptions>
      </n-collapse-item>
    </n-collapse>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import { useMessage } from 'naive-ui'
import { CaretDown } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { projectApi, toolApi } from '@/api'
import type { Project } from '@/stores/project'

// ---- constants ----
// 从 API 加载权限定义，fallback 到硬编码
const FALLBACK_PERM_DEFS = [
  { key: 'ask_user',      icon: '❓', label: '主动提问', shortLabel: '提问', tip: 'AI 遇到不明确需求时可主动向用户提问澄清', is_meta: false },
  { key: 'read_source',   icon: '📖', label: '读取源码', shortLabel: '源码', tip: '允许 AI 读取项目源代码文件内容', is_meta: false },
  { key: 'read_config',   icon: '📄', label: '读取配置', shortLabel: '配置', tip: '允许 AI 读取 package.json、Dockerfile 等配置文件', is_meta: false },
  { key: 'search',        icon: '🔍', label: '搜索代码', shortLabel: '搜索', tip: '允许 AI 在项目中进行全文搜索', is_meta: false },
  { key: 'tree',          icon: '🌳', label: '浏览目录', shortLabel: '目录', tip: '允许 AI 浏览项目的目录结构', is_meta: false },
  { key: 'execute_readonly_command', icon: '🖥️', label: '只读命令', shortLabel: '只读命令', tip: '允许 AI 执行只读命令（如 git log、ls 等）', is_meta: false },
  { key: 'execute_command', icon: '⚠️', label: '写入命令', shortLabel: '写入命令', tip: '允许 AI 执行任意 Shell 命令，每次仍需审批确认', is_meta: false },
]

const ALL_DEFAULT_PERMS = ['ask_user', 'read_source', 'read_config', 'search', 'tree', 'execute_readonly_command']

// 动态权限 (从 API 加载)
const apiPermissions = ref<typeof FALLBACK_PERM_DEFS>([])
const PERM_DEFS = computed(() => {
  const loaded = apiPermissions.value.filter(p => !p.is_meta)
  return loaded.length > 0 ? loaded : FALLBACK_PERM_DEFS
})
// 找出 meta 权限 (如 auto_approve_commands)
const META_PERMS = computed(() => apiPermissions.value.filter(p => p.is_meta))

// ---- state ----
const message = useMessage()
const loading = ref(false)
const showArchived = ref(false)
const allProjects = ref<Project[]>([])
const saving = ref<Record<number, boolean>>({})

const projects = computed(() =>
  showArchived.value
    ? allProjects.value
    : allProjects.value.filter(p => !p.is_archived)
)

// ---- data loading ----
async function loadProjects() {
  loading.value = true
  try {
    const { data } = await projectApi.list({ include_archived: true, page_size: 200 })
    allProjects.value = data
  } catch (e: any) {
    message.error('加载项目列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadPermissions() {
  try {
    const { data } = await toolApi.permissions()
    apiPermissions.value = data.map((p: any) => ({
      ...p,
      shortLabel: p.label,  // API 返回 label, shortLabel 共用
    }))
  } catch {
    // fallback to hardcoded
  }
}

onMounted(() => {
  loadProjects()
  loadPermissions()
})

// ---- permission helpers ----
function getPerms(proj: Project): string[] {
  return proj.tool_permissions ?? ALL_DEFAULT_PERMS
}

function hasPerm(proj: Project, key: string): boolean {
  return getPerms(proj).includes(key)
}

async function setPerms(proj: Project, newPerms: string[]) {
  saving.value[proj.id] = true
  try {
    const { data } = await projectApi.update(proj.id, { tool_permissions: newPerms })
    // update local state
    const idx = allProjects.value.findIndex(p => p.id === proj.id)
    if (idx >= 0) allProjects.value[idx] = { ...allProjects.value[idx], tool_permissions: data.tool_permissions }
  } catch (e: any) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value[proj.id] = false
  }
}

function togglePerm(proj: Project, key: string, enabled: boolean) {
  const cur = [...getPerms(proj)]
  let next: string[]
  if (enabled) {
    next = cur.includes(key) ? cur : [...cur, key]
  } else {
    next = cur.filter(k => k !== key)
    // turning off execute_command also removes auto_approve_commands
    if (key === 'execute_command') {
      next = next.filter(k => k !== 'auto_approve_commands')
    }
  }
  setPerms(proj, next)
}

// ---- danger rail style ----
function dangerRailStyle({ focused, checked }: { focused: boolean; checked: boolean }) {
  if (checked) {
    return { background: '#7c6cff', boxShadow: focused ? '0 0 0 2px #7c6cff40' : undefined }
  }
  return {}
}

// ---- batch actions ----
const batchOptions = [
  { label: '✅ 全部恢复默认权限', key: 'reset-default' },
  { label: '📖 全部开启所有只读', key: 'all-readonly' },
  { label: '⚠️ 全部开启写入命令', key: 'all-exec' },
  { label: '🚫 全部关闭写入命令', key: 'no-exec' },
  { label: '🔒 全部关闭自动批准', key: 'no-auto' },
]

async function handleBatchAction(key: string) {
  const targets = projects.value
  if (targets.length === 0) return

  const confirm = window.confirm(`确认对 ${targets.length} 个项目执行批量操作？`)
  if (!confirm) return

  loading.value = true
  try {
    for (const proj of targets) {
      const cur = [...getPerms(proj)]
      let next: string[]
      switch (key) {
        case 'reset-default':
          next = [...ALL_DEFAULT_PERMS]
          break
        case 'all-readonly':
          next = [...new Set([...cur, ...ALL_DEFAULT_PERMS])]
          break
        case 'all-exec':
          next = [...new Set([...cur, 'execute_command'])]
          break
        case 'no-exec':
          next = cur.filter(k => k !== 'execute_command' && k !== 'auto_approve_commands')
          break
        case 'no-auto':
          next = cur.filter(k => k !== 'auto_approve_commands')
          break
        default:
          continue
      }
      await projectApi.update(proj.id, { tool_permissions: next })
    }
    message.success('批量操作完成')
    await loadProjects()
  } catch (e: any) {
    message.error('批量操作失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.perm-grid {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  overflow: hidden;
  background: #212121;
}

.perm-grid-header {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  gap: 4px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
  user-select: none;
}

.perm-grid-row {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  gap: 4px;
  transition: background 0.15s;
}

.perm-grid-row:hover {
  background: rgba(255, 255, 255, 0.03);
}

.perm-grid-row:not(:last-child) {
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.perm-row-archived {
  opacity: 0.6;
}

.perm-col-project {
  flex: 0 0 200px;
  display: flex;
  align-items: center;
  min-width: 0;
  font-size: 13px;
}

.perm-col-toggle {
  flex: 1 1 0;
  display: flex;
  justify-content: center;
  align-items: center;
  min-width: 56px;
}

.perm-col-auto {
  flex: 0 0 72px;
  display: flex;
  justify-content: center;
  align-items: center;
}

.perm-header-icon {
  font-size: 13px;
  line-height: 1;
}

.perm-header-label {
  font-size: 11px;
  margin-left: 2px;
  white-space: nowrap;
}

/* responsive: hide short labels on very narrow screens */
@media (max-width: 900px) {
  .perm-col-project {
    flex: 0 0 140px;
  }
  .perm-header-label {
    display: none;
  }
}

@media (max-width: 768px) {
  .perm-col-project {
    flex: 0 0 100px;
  }
  .perm-col-toggle {
    min-width: 40px;
  }
  .perm-col-auto {
    flex: 0 0 48px;
  }
  .perm-grid-header,
  .perm-grid-row {
    padding: 6px 8px;
    gap: 2px;
  }
}
</style>
