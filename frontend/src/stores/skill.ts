import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { skillApi } from '@/api'

export interface Skill {
  id: number
  name: string
  icon: string
  description: string
  category: string
  is_builtin: boolean
  is_enabled: boolean
  instruction_prompt: string
  output_format: string
  examples: Array<{ input: string; output: string }>
  constraints: string[]
  recommended_tools: string[]
  tags: string[]
  sort_order: number
  created_at: string
  updated_at: string
}

export interface SkillCategory {
  name: string
  icon: string
}

export const useSkillStore = defineStore('skill', () => {
  const skills = ref<Skill[]>([])
  const categories = ref<Record<string, SkillCategory>>({})
  const loading = ref(false)

  const enabledSkills = computed(() => skills.value.filter(s => s.is_enabled))

  async function fetchSkills(enabledOnly = false, category?: string) {
    loading.value = true
    try {
      const { data } = await skillApi.list({ enabled_only: enabledOnly, category })
      skills.value = data
    } finally {
      loading.value = false
    }
  }

  async function fetchCategories() {
    const { data } = await skillApi.categories()
    categories.value = data
  }

  async function getSkill(id: number): Promise<Skill> {
    const { data } = await skillApi.get(id)
    return data
  }

  async function createSkill(payload: Partial<Skill>): Promise<Skill> {
    const { data } = await skillApi.create(payload)
    skills.value.push(data)
    return data
  }

  async function updateSkill(id: number, payload: Partial<Skill>): Promise<Skill> {
    const { data } = await skillApi.update(id, payload)
    const idx = skills.value.findIndex(s => s.id === id)
    if (idx >= 0) skills.value[idx] = data
    return data
  }

  async function deleteSkill(id: number) {
    await skillApi.delete(id)
    skills.value = skills.value.filter(s => s.id !== id)
  }

  async function duplicateSkill(id: number): Promise<Skill> {
    const { data } = await skillApi.duplicate(id)
    skills.value.push(data)
    return data
  }

  function getSkillByName(name: string): Skill | undefined {
    return skills.value.find(s => s.name === name)
  }

  return {
    skills, categories, loading, enabledSkills,
    fetchSkills, fetchCategories, getSkill,
    createSkill, updateSkill, deleteSkill, duplicateSkill,
    getSkillByName,
  }
})
