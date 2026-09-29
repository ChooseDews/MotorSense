<script setup>
import { computed, ref } from 'vue'
import { formatFlashAddress, inferSerialImageType, SERIAL_IMAGE_TYPES, validateSerialImage } from '../lib/serialFlash'

const emit = defineEmits(['flashing'])
const available = typeof navigator !== 'undefined' && 'serial' in navigator
const firmware = ref(null)
const imageType = ref('full')
const flashing = ref(false)
const progress = ref(0)
const status = ref('')
const error = ref('')
const log = ref([])

const address = computed(() => formatFlashAddress(imageType.value))

function addLog(value) {
  const text = String(value ?? '').trim()
  if (!text) return
  log.value = [...log.value.slice(-79), text]
}

function chooseFile(event) {
  error.value = ''
  progress.value = 0
  status.value = ''
  const selected = event.target.files?.[0] || null
  firmware.value = selected
  if (selected) imageType.value = inferSerialImageType(selected.name)
}

async function flash() {
  error.value = validateSerialImage(firmware.value, imageType.value)
  if (error.value || flashing.value) return

  const target = SERIAL_IMAGE_TYPES[imageType.value]
  const warning = imageType.value === 'full'
    ? 'This replaces the bootloader, partition table, application, and saved controller settings.'
    : 'This writes OTA slot 0 directly and may not replace the currently active OTA slot.'
  if (!window.confirm(`Flash ${firmware.value.name} at ${address.value}? ${warning} Keep the controller powered until it restarts.`)) return

  let transport
  flashing.value = true
  emit('flashing', true)
  error.value = ''
  progress.value = 0
  log.value = []

  try {
    status.value = 'Choose the controller serial port…'
    const port = await navigator.serial.requestPort()
    const { ESPLoader, Transport } = await import('esptool-js')
    transport = new Transport(port, false)
    const terminal = {
      clean() { log.value = [] },
      write(data) { addLog(data) },
      writeLine(data) { addLog(data) },
    }
    const loader = new ESPLoader({ transport, baudrate: 460800, terminal, debugLogging: false })

    status.value = 'Connecting to the controller…'
    const chip = await loader.main()
    if (!String(chip).toUpperCase().includes('ESP32-S3')) throw new Error(`Expected an ESP32-S3, but found ${chip}.`)

    status.value = `Writing ${target.label.toLowerCase()}…`
    const data = new Uint8Array(await firmware.value.arrayBuffer())
    await loader.writeFlash({
      fileArray: [{ data, address: target.address }],
      flashMode: 'keep',
      flashFreq: 'keep',
      flashSize: '2MB',
      eraseAll: false,
      compress: true,
      reportProgress(_fileIndex, written, total) {
        progress.value = total ? written / total : 0
      },
    })

    progress.value = 1
    status.value = 'Verified. Restarting the controller…'
    await loader.after('hard_reset')
    status.value = 'Firmware installed. Reconnect the controller when it has restarted.'
  } catch (caught) {
    if (caught?.name === 'NotFoundError') error.value = 'No serial port was selected.'
    else error.value = caught?.message || String(caught)
    status.value = ''
  } finally {
    try { await transport?.disconnect() } catch { /* Port may already be closed after reset. */ }
    flashing.value = false
    emit('flashing', false)
  }
}
</script>

<template>
  <details class="panel firmware-update serial-flasher">
    <summary>
      <span><span class="eyebrow">USB SERIAL</span><strong>Flash controller</strong></span>
      <span v-if="flashing">{{ Math.round(progress * 100) }}%</span>
    </summary>
    <div v-if="available" class="firmware-body serial-body">
      <label class="firmware-file">Firmware image
        <input type="file" accept=".bin,application/octet-stream" :disabled="flashing" @change="chooseFile">
      </label>
      <label>Image type
        <select v-model="imageType" :disabled="flashing">
          <option value="full">Full image · {{ formatFlashAddress('full') }}</option>
          <option value="app">App only · {{ formatFlashAddress('app') }}</option>
        </select>
      </label>
      <button class="primary" :disabled="!firmware || flashing" @click="flash">Choose port &amp; flash</button>
    </div>
    <p v-else class="inline-error">Web Serial is not available. Use desktop Chrome or Edge over HTTPS or localhost.</p>
    <progress v-if="flashing || progress" :value="progress" max="1"></progress>
    <p v-if="status" class="firmware-status">{{ status }}</p>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <p class="help">Use a merged <code>flash-all.bin</code> at <code>0x0</code> for recovery. App-only images write to <code>0x10000</code> and preserve settings, but may not change the active OTA slot.</p>
    <details v-if="log.length" class="flash-log"><summary>Flasher log</summary><pre>{{ log.join('\n') }}</pre></details>
  </details>
</template>
