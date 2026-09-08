/** 因子库示例数据 —— 后端 factor 域尚未开放，此模块仅用于演示信息架构与视觉。
 *  接入真实因子注册表后删除本文件并接通 /api/v1/factors。 */

export interface DemoFactor {
  id: string
  name: string
  code: string
  description: string
  tags: string[]
  ic: number
  rankIc: number
  author: '内置' | 'AI'
  updatedAt: string
  formula: string
}

export const TAG_OPTIONS = ['动量', '反转', '波动', '资金流', '量价', '均值回归']

export const demoFactors: DemoFactor[] = [
  {
    id: 'F0101',
    name: '12-1 动量',
    code: 'momentum_12_1',
    description: '过去 12 期收益减最近 1 期收益，捕捉中短期趋势的延续。',
    tags: ['动量'],
    ic: 0.041,
    rankIc: 0.052,
    author: '内置',
    updatedAt: '2026-08-18',
    formula: 'close.shift(12) / close.shift(1) - 1',
  },
  {
    id: 'F0102',
    name: 'RSI 超卖反转',
    code: 'rsi_oversold_rev',
    description: 'RSI 深度超卖后价格弹性，用于均值回归思路。',
    tags: ['反转', '均值回归'],
    ic: 0.028,
    rankIc: 0.039,
    author: '内置',
    updatedAt: '2026-08-12',
    formula: '-(rsi(14) - 50) / 50',
  },
  {
    id: 'F0103',
    name: '波动率收缩',
    code: 'vol_squeeze',
    description: '窄幅整理的 ATR 收缩信号，往往跟随方向性突破。',
    tags: ['波动'],
    ic: 0.017,
    rankIc: 0.022,
    author: '内置',
    updatedAt: '2026-07-30',
    formula: '-atr(14) / close.rolling(60).std()',
  },
  {
    id: 'F0104',
    name: 'OBV 资金斜率',
    code: 'obv_slope',
    description: '量能方向验证价格：OBV 斜率与价格方向背离时提示风险。',
    tags: ['资金流', '量价'],
    ic: 0.033,
    rankIc: 0.041,
    author: 'AI',
    updatedAt: '2026-08-21',
    formula: 'obv.rolling(10).mean() - obv.rolling(3).mean()',
  },
  {
    id: 'F0105',
    name: '日内区间位置',
    code: 'day_range_pos',
    description: '收盘价在当日高低区间中的位置，刻画日内买卖压力。',
    tags: ['量价', '反转'],
    ic: 0.046,
    rankIc: 0.058,
    author: '内置',
    updatedAt: '2026-08-05',
    formula: '(close - low) / (high - low) - 0.5',
  },
  {
    id: 'F0106',
    name: '量比异动',
    code: 'volume_ratio',
    description: '放量程度相对近期均值，配合涨跌幅可做突破确认。',
    tags: ['量价', '动量'],
    ic: 0.022,
    rankIc: 0.027,
    author: 'AI',
    updatedAt: '2026-08-25',
    formula: 'volume / volume.rolling(20).mean() - 1',
  },
]

export const recommendedFactors = demoFactors.slice(0, 4)
