import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'

test('uploads a bin with the firmware binary protocol', async () => {
  const saved = new Map()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: key => saved.get(key) || null,
    setItem: (key, value) => saved.set(key, value),
  } })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    setTimeout, clearTimeout, setInterval: () => 1,
  } })

  const encoder = new TextEncoder()
  const decoder = new TextDecoder()
  const commands = []
  const frameOffsets = []
  let notify = null
  let binaryMode = false
  let expectedSize = 0
  let received = 0
  let textBuffer = ''

  function reply(line) {
    notify?.({ target: { value: new DataView(encoder.encode(`${line}\n`).buffer) } })
  }

  async function receive(value) {
    const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
    if (binaryMode) {
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
      assert.equal(bytes[0], 0x4d)
      assert.equal(bytes[1], 0x53)
      const offset = view.getUint32(2, true)
      const length = view.getUint16(6, true)
      assert.equal(offset, received)
      assert.equal(length, bytes.length - 9)
      frameOffsets.push(offset)
      received += length
      if (received === expectedSize) binaryMode = false
      if (bytes[8] === 1) reply(`OK OTA BIN ${received}`)
      return
    }

    textBuffer += decoder.decode(bytes)
    while (textBuffer.includes('\n')) {
      const index = textBuffer.indexOf('\n')
      const command = textBuffer.slice(0, index)
      textBuffer = textBuffer.slice(index + 1)
      commands.push(command)
      if (command === 'OTA ABORT') reply('OK OTA ABORT')
      else if (command.startsWith('OTA BIN BEGIN ')) {
        expectedSize = Number(command.split(' ')[3])
        received = 0
        binaryMode = true
        reply('OK OTA BIN BEGIN')
      } else if (command === 'OTA END') reply('OK OTA REBOOT')
      else if (command === 'CALIBRATION') reply('CALIBRATION counts_rev=11840 counts_degree=32.8 degrees_count=0.03 min=-180 max=180 zero=0')
      else if (command === 'AXIS STATUS') reply('AXIS: state=IDLE start=0 target=0 position=0 duty=0 corrections=0')
      else reply('OK')
    }
  }

  const rx = {
    properties: { write: true, writeWithoutResponse: true },
    writeValueWithoutResponse: receive,
    writeValueWithResponse: receive,
  }
  const tx = {
    startNotifications: async () => {},
    addEventListener: (_, handler) => { notify = handler },
  }
  const device = {
    id: 'yaw-test', name: 'MotorSense Caboose-78',
    addEventListener: () => {},
    gatt: {
      connected: true,
      connect: async () => ({ getPrimaryService: async () => ({
        getCharacteristic: async uuid => uuid.includes('0002-') ? rx : tx,
      }) }),
      disconnect: () => {},
    },
  }
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
    bluetooth: { requestDevice: async () => device },
  } })

  setActivePinia(createPinia())
  const { useMountStore } = await import('../src/stores/ble.js')
  const store = useMountStore()
  await store.requestAxis('yaw')

  const image = new Uint8Array(9000)
  for (let index = 0; index < image.length; index += 1) image[index] = index % 251
  const file = { name: 'motorsense-v1.2.3.bin', size: image.length, arrayBuffer: async () => image.buffer }
  await store.uploadFirmware('yaw', file)

  assert.equal(store.axes.yaw.otaProgress, 1)
  assert.match(store.axes.yaw.otaStatus, /rebooting/i)
  assert.ok(commands[0] === 'STATUS')
  assert.ok(commands.includes('OTA ABORT'))
  assert.ok(commands.some(command => /^OTA BIN BEGIN 9000 [0-9a-f]{64}$/.test(command)))
  assert.equal(commands.at(-1), 'OTA END')
  assert.deepEqual(frameOffsets.slice(0, 3), [0, 500, 1000])
  assert.equal(received, image.length)
})
