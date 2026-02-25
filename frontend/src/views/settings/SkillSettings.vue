<template>
  <div>
    <!-- 顶部操作栏 -->
    <n-space justify="space-between" align="center" style="margin-bottom: 16px">
      <n-text depth="3">管理 AI 技能 — 可复用的能力模块，定义 AI 在特定任务中的方法论和输出格式</n-text>
      <n-button type="primary" size="small" @click="openCreate">
        <template #icon><n-icon :component="AddOutline" /></template>
        创建新技能
      </n-button>
    </n-space>

    <!-- 分类过滤 -->
    <n-space :size="8" style="margin-bottom: 12px">
      <n-tag
        v-for="(cat, key) in allCategories"
        :key="key"
        :type="activeCategory === key ? 'primary' : 'default'"
        :bordered="activeCategory === key"
        round
        checkable
        :checked="activeCategory === key"
        @update:checked="activeCategory = activeCategory === key ? '' : (key as string)"
        style="cursor: pointer"
      >
        {{ cat.icon }} {{ cat.name }}
      </n-tag>
    </n-space>

    <!-- 技能卡片列表 -->
    <n-spin :show="store.loading">
      <n-grid :cols="1" :y-gap="12" v-if="filteredSkills.length">
        <n-gi v-for="skill in filteredSkills" :key="skill.id">
          <n-card size="small" style="background: #1a1a2e" hoverable>
            <n-space justify="space-between" align="center">
              <n-space align="center" :size="12">
                <span style="font-size: 24px">{{ skill.icon }}</span>
                <div>
                  <n-space align="center" :size="6">
                    <n-text strong>{{ skill.name }}</n-text>
                    <n-tag v-if="skill.is_builtin" size="tiny" type="info" round>内置</n-tag>
                    <n-tag v-if="!skill.is_enabled" size="tiny" type="warning" round>已禁用</n-tag>
                    <n-tag size="tiny" :bordered="false" type="success">
                      {{ getCategoryLabel(skill.category) }}
                    </n-tag>
                  </n-space>
                  <n-text depth="3" style="font-size: 12px; display: block; margin-top: 2px">
                    {{ skill.description }}
                  </n-text>
                </div>
              </n-space>
              <n-space :size="8">
                <n-switch
                  :value="skill.is_enabled"
                  size="small"
                  @update:value="toggleEnabled(skill, $event)"
                />
                <n-button v-if="!skill.is_builtin" size="tiny" quaternary @click="openEdit(skill)">
                  <template #icon><n-icon :component="CreateOutline" /></template>
                </n-button>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="tiny" quaternary disabled>
                      <template #icon><n-icon :component="CreateOutline" /></template>
                    </n-button>
                  </template>
                  内置技能不可编辑
                </n-tooltip>
                <n-button size="tiny" quaternary @click="handleDuplicate(skill)">
                  <template #icon><n-icon :component="CopyOutline" /></template>
                </n-button>
                <n-popconfirm
                  v-if="!skill.is_builtin"
                  @positive-click="handleDelete(skill)"
                >
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  确定删除技能「{{ skill.name }}」？
                </n-popconfirm>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="tiny" quaternary disabled>
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  内置技能不可删除
                </n-tooltip>
              </n-space>
            </n-space>
            <!-- 标签 + 推荐工具预览 -->
            <n-space :size="4" style="margin-top: 8px">
              <n-tag
                v-for="tag in skill.tags"
                :key="tag"
                size="tiny"
                :bordered="false"
              >
                {{ tag }}
              </n-tag>
              <n-tag
                v-for="tool in skill.recommended_tools"
                :key="tool"
                size="tiny"
                :bordered="false"
                type="info"
              >
                🔧 {{ tool }}
              </n-tag>
            </n-space>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else description="暂无技能配置" />
    </n-spin>

    <!-- 编辑 / 创建弹窗 -->
    <n-modal
      v-model:show="showEditor"
      preset="card"
      :title="editingSkill ? `编辑技能 — ${editingSkill.name}` : '创建新技能'"
      style="width: 800px; max-width: 95vw"
      :mask-closable="false"
    >
      <n-tabs type="line" animated :value="editorTab" @update:value="editorTab = $event">
        <!-- 基本信息 -->
        <n-tab-pane name="basic" tab="基本信息">
          <n-form :model="form" label-placement="left" label-width="100">
            <n-form-item label="技能名称">
              <n-input v-model:value="form.name" placeholder="如：需求澄清、代码审查" />
            </n-form-item>
            <n-form-item label="图标">
              <n-input v-model:value="form.icon" placeholder="Emoji 图标" style="width: 80px" />
            </n-form-item>
            <n-form-item label="分类">
              <n-select
                v-model:value="form.category"
                :options="categoryOptions"
                placeholder="选择分类"
                style="width: 200px"
              />
            </n-form-item>
            <n-form-item label="描述">
              <n-input v-model:value="form.description" placeholder="简短描述技能用途" />
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 核心指令 -->
        <n-tab-pane name="instruction" tab="核心指令">
          <n-form label-placement="top">
            <n-form-item label="指令内容 (instruction_prompt)">
              <n-input
                v-model:value="form.instruction_prompt"
                type="textarea"
                :rows="16"
                placeholder="定义 AI 执行该技能时应遵循的步骤和方法论...&#10;&#10;支持 Markdown 格式"
              />
              <template #feedback>
                <n-text depth="3" style="font-size: 12px">
                  这是技能的核心 — 告诉 AI 该怎么做。支持 Markdown 格式，可包含标题、列表、代码块等。
                </n-text>
              </template>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 输出格式 -->
        <n-tab-pane name="output" tab="输出格式">
          <n-form label-placement="top">
            <n-form-item label="输出格式模板 (output_format)">
              <n-input
                v-model:value="form.output_format"
                type="textarea"
                :rows="12"
                placeholder="定义技能产出的标准格式模板...&#10;&#10;如 Markdown 表格、JSON 结构等"
              />
              <template #feedback>
                <n-text depth="3" style="font-size: 12px">
                  定义该技能的标准化输出结构。AI 会参考此格式组织输出。留空表示无固定格式要求。
                </n-text>
              </template>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 约束与示例 -->
        <n-tab-pane name="constraints" tab="约束条件">
          <n-form label-placement="top">
            <n-form-item label="约束条件">
              <n-dynamic-input
                v-model:value="form.constraints"
                placeholder="输入一条约束 (如：不要推测原因)"
                :min="0"
              />
              <template #feedback>
                <n-text depth="3" style="font-size: 12px">
                  AI 执行该技能时必须遵守的约束规则
                </n-text>
              </template>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 工具与标签 -->
        <n-tab-pane name="tools" tab="工具与标签">
          <n-form label-placement="top">
            <n-form-item label="推荐工具">
              <n-select
                v-model:value="form.recommended_tools"
                :options="toolOptions"
                multiple
                placeholder="选择该技能推荐使用的工具"
              />
              <template #feedback>
                <n-text depth="3" style="font-size: 12px">
                  AI 执行该技能时推荐使用的工具（仅作参考提示）
                </n-text>
              </template>
            </n-form-item>
            <n-form-item label="标签">
              <n-dynamic-tags v-model:value="form.tags" />
              <template #feedback>
                <n-text depth="3" style="font-size: 12px">
                  用于搜索和分组的标签
                </n-text>
              </template>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 预览 -->
        <n-tab-pane name="preview" tab="🔍 预览">
          <n-text depth="3" style="display: block; margin-bottom: 8px; font-size: 12px">
            预览技能注入到 AI 上下文中的实际内容
          </n-text>
          <div class="prompt-preview">
            <div class="preview-section">
              <n-text depth="3" style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px">
                {{ form.icon }} {{ form.name || '(未命名)' }} — 核心指令
              </n-text>
              <n-text v-if="form.instruction_prompt" tag="pre" style="font-size: 12px; white-space: pre-wrap; word-break: break-word; margin: 4px 0 0; line-height: 1.5; color: #ddd">{{ form.instruction_prompt }}</n-text>
              <n-text v-else depth="3" style="font-size: 12px; font-style: italic">（空 — 未配置指令）</n-text>
            </div>
            <div v-if="form.output_format" class="preview-section" style="margin-top: 12px">
              <n-text depth="3" style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px">
                输出格式
              </n-text>
              <n-text tag="pre" style="font-size: 12px; white-space: pre-wrap; word-break: break-word; margin: 4px 0 0; line-height: 1.5; color: #ddd">{{ form.output_format }}</n-text>
            </div>
            <div v-if="form.constraints.length" class="preview-section" style="margin-top: 12px">
              <n-text depth="3" style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px">
                约束条件
              </n-text>
              <n-text tag="pre" style="font-size: 12px; white-space: pre-wrap; word-break: break-word; margin: 4px 0 0; line-height: 1.5; color: #ddd">{{ form.constraints.map(c => '- ' + c).join('\n') }}</n-text>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showEditor = false">取消</n-button>
          <n-button type="primary" @click="handleSave" :loading="saving">
            {{ editingSkill ? '保存' : '创建' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { AddOutline, CreateOutline, CopyOutline, TrashOutline } from '@vicons/ionicons5'
import { useSkillStore, type Skill } from '@/stores/skill'

const message = useMessage()
const store = useSkillStore()

const showEditor = ref(false)
const editorTab = ref('basic')
const editingSkill = ref<Skill | null>(null)
const saving = ref(false)
const activeCategory = ref('')

// 内置分类 (备用 — categories API 未返回时)
const fallbackCategories: Record<string, { name: string; icon: string }> = {
  general: { name: '通用', icon: '⚡' },
  analysis: { name: '分析', icon: '🔎' },
  coding: { name: '编码', icon: '💻' },
  writing: { name: '写作', icon: '📝' },
  review: { name: '审查', icon: '🔍' },
  testing: { name: '测试', icon: '🧪' },
}

const allCategories = computed(() => {
  return Object.keys(store.categories).length ? store.categories : fallbackCategories
})

const filteredSkills = computed(() => {
  if (!activeCategory.value) return store.skills
  return store.skills.filter(s => s.category === activeCategory.value)
})

const categoryOptions = computed(() =>
  Object.entries(allCategories.value).map(([key, val]) => ({
    label: `${val.icon} ${val.name}`,
    value: key,
  }))
)

const toolOptions = [
  { label: 'ask_user — 提问用户', value: 'ask_user' },
  { label: 'read_file — 读取文件', value: 'read_file' },
  { label: 'search_text — 搜索文本', value: 'search_text' },
  { label: 'get_file_tree — 获取目录树', value: 'get_file_tree' },
  { label: 'list_directory — 列出目录', value: 'list_directory' },
  { label: 'run_command — 执行命令', value: 'run_command' },
]

function getCategoryLabel(key: string): string {
  const cat = allCategories.value[key]
  return cat ? `${cat.icon} ${cat.name}` : key
}

const defaultForm = () => ({
  name: '',
  icon: '⚡',
  description: '',
  category: 'general',
  instruction_prompt: '',
  output_format: '',
  examples: [] as Array<{ input: string; output: string }>,
  constraints: [] as string[],
  recommended_tools: [] as string[],
  tags: [] as string[],
  sort_order: 0,
})

const form = reactive(defaultForm())

function openCreate() {
  editingSkill.value = null
  Object.assign(form, defaultForm())
  editorTab.value = 'basic'
  showEditor.value = true
}

function openEdit(skill: Skill) {
  editingSkill.value = skill
  Object.assign(form, {
    name: skill.name,
    icon: skill.icon,
    description: skill.description,
    category: skill.category,
    instruction_prompt: skill.instruction_prompt,
    output_format: skill.output_format,
    examples: JSON.parse(JSON.stringify(skill.examples || [])),
    constraints: [...(skill.constraints || [])],
    recommended_tools: [...(skill.recommended_tools || [])],
    tags: [...(skill.tags || [])],
    sort_order: skill.sort_order,
  })
  editorTab.value = 'basic'
  showEditor.value = true
}

async function handleSave() {
  if (!form.name.trim()) {
    message.warning('请输入技能名称')
    return
  }
  if (!form.instruction_prompt.trim()) {
    message.warning('请输入核心指令')
    return
  }
  saving.value = true
  try {
    const payload = { ...form }
    if (editingSkill.value) {
      await store.updateSkill(editingSkill.value.id, payload)
      message.success('技能已更新')
    } else {
      await store.createSkill(payload)
      message.success('技能已创建')
    }
    showEditor.value = false
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(skill: Skill, enabled: boolean) {
  try {
    await store.updateSkill(skill.id, { is_enabled: enabled })
    message.success(enabled ? '已启用' : '已禁用')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  }
}

async function handleDuplicate(skill: Skill) {
  try {
    await store.duplicateSkill(skill.id)
    message.success('技能已复制')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '复制失败')
  }
}

async function handleDelete(skill: Skill) {
  try {
    await store.deleteSkill(skill.id)
    message.success('技能已删除')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(() => {
  store.fetchSkills()
  store.fetchCategories()
})
</script>

<style scoped>
.prompt-preview {
  background: #1a1a2e;
  border-radius: 6px;
  padding: 12px 16px;
  max-height: 60vh;
  overflow-y: auto;
}
.preview-section {
  margin-bottom: 12px;
}
.preview-section:last-of-type {
  margin-bottom: 0;
}
</style>
