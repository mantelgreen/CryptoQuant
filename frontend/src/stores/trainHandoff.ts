import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 因子库「候选因子」→ 模型训练页 的一次性交接，避免把长 code 拼进 URL。 */
export const useTrainHandoffStore = defineStore('trainHandoff', () => {
  const pendingCodes = ref<string[]>([])
  const hasPending = ref(false)

  function stage(codes: string[]) {
    pendingCodes.value = [...codes]
    hasPending.value = codes.length > 0
  }

  /** 读取并清空；只被消费一次，避免下次正常进入训练页被残留污染。 */
  function consume(): string[] {
    const codes = pendingCodes.value.slice()
    pendingCodes.value = []
    hasPending.value = false
    return codes
  }

  function clear() {
    pendingCodes.value = []
    hasPending.value = false
  }

  return { pendingCodes, hasPending, stage, consume, clear }
})
