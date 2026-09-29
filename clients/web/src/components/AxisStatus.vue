<script setup>
import { computed } from 'vue'
import { useMountStore } from '../stores/ble'

const props = defineProps({ name: { type: String, required: true } })
const store = useMountStore()
const axis = computed(() => store.axes[props.name])
const target = computed(() => store.target[props.name])
const stale = computed(() => axis.value.connected && store.telemetryClock - axis.value.lastNotification >= 5000)
const stateClass = computed(() => axis.value.error && axis.value.error !== 'Disconnected' ? 'bad' : stale.value ? 'warn' : axis.value.connected ? 'good' : 'muted')

async function connect() {
  try { await store.requestAxis(props.name) } catch { /* surfaced by store */ }
}
</script>

<template>
  <article class="axis-card">
    <div class="axis-heading">
      <div>
        <span class="eyebrow">{{ name === 'yaw' ? 'AZIMUTH' : 'ALTITUDE' }}</span>
        <h2>{{ name }}</h2>
      </div>
      <span class="connection" :class="stateClass">
        <i></i>{{ axis.connecting ? 'Connecting' : stale ? 'No telemetry' : axis.connected ? 'Online' : 'Offline' }}
      </span>
    </div>
    <div class="angle">{{ axis.angle.toFixed(3) }}<small>°</small></div>
    <div class="axis-metrics">
      <div><span>Target</span><strong>{{ target === null ? '—' : `${Number(target).toFixed(3)}°` }}</strong></div>
      <div><span>Encoder</span><strong>{{ axis.hasReading ? axis.ticks.toLocaleString() : '—' }}</strong></div>
      <div><span>Motor</span><strong>{{ axis.motionState }} · {{ axis.duty }}%</strong></div>
    </div>
    <p v-if="axis.error && axis.error !== 'Disconnected'" class="axis-error">{{ axis.error }}</p>
    <div class="axis-actions">
      <button v-if="!axis.connected" class="secondary" :disabled="axis.connecting" @click="connect">
        {{ axis.connecting ? 'Connecting…' : `Choose ${name} controller` }}
      </button>
      <template v-else>
        <span class="device-name">{{ axis.device?.name || 'MotorSense' }}</span>
        <button class="ghost compact" :disabled="axis.otaUploading" @click="store.disconnectAxis(name)">Disconnect</button>
      </template>
    </div>
  </article>
</template>
