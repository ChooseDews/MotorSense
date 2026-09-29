<script setup>
import { reactive, ref } from 'vue'
import { TRACKABLE_BODIES } from '../lib/astronomy'
import { useMountStore } from '../stores/ble'

const store = useMountStore()
const sky = reactive({ ra: 12, dec: 0, object: 'Jupiter' })
const error = ref('')
const busy = ref(false)

async function run(action) {
  busy.value = true; error.value = ''
  try { await action() } catch (reason) { error.value = reason.message; store.addEvent(reason.message, 'error') }
  finally { busy.value = false }
}

function locate() {
  error.value = ''
  navigator.geolocation?.getCurrentPosition(
    position => {
      store.configuration.location.latitude = position.coords.latitude
      store.configuration.location.longitude = position.coords.longitude
      if (position.coords.altitude !== null) store.configuration.location.elevationM = position.coords.altitude
      store.persistConfiguration()
    },
    reason => { error.value = reason.message },
    { enableHighAccuracy: true },
  )
}
</script>

<template>
  <section class="panel">
    <div class="section-heading">
      <div><span class="eyebrow">SKY TARGET</span><h2>Celestial pointing</h2></div>
      <span class="tracking-pill" :class="{ active: store.tracking }">{{ store.tracking ? '● Tracking' : '○ Stopped' }}</span>
    </div>
    <div class="sky-layout">
      <div class="sky-targets">
        <div class="target-row compact-fields">
          <label>Right ascension <span><input v-model.number="sky.ra" type="number" min="0" max="24" step="0.0001">h</span></label>
          <label>Declination <span><input v-model.number="sky.dec" type="number" min="-90" max="90" step="0.0001">°</span></label>
        </div>
        <div class="button-row">
          <button :disabled="busy || !store.mountConnected" @click="run(() => store.gotoRaDec(sky.ra, sky.dec))">Goto RA / Dec</button>
          <button class="primary" :disabled="busy || !store.mountConnected" @click="run(() => store.trackRaDec(sky.ra, sky.dec))">Track RA / Dec</button>
          <button :disabled="!store.axes.yaw.hasReading || !store.axes.pitch.hasReading" title="Declare the current position to be this sky coordinate" @click="run(() => store.syncSky(sky.ra, sky.dec))">Sync here</button>
        </div>
        <div class="object-row">
          <label>Solar-system object
            <select v-model="sky.object"><option v-for="body in TRACKABLE_BODIES" :key="body">{{ body }}</option></select>
          </label>
          <button class="primary" :disabled="busy || !store.mountConnected" @click="run(() => store.trackObject(sky.object))">Track object</button>
          <button :disabled="!store.tracking" @click="run(() => store.stopTracking())">Stop tracking</button>
        </div>
      </div>
      <div class="location-card">
        <div class="subheading"><strong>Observer location</strong><button class="ghost compact" @click="locate">Use my location</button></div>
        <label>Latitude <input v-model.number="store.configuration.location.latitude" type="number" min="-90" max="90" step="0.0001"></label>
        <label>Longitude <input v-model.number="store.configuration.location.longitude" type="number" min="-180" max="180" step="0.0001"></label>
        <label>Elevation <span><input v-model.number="store.configuration.location.elevationM" type="number" step="1"> m</span></label>
      </div>
    </div>
    <p v-if="store.target.raHours !== null" class="target-summary">
      {{ store.target.object || 'RA / Dec' }} · RA {{ Number(store.target.raHours).toFixed(4) }}h · Dec {{ Number(store.target.decDegrees).toFixed(4) }}°
    </p>
    <p v-if="error" class="inline-error">{{ error }}</p>
  </section>
</template>
