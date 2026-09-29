<script setup>
import { computed, ref, watch } from 'vue'
import { useMountStore } from '../stores/ble'

const store = useMountStore()
const selectedAxis = ref(store.axes.yaw.connected ? 'yaw' : 'pitch')
const firmware = ref(null)
const localError = ref('')

const axis = computed(() => store.axes[selectedAxis.value])
const connectedAxes = computed(() => ['yaw', 'pitch'].filter(name => store.axes[name].connected))

watch(connectedAxes, names => {
  if (!names.includes(selectedAxis.value) && names.length) selectedAxis.value = names[0]
})

function chooseFile(event) {
  localError.value = ''
  if (!axis.value.otaUploading) {
    axis.value.otaProgress = 0
    axis.value.otaStatus = ''
  }
  const file = event.target.files?.[0] || null
  if (file && /(?:flash-all|merged)/i.test(file.name)) {
    firmware.value = null
    localError.value = 'Choose the application image, not a merged or flash-all image.'
    event.target.value = ''
    return
  }
  firmware.value = file
}

async function install() {
  if (!firmware.value || axis.value.otaUploading) return
  const confirmed = window.confirm(`Install ${firmware.value.name} on the ${selectedAxis.value} controller? The motor will stop and the controller will reboot.`)
  if (!confirmed) return
  localError.value = ''
  try { await store.uploadFirmware(selectedAxis.value, firmware.value) }
  catch (error) { localError.value = error.message }
}
</script>

<template>
  <details class="panel firmware-update">
    <summary>
      <span><span class="eyebrow">FIRMWARE</span><strong>Update controller</strong></span>
      <span v-if="axis.otaUploading">{{ Math.round(axis.otaProgress * 100) }}%</span>
    </summary>
    <div class="firmware-body">
      <label>Controller
        <select v-model="selectedAxis" :disabled="axis.otaUploading">
          <option v-for="name in connectedAxes" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <label class="firmware-file">Application image
        <input type="file" accept=".bin,application/octet-stream" :disabled="axis.otaUploading" @change="chooseFile">
      </label>
      <button class="primary" :disabled="!firmware || axis.otaUploading" @click="install">Install firmware</button>
      <button v-if="axis.otaUploading" class="danger-outline" @click="store.cancelFirmwareUpdate(selectedAxis)">Cancel</button>
    </div>
    <progress v-if="axis.otaUploading || axis.otaProgress" :value="axis.otaProgress" max="1"></progress>
    <p v-if="axis.otaStatus" class="firmware-status">{{ axis.otaStatus }}</p>
    <p v-if="localError" class="inline-error">{{ localError }}</p>
    <p class="help">Use the MotorSense application <code>.bin</code>, not a merged flash-all image. Keep the page open and the controller powered until verification finishes.</p>
  </details>
</template>
