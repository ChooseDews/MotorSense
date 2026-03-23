<script setup>
import { computed, ref } from 'vue'
import { useBleStore } from '../stores/ble'

const store = useBleStore()

// Preview limit
const PREVIEW_LIMIT = 100

const historyCount = computed(() => store.history.length)

// Show last N items, reversed (newest first)
const previewData = computed(() => {
    return store.history.slice(-PREVIEW_LIMIT).reverse()
})

function formatTime(ts) {
    return new Date(ts).toLocaleTimeString()
}

function downloadCsv() {
    if (store.history.length === 0) return

    const headers = ['Timestamp', 'Epoch', 'Device Name', 'Device ID', 'Pos 0', 'Pos 1']
    const rows = store.history.map(row => [
        new Date(row.timestamp).toISOString(),
        row.timestamp,
        `"${row.deviceName}"`, // Quote incase of commas
        row.deviceId,
        row.pos0,
        row.pos1
    ])

    const csvContent = [
        headers.join(','),
        ...rows.map(r => r.join(','))
    ].join('\n')

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.setAttribute('href', url)
    link.setAttribute('download', `motor_sense_data_${Date.now()}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
}

function clearHistory() {
    // Pinia state is reactive, but replacing the array content in place is safer?
    // store.history.length = 0 works for reactive arrays in Vue 3
    store.history.length = 0
}
</script>

<template>
  <div class="data-logger">
    <div class="header">
        <h3>Data Logger</h3>
        <div class="stats">
            <span>Points Captured: <strong>{{ historyCount }}</strong></span>
            <button @click="downloadCsv" :disabled="historyCount === 0" class="primary sm">Export CSV</button>
            <button @click="clearHistory" :disabled="historyCount === 0" class="danger-outline sm">Clear</button>
        </div>
    </div>

    <div class="table-container">
        <table>
            <thead>
                <tr>
                   <th>Time</th>
                   <th>Device</th>
                   <th>Pos 0</th>
                   <th>Pos 1</th>
                </tr>
            </thead>
            <tbody>
                <tr v-for="(row, idx) in previewData" :key="idx">
                    <td>{{ formatTime(row.timestamp) }}</td>
                    <td>{{ row.deviceName }}</td>
                    <td>{{ row.pos0 }}</td>
                    <td>{{ row.pos1 }}</td>
                </tr>
                <tr v-if="historyCount === 0">
                    <td colspan="4" class="empty">No data collected yet.</td>
                </tr>
            </tbody>
        </table>
    </div>
    <div v-if="historyCount > PREVIEW_LIMIT" class="footer-note">
        Showing last {{ PREVIEW_LIMIT }} of {{ historyCount }} entries. Export to see all.
    </div>
  </div>
</template>

<style scoped>
.data-logger {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 2rem;
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 0.5rem;
}

h3 {
    margin: 0;
    color: var(--color-primary);
}

.stats {
    display: flex;
    gap: 1rem;
    align-items: center;
    font-size: 0.9em;
}

.table-container {
    max-height: 300px;
    overflow-y: auto;
    border: 1px solid var(--color-border);
    border-radius: 4px;
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85em;
    font-family: monospace;
}

th, td {
    padding: 0.5rem;
    text-align: left;
    border-bottom: 1px solid var(--color-border);
}

th {
    background: rgba(255,255,255,0.05);
    position: sticky;
    top: 0;
}

.empty {
    text-align: center;
    color: var(--color-text-muted);
    padding: 1rem;
}

.footer-note {
    font-size: 0.8em;
    color: var(--color-text-muted);
    margin-top: 0.5rem;
    text-align: center;
}

button.sm {
    padding: 0.2rem 0.6rem;
    font-size: 0.85em;
}

button.danger-outline {
    background: transparent;
    border: 1px solid #ef4444;
    color: #ef4444;
}
button.danger-outline:hover {
    background: #ef4444;
    color: white;
}
</style>
