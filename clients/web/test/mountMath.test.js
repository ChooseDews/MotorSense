import assert from 'node:assert/strict'
import test from 'node:test'
import { altAzForObject, altAzForRaDec } from '../src/lib/astronomy.js'
import { axisForDeviceName } from '../src/lib/deviceRoles.js'
import { altAzFromMount, angleFromTicks, mountFromAltAz, shortestDelta, splitMove, wrapDegrees } from '../src/lib/mountMath.js'
import { buildOtaFrame, bytesToHex } from '../src/lib/ota.js'
import { formatFlashAddress, inferSerialImageType, validateSerialImage } from '../src/lib/serialFlash.js'

test('normalizes yaw and chooses the shortest path', () => {
  assert.equal(wrapDegrees(-10), 350)
  assert.equal(shortestDelta(350, 10), 20)
  assert.equal(shortestDelta(10, 350), -20)
})

test('assigns authorized controller names to their mount axes', () => {
  assert.equal(axisForDeviceName('MotorSense Caboose-78'), 'yaw')
  assert.equal(axisForDeviceName('MotorSense Caboose-158'), 'pitch')
  assert.equal(axisForDeviceName('MotorSense unknown'), null)
})

test('builds MotorSense OTA binary frames with little-endian metadata', () => {
  const frame = buildOtaFrame(0x12345678, new Uint8Array([0xaa, 0xbb, 0xcc]), true)
  assert.deepEqual([...frame], [0x4d, 0x53, 0x78, 0x56, 0x34, 0x12, 3, 0, 1, 0xaa, 0xbb, 0xcc])
  assert.equal(bytesToHex(new Uint8Array([0, 15, 255])), '000fff')
  assert.throws(() => buildOtaFrame(0, new Uint8Array(501)), /Invalid OTA frame payload/)
})

test('selects and validates serial firmware image layouts', () => {
  assert.equal(inferSerialImageType('motorsense-yaw-flash-all.bin'), 'full')
  assert.equal(inferSerialImageType('merged-binary.bin'), 'full')
  assert.equal(inferSerialImageType('motorsense.bin'), 'app')
  assert.equal(formatFlashAddress('full'), '0x0')
  assert.equal(formatFlashAddress('app'), '0x10000')
  assert.equal(validateSerialImage({ name: 'motorsense.bin', size: 100 }, 'app'), '')
  assert.match(validateSerialImage({ name: 'motorsense.txt', size: 100 }, 'app'), /\.bin/)
  assert.match(validateSerialImage({ name: 'motorsense.bin', size: 0xf0001 }, 'app'), /too large/)
})

test('converts encoder ticks using a persisted reference', () => {
  assert.equal(angleFromTicks({ referenceDegrees: 10, referenceTicks: 100, degreesPerTick: 0.25 }, 120), 15)
})

test('segments board moves at the 180 degree firmware limit', () => {
  assert.deepEqual(splitMove(180), [180])
  assert.deepEqual(splitMove(-400), [-400 / 3, -400 / 3, -400 / 3])
})

test('round-trips the affine mount model', () => {
  const model = { matrix: [[1.1, 0.02], [-0.01, 0.98]], offset: [2, -3] }
  const mount = mountFromAltAz(model, 30, 100)
  const sky = altAzFromMount(model, mount.pitch, mount.yaw)
  assert.ok(Math.abs(sky.altitude - 30) < 1e-10)
  assert.ok(Math.abs(sky.azimuth - 100) < 1e-10)
})

test('calculates finite RA/Dec and planetary horizontal coordinates', () => {
  const location = { latitude: 37.3349, longitude: -122.009, elevationM: 0 }
  const date = new Date('2026-09-28T12:00:00Z')
  for (const result of [altAzForRaDec(12, 0, location, date), altAzForObject('Jupiter', location, date)]) {
    assert.ok(Number.isFinite(result.altitude))
    assert.ok(result.azimuth >= 0 && result.azimuth < 360)
  }
})
