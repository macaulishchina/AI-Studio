/**
 * 工作流管理 Store — 功能模块 + 工作流 CRUD
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { workflowModuleApi, workflowApi } from '@/api/index'

export interface WorkflowModule {
  id: number
  name: string
  display_name: string
  icon: string
  description: string
  component_key: string
  default_config: Record<string, any>
  is_builtin: boolean
  is_enabled: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

export interface WorkflowModuleItem {
  module_name: string
  tab_key: string
  tab_label: string
  stage_statuses: string[]
  role_name?: string
  config: Record<string, any>
}

export interface StageItem {
  key: string
  label: string
  status: string
  role?: string
}

export interface Workflow {
  id: number
  name: string
  display_name: string
  icon: string
  description: string
  is_builtin: boolean
  is_enabled: boolean
  stages: StageItem[]
  modules: WorkflowModuleItem[]
  ui_labels: Record<string, string>
  sort_order: number
  created_at: string
  updated_at: string
}

export const useWorkflowStore = defineStore('workflow', () => {
  const modules = ref<WorkflowModule[]>([])
  const workflows = ref<Workflow[]>([])
  const loading = ref(false)

  async function fetchModules() {
    const res = await workflowModuleApi.list()
    modules.value = res.data
  }

  async function createModule(data: Partial<WorkflowModule>) {
    const res = await workflowModuleApi.create(data)
    await fetchModules()
    return res.data
  }

  async function updateModule(id: number, data: Partial<WorkflowModule>) {
    const res = await workflowModuleApi.update(id, data)
    await fetchModules()
    return res.data
  }

  async function deleteModule(id: number) {
    await workflowModuleApi.delete(id)
    await fetchModules()
  }

  async function fetchWorkflows() {
    const res = await workflowApi.list()
    workflows.value = res.data
  }

  async function createWorkflow(data: Partial<Workflow>) {
    const res = await workflowApi.create(data)
    await fetchWorkflows()
    return res.data
  }

  async function updateWorkflow(id: number, data: Partial<Workflow>) {
    const res = await workflowApi.update(id, data)
    await fetchWorkflows()
    return res.data
  }

  async function deleteWorkflow(id: number) {
    await workflowApi.delete(id)
    await fetchWorkflows()
  }

  async function duplicateWorkflow(id: number) {
    const res = await workflowApi.duplicate(id)
    await fetchWorkflows()
    return res.data
  }

  async function fetchAll() {
    loading.value = true
    try {
      await Promise.all([fetchModules(), fetchWorkflows()])
    } finally {
      loading.value = false
    }
  }

  return {
    modules, workflows, loading,
    fetchModules, createModule, updateModule, deleteModule,
    fetchWorkflows, createWorkflow, updateWorkflow, deleteWorkflow, duplicateWorkflow,
    fetchAll,
  }
})
