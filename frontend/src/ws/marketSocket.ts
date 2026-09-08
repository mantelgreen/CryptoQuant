/** 实时行情 WebSocket 客户端（配套后端 /ws/market 协议）。 */
import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

export type StreamKind = 'trade' | 'kline' | 'depth' | 'ticker'

export interface SubChannel {
  symbol: string
  kind: StreamKind
  interval?: string
}

export interface TradeEv {
  type: 'trade'
  symbol: string
  trade: { id: number; time: number; price: number; qty: number; side: 'buy' | 'sell' }
}
export interface KlineEv {
  type: 'kline'
  symbol: string
  interval: string
  bar: { t: number; o: number; h: number; l: number; c: number; v: number; closed: boolean; T: number }
}
export interface DepthEv {
  type: 'depth'
  symbol: string
  depth: { lastUpdateId: number; bids: [string, string][]; asks: [string, string][] }
}
export interface TickerEv {
  type: 'ticker'
  symbol: string
  ticker: {
    lastPrice: string
    openPrice: string
    highPrice: string
    lowPrice: string
    priceChangePercent: string
    quoteVolume: string
    bidPrice: string
    askPrice: string
  }
}
export type MarketEvent = TradeEv | KlineEv | DepthEv | TickerEv

export interface MarketSocket {
  connected: Ref<boolean>
  sendSub: (channels: SubChannel[]) => void
  close: () => void
}

function signature(channels: SubChannel[]): string {
  return channels
    .map((c) => `${c.symbol.toLowerCase()}:${c.kind}:${c.interval ?? ''}`)
    .sort()
    .join(',')
}

/**
 * 打开一个 /ws/market 连接并持续订阅给定频道。
 * - channels 建议为响应式 getter；变化时自动重发 sub。
 * - handler 收到每次 batch 中的事件。
 * - 卸载时自动断开。同一页面通常只建一个连接。
 */
export function useMarketSocket(
  channels: () => SubChannel[],
  handler: (ev: MarketEvent) => void,
): MarketSocket {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let retryTimer: number | null = null
  let pingTimer: number | null = null
  let current = ''
  let wantClose = false
  let opened = false

  function safeSignature(): string {
    return signature(channels())
  }

  function sendSub() {
    const list = channels()
    current = signature(list)
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'sub', channels: list }))
  }

  function connect() {
    if (wantClose) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/ws/market`)
    opened = false

    ws.onopen = () => {
      connected.value = true
      ws!.send(JSON.stringify({ type: 'auth', token: 'M1' }))
      opened = true
      sendSub()
      pingTimer = window.setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
        }
      }, 20_000)
    }
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string)
        if (msg.type === 'batch' && Array.isArray(msg.items)) {
          for (const item of msg.items) handler(item as MarketEvent)
        }
      } catch {
        /* ignore malformed */
      }
    }
    ws.onclose = () => {
      connected.value = false
      if (pingTimer) {
        clearInterval(pingTimer)
        pingTimer = null
      }
      ws = null
      if (!wantClose) retryTimer = window.setTimeout(connect, 2000)
    }
    ws.onerror = () => {
      ws?.close()
    }
  }

  watch(
    channels,
    () => {
      if (signature(channels()) !== current) sendSub()
    },
    { deep: true },
  )

  connect()
  onBeforeUnmount(() => {
    wantClose = true
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (pingTimer) {
      clearInterval(pingTimer)
      pingTimer = null
    }
    ws?.close()
    ws = null
  })

  return {
    connected,
    sendSub,
    close: () => {
      wantClose = true
      ws?.close()
    },
  }
}
