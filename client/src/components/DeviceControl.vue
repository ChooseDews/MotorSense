<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { useBleStore } from '../stores/ble'
import EncoderBar from './EncoderBar.vue'
import YawReadout from './YawReadout.vue'

const props = defineProps({
  deviceId: { type: String, required: true }
})

const store = useBleStore()
const deviceEntry = computed(() => store.devices.get(props.deviceId))

// LED State
const ledR = ref(0)
const ledG = ref(0)
const ledB = ref(0)

// Motor State
// const motorDir = ref('S') // Removed: only using buttons now
const motorDuty = ref(30)
const motorSec = ref(1)

// Terminal State
const cmdInput = ref('')
const logTextarea = ref(null)

// Watch logs to auto-scroll
const logs = computed(() => deviceEntry.value?.logs || [])
watch(logs, () => {
    nextTick(() => {
        if (logTextarea.value) {
            logTextarea.value.scrollTop = logTextarea.value.scrollHeight
        }
    })
}, { deep: true })

const logContent = computed(() => logs.value.map(l => l.msg).join('\n'))

function sendLed() {
    const cmd = `LED ${ledR.value} ${ledG.value} ${ledB.value}`
    store.sendCommand(props.deviceId, cmd)
}

function sendMotor(overrideDir = null) {
    const d = overrideDir || motorDir.value
    const duty = motorDuty.value
    const sec = motorSec.value
    let cmd = `MOTOR ${d} ${duty}`
    if (sec > 0) cmd += ` ${sec}`
    store.sendCommand(props.deviceId, cmd)
}

function sendStop() {
    store.sendCommand(props.deviceId, 'MOTOR S')
}

function sendTerminalCmd(cmd = null) {
    const c = cmd || cmdInput.value
    if (!c) return
    store.sendCommand(props.deviceId, c)
    if (!cmd) cmdInput.value = ''
}

function toggleEncoder0Invert() {
    const newVal = deviceEntry.value.encoder0Inverted ? 0 : 1
    store.sendCommand(props.deviceId, `ENCODER 0 INVERT ${newVal}`)
    deviceEntry.value.encoder0Inverted = !deviceEntry.value.encoder0Inverted
}

function toggleEncoder1Invert() {
    const newVal = deviceEntry.value.encoder1Inverted ? 0 : 1
    store.sendCommand(props.deviceId, `ENCODER 1 INVERT ${newVal}`)
    deviceEntry.value.encoder1Inverted = !deviceEntry.value.encoder1Inverted
}
</script>

<template>
  <div class="device-control" v-if="deviceEntry">
    <div class="header">
        <h3>{{ deviceEntry.name }}</h3>
        <button @click="store.disconnect(deviceId)" class="danger">Disconnect</button>
    </div>

    <div class="panels">
        <!-- Encoder Dial -->
        <div class="panel">
            <h4>Encoders</h4>
            <div class="row encoder-row">
                <EncoderBar 
                    label="Pos 0" 
                    :modelValue="deviceEntry.encoderPos0"
                    :inverted="deviceEntry.encoder0Inverted"
                    @toggle-invert="toggleEncoder0Invert"
                />
                <EncoderBar 
                    label="Pos 1" 
                    :modelValue="deviceEntry.encoderPos1"
                    :inverted="deviceEntry.encoder1Inverted"
                    @toggle-invert="toggleEncoder1Invert"
                />
            </div>
            <YawReadout :position="deviceEntry.encoderPos0" :motorPosition="deviceEntry.encoderPos1"
                :connected="deviceEntry.isConnected" :hasReading="deviceEntry.hasEncoderReading"
                :inverted="deviceEntry.encoder0Inverted" :motorInverted="deviceEntry.encoder1Inverted" />
        </div>

        <!-- Controls -->
        <div class="panel">
            <h4>LED Control</h4>
            <div class="row">
                <label>R <input type="number" v-model="ledR" min="0" max="255"></label>
                <label>G <input type="number" v-model="ledG" min="0" max="255"></label>
                <label>B <input type="number" v-model="ledB" min="0" max="255"></label>
                <button @click="sendLed">Send LED</button>
            </div>

            <h4>Motor Control</h4>
            <div class="row">
                <label>Duty% <input type="number" v-model="motorDuty" min="0" max="100"></label>
                <label>Sec <input type="number" v-model="motorSec" step="0.5"></label>
            </div>
            <div class="row">
                <button @click="sendMotor('F')" class="action-btn">Forward</button>
                <button @click="sendMotor('B')" class="action-btn">Backward</button>
                <button @click="sendStop" class="danger-outline action-btn">STOP</button>
            </div>
        </div>

        <!-- Terminal / Presets -->
        <div class="panel terminal-panel">
            <h4>Terminal</h4>
            <div class="presets">
                <button class="sm" @click="sendTerminalCmd('PING')">PING</button>
                <button class="sm" @click="sendTerminalCmd('STATUS')">STATUS</button>
                <button class="sm" @click="sendTerminalCmd('HELP')">HELP</button>
                <button class="sm" @click="sendTerminalCmd('LED DANCE')">DANCE</button>
            </div>
            <div class="terminal-input">
                <input 
                    v-model="cmdInput" 
                    @keydown.enter="sendTerminalCmd()" 
                    placeholder="Type command..."
                    type="text"
                >
                <button @click="sendTerminalCmd()">Send</button>
            </div>
            <textarea 
                ref="logTextarea" 
                readonly 
                :value="logContent"
                class="log-viewer"
            ></textarea>
        </div>
    </div>
  </div>
</template>

<style scoped>
.device-control {
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
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
}

h3 { margin: 0; color: var(--color-primary); }

.panels {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1rem;
}

.panel {
    background: rgba(0,0,0,0.2);
    padding: 1rem;
    border-radius: 6px;
    border: 1px solid var(--color-border);
}

h4 { margin-top: 0; border-bottom: 1px solid var(--color-border); padding-bottom: 0.5rem; }

.row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 1rem;
    flex-wrap: wrap;
}

.encoder-row {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    width: 100%;
}

input[type="number"] {
    width: 60px;
}

.terminal-panel {
    display: flex;
    flex-direction: column;
}

.log-viewer {
    width: 100%;
    height: 200px;
    background: #000;
    color: #4ade80; /* Terminal green */
    font-family: monospace;
    font-size: 0.9em;
    border: 1px solid var(--color-border);
    resize: vertical;
}

.presets {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
}

.terminal-input {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
}
.terminal-input input {
    flex: 1;
}

button.danger {
    background-color: #ef4444;
    color: white;
}
button.danger:hover { background-color: #dc2626; }

button.danger-outline {
    border-color: #ef4444;
    color: #ef4444;
}
button.danger-outline:hover {
    background-color: #ef4444;
    color: white;
}

button.sm {
    padding: 0.2rem 0.6rem;
    font-size: 0.85em;
}

.action-btn {
    min-width: 80px;
}

/* Make primary actions pop */
.action-btn:not(.danger-outline) {
    background-color: var(--color-primary); 
    color: white; 
    border: none;
}
.action-btn:not(.danger-outline):hover {
    background-color: var(--color-primary-hover);
}

.encoder-row {
    flex-direction: column;
    gap: 0.5rem;
    align-items: stretch;
}
</style>
