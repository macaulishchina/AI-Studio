<template>
  <div>
    <!-- 顶部操作栏 -->
    <n-space justify="space-between" align="center" style="margin-bottom: 16px">
      <n-text depth="3">管理 AI 可用的工具 — 定义函数签名、权限归属和执行配置。内置工具不可删除。</n-text>
      <n-button type="primary" size="small" @click="openCreate">
        <template #icon><n-icon :component="AddOutline" /></template>
        添加自定义工具
      </n-button>
    </n-space>

    <!-- 工具卡片列表 -->
    <n-spin :show="store.loading">
      <n-grid :cols="1" :y-gap="12" v-if="store.tools.length">
        <n-gi v-for="tool in store.tools" :key="tool.id">
          <n-card size="small" style="background: #1a1a2e" hoverable>
            <n-space justify="space-between" align="center">
              <n-space align="center" :size="12">
                <span style="font-size: 24px">{{ tool.icon }}</span>
                <div>
                  <n-space align="center" :size="6">
                    <n-text strong>{{ tool.display_name }}</n-text>
                    <n-tag size="tiny" :bordered="false" round>
                      <code style="font-size: 11px">{{ tool.name }}</code>
                    </n-tag>
                    <n-tag v-if="tool.is_builtin" size="tiny" type="info" round>内置</n-tag>
                    <n-tag v-if="!tool.is_enabled" size="tiny" type="warning" round>已禁用</n-tag>
                  </n-space>
                  <n-text depth="3" style="font-size: 12px; display: block; margin-top: 2px">
                    {{ tool.description }}
                  </n-text>
                </div>
              </n-space>
              <n-space :size="8" align="center">
                <n-tag size="tiny" :bordered="false" :type="permTagType(tool.permission_key)">
                  {{ permLabel(tool.permission_key) }}
                </n-tag>
                <n-switch
                  :value="tool.is_enabled"
                  size="small"
                  @update:value="toggleEnabled(tool, $event)"
                />
                <n-button size="tiny" quaternary @click="openEdit(tool)">
                  <template #icon><n-icon :component="CreateOutline" /></template>
                </n-button>
                <n-button size="tiny" quaternary @click="handleDuplicate(tool)">
                  <template #icon><n-icon :component="CopyOutline" /></template>
                </n-button>
                <n-popconfirm v-if="!tool.is_builtin" @positive-click="handleDelete(tool)">
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  确定删除工具「{{ tool.display_name }}」？
                </n-popconfirm>
                <n-tooltip v-else>
                  <template #trigger>
                    <n-button size="tiny" quaternary disabled>
                      <template #icon><n-icon :component="TrashOutline" /></template>
                    </n-button>
                  </template>
                  内置工具不可删除
                </n-tooltip>
              </n-space>
            </n-space>
            <!-- 函数签名预览 -->
            <div style="margin-top: 8px">
              <n-space :size="4" :wrap="true">
                <n-tag size="tiny" :bordered="false" type="default" v-if="tool.function_def?.parameters?.required">
                  参数: {{ (tool.function_def.parameters.required || []).join(', ') || '无必填' }}
                </n-tag>
                <n-tag size="tiny" :bordered="false" :type="tool.executor_type === 'builtin' ? 'info' : 'warning'">
                  {{ executorLabel(tool.executor_type) }}
                </n-tag>
              </n-space>
            </div>
            <!-- 命令授权面板: 嵌入在 execute_command 工具下方 -->
            <div v-if="tool.permission_key === 'execute_readonly_command'" style="margin-top: 12px">
              <n-button
                size="small"
                quaternary
                :type="showCommandAuth ? 'primary' : 'default'"
                @click="showCommandAuth = !showCommandAuth"
                style="padding: 0 8px"
              >
                🔒 命令授权规则
                <n-icon :component="showCommandAuth ? ChevronUpOutline : ChevronDownOutline" style="margin-left: 4px" />
              </n-button>
              <n-collapse-transition :show="showCommandAuth">
                <div style="margin-top: 8px; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 12px">
                  <CommandAuthSettings />
                </div>
              </n-collapse-transition>
            </div>
          </n-card>
        </n-gi>
      </n-grid>
      <n-empty v-else description="暂无工具配置" />
    </n-spin>

    <!-- 编辑 / 创建弹窗 -->
    <n-modal
      v-model:show="showEditor"
      preset="card"
      :title="editingTool ? `编辑工具 — ${editingTool.display_name}` : '添加自定义工具'"
      style="width: 800px; max-width: 95vw"
      :mask-closable="false"
    >
      <n-tabs type="line" animated :value="editorTab" @update:value="editorTab = $event">
        <!-- 基本信息 -->
        <n-tab-pane name="basic" tab="基本信息">
          <n-form :model="form" label-placement="left" label-width="100">
            <n-form-item label="工具名称">
              <n-input
                v-model:value="form.name"
                placeholder="函数名 (如 my_tool)"
                :disabled="editingTool?.is_builtin"
              />
              <template #feedback>
                <n-text depth="3" style="font-size: 11px">OpenAI function calling 中的函数名，只允许字母、数字、下划线</n-text>
              </template>
            </n-form-item>
            <n-form-item label="显示名称">
              <n-input v-model:value="form.display_name" placeholder="中文显示名 (如 读取文件)" />
            </n-form-item>
            <n-form-item label="图标">
              <n-input v-model:value="form.icon" placeholder="Emoji" style="width: 80px" />
            </n-form-item>
            <n-form-item label="描述">
              <n-input v-model:value="form.description" placeholder="管理员可见的描述" />
            </n-form-item>
            <n-form-item label="权限标识">
              <n-select
                v-model:value="form.permission_key"
                :options="permKeyOptions"
                filterable
                tag
                placeholder="选择或输入新权限标识 (如 read_source)"
              />
              <template #feedback>
                <n-text depth="3" style="font-size: 11px">关联到项目工具权限开关，同一 permission_key 的工具受同一开关控制</n-text>
              </template>
            </n-form-item>
            <n-form-item label="执行器">
              <n-select
                v-model:value="form.executor_type"
                :options="executorOptions"
                style="width: 200px"
              />
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 函数定义 (JSON) -->
        <n-tab-pane name="function" tab="函数定义">
          <n-text depth="3" style="display: block; margin-bottom: 8px; font-size: 12px">
            OpenAI Function Calling 的函数签名 (JSON)。定义 name、description 和 parameters。
          </n-text>
          <n-input
            v-model:value="functionDefJson"
            type="textarea"
            :rows="20"
            placeholder='{"name": "my_tool", "description": "...", "parameters": {...}}'
            style="font-family: monospace; font-size: 12px"
          />
          <n-text v-if="jsonError" type="error" style="font-size: 12px; margin-top: 4px; display: block">
            ⚠️ {{ jsonError }}
          </n-text>
        </n-tab-pane>

        <!-- 预览 -->
        <n-tab-pane name="preview" tab="🔍 预览">
          <n-text depth="3" style="display: block; margin-bottom: 8px; font-size: 12px">
            预览 AI 实际看到的工具定义格式 (OpenAI tools format)
          </n-text>
          <div class="json-preview">
            <pre style="font-size: 12px; white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #ddd; margin: 0">{{ previewJson }}</pre>
          </div>
        </n-tab-pane>
      </n-tabs>

      <template #footer>
        <n-space justify="end">
          <n-button @click="showEditor = false">取消</n-button>
          <n-button type="primary" @click="handleSave" :loading="saving">
            {{ editingTool ? '保存' : '创建' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { AddOutline, CreateOutline, CopyOutline, TrashOutline, ChevronDownOutline, ChevronUpOutline } from '@vicons/ionicons5'
import { useToolStore, type ToolDef } from '@/stores/tool'
import CommandAuthSettings from './CommandAuthSettings.vue'

const message = useMessage()
const store = useToolStore()

const showCommandAuth = ref(false)
const showEditor = ref(false)
const editorTab = ref('basic')
const editingTool = ref<ToolDef | null>(null)
const saving = ref(false)
const functionDefJson = ref('')
const jsonError = ref('')

const executorOptions = [
  { label: '🔧 内置执行器', value: 'builtin' },
  { label: '🖥️ Shell 命令', value: 'command' },
  { label: '🌐 HTTP Webhook', value: 'http' },
]

const permKeyOptions = computed(() => {
  // 从已有工具 + 权限列表中收集
  const keys = new Set<string>()
  for (const t of store.tools) keys.add(t.permission_key)
  for (const p of store.permissions) {
    if (!p.is_meta) keys.add(p.key)
  }
  return [...keys].map(k => ({ label: k, value: k }))
})

function executorLabel(type: string) {
  return { builtin: '内置执行器', command: 'Shell 命令', http: 'HTTP Webhook' }[type] || type
}

function permTagType(key: string) {
  if (key.includes('execute')) return 'warning'
  if (key === 'ask_user') return 'success'
  return 'info'
}

function permLabel(key: string): string {
  const perm = store.permissions.find(p => p.key === key)
  return perm ? `${perm.icon} ${perm.label}` : `🔑 ${key}`
}

const defaultForm = () => ({
  name: '',
  display_name: '',
  icon: '🔧',
  description: '',
  permission_key: '',
  executor_type: 'builtin',
  executor_config: {} as Record<string, any>,
})

const form = reactive(defaultForm())

function openCreate() {
  editingTool.value = null
  Object.assign(form, defaultForm())
  functionDefJson.value = JSON.stringify({
    name: '',
    description: '',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  }, null, 2)
  jsonError.value = ''
  editorTab.value = 'basic'
  showEditor.value = true
}

function openEdit(tool: ToolDef) {
  editingTool.value = tool
  Object.assign(form, {
    name: tool.name,
    display_name: tool.display_name,
    icon: tool.icon,
    description: tool.description,
    permission_key: tool.permission_key,
    executor_type: tool.executor_type,
    executor_config: { ...(tool.executor_config || {}) },
  })
  functionDefJson.value = JSON.stringify(tool.function_def || {}, null, 2)
  jsonError.value = ''
  editorTab.value = 'basic'
  showEditor.value = true
}

const previewJson = computed(() => {
  try {
    const funcDef = JSON.parse(functionDefJson.value || '{}')
    return JSON.stringify({ type: 'function', function: funcDef }, null, 2)
  } catch {
    return '(JSON 格式错误)'
  }
})

function parseAndValidateJson(): Record<string, any> | null {
  try {
    const parsed = JSON.parse(functionDefJson.value || '{}')
    jsonError.value = ''
    return parsed
  } catch (e: any) {
    jsonError.value = 'JSON 解析失败: ' + e.message
    return null
  }
}

async function handleSave() {
  if (!form.name.trim()) {
    message.warning('请输入工具名称')
    return
  }
  if (!form.display_name.trim()) {
    message.warning('请输入显示名称')
    return
  }
  if (!form.permission_key) {
    message.warning('请选择权限标识')
    return
  }

  const funcDef = parseAndValidateJson()
  if (!funcDef) {
    editorTab.value = 'function'
    return
  }

  saving.value = true
  try {
    const payload = {
      ...form,
      function_def: funcDef,
    }
    if (editingTool.value) {
      await store.updateTool(editingTool.value.id, payload)
      message.success('工具已更新')
    } else {
      await store.createTool(payload)
      message.success('工具已创建')
    }
    showEditor.value = false
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(tool: ToolDef, enabled: boolean) {
  try {
    await store.updateTool(tool.id, { is_enabled: enabled })
    message.success(enabled ? '已启用' : '已禁用')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '操作失败')
  }
}

async function handleDuplicate(tool: ToolDef) {
  try {
    await store.duplicateTool(tool.id)
    message.success('工具已复制')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '复制失败')
  }
}

async function handleDelete(tool: ToolDef) {
  try {
    await store.deleteTool(tool.id)
    message.success('工具已删除')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(() => {
  store.fetchTools()
  store.fetchPermissions()
})
</script>

<style scoped>
.json-preview {
  background: #1a1a2e;
  border-radius: 6px;
  padding: 12px 16px;
  max-height: 60vh;
  overflow-y: auto;
}
</style>
