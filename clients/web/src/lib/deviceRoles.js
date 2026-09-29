export const AXIS_DEVICE_SUFFIX = Object.freeze({ yaw: '-78', pitch: '-158' })

export function axisForDeviceName(name) {
  const normalized = String(name || '').toLowerCase()
  return Object.entries(AXIS_DEVICE_SUFFIX).find(([, suffix]) => normalized.endsWith(suffix))?.[0] || null
}
