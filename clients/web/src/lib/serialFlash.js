export const SERIAL_IMAGE_TYPES = Object.freeze({
  full: { label: 'Full flash image', address: 0x0, maxBytes: 2 * 1024 * 1024 },
  app: { label: 'Application image', address: 0x10000, maxBytes: 0xf0000 },
})

export function inferSerialImageType(filename = '') {
  return /(?:flash-all|merged)/i.test(filename) ? 'full' : 'app'
}

export function validateSerialImage(file, type) {
  if (!file) return 'Choose a firmware .bin file.'
  if (!file.name.toLowerCase().endsWith('.bin')) return 'Firmware must be a .bin file.'
  const target = SERIAL_IMAGE_TYPES[type]
  if (!target) return 'Choose a firmware image type.'
  if (!file.size) return 'The firmware file is empty.'
  if (file.size > target.maxBytes) return `${target.label} is too large for its flash region.`
  return ''
}

export function formatFlashAddress(type) {
  return `0x${SERIAL_IMAGE_TYPES[type].address.toString(16)}`
}
