<script setup>
import { reactive, ref, watch } from 'vue'
import { useMountStore } from '../stores/ble'

const store = useMountStore()
const form = reactive({ yaw: 0, pitch: 0, duty: 70 })
const busy = ref(false)
const error = ref('')
const steps = [-15, -5, -1, -0.25, 0.25, 1, 5, 15]

watch(() => [store.axes.yaw.angle, store.axes.pitch.angle], ([yaw, pitch]) => {
  if (!document.activeElement?.matches('input')) { form.yaw = yaw; form.pitch = pitch }
}, { immediate: true })

async function run(action) {
  busy.value = true
  error.value = ''
  try { await action() } catch (reason) { error.value = reason.message; store.addEvent(reason.message, 'error') }
  finally { busy.value = false }
}
</script>

<template>
  <section class="panel control-panel">
    <div class="section-heading">
      <div><span class="eyebrow">MOUNT CONTROL</span><h2>Point the telescope</h2></div>
      <span class="last-command">{{ store.lastCommand }}</span>
    </div>

    <div class="target-row">
      <label>Yaw target <span><input v-model.number="form.yaw" type="number" min="0" max="360" step="0.001">°</span></label>
      <label>Pitch target <span><input v-model.number="form.pitch" type="number" min="-90" max="90" step="0.001">°</span></label>
      <label>Max duty <span><input v-model.number="form.duty" type="number" min="10" max="100">%</span></label>
      <button class="primary" :disabled="busy || !store.mountConnected" @click="run(() => store.gotoMount(form.yaw, form.pitch, form.duty))">Slew to target</button>
    </div>

    <div class="jog-grid">
      <span class="jog-label">Yaw</span>
      <button v-for="step in steps" :key="`yaw-${step}`" class="jog" :disabled="busy || !store.mountConnected" @click="run(() => store.moveRelative(step, 0, form.duty))">{{ step > 0 ? '+' : '' }}{{ step }}°</button>
      <span class="jog-label">Pitch</span>
      <button v-for="step in steps" :key="`pitch-${step}`" class="jog" :disabled="busy || !store.mountConnected" @click="run(() => store.moveRelative(0, step, form.duty))">{{ step > 0 ? '+' : '' }}{{ step }}°</button>
    </div>

    <div class="command-bar">
      <button class="danger" @click="run(store.stop)">STOP</button>
      <button :disabled="busy || !store.mountConnected" @click="run(store.home)">Home 0° / 0°</button>
      <button :disabled="busy || !store.mountConnected" @click="run(store.park)">Park</button>
      <button :disabled="busy || (!store.axes.yaw.connected && !store.axes.pitch.connected)" @click="run(store.zeroAxes)">Zero axes</button>
    </div>
    <p v-if="error" class="inline-error">{{ error }}</p>
  </section>
</template>
