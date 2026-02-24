import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { roleApi } from '@/api'

export interface RoleStage {
  key: string
  label: string
  status: string
}

export interface RoleUiLabels {
  project_noun?: string
  create_title?: string
  create_placeholder?: string
  description_placeholder?: string
  output_noun?: string
  output_tab_label?: string
  finalize_action?: string
  [key: string]: string | undefined
}

export interface Role {
  id: number
  name: string
  icon: string
  description: string
  is_builtin: boolean
  is_enabled: boolean
  role_prompt: string
  strategy_prompt: string
  tool_strategy_prompt: string
  finalization_prompt: string
  output_generation_prompt: string
  stages: RoleStage[]
  ui_labels: RoleUiLabels
  default_skills: string[]
  sort_order: number
  created_at: string
  updated_at: string
}

export const useRoleStore = defineStore('role', () => {
  const roles = ref<Role[]>([])
  const loading = ref(false)

  const enabledRoles = computed(() => roles.value.filter(s => s.is_enabled))

  async function fetchRoles(enabledOnly = false) {
    loading.value = true
    try {
      const { data } = await roleApi.list({ enabled_only: enabledOnly })
      roles.value = data
    } finally {
      loading.value = false
    }
  }

  async function getRole(id: number): Promise<Role> {
    const { data } = await roleApi.get(id)
    return data
  }

  async function createRole(payload: Partial<Role>): Promise<Role> {
    const { data } = await roleApi.create(payload)
    roles.value.push(data)
    return data
  }

  async function updateRole(id: number, payload: Partial<Role>): Promise<Role> {
    const { data } = await roleApi.update(id, payload)
    const idx = roles.value.findIndex(s => s.id === id)
    if (idx >= 0) roles.value[idx] = data
    return data
  }

  async function deleteRole(id: number) {
    await roleApi.delete(id)
    roles.value = roles.value.filter(s => s.id !== id)
  }

  async function duplicateRole(id: number): Promise<Role> {
    const { data } = await roleApi.duplicate(id)
    roles.value.push(data)
    return data
  }

  function getRoleById(id: number | null | undefined): Role | undefined {
    if (!id) return undefined
    return roles.value.find(s => s.id === id)
  }

  return {
    roles, loading, enabledRoles,
    fetchRoles, getRole, createRole, updateRole, deleteRole, duplicateRole,
    getRoleById,
  }
})
