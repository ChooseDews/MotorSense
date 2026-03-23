<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  label: { type: String, default: 'Encoder' },
  inverted: { type: Boolean, default: false }
})

const emit = defineEmits(['toggle-invert'])

const minVal = ref(0)
const maxVal = ref(0)
const hasInitialized = ref(false)

watch(() => props.modelValue, (newVal) => {
    if (!hasInitialized.value) {
        minVal.value = newVal
        maxVal.value = newVal
        hasInitialized.value = true
    } else {
        if (newVal < minVal.value) minVal.value = newVal
        if (newVal > maxVal.value) maxVal.value = newVal
    }
}, { immediate: true })

const percentage = computed(() => {
    const range = maxVal.value - minVal.value
    if (range === 0) return 50 // Center if no range
    return ((props.modelValue - minVal.value) / range) * 100
})
</script>

<template>
  <div class="encoder-bar">
    <div class="info-row">
        <span class="label">{{ label }}</span>
        <div class="controls">
            <button 
                class="invert-btn" 
                :class="{ active: inverted }"
                @click="emit('toggle-invert')"
                title="Toggle A/B channel swap"
            >
                {{ inverted ? '⇄' : '→' }}
            </button>
            <span class="current-value">{{ modelValue }}</span>
        </div>
    </div>
    
    <div class="bar-container">
        <div class="limit min">{{ minVal }}</div>
        
        <div class="track">
            <div 
                class="thumb" 
                :style="{ left: `${percentage}%` }"
            ></div>
            <!-- Optional: Fill from center or 0 if appropriate, for now just a position marker -->
        </div>
        
        <div class="limit max">{{ maxVal }}</div>
    </div>
  </div>
</template>

<style scoped>
.encoder-bar {
    background: var(--color-bg);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    border: 1px solid var(--color-border);
    width: 100%;
}

.info-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.25rem;
}

.controls {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.invert-btn {
    padding: 0.15rem 0.4rem;
    font-size: 0.75em;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    border-radius: 3px;
    cursor: pointer;
    transition: all 0.2s;
    min-width: 28px;
}

.invert-btn:hover {
    background: var(--color-surface);
}

.invert-btn.active {
    background: var(--color-primary);
    color: white;
    border-color: var(--color-primary);
}

.label {
    font-size: 0.85em;
    color: var(--color-text-muted);
    font-weight: 500;
}

.current-value {
    font-family: monospace;
    font-weight: bold;
    color: var(--color-primary);
}

.bar-container {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.limit {
    font-size: 0.75em;
    color: var(--color-text-muted);
    min-width: 2em;
    text-align: center;
}

.track {
    flex: 1;
    height: 6px;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 99px;
    position: relative;
    /* overflow: hidden; Thumbs might stick out slightly */
}

.thumb {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    width: 12px;
    height: 12px;
    background: var(--color-primary);
    border: 2px solid white;
    border-radius: 50%;
    transition: left 0.1s linear;
}
</style>
