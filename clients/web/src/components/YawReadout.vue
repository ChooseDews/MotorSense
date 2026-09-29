<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  position: { type: Number, required: true },
  motorPosition: { type: Number, required: true },
  connected: Boolean,
  hasReading: Boolean,
  inverted: Boolean,
  motorInverted: Boolean
})
// 23.95° outbound calibration, independently checked on the return sweep.
// Applies only to this yaw board with the DMA/Schmitt firmware, 2026-09-17.
const ticksPerDegree = ref(32.88339799503493)
const motorTicksPerDegree = ref(4544.896704947489)
const zero = ref(null)
const validScale = computed(() => [ticksPerDegree.value, motorTicksPerDegree.value]
  .every(value => Number.isFinite(Number(value)) && Number(value) > 0))
const angles = computed(() => zero.value === null || !validScale.value ? null : [
  (props.position - zero.value[0]) / Number(ticksPerDegree.value) * (props.inverted ? -1 : 1),
  (props.motorPosition - zero.value[1]) / Number(motorTicksPerDegree.value) * (props.motorInverted ? 1 : -1)
])
watch(() => [props.connected, props.inverted, props.motorInverted], () => { zero.value = null })
</script>

<template>
  <details class="yaw-readout">
    <summary>Yaw degree readouts · encoders 0 and 1</summary>
    <p>Yaw board with the updated firmware only. Set zero while stopped; reconnecting or changing direction inversion clears the reference.</p>
    <label>Encoder 0 ticks per degree
      <input type="number" min="0.001" step="0.001" v-model.number="ticksPerDegree" aria-label="Encoder 0 yaw ticks per degree">
    </label>
    <label>Encoder 1 ticks per degree
      <input type="number" min="0.001" step="0.001" v-model.number="motorTicksPerDegree" aria-label="Encoder 1 yaw ticks per degree">
    </label>
    <button :disabled="!connected || !hasReading || !validScale" @click="zero = [position, motorPosition]">Set both yaw zeros here</button>
    <div v-if="connected && hasReading && angles" aria-live="polite">
      <output>Encoder 0: {{ angles[0].toFixed(2) }}° relative yaw</output>
      <output>Encoder 1: {{ angles[1].toFixed(2) }}° relative yaw</output>
    </div>
    <output v-else>Set zero to show relative yaw</output>
    <small>Calibrated over 24° against vision. Typical stable-reference return agreement was about 0.2°; displayed precision is not absolute accuracy.</small>
  </details>
</template>

<style scoped>
.yaw-readout { margin-top: 1rem; }
summary { cursor: pointer; }
p, small { color: var(--color-text-muted); font-size: .85rem; }
label { display: inline-flex; gap: .5rem; align-items: center; margin: .5rem .5rem .5rem 0; }
input { width: 8rem; }
output { display: block; margin: .7rem 0; font-size: 1.25rem; font-variant-numeric: tabular-nums; }
small { display: block; }
</style>
