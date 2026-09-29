<script setup>
import { computed, onMounted } from 'vue'
import AxisStatus from './components/AxisStatus.vue'
import CalibrationPanel from './components/CalibrationPanel.vue'
import DiagnosticsPanel from './components/DiagnosticsPanel.vue'
import FirmwareUpdate from './components/FirmwareUpdate.vue'
import MountControl from './components/MountControl.vue'
import SkyControl from './components/SkyControl.vue'
import { useMountStore } from './stores/ble'

const store = useMountStore()
const updatingFirmware = computed(() => store.axes.yaw.otaUploading || store.axes.pitch.otaUploading)
const status = computed(() => {
  if (store.mountHealthy) return { label: 'Mount online', className: 'good' }
  if (store.mountConnected && store.telemetryStale) return { label: 'Mount online · telemetry stale', className: 'warn' }
  if (store.axes.yaw.connecting || store.axes.pitch.connecting) return { label: 'Connecting controller', className: 'warn' }
  return { label: 'Mount offline', className: 'bad' }
})

onMounted(() => store.autoConnectKnownDevices())
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="MotorSense mount dashboard">
        <span><strong>MotorSense</strong><small>mount console</small></span>
      </a>
      <div class="top-actions">
        <span class="mount-status" :class="status.className"><i></i>{{ status.label }}</span>
        <button class="danger stop" @click="store.stop">STOP ALL</button>
      </div>
    </header>

    <main>
      <section class="hero">
        <div>
          <h1>Mount command center</h1>
        </div>
      </section>

      <div v-if="!store.webBluetoothAvailable" class="browser-warning">
        <strong>Web Bluetooth isn’t available.</strong> Open this app in Chrome or Edge from localhost or HTTPS.
      </div>
      <div v-if="store.globalError" class="browser-warning error"><strong>Connection issue:</strong> {{ store.globalError }}</div>

      <section class="axis-grid"><AxisStatus name="yaw" /><AxisStatus name="pitch" /></section>
      <p v-if="!store.mountConnected" class="picker-note">
        <template v-if="store.autoSelecting">Checking for previously authorized controllers…</template>
        <template v-else>Connect yaw (<strong>-78</strong>) and pitch (<strong>-158</strong>). Previously approved boards reconnect automatically.</template>
      </p>

      <div v-if="updatingFirmware" class="connection-gate">
        Firmware update in progress. Mount controls are temporarily unavailable.
      </div>
      <div v-else-if="!store.mountConnected" class="connection-gate">
        Connect both controllers to unlock controls.
      </div>
      <template v-else>
        <MountControl />
        <SkyControl />
        <CalibrationPanel />
      </template>
      <FirmwareUpdate v-if="store.axes.yaw.connected || store.axes.pitch.connected" />
      <DiagnosticsPanel v-if="!updatingFirmware && (store.axes.yaw.connected || store.axes.pitch.connected)" />

      <section class="panel about" aria-labelledby="about-title">
        <div class="about-lead">
          <div>
            <h2 id="about-title">About MotorSense</h2>
          </div>
          <p>Works with a control PCB that replaces a telescope’s original motor controls.</p>
        </div>
        <div class="about-links">
          <a class="project-link primary-link" href="https://github.com/ChooseDews/MotorSense" target="_blank" rel="noopener noreferrer">
            <strong>Board Design &amp; Source Code</strong><b aria-hidden="true">↗</b>
          </a>
          <a class="project-link" href="https://johndews.com" target="_blank" rel="noopener noreferrer">
            <strong>Created by John Dews-Flick</strong><b aria-hidden="true">↗</b>
          </a>
        </div>
      </section>
    </main>
    <footer><span>A Project by John Dews-Flick 2026</span></footer>
  </div>
</template>
