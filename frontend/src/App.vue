<script setup lang="ts">
import { onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useThemeStore } from '@/stores/theme'

const theme = useThemeStore()
onMounted(() => {
  theme.applyInitial()
})
</script>

<template>
  <AppShell />
  <ToastHost />
</template>
