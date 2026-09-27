import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'

const NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
const NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
const NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

export const useBleStore = defineStore('ble', () => {
    // devices: Map<deviceId, { device, server, rx, tx, name, isConnected, logs: [], encoderPos: 0 }>
    const devices = reactive(new Map())
    const history = reactive([]) // Global history of all data points
    const scanning = ref(false)
    const error = ref(null)

    function logToDevice(deviceId, msg) {
        const dev = devices.get(deviceId)
        if (dev) {
            dev.logs.push({ ts: new Date(), msg })
            // Keep log size manageable
            if (dev.logs.length > 1000) dev.logs.shift()
        }
    }

    async function requestDevice(filters = []) {
        error.value = null
        scanning.value = true
        try {
            const options = {
                optionalServices: [NUS_SERVICE_UUID]
            }

            if (filters.length > 0) {
                options.filters = filters
            } else {
                options.acceptAllDevices = true
            }

            console.log('Requesting device with options:', options)
            const device = await navigator.bluetooth.requestDevice(options)

            if (!devices.has(device.id)) {
                devices.set(device.id, {
                    id: device.id,
                    device: device,
                    name: device.name || 'Unknown Device',
                    isConnected: false,
                    logs: [],
                    hasEncoderReading: false,
                    encoderPos0: 0,
                    encoderPos1: 0,
                    encoder0Inverted: false,
                    encoder1Inverted: false,
                    rx: null,
                    tx: null
                })
            }

            await connectToDevice(device.id)

        } catch (e) {
            error.value = e.message
            console.error(e)
        } finally {
            scanning.value = false
        }
    }

    async function connectToDevice(deviceId) {
        const entry = devices.get(deviceId)
        if (!entry) return

        try {
            entry.hasEncoderReading = false
            logToDevice(deviceId, 'Connecting...')
            entry.device.addEventListener('gattserverdisconnected', () => handleDisconnect(deviceId))

            const server = await entry.device.gatt.connect()
            entry.server = server

            const service = await server.getPrimaryService(NUS_SERVICE_UUID)
            entry.rx = await service.getCharacteristic(NUS_RX_UUID)
            const tx = await service.getCharacteristic(NUS_TX_UUID)
            entry.tx = tx

            await tx.startNotifications()
            tx.addEventListener('characteristicvaluechanged', (ev) => handleNotification(deviceId, ev))

            entry.isConnected = true
            logToDevice(deviceId, 'Connected & Subscribed')
            await sendCommand(deviceId, 'ENCODER 0')
            await sendCommand(deviceId, 'ENCODER 1')

        } catch (e) {
            logToDevice(deviceId, `Connection failed: ${e.message}`)
            handleDisconnect(deviceId)
        }
    }

    function handleDisconnect(deviceId) {
        const entry = devices.get(deviceId)
        if (entry) {
            entry.hasEncoderReading = false
            entry.isConnected = false
            entry.server = null
            entry.rx = null
            entry.tx = null
            logToDevice(deviceId, 'Disconnected')
        }
    }

    async function disconnect(deviceId) {
        const entry = devices.get(deviceId)
        if (entry && entry.device && entry.device.gatt.connected) {
            await entry.device.gatt.disconnect()
        }
    }

    const decoder = new TextDecoder()
    // Regex for single or dual encoder lines:
    // "ENC pos=123" (legacy)
    // "ENC pos0=123 pos1=456" (new)
    const encSingleRe = /\bENC\s+pos\s*=\s*(-?\d+)\b/
    const encDualRe = /\bENC\s+pos0\s*=\s*(-?\d+)\s+pos1\s*=\s*(-?\d+)\b/

    let incomingBufferMap = new Map() // Partial lines per device

    function handleNotification(deviceId, event) {
        const value = event.target.value
        const text = decoder.decode(value)

        // Log raw text
        logToDevice(deviceId, `<< ${text}`)

        // Handle partial lines for regex parsing
        let buf = incomingBufferMap.get(deviceId) || ""
        buf += text

        while (buf.includes('\n')) {
            const idx = buf.indexOf('\n')
            const line = buf.slice(0, idx).replace(/\r$/, "")
            buf = buf.slice(idx + 1)

            const dev = devices.get(deviceId)
            if (dev) {
                // Read actual firmware inversion rather than assuming defaults.
                const config = /^ENCODER ([01]): pos=(-?\d+) invert=([01])$/.exec(line)
                const invertedAck = /^OK encoder ([01]) invert=([01])$/.exec(line)
                if (config) dev[`encoder${config[1]}Inverted`] = config[3] === '1'
                if (invertedAck) dev[`encoder${invertedAck[1]}Inverted`] = invertedAck[2] === '1'
                // Try dual first
                const mDual = encDualRe.exec(line)
                if (mDual) {
                    dev.hasEncoderReading = true
                    const p0 = parseInt(mDual[1], 10)
                    const p1 = parseInt(mDual[2], 10)
                    if (!Number.isNaN(p0)) dev.encoderPos0 = p0
                    if (!Number.isNaN(p1)) dev.encoderPos1 = p1

                    // Log data point
                    history.push({
                        deviceId: dev.id,
                        deviceName: dev.name,
                        timestamp: Date.now(),
                        pos0: dev.encoderPos0,
                        pos1: dev.encoderPos1
                    })
                } else {
                    // Try single (legacy)
                    const mSingle = encSingleRe.exec(line)
                    if (mSingle) {
                        const p = parseInt(mSingle[1], 10)
                        if (!Number.isNaN(p)) {
                            dev.hasEncoderReading = true
                            // If legacy format, maybe just map to pos0? Or keep 'encoderPos'
                            // Let's map to pos0 for consistency in UI
                            dev.encoderPos0 = p

                            // Log data point (pos1 undefined or 0? lets say 0)
                            history.push({
                                deviceId: dev.id,
                                deviceName: dev.name,
                                timestamp: Date.now(),
                                pos0: dev.encoderPos0,
                                pos1: 0
                            })
                        }
                    }
                }
            }
        }
        incomingBufferMap.set(deviceId, buf)
    }

    async function sendCommand(deviceId, cmd) {
        const entry = devices.get(deviceId)
        if (!entry || !entry.isConnected || !entry.rx) return

        logToDevice(deviceId, `>> ${cmd}`)
        const encoder = new TextEncoder()
        const data = encoder.encode(cmd + '\n')

        try {
            if (entry.rx.properties.writeWithoutResponse) {
                await entry.rx.writeValueWithoutResponse(data)
            } else {
                await entry.rx.writeValue(data)
            }
        } catch (e) {
            logToDevice(deviceId, `Send failed: ${e.message}`)
        }
    }

    return {
        devices,
        scanning,
        error,
        requestDevice,
        disconnect,
        sendCommand,
        history
    }
})
