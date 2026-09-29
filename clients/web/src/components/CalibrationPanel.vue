<script setup>
import { reactive, ref } from 'vue'
import { useMountStore } from '../stores/ble'
import ExternalCalibration from './ExternalCalibration.vue'

const store = useMountStore()
const current = reactive({ yaw: 0, pitch: 0 })
const importInput = ref(null)
const message = ref('Changes are saved automatically in this browser.')

function setMount() {
  try { store.setCurrentMount(current.yaw, current.pitch); message.value = 'Current encoder position saved as the entered mount angles.' }
  catch (error) { message.value = error.message }
}

function exportConfig() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(store.configuration, null, 2)], { type: 'application/json' }))
  const link = Object.assign(document.createElement('a'), { href: url, download: 'MotorSense-mount-configuration.json' })
  link.click(); URL.revokeObjectURL(url)
}

async function importConfig(event) {
  try {
    const saved = JSON.parse(await event.target.files[0].text())
    if (!saved.location || !saved.calibration || !saved.mountModel) throw new Error('Not a MotorSense mount configuration')
    Object.assign(store.configuration.location, saved.location)
    Object.assign(store.configuration.calibration.yaw, saved.calibration.yaw)
    Object.assign(store.configuration.calibration.pitch, saved.calibration.pitch)
    store.configuration.mountModel.matrix = saved.mountModel.matrix
    store.configuration.mountModel.offset = saved.mountModel.offset
    store.persistConfiguration(); message.value = 'Configuration imported.'
  } catch (error) { message.value = `Import failed: ${error.message}` }
  event.target.value = ''
}
</script>

<template>
  <section class="panel">
    <div class="section-heading"><div><span class="eyebrow">CALIBRATION</span><h2>Mount model & references</h2></div><div class="button-row"><button class="ghost compact" @click="exportConfig">Export</button><button class="ghost compact" @click="importInput.click()">Import</button><input ref="importInput" hidden type="file" accept="application/json,.json" @change="importConfig"></div></div>
    <div class="calibration-grid">
      <div v-for="name in ['yaw', 'pitch']" :key="name" class="calibration-card">
        <h3>{{ name }} encoder</h3>
        <label>Degrees per tick <input v-model.number="store.configuration.calibration[name].degreesPerTick" type="number" step="0.000000001"></label>
        <label>Reference ticks <input v-model.number="store.configuration.calibration[name].referenceTicks" type="number" step="1"></label>
        <label>Reference degrees <input v-model.number="store.configuration.calibration[name].referenceDegrees" type="number" step="0.001"></label>
        <label>Default max duty <span><input v-model.number="store.configuration.calibration[name].maxDuty" type="number" min="10" max="100">%</span></label>
        <small>Board: {{ store.axes[name].boardCountsRev || '—' }} counts/rev · zero {{ store.axes[name].boardZeroDeg ?? '—' }}°</small>
      </div>
      <div class="calibration-card mount-model">
        <h3>Alt/Az → pitch/yaw affine model</h3>
        <div class="matrix">
          <input v-model.number="store.configuration.mountModel.matrix[0][0]" type="number" step="0.000001"><input v-model.number="store.configuration.mountModel.matrix[0][1]" type="number" step="0.000001"><span>+ {{ store.configuration.mountModel.offset[0] }}</span>
          <input v-model.number="store.configuration.mountModel.matrix[1][0]" type="number" step="0.000001"><input v-model.number="store.configuration.mountModel.matrix[1][1]" type="number" step="0.000001"><span>+ {{ store.configuration.mountModel.offset[1] }}</span>
        </div>
        <label>Pitch offset <input v-model.number="store.configuration.mountModel.offset[0]" type="number" step="0.001"></label>
        <label>Yaw offset <input v-model.number="store.configuration.mountModel.offset[1]" type="number" step="0.001"></label>
      </div>
    </div>
    <div class="set-reference">
      <strong>Declare current physical position</strong>
      <label>Yaw <span><input v-model.number="current.yaw" type="number" step="0.001">°</span></label>
      <label>Pitch <span><input v-model.number="current.pitch" type="number" step="0.001">°</span></label>
      <button :disabled="!store.axes.yaw.hasReading || !store.axes.pitch.hasReading" @click="setMount">Set current mount</button>
    </div>
    <p class="help">{{ message }}</p>
    <ExternalCalibration />
  </section>
</template>
