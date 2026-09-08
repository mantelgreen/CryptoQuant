import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useSidebarStore = defineStore(
  'sidebar',
  () => {
    const collapsed = ref(false)

    function toggle() {
      collapsed.value = !collapsed.value
    }

    function set(v: boolean) {
      collapsed.value = v
    }

    return { collapsed, toggle, set }
  },
  {
    persist: {
      key: 'cq:sidebar',
      paths: ['collapsed'],
    },
  },
)