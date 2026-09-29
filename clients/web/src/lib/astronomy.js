import { Body, Equator, Horizon, Observer } from 'astronomy-engine'
import { wrapDegrees } from './mountMath.js'

export const TRACKABLE_BODIES = [
  'Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune',
]

const bodyByName = Object.fromEntries(TRACKABLE_BODIES.map(name => [name.toLowerCase(), Body[name]]))

function observerFor(location) {
  return new Observer(Number(location.latitude), Number(location.longitude), Number(location.elevationM))
}

export function altAzForRaDec(raHours, decDegrees, location, date = new Date()) {
  const horizontal = Horizon(date, observerFor(location), Number(raHours), Number(decDegrees), 'normal')
  return { altitude: horizontal.altitude, azimuth: wrapDegrees(horizontal.azimuth) }
}

export function altAzForObject(name, location, date = new Date()) {
  const body = bodyByName[String(name).trim().toLowerCase()]
  if (body === undefined) throw new Error(`Supported objects: ${TRACKABLE_BODIES.join(', ')}`)
  const observer = observerFor(location)
  const equatorial = Equator(body, date, observer, true, true)
  const horizontal = Horizon(date, observer, equatorial.ra, equatorial.dec, 'normal')
  return {
    altitude: horizontal.altitude,
    azimuth: wrapDegrees(horizontal.azimuth),
    raHours: equatorial.ra,
    decDegrees: equatorial.dec,
  }
}
