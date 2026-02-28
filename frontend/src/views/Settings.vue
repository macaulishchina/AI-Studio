<template>
  <div class="settings-page" :class="{ 'settings-mobile': isMobile }">
    <n-space justify="space-between" align="center" style="margin-bottom: 12px">
      <n-h3 style="margin: 0">⚙️ 设置</n-h3>
      <n-tag size="small" :bordered="false" type="info">{{ activeSectionMeta?.groupLabel }} / {{ activeSectionMeta?.label }}</n-tag>
    </n-space>

    <div v-if="isMobile" class="settings-mobile-nav">
      <n-space vertical :size="10">
        <n-select
          :value="activeGroup"
          :options="mobileGroupOptions"
          @update:value="onGroupChange"
          size="small"
        />
        <n-tabs
          type="segment"
          size="small"
          animated
          :value="activeSection"
          @update:value="activeSection = $event"
        >
          <n-tab-pane
            v-for="item in activeGroupSections"
            :key="item.key"
            :name="item.key"
            :tab="item.shortLabel"
          />
        </n-tabs>
      </n-space>
    </div>

    <div class="settings-layout">
      <aside v-if="!isMobile" class="settings-sidebar">
        <n-input
          v-model:value="searchKeyword"
          clearable
          size="small"
          placeholder="搜索设置项，如 项目 / AI / MCP / 用户"
        />
        <n-menu
          style="margin-top: 10px"
          :options="filteredMenuOptions"
          :value="activeSection"
          @update:value="activeSection = $event"
          :collapsed-width="220"
        />
      </aside>

      <main class="settings-content settings-panel">
        <component :is="activeSectionMeta?.component" />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AIServiceSettings from './settings/AIServiceSettings.vue'
import AIPreferences from './settings/AIPreferences.vue'
import ModelSettings from './settings/ModelSettings.vue'
import SystemSettings from './settings/SystemSettings.vue'
import RoleSettings from './settings/RoleSettings.vue'
import SkillSettings from './settings/SkillSettings.vue'
import ToolSettings from './settings/ToolSettings.vue'
import WorkflowSettings from './settings/WorkflowSettings.vue'
import MCPSettings from './settings/MCPSettings.vue'
import ProjectPermissions from './settings/ProjectPermissions.vue'
import UserManagement from './settings/UserManagement.vue'

const props = defineProps<{ tab?: string }>()
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
const searchKeyword = ref('')

type SettingsSection = {
  key: string
  label: string
  shortLabel: string
  group: 'project' | 'ai' | 'workflow' | 'system'
  groupLabel: string
  aliases?: string[]
  adminOnly?: boolean
  component: any
}

const allSections: SettingsSection[] = [
  { key: 'project-permissions', label: '🔐 项目权限矩阵', shortLabel: '权限矩阵', group: 'project', groupLabel: '项目管理', aliases: ['projects'], component: ProjectPermissions },
  { key: 'system', label: '📁 本地工作区', shortLabel: '工作区', group: 'project', groupLabel: '项目管理', aliases: ['system'], component: SystemSettings },

  { key: 'providers', label: '🔌 服务提供商', shortLabel: '提供商', group: 'ai', groupLabel: 'AI服务', aliases: ['ai'], component: AIServiceSettings },
  { key: 'preferences', label: '⚙️ 推理偏好', shortLabel: '推理偏好', group: 'ai', groupLabel: 'AI服务', component: AIPreferences },
  { key: 'models', label: '📊 模型配置', shortLabel: '模型配置', group: 'ai', groupLabel: 'AI服务', component: ModelSettings },

  { key: 'workflows', label: '📋 工作流编排', shortLabel: '工作流', group: 'workflow', groupLabel: '工作流', component: WorkflowSettings },
  { key: 'roles', label: '🎭 角色管理', shortLabel: '角色', group: 'workflow', groupLabel: '工作流', component: RoleSettings },
  { key: 'skills', label: '⚡ 技能管理', shortLabel: '技能', group: 'workflow', groupLabel: '工作流', component: SkillSettings },
  { key: 'tools', label: '🛠️ 工具管理', shortLabel: '工具', group: 'workflow', groupLabel: '工作流', aliases: ['capabilities'], component: ToolSettings },
  { key: 'mcp', label: '🔌 MCP 集成', shortLabel: 'MCP', group: 'workflow', groupLabel: '工作流', aliases: ['mcp'], component: MCPSettings },

  { key: 'users', label: '👥 用户与权限', shortLabel: '用户权限', group: 'system', groupLabel: '系统管理', aliases: ['users'], adminOnly: true, component: UserManagement },
]

const groupDefs = [
  { key: 'project', label: '📁 项目管理' },
  { key: 'ai', label: '🤖 AI服务' },
  { key: 'workflow', label: '🛠️ 工作流' },
  { key: 'system', label: '🔐 系统管理' },
] as const

const visibleSections = computed(() =>
  allSections.filter((item) => !item.adminOnly || authStore.isAdmin)
)

const sectionMap = computed(() => {
  const map = new Map<string, SettingsSection>()
  for (const item of visibleSections.value) {
    map.set(item.key, item)
    item.aliases?.forEach((alias) => map.set(alias, item))
  }
  return map
})

function resolveInitialSection() {
  const querySection = typeof route.query.section === 'string' ? route.query.section : ''
  if (querySection && sectionMap.value.get(querySection)) {
    return sectionMap.value.get(querySection)!.key
  }

  if (props.tab && sectionMap.value.get(props.tab)) {
    return sectionMap.value.get(props.tab)!.key
  }

  const persisted = sessionStorage.getItem('settings_section')
  if (persisted && sectionMap.value.get(persisted)) {
    return sectionMap.value.get(persisted)!.key
  }

  return visibleSections.value[0]?.key || 'providers'
}

const activeSection = ref(resolveInitialSection())

const activeSectionMeta = computed(
  () => visibleSections.value.find((item) => item.key === activeSection.value) || visibleSections.value[0]
)

const groupedSections = computed(() => {
  const groups: Record<string, SettingsSection[]> = {
    project: [],
    ai: [],
    workflow: [],
    system: [],
  }
  visibleSections.value.forEach((item) => {
    groups[item.group].push(item)
  })
  return groups
})

const activeGroup = computed(() => activeSectionMeta.value?.group || 'ai')
const activeGroupSections = computed(() => groupedSections.value[activeGroup.value] || [])

const mobileGroupOptions = computed(() => {
  return groupDefs
    .filter((group) => (groupedSections.value[group.key] || []).length > 0)
    .map((group) => ({ label: group.label, value: group.key }))
})

function onGroupChange(groupKey: string) {
  const target = groupedSections.value[groupKey]?.[0]
  if (target) activeSection.value = target.key
}

const filteredMenuOptions = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  return groupDefs
    .map((group) => {
      const children = (groupedSections.value[group.key] || [])
        .filter((item) => !keyword || item.label.toLowerCase().includes(keyword) || item.shortLabel.toLowerCase().includes(keyword))
        .map((item) => ({ label: item.label, key: item.key }))

      if (!children.length) return null
      return {
        type: 'group',
        label: group.label,
        key: `group-${group.key}`,
        children,
      }
    })
    .filter(Boolean) as any[]
})

watch(activeSection, (val) => {
  if (!sectionMap.value.get(val)) {
    activeSection.value = visibleSections.value[0]?.key || 'providers'
    return
  }

  sessionStorage.setItem('settings_section', val)
  const currentQuery = typeof route.query.section === 'string' ? route.query.section : ''
  if (currentQuery !== val) {
    router.replace({ query: { ...route.query, section: val } })
  }
})

watch(
  () => authStore.isAdmin,
  () => {
    if (!sectionMap.value.get(activeSection.value)) {
      activeSection.value = visibleSections.value[0]?.key || 'providers'
    }
  }
)

watch(
  () => props.tab,
  (tab) => {
    if (!tab) return
    const mapped = sectionMap.value.get(tab)
    if (mapped) activeSection.value = mapped.key
  }
)

onMounted(() => {
  window.addEventListener('resize', onResize)
  const mapped = resolveInitialSection()
  if (mapped) activeSection.value = mapped
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<style>
.cap-row-override td {
  background: rgba(64, 152, 252, 0.08) !important;
}

.settings-page {
  min-width: 0;
}

.settings-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 14px;
  min-width: 0;
}

.settings-sidebar {
  position: sticky;
  top: 12px;
  align-self: start;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 8px;
  padding: 10px;
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.settings-content {
  min-width: 0;
}

.settings-mobile .settings-layout {
  display: block;
}

.settings-mobile .n-h3 {
  font-size: 16px !important;
  margin-bottom: 8px !important;
}

.settings-mobile .settings-mobile-nav {
  margin-bottom: 10px;
}

.settings-mobile .settings-panel {
  min-width: 0;
}

.settings-mobile :deep(.n-card),
.settings-mobile :deep(.n-space),
.settings-mobile :deep(.n-grid),
.settings-mobile :deep(.n-tabs) {
  min-width: 0;
}

.settings-mobile :deep(.n-data-table-wrapper),
.settings-mobile :deep(.n-scrollbar-content) {
  overflow-x: auto;
}

.settings-mobile :deep(.n-input-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.settings-mobile :deep(.n-input-group > .n-input-group-label) {
  width: 100% !important;
}

.settings-mobile :deep(.n-input-group > .n-input),
.settings-mobile :deep(.n-input-group > .n-button),
.settings-mobile :deep(.n-input-group > .n-base-selection) {
  width: 100% !important;
  flex: 1 1 100% !important;
}

@media (max-width: 1024px) {
  .settings-layout {
    grid-template-columns: 200px minmax(0, 1fr);
  }
}
</style>
