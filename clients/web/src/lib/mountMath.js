export const MAX_AXIS_MOVE_DEGREES = 180

export function wrapDegrees(value) {
  return ((Number(value) % 360) + 360) % 360
}

export function shortestDelta(current, target) {
  return ((Number(target) - Number(current) + 540) % 360) - 180
}

export function angleFromTicks(calibration, ticks) {
  return Number(calibration.referenceDegrees) +
    (Number(ticks) - Number(calibration.referenceTicks)) * Number(calibration.degreesPerTick)
}

export function mountFromAltAz(model, altitude, azimuth) {
  const [[a, b], [c, d]] = model.matrix
  const [pitchOffset, yawOffset] = model.offset
  return {
    pitch: a * altitude + b * azimuth + pitchOffset,
    yaw: wrapDegrees(c * altitude + d * azimuth + yawOffset),
  }
}

export function altAzFromMount(model, pitch, yaw) {
  const [[a, b], [c, d]] = model.matrix
  const determinant = a * d - b * c
  if (Math.abs(determinant) < 1e-12) throw new Error('Mount calibration matrix is singular')
  const x = pitch - model.offset[0]
  const y = yaw - model.offset[1]
  return {
    altitude: (d * x - b * y) / determinant,
    azimuth: wrapDegrees((-c * x + a * y) / determinant),
  }
}

export function splitMove(delta, maximum = MAX_AXIS_MOVE_DEGREES) {
  const count = Math.max(1, Math.ceil(Math.abs(delta) / maximum))
  return Array.from({ length: count }, () => delta / count)
}
