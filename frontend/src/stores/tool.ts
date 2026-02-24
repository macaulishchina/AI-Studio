import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { toolApi } from '@/api'

export interface ToolDef {
  id: number
  name: string
  display_name: string
  icon: string
  description: string
  permission_key: string
  is_builtin: boolean
  is_enabled: boolean
  function_def: Record<string, any>
  executor_type: string
  executor_config: Record<string, any>
  sort_order: number
  created_at: string
  updated_at: string
}

export interface PermissionInfo {
  key: string
  label: string
  icon: string
  tip: string
  is_meta: boolean
  parent?: string  // 父权限 key，用于嵌套展示
}

export const useToolStore = defineStore('tool', () => {
  const tools = ref<ToolDef[]>([])
  const permissions = ref<PermissionInfo[]>([])
  const loading = ref(false)

  const enabledTools = computed(() => tools.value.filter(t => t.is_enabled))

  async function fetchTools(enabledOnly = false) {
    loading.value = true
    try {
      const { data } = await toolApi.list({ enabled_only: enabledOnly })
      tools.value = data
    } finally {
      loading.value = false
    }
  }

  async function fetchPermissions() {
    const { data } = await toolApi.permissions()
    permissions.value = data
  }

  async function createTool(payload: Partial<ToolDef>): Promise<ToolDef> {
    const { data } = await toolApi.create(payload)
    tools.value.push(data)
    return data
  }

  async function updateTool(id: number, payload: Partial<ToolDef>): Promise<ToolDef> {
    const { data } = await toolApi.update(id, payload)
    const idx = tools.value.findIndex(t => t.id === id)
    if (idx >= 0) tools.value[idx] = data
    return data
  }

  async function deleteTool(id: number) {
    await toolApi.delete(id)
    tools.value = tools.value.filter(t => t.id !== id)
  }

  async function duplicateTool(id: number): Promise<ToolDef> {
    const { data } = await toolApi.duplicate(id)
    tools.value.push(data)
    return data
  }

  return {
    tools, permissions, loading, enabledTools,
    fetchTools, fetchPermissions, createTool, updateTool, deleteTool, duplicateTool,
  }
})
