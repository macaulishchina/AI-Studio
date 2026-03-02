<template>
  <div class="device-debug">
    <!-- ─── Left sidebar: device categories only ─── -->
    <aside class="sidebar">
      <h2 class="sidebar-title">🔧 设备调试</h2>
      <div v-for="item in menuItems" :key="item.key" class="menu-item"
           :class="{ active: activeCategory === item.key }" @click="switchCategory(item.key)">
        <span class="menu-icon">{{ item.icon }}</span>
        <span class="menu-text">{{ item.label }}</span>
      </div>
    </aside>

    <!-- ─── Right content ─── -->
    <main class="content">
      <!-- ── Header: category title + source switcher ── -->
      <div class="panel-header">
        <h2 class="panel-title">{{ activeCategory === 'audio' ? '🎙 音频设备' : '📷 摄像头' }}</h2>
        <n-radio-group v-model:value="sourceMode" size="small">
          <n-radio-button v-for="opt in sourceOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
        </n-radio-group>
      </div>

      <!-- ════════════════ Audio Panel ════════════════ -->
      <template v-if="activeCategory === 'audio'">

        <!-- Server-only: driver info (collapsible) -->
        <section v-if="sourceMode === 'server'" class="card">
          <div class="card-header-row clickable" @click="driverExpanded = !driverExpanded">
            <h3>驱动 &amp; 系统信息</h3>
            <n-button size="tiny" quaternary @click.stop="loadServerDriverInfo" :loading="serverDriverLoading">刷新</n-button>
            <span class="expand-icon">{{ driverExpanded ? '▼' : '▶' }}</span>
          </div>
          <template v-if="driverExpanded">
            <div v-if="!serverDriver" class="empty">加载中...</div>
            <div v-else class="driver-grid">
              <div class="driver-row"><span class="driver-key">系统</span><span class="driver-val">{{ serverDriver.platform }} {{ serverDriver.platform_release }} ({{ serverDriver.platform_machine }})</span></div>
              <div class="driver-row"><span class="driver-key">sounddevice</span><n-tag :type="serverDriver.sounddevice_available ? 'success' : 'error'" size="small">{{ serverDriver.sounddevice_available ? '可用' : '不可用' }}</n-tag><span v-if="serverDriver.sounddevice_error" class="driver-err">{{ serverDriver.sounddevice_error }}</span></div>
              <div v-if="serverDriver.portaudio" class="driver-row"><span class="driver-key">PortAudio</span><span class="driver-val">{{ serverDriver.portaudio }}</span></div>
              <div v-for="ha in (serverDriver.host_apis || [])" :key="ha.name" class="driver-row"><span class="driver-key">Host API</span><span class="driver-val">{{ ha.name }} ({{ ha.devices?.length || 0 }} 设备)</span></div>
              <template v-if="serverDriver.platform === 'Linux'">
                <div class="driver-row" v-for="tool in ['alsa', 'pulseaudio', 'pipewire']" :key="tool">
                  <span class="driver-key">{{ tool.toUpperCase() }}</span>
                  <template v-if="serverDriver[tool]"><n-tag :type="serverDriver[tool].installed ? 'success' : 'default'" size="small">{{ serverDriver[tool].installed ? '已安装' : '未安装' }}</n-tag></template>
                  <span v-else class="driver-val">-</span>
                </div>
              </template>
              <div v-if="serverDriver.alsa_devices" class="driver-row" style="flex-direction:column;align-items:flex-start"><span class="driver-key" style="margin-bottom:4px">ALSA 录音设备</span><pre class="driver-pre">{{ serverDriver.alsa_devices }}</pre></div>
            </div>
          </template>
        </section>

        <!-- Unified: device list -->
        <section class="card">
          <div class="card-header-row">
            <h3>设备列表</h3>
            <n-button size="tiny" quaternary @click="audioRefreshDevices" :loading="audioDevicesLoading">刷新</n-button>
          </div>
          <div v-if="audioDevicesError" class="error-text">⚠️ {{ audioDevicesError }}</div>
          <div v-else-if="audioDeviceList.length === 0" class="empty">{{ audioDevicesLoaded ? '未检测到音频输入设备' : '点击刷新获取列表' }}</div>
          <div v-for="dev in audioDeviceList" :key="dev.id" class="dev-item"
               :class="{ active: dev.id === audioSelectedId }" @click="audioSelectDevice(dev.id)">
            <span class="dev-icon">🎙</span>
            <div class="dev-info">
              <div>{{ dev.name }}</div>
              <div v-if="dev.detail" class="dev-detail">{{ dev.detail }}</div>
            </div>
            <n-tag v-if="dev.isDefault" type="info" size="small">默认</n-tag>
            <n-tag v-if="dev.id === audioSelectedId" type="success" size="small">已选</n-tag>
          </div>
        </section>

        <!-- Unified: real-time monitoring -->
        <section class="card">
          <h3>实时监听 &amp; 电平</h3>
          <div class="ctrl-row">
            <n-button :type="audioStreaming ? 'error' : 'primary'" @click="audioToggleStream"
                      :disabled="audioDeviceList.length === 0 && !audioStreaming">
              {{ audioStreaming ? '⏹ 停止' : '▶ 开始监听' }}
            </n-button>
            <div class="meter">
              <span class="meter-label">RMS:</span>
              <div class="meter-bg"><div class="meter-bar" :style="{ width: audioRmsPct + '%', background: audioVolumeColor }"></div></div>
              <span class="meter-val">{{ audioRmsPct.toFixed(0) }}% / {{ audioRmsDb.toFixed(0) }}dB</span>
            </div>
            <div class="meter" style="max-width:180px">
              <span class="meter-label">Peak:</span>
              <div class="meter-bg"><div class="meter-bar" :style="{ width: audioPeakPct + '%', background: '#4caf50' }"></div></div>
              <span class="meter-val">{{ audioPeakDb.toFixed(0) }}dB</span>
            </div>
          </div>
          <canvas ref="audioCanvasRef" class="wave-canvas" width="800" height="120"></canvas>
          <div class="status-row">
            <span :class="['dot', audioStreaming ? 'on' : 'off']"></span>
            {{ audioStreaming ? '采集中...' : '未启动' }}
            <span v-if="audioStreamError" class="error-text" style="margin-left:12px">{{ audioStreamError }}</span>
            <span v-if="audioSampleRate" class="hint" style="margin-left:auto">{{ audioSampleRate }} Hz</span>
          </div>
        </section>

        <!-- Unified: recording test -->
        <section class="card">
          <h3>录音测试</h3>
          <div class="ctrl-row">
            <n-input-number v-model:value="serverCaptureDuration" :min="1" :max="600" :step="1" size="small" style="width:100px" />
            <span class="hint">秒</span>
            <n-button :type="audioRecording ? 'error' : 'warning'" @click="audioToggleRecord"
                      :disabled="!audioCanRecord">
              {{ audioRecording ? '⏹ 停止录音' : '🎙 开始录音' }}
            </n-button>
            <n-button v-if="audioRecordUrl" quaternary size="small" @click="downloadAudioRecord">💾 下载</n-button>
          </div>
          <!-- Unified record result -->
          <div v-if="audioRecordResult" class="result-box">
            <div class="result-row"><span>时长:</span><span>{{ audioRecordResult.duration }}s</span></div>
            <div class="result-row"><span>格式:</span><span>{{ audioRecordResult.mimeType }}</span></div>
            <div class="result-row"><span>大小:</span><span>{{ audioRecordResult.size }}</span></div>
            <audio v-if="audioRecordUrl" :src="audioRecordUrl" controls class="audio-player"></audio>
          </div>
        </section>

        <!-- STT (多模式: 浏览器 Web Speech / 服务端非流式 / 服务端流式) -->
        <section class="card">
          <h3>语音识别 (STT)</h3>
          <div class="ctrl-row" style="margin-bottom: 8px">
            <n-radio-group v-model:value="sttMode" size="small">
              <n-radio-button value="browser">浏览器 STT</n-radio-button>
              <n-radio-button value="server">服务端 STT</n-radio-button>
              <n-radio-button value="server-stream">服务端流式</n-radio-button>
            </n-radio-group>
            <n-select v-model:value="sttLang" :options="langOptions" size="small" style="width:120px" />
            <n-button quaternary size="small" @click="clearTranscripts">清空</n-button>
          </div>

          <!-- Browser STT -->
          <template v-if="sttMode === 'browser'">
            <div v-if="!sttSupported" class="error-text">⚠️ 当前浏览器不支持 Web Speech API</div>
            <template v-else>
              <div class="ctrl-row">
                <n-button :type="isListening ? 'warning' : 'success'" @click="toggleSTT" :disabled="!audioStreaming">{{ isListening ? '⏸ 暂停' : '🎙 开始识别' }}</n-button>
              </div>
            </template>
          </template>

          <!-- Server STT (non-streaming) -->
          <template v-if="sttMode === 'server'">
            <div v-if="!serverSttConfigured" class="error-text">
              ⚠️ 服务端 STT 未配置, 请在 设置 → 推理偏好 中配置 STT API 地址
            </div>
            <template v-else>
              <div class="ctrl-row">
                <n-select
                  v-model:value="sttModel"
                  :options="sttModelOptions"
                  size="small"
                  style="width: 220px"
                  placeholder="STT 模型"
                />
                <n-button
                  :type="serverSttRecording ? 'warning' : 'success'"
                  @click="serverSttRecording ? stopServerSttRecording() : startServerSttRecording()"
                  :disabled="!audioStreaming && sourceMode === 'browser'"
                  :loading="serverSttTranscribing"
                >
                  {{ serverSttRecording ? '⏹ 停止录音' : '🎙 录音并转写' }}
                </n-button>
                <n-text v-if="serverSttRecording" type="warning" style="font-size: 12px">
                  录音中... {{ serverSttRecordTime }}s
                </n-text>
              </div>
            </template>
          </template>

          <!-- Server STT (streaming) -->
          <template v-if="sttMode === 'server-stream'">
            <div v-if="!serverSttConfigured" class="error-text">
              ⚠️ 服务端 STT 未配置, 请在 设置 → 推理偏好 中配置 STT API 地址
            </div>
            <div v-else-if="sourceMode === 'browser'" class="error-text">
              ⚠️ 服务端流式 STT 需要使用服务端音频源 (使用服务端麦克风)
            </div>
            <template v-else>
              <div class="ctrl-row">
                <n-select
                  v-model:value="sttModel"
                  :options="sttModelOptions"
                  size="small"
                  style="width: 220px"
                  placeholder="STT 模型"
                />
                <n-button
                  :type="serverSttStreaming ? 'warning' : 'success'"
                  @click="serverSttStreaming ? stopServerSttStream() : startServerSttStream()"
                >
                  {{ serverSttStreaming ? '⏹ 停止' : '🎙 开始流式转写' }}
                </n-button>
              </div>
            </template>
          </template>

          <!-- 识别结果 (共享) -->
          <div class="live-subtitle" :class="{ active: interimText }"><span class="interim-label">实时:</span>{{ interimText || '...' }}</div>
          <div class="transcript-list">
            <div v-for="(t, i) in transcripts" :key="i" class="transcript-item">
              <span class="ts-time">{{ t.time }}</span><span class="ts-text">{{ t.text }}</span>
              <n-tag v-if="t.confidence >= 0" :type="t.confidence > 0.8 ? 'success' : t.confidence > 0.5 ? 'warning' : 'error'" size="tiny">{{ (t.confidence * 100).toFixed(0) }}%</n-tag>
              <n-tag v-if="t.source" size="tiny" :bordered="false">{{ t.source }}</n-tag>
            </div>
            <div v-if="transcripts.length === 0" class="empty">等待语音输入...</div>
          </div>
        </section>
      </template>

      <!-- ════════════════ Camera Panel ════════════════ -->
      <template v-if="activeCategory === 'camera'">

        <!-- Unified: device list -->
        <section class="card">
          <div class="card-header-row">
            <h3>设备列表</h3>
            <n-button size="tiny" quaternary @click="cameraRefreshDevices" :loading="cameraDevicesLoading">刷新</n-button>
          </div>
          <div v-if="cameraDevicesError" class="error-text">⚠️ {{ cameraDevicesError }}</div>
          <div v-else-if="cameraDeviceList.length === 0" class="empty">{{ cameraDevicesLoaded ? '未检测到摄像头' : '点击刷新获取列表' }}</div>
          <div v-for="cam in cameraDeviceList" :key="cam.id" class="dev-item"
               :class="{ active: cam.id === cameraSelectedId }" @click="cameraSelectDevice(cam.id)">
            <span class="dev-icon">📷</span>
            <div class="dev-info">
              <div>{{ cam.name }}</div>
              <div v-if="cam.detail" class="dev-detail">{{ cam.detail }}</div>
            </div>
            <n-tag v-if="cam.statusTag" :type="cam.statusType" size="small">{{ cam.statusTag }}</n-tag>
            <n-tag v-if="cam.id === cameraSelectedId" type="success" size="small">已选</n-tag>
          </div>
        </section>

        <!-- Unified: live preview -->
        <section class="card">
          <h3>实时预览</h3>
          <div class="ctrl-row">
            <n-button :type="cameraStreamActive ? 'error' : 'success'" @click="cameraToggleStream"
                      :disabled="cameraDeviceList.length === 0 && !cameraStreamActive">
              {{ cameraStreamActive ? '⏹ 停止' : '▶ 开始预览' }}
            </n-button>
            <template v-if="sourceMode === 'server'">
              <n-input-number v-model:value="cameraStreamFps" :min="1" :max="30" :step="1" size="small" style="width:100px" />
              <span class="hint">FPS</span>
            </template>
          </div>
          <!-- Server: MJPEG img -->
          <div v-if="sourceMode === 'server' && cameraStreamActive" class="stream-box">
            <img :src="serverStreamSrc" class="stream-img" alt="live" />
          </div>
          <!-- Browser: video element -->
          <video v-if="sourceMode === 'browser'" v-show="cameraStreamActive" ref="browserVideoRef"
                 autoplay playsinline muted class="preview-video"></video>
          <div class="status-row">
            <span :class="['dot', cameraStreamActive ? 'on' : 'off']"></span>
            {{ cameraStreamActive ? '预览中' : '未启动' }}
          </div>
        </section>

        <!-- Unified: snapshot -->
        <section class="card">
          <h3>拍照</h3>
          <div class="ctrl-row">
            <n-button type="primary" @click="cameraTakeSnapshot" :loading="snapshotLoading"
                      :disabled="sourceMode === 'server' ? cameraSelectedId === '' : !cameraStreamActive">
              📸 拍照
            </n-button>
            <n-button v-if="snapshotUrl" quaternary size="small" @click="downloadSnapshot">💾 下载</n-button>
          </div>
          <div v-if="snapshotUrl" class="snapshot-box">
            <img :src="snapshotUrl" class="snapshot-img" alt="snapshot" />
            <div v-if="snapshotTime" class="snapshot-time">{{ snapshotTime }}</div>
          </div>
          <div v-if="snapshotError" class="error-text">{{ snapshotError }}</div>
          <!-- Hidden canvas for browser snapshot -->
          <canvas v-if="sourceMode === 'browser'" ref="browserSnapshotCanvas" style="display:none"></canvas>
        </section>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, computed, nextTick } from 'vue'
import { NTag, NButton, NSelect, NInputNumber, NRadioGroup, NRadioButton, useMessage } from 'naive-ui'
import { voiceApi, cameraApi, sttApi } from '../api'

const message = useMessage()

// ═══════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════
const activeCategory = ref<'audio' | 'camera'>('audio')
const sourceMode = ref<'server' | 'browser'>('server')

const menuItems = [
  { key: 'audio' as const, icon: '🎙', label: '音频' },
  { key: 'camera' as const, icon: '📷', label: '摄像头' },
]

const sourceOptions = [
  { value: 'server', label: '🖥 服务端' },
  { value: 'browser', label: '🌐 浏览器' },
]

function switchCategory(key: 'audio' | 'camera') {
  if (key === activeCategory.value) return
  releaseAllResources()
  activeCategory.value = key
  // Auto refresh devices when switching to audio
  if (key === 'audio') {
    nextTick(() => audioRefreshDevices())
  }
}

// When source changes, release resources from the previous source
watch(sourceMode, () => {
  releaseAllResources()
  // Auto refresh devices when source mode changes and audio is active
  if (activeCategory.value === 'audio') {
    nextTick(() => audioRefreshDevices())
  }
})

function releaseAllResources() {
  // audio
  if (serverStreaming.value) stopServerAudioStream()
  if (serverCapturing.value) stopServerRecord()
  if (browserAudioStreaming.value) stopBrowserAudio()
  if (browserRecording.value) stopBrowserRecord()
  // STT
  if (isListening.value) stopSTT()
  if (serverSttRecording.value) stopServerSttRecording()
  if (serverSttStreaming.value) stopServerSttStream()
  // camera
  if (serverCamStreaming.value) { serverCamStreaming.value = false; serverStreamSrc.value = '' }
  if (browserCamActive.value) stopBrowserCamera()
}

// ═══════════════════════════════════════════════════
// Unified device item interface
// ═══════════════════════════════════════════════════
interface DeviceItem {
  id: string
  name: string
  detail: string
  isDefault?: boolean
  statusTag?: string
  statusType?: 'success' | 'warning' | 'error' | 'info' | 'default'
}

// ═══════════════════════════════════════════════════
// AUDIO — Unified computed views
// ═══════════════════════════════════════════════════

// --- Server audio state ---
const driverExpanded = ref(false)
const serverDriver = ref<any>(null)
const serverDriverLoading = ref(false)
const serverAudioDevices = ref<any[]>([])
const serverAudioDevicesLoading = ref(false)
const serverAudioDevicesLoaded = ref(false)
const serverAudioDevicesError = ref('')
const selectedServerAudioIdx = ref<number>(-1)

const serverStreaming = ref(false)
const serverRmsPct = ref(0)
const serverRmsDb = ref(-100)
const serverPeakPct = ref(0)
const serverPeakDb = ref(-100)
const serverStreamError = ref('')
const serverSampleRate = ref(16000)
const audioCanvasRef = ref<HTMLCanvasElement | null>(null)
let serverEventSource: EventSource | null = null
let serverWaveformHistory: number[] = []
let serverAnimFrame = 0

const serverCapturing = ref(false)
const serverCaptureDuration = ref(60)
const serverCaptureResult = ref<any>(null)
const serverRecordSec = ref(0)
const serverRecordUrl = ref('')
const serverRecordResult = ref<{ duration: string; mimeType: string; size: string } | null>(null)
let serverRecordManuallyStopped = false
let serverRecordTimer: ReturnType<typeof setInterval> | null = null
let serverRecordStartTime = 0

// --- Browser audio state ---
const browserMicrophones = ref<MediaDeviceInfo[]>([])
const selectedBrowserMicId = ref('')
const permissionDenied = ref(false)
const browserAudioStreaming = ref(false)
const browserVolumePct = ref(0)
const browserRmsDb = ref(-100)
const browserPeakPct = ref(0)
const browserPeakDb = ref(-100)
const browserSampleRate = ref(0)
let audioCtx: AudioContext | null = null
let analyser: AnalyserNode | null = null
let mediaStream: MediaStream | null = null
let browserAnimFrame = 0

const browserRecording = ref(false)
const browserRecordSec = ref(0)
const browserRecordUrl = ref('')
const browserRecordResult = ref<{ duration: string; mimeType: string; size: string } | null>(null)
let mediaRecorder: MediaRecorder | null = null
let recordChunks: Blob[] = []
let recordTimer: ReturnType<typeof setInterval> | null = null
let recordStartTime = 0

// STT
const sttMode = ref<'browser' | 'server' | 'server-stream'>('browser')
const sttSupported = ref(false)
const isListening = ref(false)
const sttLang = ref('zh-CN')
const interimText = ref('')
const transcripts = ref<{ time: string; text: string; confidence: number; source?: string }[]>([])
let recognition: any = null
const langOptions = [
  { label: '中文', value: 'zh-CN' },
  { label: 'English', value: 'en-US' },
  { label: '日本語', value: 'ja-JP' },
]

// Server STT state
const serverSttConfigured = ref(false)
const sttModel = ref('')
const sttModelOptions = ref<{ label: string; value: string }[]>([])
const serverSttRecording = ref(false)
const serverSttRecordTime = ref(0)
const serverSttTranscribing = ref(false)
const serverSttStreaming = ref(false)
let serverSttRecordChunks: Blob[] = []
let serverSttMediaRecorder: MediaRecorder | null = null
let serverSttRecordTimer: ReturnType<typeof setInterval> | null = null
let serverSttEventSource: EventSource | null = null

// --- Unified audio computed ---
const audioDeviceList = computed<DeviceItem[]>(() => {
  if (sourceMode.value === 'server') {
    return serverAudioDevices.value.map((d: any) => ({
      id: String(d.index),
      name: d.name,
      detail: `CH: ${d.channels} · ${d.default_samplerate} Hz · ${d.host_api}`,
      isDefault: d.is_default,
    }))
  } else {
    return browserMicrophones.value.map((m) => ({
      id: m.deviceId,
      name: m.label || '麦克风 ' + m.deviceId.slice(0, 8),
      detail: '',
    }))
  }
})
const audioDevicesLoading = computed(() =>
  sourceMode.value === 'server' ? serverAudioDevicesLoading.value : false,
)
const audioDevicesLoaded = computed(() =>
  sourceMode.value === 'server' ? serverAudioDevicesLoaded.value : browserMicrophones.value.length > 0,
)
const audioDevicesError = computed(() => {
  if (sourceMode.value === 'server') return serverAudioDevicesError.value
  return permissionDenied.value ? '麦克风权限被拒绝' : ''
})
const audioSelectedId = computed(() =>
  sourceMode.value === 'server' ? String(selectedServerAudioIdx.value) : selectedBrowserMicId.value,
)
const audioStreaming = computed(() =>
  sourceMode.value === 'server' ? serverStreaming.value : browserAudioStreaming.value,
)
const audioRmsPct = computed(() =>
  sourceMode.value === 'server' ? serverRmsPct.value : browserVolumePct.value,
)
const audioRmsDb = computed(() =>
  sourceMode.value === 'server' ? serverRmsDb.value : browserRmsDb.value,
)
const audioPeakPct = computed(() =>
  sourceMode.value === 'server' ? serverPeakPct.value : browserPeakPct.value,
)
const audioPeakDb = computed(() =>
  sourceMode.value === 'server' ? serverPeakDb.value : browserPeakDb.value,
)
const audioSampleRate = computed(() =>
  sourceMode.value === 'server' ? serverSampleRate.value : browserSampleRate.value,
)
const audioCanRecord = computed(() =>
  sourceMode.value === 'server' ? audioDeviceList.value.length > 0 : audioStreaming.value,
)
const audioVolumeColor = computed(() => {
  const v = audioRmsPct.value
  if (v < 30) return '#4caf50'
  if (v < 70) return '#ff9800'
  return '#f44336'
})
const audioStreamError = computed(() =>
  sourceMode.value === 'server' ? serverStreamError.value : '',
)
const audioRecording = computed(() =>
  sourceMode.value === 'server' ? serverCapturing.value : browserRecording.value,
)
const audioRecordSec = computed(() =>
  sourceMode.value === 'server' ? serverRecordSec.value : browserRecordSec.value,
)
const audioRecordUrl = computed(() =>
  sourceMode.value === 'server' ? serverRecordUrl.value : browserRecordUrl.value,
)
const audioRecordResult = computed(() =>
  sourceMode.value === 'server' ? serverRecordResult.value : browserRecordResult.value,
)

// --- Unified audio actions ---
function audioRefreshDevices() {
  sourceMode.value === 'server' ? loadServerAudioDevices() : refreshBrowserMics()
}

function audioSelectDevice(id: string) {
  if (sourceMode.value === 'server') {
    selectedServerAudioIdx.value = parseInt(id)
  } else {
    selectedBrowserMicId.value = id
    if (browserAudioStreaming.value) { stopBrowserAudio(); startBrowserAudio() }
  }
}

function audioToggleStream() {
  if (sourceMode.value === 'server') {
    serverStreaming.value ? stopServerAudioStream() : startServerAudioStream()
  } else {
    browserAudioStreaming.value ? stopBrowserAudio() : startBrowserAudio()
  }
}

function audioToggleRecord() {
  if (sourceMode.value === 'server') {
    serverCapturing.value ? stopServerRecord() : startServerRecord()
  } else {
    browserRecording.value ? stopBrowserRecord() : startBrowserRecord()
  }
}

function downloadAudioRecord() {
  sourceMode.value === 'server' ? downloadServerRecord() : downloadBrowserRecord()
}

// ─── Server audio impl ───
async function loadServerDriverInfo() {
  serverDriverLoading.value = true
  try {
    const res = await voiceApi.getDriverInfo()
    serverDriver.value = res.data
    driverExpanded.value = true
  } catch (e: any) {
    message.error('获取驱动信息失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    serverDriverLoading.value = false
  }
}

async function loadServerAudioDevices() {
  serverAudioDevicesLoading.value = true
  serverAudioDevicesError.value = ''
  try {
    const res = await voiceApi.getDevices()
    const data = res.data
    if (!data.available) {
      serverAudioDevicesError.value = data.error || 'sounddevice 不可用'
      serverAudioDevices.value = []
    } else {
      serverAudioDevices.value = data.input_devices || data.devices || []
      if (serverAudioDevices.value.length) {
        const def = serverAudioDevices.value.find((d: any) => d.is_default)
        selectedServerAudioIdx.value = def ? def.index : serverAudioDevices.value[0].index
      }
    }
    serverAudioDevicesLoaded.value = true
  } catch (e: any) {
    serverAudioDevicesError.value = e.response?.data?.detail || e.message
  } finally {
    serverAudioDevicesLoading.value = false
  }
}

function startServerAudioStream() {
  const selected = serverAudioDevices.value.find((d: any) => d.index === selectedServerAudioIdx.value)
  const samplerate = selected?.default_samplerate ? Math.round(Number(selected.default_samplerate)) : undefined
  const params: { device?: number; samplerate?: number; interval_ms?: number } = { interval_ms: 40 }
  if (selectedServerAudioIdx.value >= 0) params.device = selectedServerAudioIdx.value
  if (samplerate && samplerate > 0) params.samplerate = samplerate
  const url = voiceApi.levelStreamUrl(params)
  serverEventSource = new EventSource(url)
  serverStreaming.value = true
  serverStreamError.value = ''
  serverWaveformHistory = []
  serverEventSource.onmessage = (evt) => {
    try {
      const d = JSON.parse(evt.data)
      if (d.error) { serverStreamError.value = d.error; stopServerAudioStream(); return }
      if (d.event === 'started') { if (d.samplerate) serverSampleRate.value = d.samplerate; return }
      const rmsDb = Number(d.rms_db ?? -100)
      const peakDb = Number(d.peak_db ?? -100)
      const rmsRawPct = Math.max(0, Math.min(100, (rmsDb + 60) / 60 * 100))
      const peakRawPct = Math.max(0, Math.min(100, (peakDb + 60) / 60 * 100))
      serverRmsDb.value = rmsDb
      serverPeakDb.value = peakDb
      serverRmsPct.value = serverRmsPct.value * 0.6 + rmsRawPct * 0.4
      serverPeakPct.value = Math.max(peakRawPct, serverPeakPct.value * 0.92)
      if (Array.isArray(d.waveform)) {
        serverWaveformHistory = d.waveform
      }
    } catch { /* ignore */ }
  }
  serverEventSource.onerror = () => {
    if (serverStreaming.value) { serverStreamError.value = 'SSE 连接断开'; stopServerAudioStream() }
  }
  drawAudioCanvas()
}

function stopServerAudioStream() {
  serverStreaming.value = false
  if (serverEventSource) { serverEventSource.close(); serverEventSource = null }
  cancelAnimationFrame(serverAnimFrame)
  serverRmsPct.value = 0
  serverRmsDb.value = -100
  serverPeakPct.value = 0
  serverPeakDb.value = -100
  serverWaveformHistory = []
}

let serverRecordAbort: AbortController | null = null

function startServerRecord() {
  if (serverCapturing.value) return
  serverCapturing.value = true
  serverRecordManuallyStopped = false
  serverRecordResult.value = null
  serverCaptureResult.value = null
  if (serverRecordUrl.value) { URL.revokeObjectURL(serverRecordUrl.value); serverRecordUrl.value = '' }

  // Start timer UI
  serverRecordStartTime = Date.now()
  serverRecordSec.value = 0
  serverRecordTimer = setInterval(() => {
    serverRecordSec.value = Math.floor((Date.now() - serverRecordStartTime) / 1000)
  }, 500)

  // Fire recording API
  const dur = serverCaptureDuration.value
  const params: any = { duration: dur }
  if (selectedServerAudioIdx.value >= 0) params.device = selectedServerAudioIdx.value

  const timeoutMs = Math.max(30000, Math.ceil(dur * 1000) + 10000)
  serverRecordAbort = new AbortController()

  voiceApi.recordAudio(params, timeoutMs)
    .then((res: any) => {
      // Blob response
      const blob: Blob = res.data
      if (serverRecordUrl.value) URL.revokeObjectURL(serverRecordUrl.value)
      serverRecordUrl.value = URL.createObjectURL(blob)

      const durActual = res.headers['x-audio-duration'] || String(dur)
      const rmsDb = res.headers['x-audio-rms-db'] || ''
      const peakDb = res.headers['x-audio-peak-db'] || ''
      const isSilent = res.headers['x-audio-is-silent'] === 'true'
      const sizeKB = (blob.size / 1024).toFixed(1)

      serverRecordResult.value = {
        duration: durActual,
        mimeType: 'audio/wav',
        size: sizeKB + ' KB',
      }
      serverCaptureResult.value = {
        success: true,
        rms_db: rmsDb,
        peak_db: peakDb,
        is_silent: isSilent,
      }
    })
    .catch((e: any) => {
      if (e?.code === 'ERR_CANCELED') {
        if (serverRecordManuallyStopped) {
          message.warning('录音已手动停止。由于服务端录音技术限制，无法获取已录制的音频数据。请使用浏览器模式进行录音以获得更好的控制。')
        }
        return
      }
      const errMsg = e?.response?.data?.detail || e?.message || String(e)
      message.error('录音失败: ' + errMsg)
    })
    .finally(() => {
      serverCapturing.value = false
      if (serverRecordTimer) { clearInterval(serverRecordTimer); serverRecordTimer = null }
      serverRecordAbort = null
    })
}

function stopServerRecord() {
  // Server recording is fixed-duration, we can't truly stop early.
  // But we can abort the request if the user clicks stop.
  serverRecordManuallyStopped = true
  if (serverRecordAbort) serverRecordAbort.abort()
  serverCapturing.value = false
  if (serverRecordTimer) { clearInterval(serverRecordTimer); serverRecordTimer = null }
}

function downloadServerRecord() {
  if (!serverRecordUrl.value) return
  const a = document.createElement('a')
  a.href = serverRecordUrl.value
  a.download = 'recording_' + new Date().toISOString().replace(/[:.]/g, '-') + '.wav'
  a.click()
}

// ─── Browser audio impl ───
async function refreshBrowserMics() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach(t => t.stop())
    permissionDenied.value = false
  } catch {
    permissionDenied.value = true
    return
  }
  const devices = await navigator.mediaDevices.enumerateDevices()
  browserMicrophones.value = devices.filter(d => d.kind === 'audioinput')
  if (browserMicrophones.value.length && !selectedBrowserMicId.value) {
    selectedBrowserMicId.value = browserMicrophones.value[0].deviceId
  }
}

async function startBrowserAudio() {
  try {
    const audioOptions: MediaTrackConstraints = selectedBrowserMicId.value
      ? { deviceId: { exact: selectedBrowserMicId.value } }
      : {}
    audioOptions.channelCount = 1
    audioOptions.echoCancellation = false
    audioOptions.noiseSuppression = false
    audioOptions.autoGainControl = false
    const constraints: MediaStreamConstraints = { audio: audioOptions }
    mediaStream = await navigator.mediaDevices.getUserMedia(constraints)
    audioCtx = new AudioContext()
    browserSampleRate.value = audioCtx.sampleRate
    const source = audioCtx.createMediaStreamSource(mediaStream)
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = 2048
    analyser.smoothingTimeConstant = 0.7
    source.connect(analyser)
    browserAudioStreaming.value = true
    drawAudioCanvas()
  } catch (e: any) {
    message.error('无法启动麦克风: ' + e.message)
  }
}

function stopBrowserAudio() {
  browserAudioStreaming.value = false
  cancelAnimationFrame(browserAnimFrame)
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null }
  if (audioCtx) { audioCtx.close(); audioCtx = null }
  analyser = null
  browserVolumePct.value = 0
  browserRmsDb.value = -100
  browserPeakPct.value = 0
  browserPeakDb.value = -100
  if (isListening.value) stopSTT()
  if (browserRecording.value) stopBrowserRecord()
}

// ─── Unified canvas draw ───
function drawWaveOverlay(
  ctx: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  waveform: ArrayLike<number>,
  color: string,
  rmsPct: number,
  peakPct: number,
) {
  const w = canvas.width
  const h = canvas.height
  const midY = h / 2
  ctx.fillStyle = '#1a1a1a'
  ctx.fillRect(0, 0, w, h)

  ctx.lineWidth = 1
  ctx.strokeStyle = 'rgba(124,108,255,0.16)'
  ctx.beginPath()
  ctx.moveTo(0, midY)
  ctx.lineTo(w, midY)
  ctx.stroke()

  const length = waveform.length || 0
  if (length > 1) {
    ctx.lineWidth = 2
    ctx.strokeStyle = color
    ctx.beginPath()
    const step = Math.max(1, Math.floor(length / 260))
    let pointIndex = 0
    for (let i = 0; i < length; i += step) {
      const x = (pointIndex / Math.max(Math.ceil(length / step) - 1, 1)) * w
      const amp = Math.max(-1, Math.min(1, Number(waveform[i] || 0)))
      const y = midY + amp * (h * 0.42)
      pointIndex === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      pointIndex += 1
    }
    ctx.stroke()
  }

  const meterW = 10
  const meterX = w - meterW - 6
  const meterY = 6
  const meterH = h - 12
  ctx.fillStyle = '#2a2a2a'
  ctx.fillRect(meterX, meterY, meterW, meterH)
  const rmsH = meterH * Math.max(0, Math.min(1, rmsPct / 100))
  ctx.fillStyle = color
  ctx.fillRect(meterX, meterY + meterH - rmsH, meterW, rmsH)
  const peakY = meterY + meterH - meterH * Math.max(0, Math.min(1, peakPct / 100))
  ctx.strokeStyle = '#ff9800'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(meterX - 3, peakY)
  ctx.lineTo(meterX + meterW + 3, peakY)
  ctx.stroke()
}

function drawAudioCanvas() {
  const canvas = audioCanvasRef.value
  if (!canvas) {
    // canvas not ready yet after source switch, retry once
    const frame = requestAnimationFrame(drawAudioCanvas)
    if (sourceMode.value === 'server') serverAnimFrame = frame
    else browserAnimFrame = frame
    return
  }
  const viewCanvas = canvas
  const ctx = viewCanvas.getContext('2d')!

  if (sourceMode.value === 'server') {
    function drawServer() {
      if (!serverStreaming.value) return
      serverAnimFrame = requestAnimationFrame(drawServer)
      drawWaveOverlay(ctx, viewCanvas, serverWaveformHistory, '#4caf50', serverRmsPct.value, serverPeakPct.value)
    }
    drawServer()
  } else {
    if (!analyser) return
    const bufLen = analyser.fftSize
    const data = new Float32Array(bufLen)
    const localAnalyser = analyser
    function drawBrowser() {
      if (!localAnalyser || !browserAudioStreaming.value) return
      browserAnimFrame = requestAnimationFrame(drawBrowser)
      localAnalyser.getFloatTimeDomainData(data)
      let sum = 0
      let maxAbs = 0
      for (let i = 0; i < bufLen; i++) {
        const v = data[i]
        sum += v * v
        const abs = Math.abs(v)
        if (abs > maxAbs) maxAbs = abs
      }
      const rms = Math.sqrt(sum / bufLen)
      const rmsDb = 20 * Math.log10(rms + 1e-10)
      const peakDb = 20 * Math.log10(maxAbs + 1e-10)
      const rmsRawPct = Math.max(0, Math.min(100, (rmsDb + 60) / 60 * 100))
      const peakRawPct = Math.max(0, Math.min(100, (peakDb + 60) / 60 * 100))
      browserRmsDb.value = rmsDb
      browserPeakDb.value = peakDb
      browserVolumePct.value = browserVolumePct.value * 0.6 + rmsRawPct * 0.4
      browserPeakPct.value = Math.max(peakRawPct, browserPeakPct.value * 0.92)
      drawWaveOverlay(ctx, viewCanvas, data, '#7c6cff', browserVolumePct.value, browserPeakPct.value)
    }
    drawBrowser()
  }
}

// ─── Browser recording ───
function startBrowserRecord() {
  if (!mediaStream) return
  recordChunks = []
  const options: MediaRecorderOptions = {}
  if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) options.mimeType = 'audio/webm;codecs=opus'
  else if (MediaRecorder.isTypeSupported('audio/webm')) options.mimeType = 'audio/webm'
  mediaRecorder = new MediaRecorder(mediaStream, options)
  mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordChunks.push(e.data) }
  mediaRecorder.onstop = () => {
    const blob = new Blob(recordChunks, { type: mediaRecorder?.mimeType || 'audio/webm' })
    if (browserRecordUrl.value) URL.revokeObjectURL(browserRecordUrl.value)
    browserRecordUrl.value = URL.createObjectURL(blob)
    const dur = ((Date.now() - recordStartTime) / 1000).toFixed(1)
    const sizeKB = (blob.size / 1024).toFixed(1)
    browserRecordResult.value = { duration: dur, mimeType: blob.type, size: sizeKB + ' KB' }
  }
  mediaRecorder.start(200)
  recordStartTime = Date.now()
  browserRecording.value = true
  browserRecordSec.value = 0
  recordTimer = setInterval(() => { browserRecordSec.value = Math.floor((Date.now() - recordStartTime) / 1000) }, 500)
}

function stopBrowserRecord() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
  mediaRecorder = null
  browserRecording.value = false
  if (recordTimer) { clearInterval(recordTimer); recordTimer = null }
}

function downloadBrowserRecord() {
  if (!browserRecordUrl.value) return
  const a = document.createElement('a')
  a.href = browserRecordUrl.value
  a.download = 'recording_' + new Date().toISOString().replace(/[:.]/g, '-') + '.webm'
  a.click()
}

// ─── STT ───
function initSTT() {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  if (!SR) { sttSupported.value = false; return }
  sttSupported.value = true
  recognition = new SR()
  recognition.continuous = true
  recognition.interimResults = true
  recognition.lang = sttLang.value
  recognition.onresult = (e: any) => {
    let interim = ''
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i]
      if (r.isFinal) {
        transcripts.value.push({ time: new Date().toLocaleTimeString(), text: r[0].transcript, confidence: r[0].confidence || 0.5 })
        interim = ''
      } else { interim += r[0].transcript }
    }
    interimText.value = interim
  }
  recognition.onerror = (e: any) => { if (e.error !== 'no-speech') message.warning('STT 错误: ' + e.error) }
  recognition.onend = () => { if (isListening.value) recognition.start() }
}
function startSTT() { if (!recognition) initSTT(); if (!recognition) return; recognition.lang = sttLang.value; recognition.start(); isListening.value = true }
function stopSTT() { recognition?.stop(); isListening.value = false; interimText.value = '' }
function toggleSTT() { isListening.value ? stopSTT() : startSTT() }
function clearTranscripts() { transcripts.value = []; interimText.value = '' }

// ─── Server STT ───
async function loadSttStatus() {
  try {
    const { data } = await sttApi.status()
    serverSttConfigured.value = !!data.configured
    sttModel.value = data.default_model || ''
  } catch { serverSttConfigured.value = false }
}

async function loadSttModels() {
  try {
    const { data } = await sttApi.models()
    sttModelOptions.value = (data || []).map((m: any) => ({ label: m.name || m.id, value: m.id }))
    if (!sttModel.value && sttModelOptions.value.length > 0) {
      sttModel.value = sttModelOptions.value[0].value
    }
  } catch { sttModelOptions.value = [] }
}

// Server STT (non-streaming): record from browser, send file to server
function startServerSttRecording() {
  if (!mediaStream) {
    message.warning('请先开启音频源')
    return
  }
  serverSttRecordChunks = []
  const options: MediaRecorderOptions = {}
  try { if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) options.mimeType = 'audio/webm;codecs=opus' } catch {}
  serverSttMediaRecorder = new MediaRecorder(mediaStream, options)
  serverSttMediaRecorder.ondataavailable = (e: BlobEvent) => { if (e.data.size > 0) serverSttRecordChunks.push(e.data) }
  serverSttMediaRecorder.onstop = async () => {
    const blob = new Blob(serverSttRecordChunks, { type: serverSttMediaRecorder?.mimeType || 'audio/webm' })
    serverSttRecordChunks = []
    await transcribeServerFile(blob)
  }
  serverSttMediaRecorder.start(250)
  serverSttRecording.value = true
  serverSttRecordTime.value = 0
  const t0 = Date.now()
  serverSttRecordTimer = setInterval(() => { serverSttRecordTime.value = Math.floor((Date.now() - t0) / 1000) }, 500)
}

function stopServerSttRecording() {
  if (serverSttMediaRecorder && serverSttMediaRecorder.state !== 'inactive') {
    serverSttMediaRecorder.stop()
  }
  serverSttRecording.value = false
  if (serverSttRecordTimer) { clearInterval(serverSttRecordTimer); serverSttRecordTimer = null }
}

async function transcribeServerFile(blob: Blob) {
  serverSttTranscribing.value = true
  interimText.value = '转写中...'
  try {
    const file = new File([blob], 'recording.webm', { type: blob.type })
    const lang = sttLang.value.split('-')[0] // "zh-CN" → "zh"
    const { data } = await sttApi.transcribe(file, {
      model: sttModel.value || undefined,
      language: lang,
    })
    if (data.error) {
      message.error('STT 错误: ' + data.error)
    } else if (data.text) {
      transcripts.value.push({
        time: new Date().toLocaleTimeString(),
        text: data.text,
        confidence: -1,
        source: `server:${data.model || 'unknown'} (${data.duration_ms}ms)`,
      })
    }
  } catch (e: any) {
    message.error('转写失败: ' + (e?.response?.data?.error || e.message))
  } finally {
    serverSttTranscribing.value = false
    interimText.value = ''
  }
}

// Server STT (streaming): server-side mic → SSE events
function startServerSttStream() {
  if (sourceMode.value !== 'server') {
    message.warning('服务端流式 STT 需要使用服务端音频源')
    return
  }
  const device = selectedServerAudioIdx.value >= 0 ? selectedServerAudioIdx.value : undefined
  const lang = sttLang.value.split('-')[0]
  const url = sttApi.transcribeStreamUrl({
    device,
    model: sttModel.value || undefined,
    language: lang,
    samplerate: 16000,
    duration: 60,
  })
  serverSttEventSource = new EventSource(url)
  serverSttStreaming.value = true
  interimText.value = '等待录音开始...'

  serverSttEventSource.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.type === 'recording') {
        interimText.value = data.message || '录音中...'
      } else if (data.type === 'partial') {
        interimText.value = data.text || ''
        // 也添加到历史
        transcripts.value.push({
          time: new Date().toLocaleTimeString(),
          text: data.text,
          confidence: -1,
          source: 'server-stream',
        })
      } else if (data.type === 'final') {
        interimText.value = ''
        if (data.text) {
          transcripts.value.push({
            time: new Date().toLocaleTimeString(),
            text: data.text,
            confidence: -1,
            source: 'server-stream:final',
          })
        }
        stopServerSttStream()
      } else if (data.type === 'error') {
        message.error('流式 STT 错误: ' + data.error)
      } else if (data.type === 'done') {
        stopServerSttStream()
      }
    } catch {}
  }
  serverSttEventSource.onerror = () => {
    stopServerSttStream()
  }
}

function stopServerSttStream() {
  if (serverSttEventSource) {
    serverSttEventSource.close()
    serverSttEventSource = null
  }
  serverSttStreaming.value = false
  interimText.value = ''
}

// ═══════════════════════════════════════════════════
// CAMERA — Unified computed views
// ═══════════════════════════════════════════════════

// --- Server camera state ---
const serverCameraDevices = ref<any[]>([])
const serverCameraDevicesLoading = ref(false)
const serverCameraDevicesLoaded = ref(false)
const serverCameraDevicesError = ref('')
const selectedServerCamIdx = ref<number>(-1)
const serverCamStreaming = ref(false)
const cameraStreamFps = ref(10)
const serverStreamSrc = ref('')

// --- Browser camera state ---
const browserCameras = ref<MediaDeviceInfo[]>([])
const selectedBrowserCamId = ref('')
const browserCamActive = ref(false)
const browserVideoRef = ref<HTMLVideoElement | null>(null)
let browserCamStream: MediaStream | null = null

// --- Snapshot (shared) ---
const snapshotUrl = ref('')
const snapshotTime = ref('')
const snapshotLoading = ref(false)
const snapshotError = ref('')
const browserSnapshotCanvas = ref<HTMLCanvasElement | null>(null)

// --- Unified camera computed ---
const cameraDeviceList = computed<DeviceItem[]>(() => {
  if (sourceMode.value === 'server') {
    return serverCameraDevices.value.map((c: any) => ({
      id: String(c.index),
      name: c.device_name || c.name || `摄像头 ${c.index}`,
      detail: `${c.path || ''} · ${c.width || '?'}x${c.height || '?'} · ${c.fps || '?'}fps`,
      statusTag: c.can_open ? '可用' : (!c.readable ? '无权限' : '无法打开'),
      statusType: (c.can_open ? 'success' : (!c.readable ? 'error' : 'warning')) as DeviceItem['statusType'],
    }))
  } else {
    return browserCameras.value.map((c) => ({
      id: c.deviceId,
      name: c.label || '摄像头 ' + c.deviceId.slice(0, 8),
      detail: '',
    }))
  }
})
const cameraDevicesLoading = computed(() =>
  sourceMode.value === 'server' ? serverCameraDevicesLoading.value : false,
)
const cameraDevicesLoaded = computed(() =>
  sourceMode.value === 'server' ? serverCameraDevicesLoaded.value : browserCameras.value.length > 0,
)
const cameraDevicesError = computed(() =>
  sourceMode.value === 'server' ? serverCameraDevicesError.value : '',
)
const cameraSelectedId = computed(() =>
  sourceMode.value === 'server' ? String(selectedServerCamIdx.value) : selectedBrowserCamId.value,
)
const cameraStreamActive = computed(() =>
  sourceMode.value === 'server' ? serverCamStreaming.value : browserCamActive.value,
)

// --- Unified camera actions ---
function cameraRefreshDevices() {
  sourceMode.value === 'server' ? loadServerCameraDevices() : refreshBrowserCameras()
}

function cameraSelectDevice(id: string) {
  if (sourceMode.value === 'server') {
    selectedServerCamIdx.value = parseInt(id)
  } else {
    selectedBrowserCamId.value = id
  }
}

function cameraToggleStream() {
  if (sourceMode.value === 'server') {
    if (serverCamStreaming.value) {
      serverCamStreaming.value = false
      serverStreamSrc.value = ''
    } else {
      const idx = selectedServerCamIdx.value >= 0 ? selectedServerCamIdx.value : 0
      serverStreamSrc.value = cameraApi.streamUrl({ device: idx, fps: cameraStreamFps.value })
      serverCamStreaming.value = true
    }
  } else {
    browserCamActive.value ? stopBrowserCamera() : startBrowserCamera()
  }
}

function cameraTakeSnapshot() {
  sourceMode.value === 'server' ? takeServerSnapshot() : takeBrowserSnapshot()
}

function downloadSnapshot() {
  if (!snapshotUrl.value) return
  const a = document.createElement('a')
  a.href = snapshotUrl.value
  a.download = 'snapshot_' + new Date().toISOString().replace(/[:.]/g, '-') + '.jpg'
  a.click()
}

// ─── Server camera impl ───
async function loadServerCameraDevices() {
  serverCameraDevicesLoading.value = true
  serverCameraDevicesError.value = ''
  try {
    const res = await cameraApi.getDevices()
    const data = res.data
    if (!data.available) {
      serverCameraDevicesError.value = data.opencv_error || 'OpenCV 不可用'
      serverCameraDevices.value = []
    } else {
      serverCameraDevices.value = data.devices || []
      if (serverCameraDevices.value.length && selectedServerCamIdx.value < 0) {
        const first = serverCameraDevices.value.find((c: any) => c.can_open)
        selectedServerCamIdx.value = first ? first.index : serverCameraDevices.value[0].index
      }
    }
    serverCameraDevicesLoaded.value = true
  } catch (e: any) {
    serverCameraDevicesError.value = e.response?.data?.detail || e.message
  } finally {
    serverCameraDevicesLoading.value = false
  }
}

async function takeServerSnapshot() {
  snapshotLoading.value = true
  snapshotError.value = ''
  try {
    const url = cameraApi.snapshotUrl({ device: selectedServerCamIdx.value >= 0 ? selectedServerCamIdx.value : 0 })
    const fullUrl = url + (url.includes('?') ? '&' : '?') + '_t=' + Date.now()
    snapshotUrl.value = fullUrl
    snapshotTime.value = new Date().toLocaleTimeString()
  } catch (e: any) {
    snapshotError.value = e.message
  } finally {
    snapshotLoading.value = false
  }
}

// ─── Browser camera impl ───
async function refreshBrowserCameras() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true })
    stream.getTracks().forEach(t => t.stop())
  } catch { return }
  const devices = await navigator.mediaDevices.enumerateDevices()
  browserCameras.value = devices.filter(d => d.kind === 'videoinput')
  if (browserCameras.value.length && !selectedBrowserCamId.value) {
    selectedBrowserCamId.value = browserCameras.value[0].deviceId
  }
}

async function startBrowserCamera() {
  try {
    const constraints: MediaStreamConstraints = {
      video: selectedBrowserCamId.value ? { deviceId: { exact: selectedBrowserCamId.value } } : true,
    }
    browserCamStream = await navigator.mediaDevices.getUserMedia(constraints)
    browserCamActive.value = true
    await nextTick()
    if (browserVideoRef.value) browserVideoRef.value.srcObject = browserCamStream
  } catch (e: any) {
    message.error('无法启动摄像头: ' + e.message)
  }
}

function stopBrowserCamera() {
  if (browserCamStream) { browserCamStream.getTracks().forEach(t => t.stop()); browserCamStream = null }
  if (browserVideoRef.value) browserVideoRef.value.srcObject = null
  browserCamActive.value = false
}

function takeBrowserSnapshot() {
  if (!browserVideoRef.value || !browserSnapshotCanvas.value) return
  const video = browserVideoRef.value
  const canvas = browserSnapshotCanvas.value
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(video, 0, 0)
  if (snapshotUrl.value) URL.revokeObjectURL(snapshotUrl.value)
  canvas.toBlob((blob) => {
    if (blob) {
      snapshotUrl.value = URL.createObjectURL(blob)
      snapshotTime.value = new Date().toLocaleTimeString()
    }
  }, 'image/jpeg', 0.9)
}

// ═══════════════════════════════════════════════════
// Lifecycle
// ═══════════════════════════════════════════════════
onMounted(() => {
  initSTT()
  // Load server STT status & models
  loadSttStatus()
  loadSttModels()
  // Auto refresh audio devices on mount if audio is active
  if (activeCategory.value === 'audio') {
    audioRefreshDevices()
  }
})

onBeforeUnmount(() => {
  releaseAllResources()
  if (browserRecordUrl.value) URL.revokeObjectURL(browserRecordUrl.value)
  if (serverRecordUrl.value) URL.revokeObjectURL(serverRecordUrl.value)
  if (snapshotUrl.value && snapshotUrl.value.startsWith('blob:')) URL.revokeObjectURL(snapshotUrl.value)
})
</script>

<style scoped>
.device-debug {
  display: flex;
  height: 100%;
  min-height: 0;
}

/* ─── Sidebar ─── */
.sidebar {
  width: 160px;
  min-width: 160px;
  background: #171717;
  border-right: 1px solid #2a2a2a;
  padding: 16px 0;
  overflow-y: auto;
  flex-shrink: 0;
}
.sidebar-title {
  font-size: 15px;
  padding: 0 16px 14px;
  color: #e0e0e0;
  border-bottom: 1px solid #2a2a2a;
  margin: 0 0 10px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  cursor: pointer;
  font-size: 14px;
  color: #bbb;
  transition: .15s;
  border-left: 3px solid transparent;
}
.menu-item:hover { background: #222; color: #e0e0e0; }
.menu-item.active {
  background: #1e1a2e;
  color: #fff;
  border-left-color: #7c6cff;
}
.menu-icon { font-size: 17px; }

/* ─── Content ─── */
.content {
  flex: 1;
  min-width: 0;
  padding: 20px 28px;
  overflow-y: auto;
  max-width: 880px;
}

/* ─── Panel header ─── */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
  gap: 16px;
}
.panel-title {
  font-size: 18px;
  margin: 0;
  color: #e0e0e0;
}

/* ─── Cards ─── */
.card {
  background: #212121;
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 14px;
}
.card h3 {
  font-size: 14px;
  margin: 0 0 10px;
  color: #ccc;
}
.card-header-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.card-header-row.clickable { cursor: pointer; }
.card-header-row h3 { flex: 1; margin-bottom: 0; }
.expand-icon { font-size: 11px; color: #888; }

/* ─── Device list ─── */
.dev-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: #2a2a2a;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 5px;
  transition: .15s;
}
.dev-item:hover { background: #333; }
.dev-item.active { outline: 1.5px solid #7c6cff; background: #2d2840; }
.dev-icon { font-size: 18px; }
.dev-info { flex: 1; font-size: 13px; }
.dev-detail { font-size: 11px; color: #888; margin-top: 2px; }

/* ─── Controls ─── */
.ctrl-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.hint { font-size: 12px; color: #888; }

/* ─── Meter ─── */
.meter {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  max-width: 260px;
}
.meter-label { font-size: 11px; color: #aaa; white-space: nowrap; }
.meter-bg { flex: 1; height: 7px; background: #333; border-radius: 4px; overflow: hidden; }
.meter-bar { height: 100%; border-radius: 4px; transition: width .08s; }
.meter-val { font-size: 11px; color: #ccc; width: 98px; text-align: right; }

/* ─── Waveform ─── */
.wave-canvas { width: 100%; height: 120px; border-radius: 8px; background: #1a1a1a; }
.status-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: #888;
}
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.off { background: #555; }
.dot.on { background: #f44336; animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: .3; } }

/* ─── STT ─── */
.live-subtitle {
  background: #1a1a1a;
  border-radius: 8px;
  padding: 10px 14px;
  min-height: 36px;
  color: #888;
  font-size: 14px;
  margin-bottom: 8px;
  border: 1px solid transparent;
  transition: .2s;
}
.live-subtitle.active { border-color: #7c6cff55; color: #e0e0e0; }
.interim-label { font-size: 10px; color: #7c6cff; margin-right: 6px; }
.transcript-list { max-height: 180px; overflow-y: auto; }
.transcript-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid #2a2a2a;
}
.ts-time { font-size: 10px; color: #666; white-space: nowrap; }
.ts-text { flex: 1; font-size: 13px; }

/* ─── Recording badge ─── */
.rec-badge { color: #f44336; font-weight: 600; font-size: 13px; animation: blink 1s infinite; }
.audio-player { width: 100%; margin-top: 6px; border-radius: 8px; }

/* ─── Results ─── */
.result-box {
  background: #1a1a1a;
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 8px;
}
.result-box.silent { border: 1px solid #ff980055; }
.result-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 12px; }
.result-row > span:first-child { color: #888; }

/* ─── Driver ─── */
.driver-grid { margin-top: 10px; }
.driver-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid #2a2a2a;
  font-size: 12px;
}
.driver-key { color: #888; min-width: 100px; }
.driver-val { color: #ccc; }
.driver-err { color: #f44336; font-size: 11px; margin-left: 6px; }
.driver-pre {
  background: #111;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 11px;
  color: #aaa;
  width: 100%;
  overflow-x: auto;
  white-space: pre;
}

/* ─── Camera ─── */
.snapshot-box { margin-top: 8px; text-align: center; }
.snapshot-img { max-width: 100%; border-radius: 8px; }
.snapshot-time { font-size: 11px; color: #888; margin-top: 4px; }
.stream-box { margin-top: 8px; }
.stream-img { width: 100%; border-radius: 8px; background: #000; }
.preview-video { width: 100%; border-radius: 8px; background: #000; margin-top: 8px; }

/* ─── Misc ─── */
.empty { color: #666; font-size: 12px; padding: 6px 0; }
.error-text { color: #f44336; font-size: 12px; }
</style>
