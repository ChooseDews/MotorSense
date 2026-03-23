<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, shallowRef } from 'vue'
import { useBleStore } from '../stores/ble'
import * as THREE from 'three'

const store = useBleStore()

// Selection State
// Selection State
const azSelection = ref('') // "deviceId:encoderIdx"
const azMinVal = ref(0)     // Steps at 0 degrees
const azMaxVal = ref(1000)  // Steps at 360 degrees
const azVisualOffset = ref(0) // Degrees to shift visualization

const altSelection = ref('') // "deviceId:encoderIdx"
const altMinVal = ref(0)     // Steps at 0 degrees
const altMaxVal = ref(1000)  // Steps at 180 degrees
const altVisualOffset = ref(0) // Degrees to shift visualization

// Computed Values
const currentAz = computed(() => {
    let raw = 0
    if (azSelection.value) {
        const [deviceId, encIdx] = azSelection.value.split(':')
        const dev = store.devices.get(deviceId)
        if (dev) {
            raw = encIdx === '0' ? dev.encoderPos0 : dev.encoderPos1
        }
    }
    
    // Range Normalization
    const range = azMaxVal.value - azMinVal.value
    if (range === 0) return 0
    const deg = ((raw - azMinVal.value) / range) * 360
    return deg + azVisualOffset.value
})

const currentAlt = computed(() => {
    let raw = 0
    if (altSelection.value) {
        const [deviceId, encIdx] = altSelection.value.split(':')
        const dev = store.devices.get(deviceId)
        if (dev) {
            raw = encIdx === '0' ? dev.encoderPos0 : dev.encoderPos1
        }
    }
    
    // Range Normalization
    const range = altMaxVal.value - altMinVal.value
    if (range === 0) return 0
    const deg = ((raw - altMinVal.value) / range) * 180
    return deg + altVisualOffset.value
})

// Three.js SCENE
const canvasContainer = ref(null)
const scene = new THREE.Scene()
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000)
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })

// Refs for 3D objects to animate
const mountGroup = new THREE.Group() // Rotates for AZ
const tubeGroup = new THREE.Group()  // Rotates for ALT

let animationId = null
let resizeObserver = null

function initThree() {
    if (!canvasContainer.value) return

    // Appending first
    const w = canvasContainer.value.clientWidth
    const h = 400 
    renderer.setSize(w, h)
    canvasContainer.value.appendChild(renderer.domElement)
    
    // Initial size check
    onResize()

    // Camera setup
    camera.position.set(5, 5, 5)
    camera.lookAt(0, 1, 0)
    
    // Lights
    const ambientLight = new THREE.AmbientLight(0x404040, 2)
    scene.add(ambientLight)
    const dirLight = new THREE.DirectionalLight(0xffffff, 2)
    dirLight.position.set(5, 10, 7)
    scene.add(dirLight)

    // --- Model ---
    
    // Base (Static)
    const baseGeo = new THREE.CylinderGeometry(1, 1.2, 0.5, 32)
    const baseMat = new THREE.MeshPhongMaterial({ color: 0x333333 })
    const base = new THREE.Mesh(baseGeo, baseMat)
    base.position.y = 0.25
    scene.add(base)

    // AZ Mount (Rotates Y)
    scene.add(mountGroup)
    
    const forkGeo = new THREE.BoxGeometry(1.5, 2, 1)
    const forkMat = new THREE.MeshPhongMaterial({ color: 0x555555 })
    
    // Left Fork
    const forkL = new THREE.Mesh(forkGeo, forkMat)
    forkL.position.set(-0.8, 1.5, 0)
    forkL.scale.set(0.2, 1, 1)
    mountGroup.add(forkL)
    
    // Right Fork
    const forkR = new THREE.Mesh(forkGeo, forkMat)
    forkR.position.set(0.8, 1.5, 0)
    forkR.scale.set(0.2, 1, 1)
    mountGroup.add(forkR)

    // Base of Mount
    const mountBaseGeo = new THREE.CylinderGeometry(0.9, 0.9, 0.2, 32)
    const mountBase = new THREE.Mesh(mountBaseGeo, forkMat)
    mountBase.position.y = 0.6
    mountGroup.add(mountBase)

    // ALT Tube (Rotates X inside Mount)
    mountGroup.add(tubeGroup)
    tubeGroup.position.set(0, 2, 0) // Pivot point at top of forks

    // Telescope Tube
    const tubeGeo = new THREE.CylinderGeometry(0.4, 0.5, 4, 32)
    const tubeMat = new THREE.MeshPhongMaterial({ color: 0xeeeeee })
    const tube = new THREE.Mesh(tubeGeo, tubeMat)
    // Cylinder is Y-up by default. Rotate it to point Z first?
    // We want it pointing "forward" (Z) initially? 
    // Let's make it horizontal: Rotate X 90
    tube.rotation.x = Math.PI / 2
    tubeGroup.add(tube)
    
    // Decoration: Lens cap
    const capGeo = new THREE.CylinderGeometry(0.52, 0.52, 0.2, 32)
    const capMat = new THREE.MeshPhongMaterial({ color: 0x111111 })
    const cap = new THREE.Mesh(capGeo, capMat)
    cap.rotation.x = Math.PI / 2
    cap.position.z = 2 // Front of tube
    tubeGroup.add(cap)

    // Floor Grid
    const gridHelper = new THREE.GridHelper(10, 10, 0x444444, 0x222222)
    scene.add(gridHelper)

    animate()
}

function animate() {
    animationId = requestAnimationFrame(animate)
    
    // Update Rotations based on Computed Values
    // Azimuth: Y axis. Map 0-360 to Radians.
    // Three.js Y is up. Y rotation is Azimuth.
    // Note: Direction might need negation depending on coordinate system.
    mountGroup.rotation.y = - THREE.MathUtils.degToRad(currentAz.value)

    // Altitude: X axis. Map 0-180 to Radians.
    // 0 deg = Horizon? 90 deg = Zenith?
    // Let's assume 0 is Horizon.
    // Our tube defaults to Horizon (Z-forward) when rotation.x is 0 (relative to group? Wait, we verified tube geometry).
    // Actually, cylinder default is Y-up. We rotated it PI/2 (90 deg) around X to make it lie flat (Z-aligned).
    // So group rotation X=0 means pointing Horizon.
    // X increasing -> tilts up?
    tubeGroup.rotation.x = THREE.MathUtils.degToRad(currentAlt.value)

    renderer.render(scene, camera)
}

onMounted(() => {
    initThree()
    window.addEventListener('resize', onResize)
    
    // Robust sizing
    if (canvasContainer.value) {
        resizeObserver = new ResizeObserver(() => onResize())
        resizeObserver.observe(canvasContainer.value)
    }
})

onBeforeUnmount(() => {
    cancelAnimationFrame(animationId)
    window.removeEventListener('resize', onResize)
    if (resizeObserver) resizeObserver.disconnect()
})

function onResize() {
    if (!canvasContainer.value) return
    const w = canvasContainer.value.clientWidth
    const h = 400
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
}

// Helpers
const availableSources = computed(() => {
    const list = []
    for (const dev of store.devices.values()) {
        list.push({
            value: `${dev.id}:0`,
            label: `${dev.name} (Enc 0)`
        })
        list.push({
            value: `${dev.id}:1`,
            label: `${dev.name} (Enc 1)`
        })
    }
    return list
})
</script>

<template>
  <div class="telescope-vis">
    <h3>3D Telescope Visualization</h3>
    
    <div class="controls-grid">
        <div class="control-group">
            <div class="group-title">Azimuth (Y-Axis)</div>
            <label>Encoder Source
                <select v-model="azSelection">
                    <option value="">-- Select --</option>
                    <option v-for="source in availableSources" :key="source.value" :value="source.value">
                        {{ source.label }}
                    </option>
                </select>
            </label>
            <div class="row">
                <label>Min Steps (0°)
                    <input type="number" v-model.number="azMinVal">
                </label>
                <label>Max Steps (360°)
                    <input type="number" v-model.number="azMaxVal">
                </label>
            </div>
            <div class="row">
                <label>Visual Offset (deg)
                    <input type="number" v-model.number="azVisualOffset">
                </label>
            </div>
            <div class="value-display">{{ currentAz.toFixed(1) }}°</div>
        </div>

        <div class="control-group">
            <div class="group-title">Altitude (X-Axis)</div>
            <label>Encoder Source
                <select v-model="altSelection">
                    <option value="">-- Select --</option>
                    <option v-for="source in availableSources" :key="source.value" :value="source.value">
                        {{ source.label }}
                    </option>
                </select>
            </label>
            <div class="row">
                <label>Min Steps (0°)
                    <input type="number" v-model.number="altMinVal">
                </label>
                <label>Max Steps (180°)
                    <input type="number" v-model.number="altMaxVal">
                </label>
            </div>
            <div class="row">
                <label>Visual Offset (deg)
                    <input type="number" v-model.number="altVisualOffset">
                </label>
            </div>
            <div class="value-display">{{ currentAlt.toFixed(1) }}°</div>
        </div>
    </div>

    <div ref="canvasContainer" class="canvas-container"></div>
</div>
</template>

<style scoped>
.telescope-vis {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 2rem;
}

h3 {
    margin-top: 0;
    color: var(--color-primary);
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 0.5rem;
}

.controls-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1rem;
    margin-bottom: 1rem;
}

.control-group {
    background: rgba(0,0,0,0.2);
    padding: 1rem;
    border-radius: 6px;
}

.group-title {
    font-weight: bold;
    margin-bottom: 0.5rem;
    color: var(--color-text-muted);
}

.row {
    display: flex;
    gap: 1rem;
}

label {
    display: block;
    font-size: 0.9em;
    margin-bottom: 0.5rem;
}

select, input {
    width: 100%;
    margin-top: 0.2rem;
}

.value-display {
    text-align: right;
    font-family: monospace;
    font-size: 1.5rem;
    color: var(--color-primary);
    margin-top: 0.5rem;
}

.canvas-container {
    width: 100%;
    height: 400px;
    background: #111;
    border-radius: 6px;
    overflow: hidden;
}
</style>
