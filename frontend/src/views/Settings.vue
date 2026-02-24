<template>
  <div :class="{ 'settings-mobile': isMobile }">
    <n-h3>⚙️ 设置</n-h3>

    <n-tabs :type="isMobile ? 'segment' : 'line'" animated :value="activeTab" @update:value="activeTab = $event" :size="isMobile ? 'small' : 'medium'">
      <n-tab-pane name="ai" tab="🤖 AI 服务">
        <n-tabs
          type="segment"
          animated
          size="small"
          :value="aiSubTab"
          @update:value="aiSubTab = $event"
          style="margin-bottom: 16px"
        >
          <n-tab-pane name="providers" tab="🔌 服务商">
            <AIServiceSettings />
          </n-tab-pane>
          <n-tab-pane name="preferences" tab="⚙️ 偏好">
            <AIPreferences />
          </n-tab-pane>
          <n-tab-pane name="models" tab="📊 模型">
            <ModelSettings />
          </n-tab-pane>
        </n-tabs>
      </n-tab-pane>
      <n-tab-pane name="capabilities" tab="🧩 工作流">
        <n-tabs
          type="segment"
          animated
          size="small"
          :value="capSubTab"
          @update:value="capSubTab = $event"
          style="margin-bottom: 16px"
        >
          <n-tab-pane name="roles" tab="🎭 角色">
            <RoleSettings />
          </n-tab-pane>
          <n-tab-pane name="skills" tab="⚡ 技能">
            <SkillSettings />
          </n-tab-pane>
          <n-tab-pane name="tools" tab="🛠️ 工具">
            <ToolSettings />
          </n-tab-pane>
          <n-tab-pane name="workflows" tab="📋 编排">
            <WorkflowSettings />
          </n-tab-pane>
        </n-tabs>
      </n-tab-pane>
      <n-tab-pane name="system" tab="🖥️ 系统">
        <SystemSettings />
      </n-tab-pane>
      <n-tab-pane v-if="authStore.isAdmin" name="users" tab="👥 用户">
        <UserManagement />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import AIServiceSettings from './settings/AIServiceSettings.vue'
import AIPreferences from './settings/AIPreferences.vue'
import ModelSettings from './settings/ModelSettings.vue'
import SystemSettings from './settings/SystemSettings.vue'
import RoleSettings from './settings/RoleSettings.vue'
import SkillSettings from './settings/SkillSettings.vue'
import ToolSettings from './settings/ToolSettings.vue'
import WorkflowSettings from './settings/WorkflowSettings.vue'
import UserManagement from './settings/UserManagement.vue'

const authStore = useAuthStore()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

// 持久化 tab 状态: 导航切换后保留上次离开的子页面
function usePersistedTab(key: string, defaultVal: string) {
  const stored = sessionStorage.getItem(key)
  const r = ref(stored || defaultVal)
  watch(r, (v) => sessionStorage.setItem(key, v))
  return r
}

const activeTab = usePersistedTab('settings_tab', 'ai')
const aiSubTab = usePersistedTab('settings_ai_sub', 'providers')
const capSubTab = usePersistedTab('settings_cap_sub', 'tools')
</script>

<style>
.cap-row-override td {
  background: rgba(64, 152, 252, 0.08) !important;
}

.settings-mobile .n-h3 {
  font-size: 16px !important;
  margin-bottom: 8px !important;
}

.settings-mobile .n-tabs .n-tab-pane {
  padding: 8px 0 !important;
}
</style>
