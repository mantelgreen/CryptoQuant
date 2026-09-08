import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ToastItem {
  id: number
  tone: 'success' | 'error' | 'info' | 'ai'
  title: string
  desc?: string
}

let seed = 0

export const useToastStore = defineStore('toast', () => {
  const items = ref<ToastItem[]>([])

  function push(tone: ToastItem['tone'], title: string, desc?: string) {
    const id = ++seed
    items.value.push({ id, tone, title, desc })
    window.setTimeout(() => remove(id), tone === 'error' ? 5200 : 3400)
  }

  function remove(id: number) {
    items.value = items.value.filter((t) => t.id !== id)
  }

  function success(title: string, desc?: string) {
    push('success', title, desc)
  }
  function error(title: string, desc?: string) {
    push('error', title, desc)
  }
  function info(title: string, desc?: string) {
    push('info', title, desc)
  }
  function ai(title: string, desc?: string) {
    push('ai', title, desc)
  }

  return { items, push, remove, success, error, info, ai }
})
