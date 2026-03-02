<template>
  <!-- === 桌面端: 侧边栏布局 === -->
  <n-layout v-if="!isMobile" has-sider style="height: 100vh">
    <n-layout-sider
      bordered
      :collapsed="collapsed"
      collapse-mode="width"
      :collapsed-width="64"
      :width="220"
      show-trigger
      @collapse="collapsed = true"
      @expand="collapsed = false"
      :native-scrollbar="false"
      style="background: #171717"
    >
      <div style="padding: 16px; text-align: center">
        <n-text style="font-size: 20px; color: #7c6cff" strong>
          {{ collapsed ? '🐕' : '🐕 Dogi' }}
        </n-text>
      </div>

      <n-menu
        :collapsed="collapsed"
        :collapsed-width="64"
        :collapsed-icon-size="22"
        :options="menuOptions"
        :value="activeKey"
        @update:value="handleMenuClick"
        :root-indent="20"
      />

      <div v-if="!collapsed" style="position: absolute; bottom: 16px; left: 16px; right: 16px">
        <n-text depth="3" style="font-size: 12px">
          Dogi v2.0
        </n-text>
      </div>
    </n-layout-sider>

    <n-layout>
      <n-layout-header bordered style="height: 56px; padding: 0 24px; display: flex; align-items: center; justify-content: space-between; background: #171717">
        <n-breadcrumb>
          <n-breadcrumb-item @click="$router.push('/')">Dogi</n-breadcrumb-item>
          <n-breadcrumb-item v-if="routeName">{{ routeName }}</n-breadcrumb-item>
        </n-breadcrumb>

        <n-space align="center" :size="12">
          <!-- 工作目录快速切换 -->
          <n-popselect
            v-model:value="activeWorkspaceId"
            :options="workspaceOptions"
            trigger="click"
            @update:value="handleSwitchWorkspace"
            :render-label="renderWsLabel"
          >
            <n-button size="small" quaternary style="max-width: 220px">
              <template #icon><span style="font-size: 12px">📂</span></template>
              <n-ellipsis style="max-width: 160px; font-size: 12px">
                {{ activeWorkspaceLabel }}
              </n-ellipsis>
            </n-button>
          </n-popselect>

          <n-tag :bordered="false" type="success" size="small" round>
            <template #icon><n-icon :component="PulseOutline" /></template>
            运行中
          </n-tag>

          <n-tag v-if="authStore.user" :bordered="false" :type="authStore.isAdmin ? 'warning' : 'info'" size="small" round>
            {{ authStore.user.nickname || authStore.user.username }}
            <template #icon>
              <span style="font-size: 12px">{{ authStore.isAdmin ? '👑' : '👤' }}</span>
            </template>
          </n-tag>

          <n-button quaternary circle size="small" @click="$router.push('/settings')">
            <template #icon><n-icon :component="SettingsOutline" /></template>
          </n-button>
          <n-button quaternary circle size="small" tag="a" href="/" target="_blank">
            <template #icon><n-icon :component="OpenOutline" /></template>
          </n-button>
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button quaternary circle size="small" @click="handleLogout">
                <template #icon><n-icon :component="LogOutOutline" /></template>
              </n-button>
            </template>
            退出登录
          </n-tooltip>
        </n-space>
      </n-layout-header>

      <n-layout-content
        :content-style="route.name === 'ChatHome' || route.name === 'ChatView' ? 'padding: 0; height: 100%' : 'padding: 24px; min-height: 100%'"
        :native-scrollbar="false"
        style="background: #1a1a1a; display: flex; flex-direction: column;"
      >
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>

  <!-- === 移动端: 顶栏 + 底部导航 === -->
  <n-layout v-else style="height: 100vh; height: 100dvh">
    <!-- 移动端顶栏 -->
    <n-layout-header bordered class="mobile-header">
      <div class="mobile-header-left">
        <n-text style="font-size: 16px; color: #7c6cff; white-space: nowrap" strong>🐕 Dogi</n-text>
      </div>
      <div class="mobile-header-right">
        <n-tag v-if="authStore.user" :bordered="false" :type="authStore.isAdmin ? 'warning' : 'info'" size="small" round>
          <template #icon>
            <span style="font-size: 10px">{{ authStore.isAdmin ? '👑' : '👤' }}</span>
          </template>
          {{ authStore.user.nickname || authStore.user.username }}
        </n-tag>
        <n-button quaternary circle size="small" @click="handleLogout">
          <template #icon><n-icon :component="LogOutOutline" /></template>
        </n-button>
      </div>
    </n-layout-header>

    <!-- 移动端内容区 -->
    <n-layout-content
      content-style="padding: 12px"
      :native-scrollbar="false"
      style="background: #1a1a1a; flex: 1; overflow: auto"
      class="mobile-content"
    >
      <router-view />
    </n-layout-content>

    <!-- 移动端底部导航栏 -->
    <div class="mobile-tabbar">
      <div
        v-for="tab in mobileTabItems"
        :key="tab.key"
        class="mobile-tab-item"
        :class="{ 'mobile-tab-active': activeKey === tab.key }"
        @click="handleMenuClick(tab.key)"
      >
        <n-icon :component="tab.icon" :size="20" />
        <span class="mobile-tab-label">{{ tab.label }}</span>
      </div>
    </div>
  </n-layout>
</template>

<script setup lang="ts">
import { ref, computed, h, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon, useMessage } from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { workspaceDirApi } from '@/api'
import {
  ChatbubblesOutline,
  HomeOutline,
  DocumentTextOutline,
  CameraOutline,
  SettingsOutline,
  PulseOutline,
  OpenOutline,
  LogOutOutline,
} from '@vicons/ionicons5'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const collapsed = ref(false)
const wsMessage = useMessage()

// ── 工作目录切换 ──────────────────────────────────────────
const workspaceDirList = ref<any[]>([])
const activeWorkspaceId = ref<number | null>(null)

const workspaceOptions = computed(() =>
  workspaceDirList.value.map(d => ({
    label: d.label || d.path.split(/[\\/]/).pop() || d.path,
    value: d.id,
    path: d.path,
    vcs: d.vcs_type,
    active: d.is_active,
  }))
)

const activeWorkspaceLabel = computed(() => {
  const active = workspaceDirList.value.find(d => d.is_active)
  if (active) return active.label || active.path.split(/[\\/]/).pop() || active.path
  return '选择工作目录'
})

function renderWsLabel(option: any) {
  const vcsIcon = option.vcs === 'git' ? '🔀' : option.vcs === 'svn' ? '🔶' : '📁'
  return h('div', { style: 'display: flex; flex-direction: column; gap: 2px; padding: 2px 0' }, [
    h('div', { style: 'display: flex; align-items: center; gap: 6px; font-size: 12px' }, [
      h('span', null, vcsIcon),
      h('span', { style: option.active ? 'font-weight: bold; color: #63e2b7' : '' }, option.label),
      option.active ? h('span', { style: 'color: #63e2b7; font-size: 10px' }, ' ●') : null,
    ]),
    h('div', { style: 'font-size: 10px; color: rgba(255,255,255,0.35); padding-left: 22px; word-break: break-all' }, option.path),
  ])
}

async function fetchWorkspaceDirs() {
  try {
    const { data } = await workspaceDirApi.list()
    workspaceDirList.value = data
    const active = data.find((d: any) => d.is_active)
    if (active) activeWorkspaceId.value = active.id
  } catch {}
}

async function handleSwitchWorkspace(id: number) {
  if (!id) return
  const dir = workspaceDirList.value.find(d => d.id === id)
  if (!dir || dir.is_active) return
  try {
    await workspaceDirApi.activate(id)
    wsMessage.success(`已切换到: ${dir.label || dir.path}`)
    await fetchWorkspaceDirs()
    // 通知其他组件（如 Dashboard）工作目录已切换
    window.dispatchEvent(new CustomEvent('workspace-switched', { detail: { id, path: dir.path } }))
  } catch (e: any) {
    wsMessage.error(e.response?.data?.detail || '切换失败')
  }
}

// ── 响应式检测 ──────────────────────────────────────────
const MOBILE_BREAKPOINT = 768
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
const isMobile = computed(() => windowWidth.value < MOBILE_BREAKPOINT)

// 监听其他组件(如 Settings)触发的 workspace-switched 事件，刷新头部状态
function _onExternalSwitch() {
  fetchWorkspaceDirs()
}

function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => {
  window.addEventListener('resize', onResize)
  window.addEventListener('workspace-switched', _onExternalSwitch)
  fetchWorkspaceDirs()
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('workspace-switched', _onExternalSwitch)
})

// ── 移动端底部 tab 定义 ──────────────────────────────────
const mobileTabItems = [
  { key: 'chat', label: '对话', icon: ChatbubblesOutline },
  { key: 'projects', label: '项目', icon: DocumentTextOutline },
  { key: 'settings', label: '设置', icon: SettingsOutline },
]

const activeKey = computed(() => {
  const path = route.path
  if (path === '/' || path === '' || path.startsWith('/c/')) return 'chat'
  if (path.includes('/projects/')) return 'projects'
  if (path.includes('/projects')) return 'projects'
  if (path.includes('/snapshots')) return 'snapshots'
  if (path.includes('/settings')) return 'settings'
  if (path.includes('/device-debug')) return 'device-debug'
  return 'chat'
})

const routeName = computed(() => {
  const map: Record<string, string> = {
    ChatHome: '对话',
    ChatView: '对话',
    ProjectList: '项目',
    ProjectDetail: '项目详情',
    Snapshots: '快照管理',
    Settings: '设置',
    DeviceDebug: '设备调试',
  }
  return map[route.name as string] || ''
})

function renderIcon(icon: any) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menuOptions: MenuOption[] = [
  { label: '对话', key: 'chat', icon: renderIcon(ChatbubblesOutline) },
  { label: '项目', key: 'projects', icon: renderIcon(DocumentTextOutline) },
  { label: '快照', key: 'snapshots', icon: renderIcon(CameraOutline) },
  { label: '调试', key: 'device-debug', icon: renderIcon(PulseOutline) },
  { label: '设置', key: 'settings', icon: renderIcon(SettingsOutline) },
]

// 持久化每个菜单区域最后访问的路径
const lastPaths: Record<string, string> = {
  chat: sessionStorage.getItem('nav_chat') || '/',
  projects: sessionStorage.getItem('nav_projects') || '/projects',
  snapshots: sessionStorage.getItem('nav_snapshots') || '/snapshots',
  settings: sessionStorage.getItem('nav_settings') || '/settings',
  'device-debug': sessionStorage.getItem('nav_device-debug') || '/device-debug',
}

// 监听路由变化，记录当前菜单区域的路径
watch(() => route.fullPath, (path) => {
  const key = activeKey.value
  if (key) {
    lastPaths[key] = path
    sessionStorage.setItem(`nav_${key}`, path)
  }
}, { immediate: true })

function handleMenuClick(key: string) {
  router.push(lastPaths[key] || '/')
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
/* ── 移动端顶栏 ── */
.mobile-header {
  height: 48px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #171717;
  flex-shrink: 0;
}
.mobile-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mobile-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* ── 移动端内容区: 减去顶栏 48px 和底栏 56px ── */
.mobile-content {
  height: calc(100vh - 104px);
  height: calc(100dvh - 104px);
}

/* ── 移动端底部导航栏 ── */
.mobile-tabbar {
  display: flex;
  align-items: center;
  justify-content: space-around;
  height: 56px;
  background: #171717;
  border-top: 1px solid rgba(255,255,255,0.08);
  flex-shrink: 0;
  padding-bottom: env(safe-area-inset-bottom, 0);
}
.mobile-tab-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  flex: 1;
  padding: 6px 0;
  cursor: pointer;
  color: rgba(255,255,255,0.4);
  transition: color 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.mobile-tab-item:active {
  opacity: 0.7;
}
.mobile-tab-active {
  color: #7c6cff;
}
.mobile-tab-label {
  font-size: 10px;
  line-height: 1;
}
</style>
