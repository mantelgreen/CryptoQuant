import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/dashboard/DashboardView.vue'),
  },
  {
    path: '/market/:symbol',
    name: 'market-detail',
    component: () => import('@/views/market/MarketDetailView.vue'),
  },
  {
    path: '/data/downloads',
    name: 'data-downloads',
    component: () => import('@/views/data/DownloadsView.vue'),
  },
  {
    path: '/data/schedules',
    name: 'data-schedules',
    component: () => import('@/views/data/SchedulesView.vue'),
  },
  {
    path: '/strategy',
    name: 'strategy-list',
    component: () => import('@/views/strategy/StrategyListView.vue'),
  },
  {
    path: '/strategy/:id',
    name: 'strategy-detail',
    component: () => import('@/views/strategy/StrategyDetailView.vue'),
  },
  {
    path: '/factor',
    name: 'factor-hub',
    component: () => import('@/views/factor/FactorHubView.vue'),
  },
  {
    path: '/factor/ai-lab',
    name: 'factor-ai-lab',
    component: () => import('@/views/factor/AiFactorLabView.vue'),
  },
  {
    path: '/train',
    name: 'ml-train',
    component: () => import('@/views/train/TrainView.vue'),
  },
  {
    path: '/sim',
    name: 'sim',
    component: () => import('@/views/sim/SimView.vue'),
  },
  {
    path: '/settings/models',
    name: 'settings-models',
    component: () => import('@/views/settings/SettingsModelsView.vue'),
  },
  {
    path: '/analytics/llm',
    name: 'analytics-llm',
    component: () => import('@/views/analytics/AnalyticsLlmView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

export default router
