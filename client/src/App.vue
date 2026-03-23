<script setup>
import BleManager from './components/BleManager.vue'
</script>

<template>
  <div class="app-container">
    <header>
      <h1>MotorSense Hub</h1>
    </header>
    
    <main>
      <BleManager />
    </main>
  </div>
</template>

<style scoped>
header {
  margin-bottom: 2rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 1rem;
}

h1 {
  margin: 0;
  font-size: 2rem;
  background: -webkit-linear-gradient(315deg, #42d392 25%, #647eff);
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.subtitle {
  color: var(--color-text-muted);
  margin-top: 0.5rem;
}
</style>
