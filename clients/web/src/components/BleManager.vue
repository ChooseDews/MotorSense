<script setup>
import { ref } from 'vue'
import { useBleStore } from '../stores/ble'
import DeviceControl from './DeviceControl.vue'
import TelescopeVisualizer from './TelescopeVisualizer.vue'
import DataLogger from './DataLogger.vue'

const store = useBleStore()
const filterByName = ref(true)

function scan() {
    const filters = filterByName.value ? [{ namePrefix: 'MotorSense' }] : []
    store.requestDevice(filters)
}
</script>

<template>
  <div class="manager">
    <div class="controls">
       <button @click="scan" :disabled="store.scanning" class="primary">
         {{ store.scanning ? 'Scanning...' : 'Add Device' }}
       </button>
       <label class="filter-option">
          <input type="checkbox" v-model="filterByName">
          Only "MotorSense" devices
       </label>
       <div v-if="store.error" class="error">{{ store.error }}</div>
    </div>

    <TelescopeVisualizer />

    <DataLogger />

    <div class="device-list">
        <div v-if="store.devices.size === 0" class="empty-state">
            No devices connected. Click "Add Device" to start.
        </div>
        
        <DeviceControl 
            v-for="[id, device] in store.devices" 
            :key="id" 
            :deviceId="id" 
        />
    </div>
  </div>
</template>

<style scoped>
.controls {
    margin-bottom: 2rem;
    display: flex;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
}

.filter-option {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    cursor: pointer;
    user-select: none;
}

.error {
    color: #ef4444;
}

.empty-state {
    text-align: center;
    padding: 3rem;
    color: var(--color-text-muted);
    border: 2px dashed var(--color-border);
    border-radius: 8px;
}
</style>
