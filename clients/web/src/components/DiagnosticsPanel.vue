<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useMountStore } from '../stores/ble'

const store = useMountStore()
const selected = ref('yaw')
const command = ref('')
const logBox = ref(null)
const axis = computed(() => store.axes[selected.value])
const logText = computed(() => axis.value.logs.map(row => `${new Date(row.timestamp).toLocaleTimeString()} ${row.direction === 'out' ? '→' : '←'} ${row.message}`).join('\n'))

watch(logText, async () => { await nextTick(); if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })

async function send(value = command.value) {
  if (!value.trim()) return
  try { await store.sendAxis(selected.value, value.trim()) } catch (error) { store.addEvent(error.message, 'error') }
  command.value = ''
}

function downloadHistory() {
  const header = ['timestamp', 'axis', 'device', 'ticks', 'angle_degrees', 'motion_state', 'duty_percent']
  const rows = store.history.map(row => [new Date(row.timestamp).toISOString(), row.axis, `"${row.deviceName}"`, row.ticks, row.angle, row.motionState, row.duty])
  const url = URL.createObjectURL(new Blob([[header, ...rows].map(row => row.join(',')).join('\n')], { type: 'text/csv' }))
  const link = Object.assign(document.createElement('a'), { href: url, download: `MotorSense-telemetry-${Date.now()}.csv` })
  link.click(); URL.revokeObjectURL(url)
}
</script>

<template>
  <details class="panel diagnostics">
    <summary><span><span class="eyebrow">DIAGNOSTICS</span><strong>Raw board console & telemetry</strong></span><span>{{ store.history.length.toLocaleString() }} samples</span></summary>
    <div class="diagnostic-toolbar">
      <div class="segmented"><button :class="{ active: selected === 'yaw' }" @click="selected = 'yaw'">Yaw</button><button :class="{ active: selected === 'pitch' }" @click="selected = 'pitch'">Pitch</button></div>
      <button v-for="preset in ['PING', 'STATUS', 'CALIBRATION', 'HELP']" :key="preset" class="ghost compact" :disabled="!axis.connected" @click="send(preset)">{{ preset }}</button>
      <button class="ghost compact" :disabled="!store.history.length" @click="downloadHistory">Export telemetry CSV</button>
      <button class="ghost compact" :disabled="!store.history.length" @click="store.history.splice(0)">Clear samples</button>
    </div>
    <div class="terminal-command"><input v-model="command" :disabled="!axis.connected" placeholder="Send a firmware command" @keyup.enter="send()"><button :disabled="!axis.connected || !command.trim()" @click="send()">Send</button></div>
    <textarea ref="logBox" readonly :value="logText" class="terminal"></textarea>
    <div class="event-log"><h3>Mount events</h3><p v-if="!store.events.length">No events yet.</p><p v-for="event in [...store.events].reverse().slice(0, 30)" :key="event.timestamp" :class="event.level"><time>{{ new Date(event.timestamp).toLocaleTimeString() }}</time>{{ event.message }}</p></div>
  </details>
</template>
