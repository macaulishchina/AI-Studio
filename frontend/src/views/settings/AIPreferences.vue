<template>
  <n-space vertical :size="16">
    <n-alert type="info" :bordered="false">
      配置 AI 模型的全局使用偏好，影响所有讨论和实施面板中的模型选择和行为。
    </n-alert>

    <!-- 模型筛选 -->
    <n-card size="small" style="background: #16213e">
      <template #header>
        <n-space align="center" :size="8">
          <span>🎯 模型筛选</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-space align="center">
          <n-switch v-model:value="studioConfig.freeModelsOnly" />
          <n-text>仅使用免费模型</n-text>
          <n-text depth="3" style="font-size: 11px">开启后只显示 x0 的免费模型，不消耗高级请求额度</n-text>
        </n-space>

        <n-space align="center">
          <n-switch v-model:value="studioConfig.docModelsOnly" />
          <n-text>只用官方推荐模型</n-text>
          <n-text depth="3" style="font-size: 11px">仅影响 Copilot 来源的模型过滤，开启后只显示 GitHub 官方文档中列出的 Copilot 模型，不影响其他来源</n-text>
        </n-space>
      </n-space>
    </n-card>

    <!-- 工具调用轮次 & AI 行为 -->
    <n-card size="small" style="background: #16213e">
      <template #header>
        <n-space align="center" :size="8">
          <span>🔧 工具调用 & AI 行为</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-text depth="3" style="font-size: 11px">
          工具轮次 = AI 可查看代码的次数。免费模型多次调用不影响额度，付费模型每次都消耗高级请求。
        </n-text>
        <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" bordered size="small">
          <n-descriptions-item label="免费模型工具轮次">
            <n-input-number
              v-model:value="studioConfig.freeToolRounds"
              :min="1" :max="100" size="small" style="width: 100px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="付费模型工具轮次">
            <n-input-number
              v-model:value="studioConfig.paidToolRounds"
              :min="1" :max="50" size="small" style="width: 100px"
            />
          </n-descriptions-item>
          <n-descriptions-item label="截断自动继续次数">
            <n-space align="center" :size="4">
              <n-input-number
                v-model:value="studioConfig.maxAutoContinues"
                :min="0" :max="10" size="small" style="width: 100px"
              />
              <n-text depth="3" style="font-size: 10px">AI 输出因 token 截断时自动继续的最大次数 (0=关闭)</n-text>
            </n-space>
          </n-descriptions-item>
        </n-descriptions>
      </n-space>
    </n-card>

    <!-- 模型黑名单 -->
    <n-card size="small" style="background: #16213e">
      <template #header>
        <n-space align="center" :size="8">
          <span>🚫 模型黑名单</span>
        </n-space>
      </template>
      <n-space vertical :size="12">
        <n-text depth="3" style="font-size: 11px">
          匹配到关键词的模型不会出现在选择列表中 (模糊匹配，不区分大小写)
        </n-text>
        <n-space :size="4" :wrap="true">
          <n-tag
            v-for="item in studioConfig.modelBlacklist" :key="item"
            closable size="small" type="error"
            @close="studioConfig.removeFromBlacklist(item)"
          >
            {{ item }}
          </n-tag>
          <n-text v-if="!studioConfig.modelBlacklist.length" depth="3" style="font-size: 12px">
            暂无黑名单规则
          </n-text>
        </n-space>
        <n-space>
          <n-input
            v-model:value="blacklistInput"
            placeholder="输入模型名关键词..."
            size="small" style="width: 200px"
            @keydown.enter="addBlacklist"
          />
          <n-button size="small" @click="addBlacklist">添加</n-button>
        </n-space>
      </n-space>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useStudioConfigStore } from '@/stores/studioConfig'

const studioConfig = useStudioConfigStore()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

const blacklistInput = ref('')
function addBlacklist() {
  if (blacklistInput.value.trim()) {
    studioConfig.addToBlacklist(blacklistInput.value)
    blacklistInput.value = ''
  }
}
</script>
