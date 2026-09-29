import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { altAzForObject, altAzForRaDec } from '../lib/astronomy.js'
import { AXIS_DEVICE_SUFFIX, axisForDeviceName } from '../lib/deviceRoles.js'
import { angleFromTicks, mountFromAltAz, shortestDelta, splitMove, wrapDegrees } from '../lib/mountMath.js'
import { OTA_FRAME_PAYLOAD_BYTES, OTA_MAX_IMAGE_BYTES, OTA_WINDOW_FRAMES, buildOtaFrame, bytesToHex } from '../lib/ota.js'

const NUS_SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
const NUS_RX_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
const NUS_TX_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
const CONFIG_KEY = 'motor-sense.mount.v2'
const IDLE_STATES = new Set(['IDLE', 'DONE', 'CANCELLED'])
const FAULT_STATES = new Set(['STALL', 'TIMEOUT', 'SENSOR_FAULT', 'WRONG_DIRECTION'])
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

const defaults = {
  location: { latitude: 37.3349, longitude: -122.0090, elevationM: 0 },
  calibration: {
    yaw: { degreesPerTick: 0.03041048252, referenceTicks: 0, referenceDegrees: 0, maxDuty: 80 },
    pitch: { degreesPerTick: 0.030361306325309, referenceTicks: 0, referenceDegrees: 0, maxDuty: 80 },
  },
  mountModel: { matrix: [[1, 0], [0, 1]], offset: [0, 0] },
}

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function loadConfig() {
  const fallback = clone(defaults)
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIG_KEY) || 'null')
    if (!saved) return fallback
    return {
      location: { ...fallback.location, ...saved.location },
      calibration: {
        yaw: { ...fallback.calibration.yaw, ...saved.calibration?.yaw },
        pitch: { ...fallback.calibration.pitch, ...saved.calibration?.pitch },
      },
      mountModel: saved.mountModel || fallback.mountModel,
    }
  } catch {
    return fallback
  }
}

function makeAxis(name, calibration) {
  return reactive({
    name, calibration, device: null, server: null, rx: null, tx: null,
    connected: false, connecting: false, ticks: 0, angle: 0, hasReading: false,
    motionState: 'IDLE', targetTicks: null, duty: 0, corrections: 0,
    boardCountsRev: null, boardMinDeg: null, boardMaxDeg: null, boardZeroDeg: null,
    lastNotification: 0, error: null, buffer: '', logs: [], writeChain: Promise.resolve(), waiters: [],
    otaUploading: false, otaProgress: 0, otaStatus: '', otaCancelRequested: false,
  })
}

export const useMountStore = defineStore('mount', () => {
  const configuration = reactive(loadConfig())
  const axes = reactive({
    yaw: makeAxis('yaw', configuration.calibration.yaw),
    pitch: makeAxis('pitch', configuration.calibration.pitch),
  })
  const history = reactive([])
  const events = reactive([])
  const globalError = ref(null)
  const target = reactive({ yaw: null, pitch: null, raHours: null, decDegrees: null, object: null })
  const tracking = ref(false)
  const lastCommand = ref('No mount command sent')
  const telemetryClock = ref(Date.now())
  const autoSelecting = ref(false)
  let pollTimer = null
  let trackingTimer = null

  const webBluetoothAvailable = computed(() => typeof navigator !== 'undefined' && Boolean(navigator.bluetooth))
  const mountConnected = computed(() => axes.yaw.connected && axes.pitch.connected)
  const telemetryStale = computed(() => Object.values(axes).some(axis => axis.connected && telemetryClock.value - axis.lastNotification >= 5000))
  const mountHealthy = computed(() => mountConnected.value && !telemetryStale.value)

  function persistConfiguration() {
    try { localStorage.setItem(CONFIG_KEY, JSON.stringify(configuration)) } catch { /* private mode */ }
  }
  watch(configuration, persistConfiguration, { deep: true })

  function addEvent(message, level = 'info') {
    events.push({ timestamp: Date.now(), message, level })
    if (events.length > 300) events.shift()
  }

  function logAxis(axis, message, direction = '') {
    axis.logs.push({ timestamp: Date.now(), message, direction })
    if (axis.logs.length > 600) axis.logs.shift()
  }

  function updateTicks(axis, ticks) {
    axis.ticks = Number(ticks)
    axis.angle = angleFromTicks(axis.calibration, axis.ticks)
    axis.hasReading = true
    axis.lastNotification = Date.now()
    axis.error = null
  }

  function parseLine(axis, line) {
    if (!line) return
    logAxis(axis, line, 'in')
    axis.lastNotification = Date.now()
    const encoder = /(?:\bENC\s+pos0=|\bENCODER0:.*?\bpos=|quad_enc: enc=0 pos=)(-?\d+)/.exec(line)
    if (encoder) updateTicks(axis, encoder[1])
    const status = /AXIS: state=(\w+)(?: start=-?\d+)? target=(-?\d+) position=(-?\d+) duty=(-?\d+) corrections=(\d+)/.exec(line)
    if (status) {
      axis.motionState = status[1]
      axis.targetTicks = Number(status[2])
      updateTicks(axis, status[3])
      axis.duty = Number(status[4])
      axis.corrections = Number(status[5])
    }
    const calibration = /CALIBRATION counts_rev=(\d+) counts_degree=\S+ degrees_count=\S+ min=(-?[\d.eE+-]+) max=(-?[\d.eE+-]+) zero=(-?[\d.eE+-]+)/.exec(line)
    if (calibration) {
      axis.boardCountsRev = Number(calibration[1])
      axis.boardMinDeg = Number(calibration[2])
      axis.boardMaxDeg = Number(calibration[3])
      axis.boardZeroDeg = Number(calibration[4])
    }
    if (line.startsWith('ERR')) {
      axis.error = line
      addEvent(`${axis.name}: ${line}`, 'error')
    }
    for (const waiter of [...axis.waiters]) {
      if (line.startsWith('ERR')) waiter.reject(new Error(line))
      else if (waiter.predicate(line)) waiter.resolve(line)
    }
    if (encoder || status) {
      history.push({ timestamp: Date.now(), axis: axis.name, deviceName: axis.device?.name || axis.name,
        ticks: axis.ticks, angle: axis.angle, motionState: axis.motionState, duty: axis.duty })
      if (history.length > 20000) history.splice(0, 1000)
    }
  }

  function handleNotification(axis, event) {
    axis.buffer += new TextDecoder().decode(event.target.value)
    while (axis.buffer.includes('\n')) {
      const index = axis.buffer.indexOf('\n')
      const line = axis.buffer.slice(0, index).replace(/\r$/, '')
      axis.buffer = axis.buffer.slice(index + 1)
      parseLine(axis, line)
    }
  }

  function handleDisconnect(axis) {
    for (const waiter of [...axis.waiters]) waiter.reject(new Error(`${axis.name} disconnected`))
    axis.connected = false
    axis.connecting = false
    axis.server = axis.rx = axis.tx = null
    axis.error = 'Disconnected'
    addEvent(`${axis.name} controller disconnected`, 'warning')
  }

  function validateAxisDevice(name, device) {
    const expectedSuffix = AXIS_DEVICE_SUFFIX[name]
    if (axisForDeviceName(device.name) !== name) {
      throw new Error(`Choose the ${name} controller whose name ends in ${expectedSuffix}`)
    }
    const other = name === 'yaw' ? axes.pitch : axes.yaw
    if (other.device?.id === device.id) throw new Error(`That controller is already assigned to ${other.name}`)
  }

  async function connectDevice(name, device) {
    const axis = axes[name]
    if (!axis) throw new Error(`Unknown axis: ${name}`)
    validateAxisDevice(name, device)
    axis.connecting = true
    axis.error = null
    try {
      await disconnectAxis(name, false)
      axis.device = device
      device.addEventListener('gattserverdisconnected', () => handleDisconnect(axis), { once: true })
      const server = await device.gatt.connect()
      const service = await server.getPrimaryService(NUS_SERVICE_UUID)
      axis.rx = await service.getCharacteristic(NUS_RX_UUID)
      axis.tx = await service.getCharacteristic(NUS_TX_UUID)
      axis.server = server
      await axis.tx.startNotifications()
      axis.tx.addEventListener('characteristicvaluechanged', event => handleNotification(axis, event))
      axis.connected = true
      axis.error = null
      axis.lastNotification = Date.now()
      addEvent(`${name} connected to ${device.name || 'MotorSense'}`)
      await sendAxis(name, 'STATUS', { quiet: true })
      await sendAxis(name, 'CALIBRATION', { quiet: true })
      await sendAxis(name, 'AXIS STATUS', { quiet: true })
      ensurePolling()
    } catch (error) {
      axis.error = error.message
      addEvent(`${name} connection failed: ${error.message}`, 'error')
      throw error
    } finally {
      axis.connecting = false
    }
  }

  async function requestAxis(name) {
    if (!axes[name]) throw new Error(`Unknown axis: ${name}`)
    if (!webBluetoothAvailable.value) throw new Error('Web Bluetooth is unavailable. Use Chrome or Edge on localhost/HTTPS.')
    globalError.value = null
    try {
      const device = await navigator.bluetooth.requestDevice({ filters: [{ namePrefix: 'MotorSense' }], optionalServices: [NUS_SERVICE_UUID] })
      await connectDevice(name, device)
    } catch (error) {
      axes[name].error = error.message
      globalError.value = error.message
      throw error
    }
  }

  async function autoConnectKnownDevices() {
    if (!webBluetoothAvailable.value || typeof navigator.bluetooth.getDevices !== 'function') return
    autoSelecting.value = true
    try {
      const devices = await navigator.bluetooth.getDevices()
      const selections = {
        yaw: devices.find(device => axisForDeviceName(device.name) === 'yaw'),
        pitch: devices.find(device => axisForDeviceName(device.name) === 'pitch'),
      }
      const reconnects = Object.entries(selections)
        .filter(([name, device]) => device && !axes[name].connected)
        .map(async ([name, device]) => {
          try { await connectDevice(name, device) }
          catch (error) { addEvent(`Automatic ${name} reconnect failed: ${error.message}`, 'warning') }
        })
      if (reconnects.length) {
        addEvent(`Reconnecting ${reconnects.length === 2 ? 'both authorized controllers' : 'an authorized controller'}…`)
        await Promise.all(reconnects)
      }
    } catch (error) {
      addEvent(`Could not check authorized Bluetooth controllers: ${error.message}`, 'warning')
    } finally {
      autoSelecting.value = false
    }
  }

  async function disconnectAxis(name, announce = true) {
    const axis = axes[name]
    const device = axis.device
    if (device?.gatt?.connected) device.gatt.disconnect()
    axis.connected = false
    axis.server = axis.rx = axis.tx = null
    if (announce) addEvent(`${name} disconnected`)
  }

  async function sendAxis(name, command, options = {}) {
    const axis = axes[name]
    if (!axis.connected || !axis.rx) throw new Error(`${name} BLE is not connected`)
    if (axis.otaUploading && !command.startsWith('OTA ')) throw new Error(`${name} firmware update is in progress`)
    const write = async () => {
      logAxis(axis, command, 'out')
      const bytes = new TextEncoder().encode(`${command}\n`)
      if (axis.rx.properties.writeWithoutResponse) await axis.rx.writeValueWithoutResponse(bytes)
      else await axis.rx.writeValue(bytes)
      if (!options.quiet) lastCommand.value = `${name.toUpperCase()}: ${command}`
    }
    axis.writeChain = axis.writeChain.then(write, write)
    return axis.writeChain
  }

  function ensurePolling() {
    if (pollTimer) return
    pollTimer = window.setInterval(() => {
      telemetryClock.value = Date.now()
      for (const axis of Object.values(axes)) {
        if (axis.connected && !axis.otaUploading) sendAxis(axis.name, 'AXIS STATUS', { quiet: true }).catch(() => {})
      }
    }, 500)
  }

  function waitForLine(axis, predicate, timeoutMs) {
    return new Promise((resolve, reject) => {
      const waiter = {
        predicate,
        resolve: line => { cleanup(); resolve(line) },
        reject: error => { cleanup(); reject(error) },
      }
      const timeout = window.setTimeout(() => waiter.reject(new Error(`Timed out waiting for ${axis.name} controller`)), timeoutMs)
      function cleanup() {
        window.clearTimeout(timeout)
        const index = axis.waiters.indexOf(waiter)
        if (index >= 0) axis.waiters.splice(index, 1)
      }
      axis.waiters.push(waiter)
    })
  }

  async function writeGatt(axis, bytes, withResponse = false) {
    if (!axis.connected || !axis.rx) throw new Error(`${axis.name} BLE is not connected`)
    if (withResponse && typeof axis.rx.writeValueWithResponse === 'function') return axis.rx.writeValueWithResponse(bytes)
    if (!withResponse && typeof axis.rx.writeValueWithoutResponse === 'function') return axis.rx.writeValueWithoutResponse(bytes)
    return axis.rx.writeValue(bytes)
  }

  async function otaCommand(axis, command, expectedPrefix, timeoutMs = 30000) {
    logAxis(axis, command, 'out')
    const response = waitForLine(axis, line => line.startsWith(expectedPrefix), timeoutMs)
    const packet = new TextEncoder().encode(`${command}\n`)
    try {
      for (let offset = 0; offset < packet.length; offset += 20) {
        const end = Math.min(offset + 20, packet.length)
        await writeGatt(axis, packet.subarray(offset, end), end === packet.length)
      }
      return await response
    } catch (error) {
      // Ensure the registered waiter is released if the GATT write itself fails.
      for (const waiter of [...axis.waiters]) waiter.reject(error)
      await response.catch(() => {})
      throw error
    }
  }

  async function uploadFirmware(name, file) {
    const axis = axes[name]
    if (!axis?.connected) throw new Error(`Connect the ${name} controller first`)
    if (axis.otaUploading) throw new Error(`${name} firmware update is already running`)
    if (!file || !file.name.toLowerCase().endsWith('.bin')) throw new Error('Choose a .bin application image')
    if (file.size < 256 || file.size > OTA_MAX_IMAGE_BYTES) throw new Error(`Firmware image must be between 256 bytes and ${OTA_MAX_IMAGE_BYTES.toLocaleString()} bytes`)

    const firmware = new Uint8Array(await file.arrayBuffer())
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', firmware))
    const hash = bytesToHex(digest)
    axis.otaUploading = true
    axis.otaProgress = 0
    axis.otaStatus = 'Preparing controller…'
    axis.otaCancelRequested = false
    addEvent(`Updating ${name} with ${file.name} (${file.size.toLocaleString()} bytes)`)

    try {
      await axis.writeChain.catch(() => {})
      await otaCommand(axis, 'OTA ABORT', 'OK OTA ABORT')
      await otaCommand(axis, `OTA BIN BEGIN ${firmware.length} ${hash}`, 'OK OTA BIN BEGIN', 120000)
      axis.otaStatus = 'Writing firmware…'

      const windowBytes = OTA_FRAME_PAYLOAD_BYTES * OTA_WINDOW_FRAMES
      for (let windowStart = 0; windowStart < firmware.length; windowStart += windowBytes) {
        if (axis.otaCancelRequested) throw new Error('Firmware update cancelled')
        const windowEnd = Math.min(windowStart + windowBytes, firmware.length)
        const acknowledgement = waitForLine(axis, line => line.startsWith('OK OTA BIN '), 30000)
        for (let offset = windowStart; offset < windowEnd; offset += OTA_FRAME_PAYLOAD_BYTES) {
          const end = Math.min(offset + OTA_FRAME_PAYLOAD_BYTES, windowEnd)
          await writeGatt(axis, buildOtaFrame(offset, firmware.subarray(offset, end), end === windowEnd), false)
        }
        const reply = await acknowledgement
        if (reply !== `OK OTA BIN ${windowEnd}`) throw new Error(`Unexpected acknowledgement: ${reply}`)
        axis.otaProgress = windowEnd / firmware.length
      }

      axis.otaStatus = 'Verifying image…'
      await otaCommand(axis, 'OTA END', 'OK OTA REBOOT', 120000)
      axis.otaProgress = 1
      axis.otaStatus = 'Update complete. The controller is rebooting; reconnect in about 15 seconds.'
      addEvent(`${name} firmware verified; controller rebooting`)
    } catch (error) {
      axis.otaStatus = `Update failed: ${error.message}`
      addEvent(`${name} firmware update failed: ${error.message}`, 'error')
      if (axis.connected) {
        try { await otaCommand(axis, 'OTA ABORT', 'OK OTA ABORT', 5000) } catch { /* best effort */ }
      }
      throw error
    } finally {
      axis.otaUploading = false
      axis.otaCancelRequested = false
    }
  }

  function cancelFirmwareUpdate(name) {
    const axis = axes[name]
    if (axis?.otaUploading) {
      axis.otaCancelRequested = true
      axis.otaStatus = 'Stopping after the current transfer window…'
    }
  }

  async function waitForIdle(axis, timeoutMs = 60000) {
    const initialTarget = axis.targetTicks
    await sleep(120)
    await sendAxis(axis.name, 'AXIS STATUS', { quiet: true })
    await sleep(120)
    const deadline = Date.now() + timeoutMs
    let sawMotion = !IDLE_STATES.has(axis.motionState) || axis.targetTicks !== initialTarget
    while (Date.now() < deadline) {
      if (!axis.connected) throw new Error(`${axis.name} disconnected during movement`)
      if (FAULT_STATES.has(axis.motionState)) throw new Error(`${axis.name} axis reported ${axis.motionState}`)
      if (!IDLE_STATES.has(axis.motionState)) sawMotion = true
      if (sawMotion && IDLE_STATES.has(axis.motionState)) return
      await sleep(150)
    }
    throw new Error(`${axis.name} did not finish its move`)
  }

  async function moveAxis(axis, delta, duty) {
    const pieces = splitMove(delta)
    for (let index = 0; index < pieces.length; index += 1) {
      await sendAxis(axis.name, `AXIS MOVE ${pieces[index].toFixed(5)} ${duty || axis.calibration.maxDuty}`)
      if (index < pieces.length - 1) await waitForIdle(axis)
    }
  }

  async function gotoMount(yaw, pitch, duty = null) {
    if (!mountConnected.value) throw new Error('Connect both axes first')
    const safeYaw = wrapDegrees(yaw)
    const safePitch = Number(pitch)
    if (!Number.isFinite(safeYaw) || !Number.isFinite(safePitch)) throw new Error('Yaw and pitch must be numbers')
    target.yaw = safeYaw
    target.pitch = safePitch
    const moves = []
    const yawDelta = shortestDelta(axes.yaw.angle, safeYaw)
    const pitchDelta = safePitch - axes.pitch.angle
    if (Math.abs(yawDelta) >= Math.abs(axes.yaw.calibration.degreesPerTick) / 2) moves.push(moveAxis(axes.yaw, yawDelta, duty))
    if (Math.abs(pitchDelta) >= Math.abs(axes.pitch.calibration.degreesPerTick) / 2) moves.push(moveAxis(axes.pitch, pitchDelta, duty))
    await Promise.all(moves)
  }

  async function moveRelative(yawDelta, pitchDelta, duty = null) {
    return gotoMount(wrapDegrees(axes.yaw.angle + Number(yawDelta)), axes.pitch.angle + Number(pitchDelta), duty)
  }

  async function stop() {
    stopTracking(false)
    await Promise.all(Object.values(axes).filter(axis => axis.connected).map(axis => sendAxis(axis.name, 'AXIS STOP').catch(() => {})))
  }

  async function home() { return gotoMount(0, 0) }
  async function park() { await stop(); return home() }

  async function zeroAxes() {
    const connected = Object.values(axes).filter(axis => axis.connected)
    if (!connected.length) throw new Error('No axis controller is connected')
    const busy = connected.filter(axis => !IDLE_STATES.has(axis.motionState))
    if (busy.length) throw new Error(`Stop the motors before zeroing: ${busy.map(axis => axis.name).join(', ')}`)
    await Promise.all(connected.map(axis => sendAxis(axis.name, 'AXIS ZERO')))
    await Promise.all(connected.map(axis => sendAxis(axis.name, 'CALIBRATION', { quiet: true })))
    await sleep(500)
    await Promise.all(connected.map(axis => sendAxis(axis.name, 'AXIS STATUS', { quiet: true })))
    await sleep(350)
    for (const axis of connected) {
      if (axis.boardZeroDeg === null || axis.boardCountsRev === null) throw new Error(`${axis.name} did not report calibration`)
      const expected = Math.round(axis.boardZeroDeg * axis.boardCountsRev / 360)
      if (Math.abs(axis.ticks - expected) > 1) throw new Error(`${axis.name} did not accept AXIS ZERO (expected ${expected}, got ${axis.ticks})`)
      axis.calibration.referenceTicks = axis.ticks
      axis.calibration.referenceDegrees = axis.boardZeroDeg
      updateTicks(axis, axis.ticks)
    }
    persistConfiguration()
    addEvent('Axes zeroed; sky sync references were reset')
  }

  function setCurrentMount(yaw, pitch) {
    axes.yaw.calibration.referenceTicks = axes.yaw.ticks
    axes.yaw.calibration.referenceDegrees = wrapDegrees(yaw)
    axes.pitch.calibration.referenceTicks = axes.pitch.ticks
    axes.pitch.calibration.referenceDegrees = Number(pitch)
    updateTicks(axes.yaw, axes.yaw.ticks)
    updateTicks(axes.pitch, axes.pitch.ticks)
    persistConfiguration()
    addEvent(`Current position set to yaw ${axes.yaw.angle.toFixed(3)}°, pitch ${axes.pitch.angle.toFixed(3)}°`)
  }

  function skyToMount(raHours, decDegrees, object = null) {
    const sky = object ? altAzForObject(object, configuration.location) : altAzForRaDec(raHours, decDegrees, configuration.location)
    return { ...sky, ...mountFromAltAz(configuration.mountModel, sky.altitude, sky.azimuth) }
  }

  async function gotoRaDec(raHours, decDegrees) {
    target.raHours = Number(raHours)
    target.decDegrees = Number(decDegrees)
    target.object = null
    const mount = skyToMount(target.raHours, target.decDegrees)
    await gotoMount(mount.yaw, mount.pitch)
  }

  function scheduleTrackingTick() {
    clearTimeout(trackingTimer)
    trackingTimer = window.setTimeout(trackingTick, 500)
  }

  async function trackingTick() {
    if (!tracking.value) return
    try {
      const mount = skyToMount(target.raHours || 0, target.decDegrees || 0, target.object)
      if (mount.raHours !== undefined) {
        target.raHours = mount.raHours
        target.decDegrees = mount.decDegrees
      }
      const idle = IDLE_STATES.has(axes.yaw.motionState) && IDLE_STATES.has(axes.pitch.motionState)
      const error = Math.max(Math.abs(shortestDelta(axes.yaw.angle, mount.yaw)), Math.abs(axes.pitch.angle - mount.pitch))
      if (idle && error >= 0.15) await gotoMount(mount.yaw, mount.pitch, 35)
    } catch (error) {
      addEvent(`Tracking stopped: ${error.message}`, 'error')
      tracking.value = false
    }
    if (tracking.value) scheduleTrackingTick()
  }

  function trackRaDec(raHours, decDegrees) {
    target.raHours = Number(raHours)
    target.decDegrees = Number(decDegrees)
    target.object = null
    tracking.value = true
    scheduleTrackingTick()
  }

  function trackObject(name) {
    skyToMount(0, 0, name)
    target.object = name
    tracking.value = true
    scheduleTrackingTick()
  }

  function stopTracking(stopMotors = true) {
    tracking.value = false
    clearTimeout(trackingTimer)
    trackingTimer = null
    if (stopMotors) return Promise.all(Object.values(axes).filter(axis => axis.connected).map(axis => sendAxis(axis.name, 'AXIS STOP').catch(() => {})))
  }

  function syncSky(raHours, decDegrees) {
    if (!axes.yaw.hasReading || !axes.pitch.hasReading) throw new Error('Fresh encoder readings are required')
    const mount = skyToMount(Number(raHours), Number(decDegrees))
    setCurrentMount(mount.yaw, mount.pitch)
    target.raHours = Number(raHours)
    target.decDegrees = Number(decDegrees)
    target.object = null
    addEvent('Sky sync saved')
  }

  function resetConfiguration() {
    const fresh = clone(defaults)
    Object.assign(configuration.location, fresh.location)
    Object.assign(configuration.calibration.yaw, fresh.calibration.yaw)
    Object.assign(configuration.calibration.pitch, fresh.calibration.pitch)
    configuration.mountModel.matrix = fresh.mountModel.matrix
    configuration.mountModel.offset = fresh.mountModel.offset
    for (const axis of Object.values(axes)) updateTicks(axis, axis.ticks)
    persistConfiguration()
  }

  return {
    axes, configuration, history, events, globalError, target, tracking, lastCommand, telemetryClock, autoSelecting,
    webBluetoothAvailable, mountConnected, telemetryStale, mountHealthy,
    requestAxis, autoConnectKnownDevices, disconnectAxis, sendAxis, uploadFirmware, cancelFirmwareUpdate,
    gotoMount, moveRelative, stop, home, park, zeroAxes,
    setCurrentMount, gotoRaDec, trackRaDec, trackObject, stopTracking, syncSky,
    persistConfiguration, resetConfiguration, waitForIdle, addEvent,
  }
})

export const useBleStore = useMountStore
