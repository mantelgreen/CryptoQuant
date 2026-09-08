<script setup lang="ts">
import { computed, ref, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Gauge,
  Database,
  Clock3,
  FlaskConical,
  Boxes,
  Sparkles,
  Wallet,
  Bot,
  Cpu,
  SlidersHorizontal,
  Activity,
  Sun,
  Moon,
  Menu,
  X,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from '@lucide/vue'
import { useThemeStore } from '@/stores/theme'
import { useSidebarStore } from '@/stores/sidebar'

interface NavItem {
  label: string
  to?: string
  icon: Component
  soon?: boolean
}
interface NavGroup {
  label: string
  items: NavItem[]
}

const nav: NavGroup[] = [
  {
    label: '市场',
    items: [{ label: '行情看板', to: '/dashboard', icon: Gauge }],
  },
  {
    label: '数据',
    items: [
      { label: 'K 线下载', to: '/data/downloads', icon: Database },
      { label: '定时任务', to: '/data/schedules', icon: Clock3 },
    ],
  },
  {
    label: '研究',
    items: [
      { label: '策略研究', to: '/strategy', icon: FlaskConical },
      { label: '因子库', to: '/factor', icon: Boxes },
      { label: 'AI 因子工坊', to: '/factor/ai-lab', icon: Sparkles },
      { label: '模型训练', to: '/train', icon: Cpu },
    ],
  },
  {
    label: '模拟 · LLM',
    items: [
      { label: '模拟交易', to: '/sim', icon: Wallet },
      { label: '模型设置', to: '/settings/models', icon: SlidersHorizontal },
      { label: 'LLM 用量', to: '/analytics/llm', icon: Activity },
      { label: 'Agent 实验室', icon: Bot, soon: true },
    ],
  },
]

const theme = useThemeStore()
const sidebar = useSidebarStore()
const route = useRoute()
const router = useRouter()
const drawerOpen = ref(false)

const current = computed(() => {
  const flat = nav.flatMap((g) => g.items)
  const hit = activeTarget(flat)
  return flat.find((i) => i.to === hit) ?? null
})
const groupOf = computed(() => {
  const g = nav.find((grp) => grp.items.some((i) => i.to && route.path.startsWith(i.to)))
  return g?.label
})

function activeTarget(flat: NavItem[]): string | null {
  let best: string | null = null
  for (const it of flat) {
    if (!it.to) continue
    const matched = route.path === it.to || route.path.startsWith(it.to + '/')
    if (matched && (!best || it.to.length > best.length)) best = it.to
  }
  return best
}

function isActive(path: string) {
  const flat = nav.flatMap((g) => g.items)
  return activeTarget(flat) === path
}

function navigate(item: NavItem) {
  drawerOpen.value = false
  if (item.to && route.path !== item.to) router.push(item.to)
}
</script>

<template>
  <div class="flex h-full overflow-hidden bg-bg">
    <!-- 桌面侧栏 -->
    <aside
      class="hidden shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-200 ease-out lg:flex"
      :class="sidebar.collapsed ? 'w-[64px]' : 'w-[238px]'"
    >
      <div
        class="flex items-center px-5 pb-2 pt-5"
        :class="sidebar.collapsed ? 'justify-center px-0' : ''"
      >
        <router-link
          to="/dashboard"
          class="flex items-center gap-2.5"
          :title="sidebar.collapsed ? 'CryptoQuant' : undefined"
        >
          <span
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-[#0e7490] to-[#134e5e] font-display text-[13px] font-bold text-white dark:from-[#4fd0f0] dark:to-[#1f6d85] dark:text-[#06262f]"
          >
            CQ
          </span>
          <span v-if="!sidebar.collapsed" class="flex flex-col leading-none">
            <span class="font-display text-[15px] font-bold tracking-tight text-fg">CryptoQuant</span>
            <span class="mt-1 text-[10px] tracking-[0.14em] text-subtle">数字币量化研究</span>
          </span>
        </router-link>
      </div>

      <nav class="min-h-0 flex-1 space-y-5 overflow-y-auto px-3 pb-4 pt-4">
        <div v-for="g in nav" :key="g.label">
          <p
            v-if="!sidebar.collapsed"
            class="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-subtle"
          >
            {{ g.label }}
          </p>
          <p v-else class="mx-auto mb-1.5 h-px w-6 bg-border" />
          <ul class="space-y-0.5">
            <li v-for="it in g.items" :key="it.label">
              <button
                v-if="it.soon"
                class="flex w-full cursor-not-allowed items-center gap-2.5 rounded-[9px] py-[7px] text-left text-[13px] text-subtle"
                :class="sidebar.collapsed ? 'justify-center px-0' : 'px-2.5'"
                :title="sidebar.collapsed ? `${it.label}（规划中）` : undefined"
                disabled
              >
                <component :is="it.icon" :size="16" class="shrink-0 opacity-70" />
                <span v-if="!sidebar.collapsed" class="min-w-0 flex-1 truncate">{{ it.label }}</span>
                <span
                  v-if="!sidebar.collapsed"
                  class="rounded border border-border px-1.5 py-px text-[9px] tracking-wide text-subtle"
                >
                  规划中
                </span>
              </button>
              <router-link
                v-else
                :to="it.to!"
                class="flex items-center gap-2.5 rounded-[9px] py-[7px] text-[13px] font-medium transition-colors"
                :class="[
                  sidebar.collapsed ? 'justify-center px-0' : 'px-2.5',
                  isActive(it.to!)
                    ? 'tint-primary text-primary-ink dark:text-primary'
                    : 'text-muted hover:bg-surface-2 hover:text-fg',
                ]"
                :title="sidebar.collapsed ? it.label : undefined"
              >
                <component :is="it.icon" :size="16" class="shrink-0" />
                <span v-if="!sidebar.collapsed" class="min-w-0 flex-1 truncate">{{ it.label }}</span>
              </router-link>
            </li>
          </ul>
        </div>
      </nav>

      <div
        class="flex items-center border-t border-border px-3 py-3"
        :class="sidebar.collapsed ? 'justify-center' : 'justify-between'"
      >
        <button
          class="btn btn-icon text-muted hover:border-strong hover:text-fg"
          :aria-label="sidebar.collapsed ? '展开侧栏' : '收起侧栏'"
          :title="sidebar.collapsed ? '展开侧栏' : '收起侧栏'"
          @click="sidebar.toggle()"
        >
          <PanelLeftClose v-if="!sidebar.collapsed" :size="16" />
          <PanelLeftOpen v-else :size="16" />
        </button>
        <div v-if="!sidebar.collapsed">
          <p class="text-[10px] leading-relaxed text-subtle">CryptoQuant · Research Terminal</p>
          <p class="mt-0.5 font-mono text-[10px] text-subtle">v0.1.0 · data → factor → strategy</p>
        </div>
      </div>
    </aside>

    <!-- 移动端抽屉 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="drawerOpen" class="fixed inset-0 z-[70] bg-black/40 lg:hidden" @click="drawerOpen = false" />
      </Transition>
      <Transition name="slide">
        <aside v-if="drawerOpen" class="fixed inset-y-0 left-0 z-[71] flex w-[262px] flex-col bg-surface shadow-pop lg:hidden">
          <div class="flex items-center justify-between border-b border-border pr-2 pl-5">
            <router-link to="/dashboard" class="flex items-center gap-2.5 py-4">
              <span
                class="flex h-8 w-8 items-center justify-center rounded-[10px] bg-gradient-to-br from-[#0e7490] to-[#134e5e] font-display text-[13px] font-bold text-white dark:from-[#4fd0f0] dark:to-[#1f6d85] dark:text-[#06262f]"
              >
                CQ
              </span>
              <span class="font-display text-[15px] font-bold tracking-tight text-fg">CryptoQuant</span>
            </router-link>
            <button class="btn btn-icon text-muted" aria-label="关闭菜单" @click="drawerOpen = false">
              <X :size="17" />
            </button>
          </div>
          <nav class="min-h-0 flex-1 space-y-5 overflow-y-auto px-3 pb-4 pt-4">
            <div v-for="g in nav" :key="g.label">
              <p class="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-subtle">{{ g.label }}</p>
              <ul class="space-y-0.5">
                <li v-for="it in g.items" :key="it.label">
                  <button
                    v-if="it.soon"
                    class="flex w-full cursor-not-allowed items-center gap-2.5 rounded-[9px] px-2.5 py-[7px] text-left text-[13px] text-subtle"
                    disabled
                  >
                    <component :is="it.icon" :size="16" class="shrink-0 opacity-70" />
                    <span class="min-w-0 flex-1 truncate">{{ it.label }}</span>
                  </button>
                  <button
                    v-else
                    class="flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-[7px] text-left text-[13px] font-medium transition-colors"
                    :class="
                      isActive(it.to!)
                        ? 'tint-primary text-primary-ink dark:text-primary'
                        : 'text-muted hover:bg-surface-2 hover:text-fg'
                    "
                    @click="navigate(it)"
                  >
                    <component :is="it.icon" :size="16" class="shrink-0" />
                    <span class="min-w-0 flex-1 truncate">{{ it.label }}</span>
                  </button>
                </li>
              </ul>
            </div>
          </nav>
          <div class="border-t border-border px-5 py-3.5">
            <p class="text-[10px] leading-relaxed text-subtle">CryptoQuant · v0.1.0</p>
          </div>
        </aside>
      </Transition>
    </Teleport>

    <!-- 主列 -->
    <div class="flex min-w-0 flex-1 flex-col">
      <header
        class="glass sticky top-0 z-40 flex h-[54px] shrink-0 items-center gap-3 border-b border-border px-4 sm:px-6"
      >
        <button class="btn btn-icon -ml-1 text-muted lg:hidden" aria-label="打开菜单" @click="drawerOpen = true">
          <Menu :size="18" />
        </button>
        <button
          class="btn btn-icon hidden -ml-1 text-muted lg:inline-flex"
          :aria-label="sidebar.collapsed ? '展开侧栏' : '收起侧栏'"
          :title="sidebar.collapsed ? '展开侧栏' : '收起侧栏'"
          @click="sidebar.toggle()"
        >
          <PanelLeftClose v-if="!sidebar.collapsed" :size="16" />
          <PanelLeftOpen v-else :size="16" />
        </button>
        <div class="flex min-w-0 items-center gap-1.5 text-[13px]">
          <span class="hidden text-subtle sm:inline">{{ groupOf }}</span>
          <ChevronRight v-if="groupOf" :size="13" class="hidden text-subtle sm:inline" />
          <span class="truncate font-semibold text-fg">{{ current?.label ?? '控制台' }}</span>
        </div>
        <div class="ml-auto flex items-center gap-1.5">
          <span
            class="mr-1 hidden items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[11px] font-medium text-muted sm:inline-flex"
          >
            <span class="h-1.5 w-1.5 rounded-full bg-up" />
            数据服务在线
          </span>
          <button
            class="btn btn-icon text-muted hover:border-strong hover:text-fg"
            :aria-label="theme.isDark ? '切换到亮色主题' : '切换到暗色主题'"
            :title="theme.isDark ? '亮色主题' : '暗色主题'"
            @click="theme.toggle()"
          >
            <Sun v-if="theme.isDark" :size="16" />
            <Moon v-else :size="16" />
          </button>
        </div>
      </header>

      <main class="min-h-0 flex-1 overflow-y-auto">
        <div class="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <router-view v-slot="{ Component }">
            <transition name="page" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: transform 0.22s cubic-bezier(0.2, 0.8, 0.3, 1);
}
.slide-enter-from,
.slide-leave-to {
  transform: translateX(-100%);
}
.page-enter-active,
.page-leave-active {
  transition: opacity 0.14s ease, transform 0.14s ease;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.page-leave-to {
  opacity: 0;
}
</style>