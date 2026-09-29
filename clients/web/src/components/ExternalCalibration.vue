<script setup>
import { computed, reactive, ref } from 'vue'
import { shortestDelta, wrapDegrees } from '../lib/mountMath'
import { useMountStore } from '../stores/ble'

const store = useMountStore()
const settings = reactive({ mode: 'Yaw', step: 2, points: 5, duty: 35, settle: 2, returnHome: true })
const fileHandle = ref(null)
const fallbackFile = ref(null)
const fileInput = ref(null)
const reference = ref(null)
const results = reactive([])
const status = ref('Choose the camera tracker CSV, then capture a reference.')
const running = ref(false)
let aborted = false

const canUseFilePicker = computed(() => typeof window.showOpenFilePicker === 'function')
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

async function chooseCsv() {
  if (!canUseFilePicker.value) { fileInput.value.click(); return }
  try {
    const [handle] = await window.showOpenFilePicker({ types: [{ description: 'Camera tracker CSV', accept: { 'text/csv': ['.csv'] } }] })
    fileHandle.value = handle
    fallbackFile.value = null
    status.value = `Camera source: ${handle.name}`
  } catch (error) {
    if (error.name !== 'AbortError') status.value = error.message
  }
}

function useFallback(event) {
  fallbackFile.value = event.target.files?.[0] || null
  if (fallbackFile.value) status.value = `Camera snapshot: ${fallbackFile.value.name}. Re-select it to read later updates.`
}

function parseCsv(text, modified) {
  const lines = text.trim().split(/\r?\n/)
  if (lines.length < 4) throw new Error('Camera CSV needs at least three samples')
  const headers = lines[0].split(',').map(value => value.trim())
  const yawIndex = headers.indexOf('yaw'); const pitchIndex = headers.indexOf('pitch'); const timeIndex = headers.indexOf('datetime')
  if (yawIndex < 0 || pitchIndex < 0) throw new Error('Camera CSV must contain yaw and pitch columns')
  const rows = lines.slice(1).map(line => line.split(',')).map(columns => ({
    yaw: Number(columns[yawIndex]), pitch: Number(columns[pitchIndex]), stamp: columns[timeIndex] || String(modified),
  })).filter(row => Number.isFinite(row.yaw) && Number.isFinite(row.pitch))
  if (rows.length < 3) throw new Error('Camera CSV needs at least three finite samples')
  const recent = rows.slice(-3)
  const median = values => [...values].sort((a, b) => a - b)[1]
  return { yaw: median(recent.map(row => row.yaw)), pitch: median(recent.map(row => row.pitch)), stamp: recent.at(-1).stamp, modified }
}

async function camera() {
  const file = fileHandle.value ? await fileHandle.value.getFile() : fallbackFile.value
  if (!file) throw new Error('Choose the camera tracker CSV first')
  if (Date.now() - file.lastModified > 5000 && fileHandle.value) throw new Error('Camera CSV is stale; check the tracker')
  return parseCsv(await file.text(), file.lastModified)
}

async function freshCamera(previous = null) {
  const deadline = Date.now() + 8000
  let lastError = 'Camera CSV did not update'
  while (Date.now() < deadline) {
    try {
      const reading = await camera()
      if (previous === null || reading.stamp !== previous) return reading
      lastError = 'Waiting for a fresh camera row'
    } catch (error) { lastError = error.message }
    await sleep(250)
  }
  throw new Error(lastError)
}

async function captureReference() {
  try {
    const cv = await freshCamera()
    reference.value = { encoderYaw: store.axes.yaw.angle, encoderPitch: store.axes.pitch.angle, cvYaw: cv.yaw, cvPitch: cv.pitch, stamp: cv.stamp }
    status.value = `Reference captured · encoders ${store.axes.yaw.angle.toFixed(3)}°, ${store.axes.pitch.angle.toFixed(3)}° · camera ${cv.yaw.toFixed(3)}°, ${cv.pitch.toFixed(3)}°`
    return true
  } catch (error) { status.value = `Reference failed: ${error.message}`; return false }
}

async function waitSettled(targetYaw, targetPitch) {
  const deadline = Date.now() + 45000
  let stableSince = null
  while (Date.now() < deadline) {
    if (aborted) throw new Error('Sweep stopped')
    const ready = Math.abs(shortestDelta(store.axes.yaw.angle, targetYaw)) <= 0.15 && Math.abs(store.axes.pitch.angle - targetPitch) <= 0.15
    if (ready) {
      stableSince ||= Date.now()
      if (Date.now() - stableSince >= settings.settle * 1000) return
    } else stableSince = null
    await sleep(150)
  }
  throw new Error('Timed out waiting for the mount to settle')
}

async function collect(point, targetYaw, targetPitch, returning = false) {
  status.value = `${returning ? 'Return' : `Point ${point}/${settings.points}`}: moving and settling…`
  await store.gotoMount(targetYaw, targetPitch, settings.duty)
  await waitSettled(targetYaw, targetPitch)
  const settled = await camera()
  const cv = await freshCamera(settled.stamp)
  const base = reference.value
  const yawActive = settings.mode !== 'Pitch'
  const pitchActive = settings.mode !== 'Yaw'
  const encoderYaw = yawActive ? shortestDelta(base.encoderYaw, store.axes.yaw.angle) : null
  const encoderPitch = pitchActive ? store.axes.pitch.angle - base.encoderPitch : null
  const cvYaw = yawActive ? shortestDelta(base.cvYaw, cv.yaw) : null
  const cvPitch = pitchActive ? cv.pitch - base.cvPitch : null
  results.push({ point: returning ? 'return' : point, mode: settings.mode, timestamp: cv.stamp,
    encoderYaw, cvYaw, yawError: encoderYaw === null ? null : encoderYaw - cvYaw,
    encoderPitch, cvPitch, pitchError: encoderPitch === null ? null : encoderPitch - cvPitch })
}

async function runSweep() {
  if (running.value) return
  running.value = true; aborted = false; results.splice(0)
  try {
    if (!store.mountConnected) throw new Error('Connect both controllers first')
    if (!reference.value && !await captureReference()) return
    if (settings.mode !== 'Pitch' && settings.step * settings.points > 150) throw new Error('Keep yaw sweeps at 150° or less')
    const base = reference.value
    for (let point = 1; point <= settings.points; point += 1) {
      await collect(point,
        wrapDegrees(base.encoderYaw + (settings.mode !== 'Pitch' ? point * settings.step : 0)),
        base.encoderPitch + (settings.mode !== 'Yaw' ? point * settings.step : 0))
    }
    if (settings.returnHome) await collect(settings.points + 1, base.encoderYaw, base.encoderPitch, true)
    const metric = (key, label) => {
      const errors = results.map(row => row[key]).filter(value => value !== null)
      if (!errors.length) return null
      const rms = Math.sqrt(errors.reduce((sum, value) => sum + value * value, 0) / errors.length)
      return `${label} RMS ${rms.toFixed(3)}°, max ${Math.max(...errors.map(Math.abs)).toFixed(3)}°`
    }
    status.value = `Relative camera agreement · ${[metric('yawError', 'yaw'), metric('pitchError', 'pitch')].filter(Boolean).join(' · ')}`
  } catch (error) { status.value = `Sweep failed: ${error.message}` }
  finally { running.value = false }
}

async function abort() {
  aborted = true
  await store.stop()
  status.value = 'Sweep stopped; motors commanded to stop.'
}

function exportCsv() {
  if (!results.length) return
  const keys = Object.keys(results[0])
  const csv = [keys.join(','), ...results.map(row => keys.map(key => row[key] ?? '').join(','))].join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  const link = Object.assign(document.createElement('a'), { href: url, download: `MotorSense-external-calibration-${Date.now()}.csv` })
  link.click(); URL.revokeObjectURL(url)
}
</script>

<template>
  <details class="advanced calibration-sweep">
    <summary>External camera comparison sweep</summary>
    <p class="help">Compares settled encoder displacement with the rolling <code>yaw</code>/<code>pitch</code> camera CSV. Chromium’s file handle can reread live updates; the fallback upload is a snapshot.</p>
    <input ref="fileInput" hidden type="file" accept=".csv,text/csv" @change="useFallback">
    <div class="button-row"><button @click="chooseCsv">Choose camera CSV</button><button @click="captureReference">Capture reference</button></div>
    <div class="sweep-settings">
      <label>Sweep <select v-model="settings.mode"><option>Yaw</option><option>Pitch</option><option>Yaw + Pitch</option></select></label>
      <label>Step <span><input v-model.number="settings.step" type="number" min="0.1" max="30" step="0.1">°</span></label>
      <label>Points <input v-model.number="settings.points" type="number" min="1" max="20"></label>
      <label>Duty <span><input v-model.number="settings.duty" type="number" min="10" max="100">%</span></label>
      <label>Settle <span><input v-model.number="settings.settle" type="number" min="0.5" max="15" step="0.5">s</span></label>
      <label class="check"><input v-model="settings.returnHome" type="checkbox"> Return to reference</label>
    </div>
    <div class="button-row"><button class="primary" :disabled="running" @click="runSweep">{{ running ? 'Sweep running…' : 'Run sweep' }}</button><button class="danger-outline" @click="abort">STOP</button><button :disabled="!results.length" @click="exportCsv">Export results</button></div>
    <p class="sweep-status">{{ status }}</p>
    <div v-if="results.length" class="table-wrap"><table><thead><tr><th>Point</th><th>Enc yaw Δ</th><th>CV yaw Δ</th><th>Yaw error</th><th>Enc pitch Δ</th><th>CV pitch Δ</th><th>Pitch error</th></tr></thead><tbody><tr v-for="row in results" :key="row.point"><td>{{ row.point }}</td><td v-for="key in ['encoderYaw','cvYaw','yawError','encoderPitch','cvPitch','pitchError']" :key="key">{{ row[key] === null ? '—' : `${row[key].toFixed(3)}°` }}</td></tr></tbody></table></div>
  </details>
</template>
