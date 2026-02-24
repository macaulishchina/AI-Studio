<template>
  <div>
    <n-space justify="space-between" align="center" style="margin-bottom: 12px">
      <n-text strong>项目关联快照</n-text>
      <n-button size="small" type="primary" @click="handleCreate" :loading="creating">
        📸 为此项目创建快照
      </n-button>
    </n-space>

    <n-spin :show="loading">
      <n-timeline v-if="snapshots.length">
        <n-timeline-item
          v-for="s in snapshots"
          :key="s.id"
          :type="s.is_healthy ? 'success' : 'error'"
          :title="s.description || s.git_tag"
          :time="formatDate(s.created_at)"
        >
          <template #default>
            <n-text depth="3" style="font-size: 12px">
              Commit: {{ s.git_commit.slice(0, 8) }} · {{ s.git_tag }}
            </n-text>
          </template>
          <template #footer>
            <n-button size="tiny" @click="handleRollback(s)" :loading="rollingBack === s.id">
              🔄 回滚到此
            </n-button>
          </template>
        </n-timeline-item>
      </n-timeline>
      <n-empty v-else description="此项目暂无快照" />
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { snapshotApi } from '@/api'

const props = defineProps<{ projectId: number }>()
const message = useMessage()
const dialog = useDialog()

const snapshots = ref<any[]>([])
const loading = ref(false)
const creating = ref(false)
const rollingBack = ref<number | null>(null)

async function fetchSnapshots() {
  loading.value = true
  try {
    const { data } = await snapshotApi.list({ project_id: props.projectId })
    snapshots.value = data
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  creating.value = true
  try {
    await snapshotApi.create({
      description: `项目 #${props.projectId} 手动快照`,
      project_id: props.projectId,
    })
    message.success('快照已创建')
    fetchSnapshots()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

function handleRollback(s: any) {
  dialog.warning({
    title: '确认回滚',
    content: `将回滚到 "${s.description || s.git_tag}"`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      rollingBack.value = s.id
      try {
        const { data } = await snapshotApi.rollback(s.id)
        data.success ? message.success('回滚成功') : message.error(data.error || '回滚失败')
      } catch (e: any) {
        message.error(e.response?.data?.detail || '回滚失败')
      } finally {
        rollingBack.value = null
      }
    },
  })
}

function formatDate(d: string) {
  return new Date(d).toLocaleString('zh-CN')
}

onMounted(() => fetchSnapshots())
</script>
