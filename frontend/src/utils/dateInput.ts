/**
 * 原生 date / datetime-local / month 控件的「年」字段允许输入超过 4 位
 * （浏览器规范上限是 275760），用户可能输入 5 位年份并被当作合法日期提交。
 * 这里用事件委托在输入阶段统一把年份截断为 4 位，覆盖全站所有日期控件。
 */
const DATE_INPUT_TYPES = new Set(['date', 'datetime-local', 'month'])

function clampYear(el: HTMLInputElement): void {
  const v = el.value
  if (!v) return
  const m = /^(\d+)(-\d{2}.*)$/.exec(v)
  if (!m || m[1].length <= 4) return
  el.value = `${m[1].slice(0, 4)}${m[2]}`
}

export function installDateInputGuard(): void {
  const handler = (e: Event) => {
    const el = e.target
    if (el instanceof HTMLInputElement && DATE_INPUT_TYPES.has(el.type)) clampYear(el)
  }
  document.addEventListener('input', handler, true)
  document.addEventListener('change', handler, true)
}
