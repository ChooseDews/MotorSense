export const OTA_MAX_IMAGE_BYTES = 0x0f0000
export const OTA_FRAME_PAYLOAD_BYTES = 500
export const OTA_WINDOW_FRAMES = 8

export function buildOtaFrame(offset, payload, acknowledge = false) {
  if (!(payload instanceof Uint8Array) || payload.length < 1 || payload.length > OTA_FRAME_PAYLOAD_BYTES) {
    throw new Error('Invalid OTA frame payload')
  }
  const frame = new Uint8Array(9 + payload.length)
  const view = new DataView(frame.buffer)
  frame[0] = 0x4d
  frame[1] = 0x53
  view.setUint32(2, offset, true)
  view.setUint16(6, payload.length, true)
  frame[8] = acknowledge ? 1 : 0
  frame.set(payload, 9)
  return frame
}

export function bytesToHex(bytes) {
  return [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
}
