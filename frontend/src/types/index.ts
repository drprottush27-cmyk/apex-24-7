/**
 * AEGIS ALPHA — shared domain types.
 *
 * Data honesty contract: numeric/status values that are not yet provided by a
 * live backend are represented as `null` or the `DATA_UNAVAILABLE` sentinel and
 * are NEVER invented by the UI.
 */

export const DATA_UNAVAILABLE = 'DATA_UNAVAILABLE'

/** Aggregated state of a data feed within the UI. */
export type AsyncStatus = 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE'

/** Where the data actually came from. */
export type DataSource = 'none' | 'backend' | 'devmock'

/** Envelope that every dynamic payload flows through. */
export interface DataEnvelope<T> {
  status: AsyncStatus
  source: DataSource
  data: T | null
  updatedAt: string | null
}

/** Backend service health per component. */
export type ComponentHealth =
  | 'HEALTHY'
  | 'DEGRADED'
  | 'OFFLINE'
  | typeof DATA_UNAVAILABLE

/** Mirrors GET /api/v1/health in src/webhook_server.py. */
export interface HealthResponse {
  status: string
  service: string
  tradingMode: string
  liveTradingEnabled: boolean
  circuitBreakerTripped: boolean | null
  openPositionsCount: number | null
}

export type MarketRegimeLabel =
  | 'BULLISH TREND'
  | 'BEARISH TREND'
  | 'RANGING'
  | 'BREAKOUT'
  | 'BREAKDOWN'
  | typeof DATA_UNAVAILABLE

export type TrendLabel = 'UP' | 'DOWN' | 'FLAT' | typeof DATA_UNAVAILABLE

export type LiquidityLabel = 'HIGH' | 'MEDIUM' | 'LOW' | typeof DATA_UNAVAILABLE

export type RiskStatusLabel =
  | 'CLEAR'
  | 'WATCH'
  | 'LIMITED'
  | 'BLOCKED'
  | typeof DATA_UNAVAILABLE

export interface ScannerRow {
  symbol: string
  venue: string
  price: number | null
  marketRegime: MarketRegimeLabel
  trend: TrendLabel
  adx: number | null
  rsi: number | null
  rvol: number | null
  liquidity: LiquidityLabel
  setupGrade: number | null
  riskStatus: RiskStatusLabel
}

export type PositionSide = 'LONG' | 'SHORT'

export type PositionStatus = 'OPEN' | 'CLOSED' | typeof DATA_UNAVAILABLE

export interface PaperPosition {
  id: string
  symbol: string
  side: PositionSide
  entry: number | null
  currentPrice: number | null
  stopLoss: number | null
  takeProfit: number | null
  riskReward: number | null
  unrealizedPnl: number | null
  riskPct: number | null
  status: PositionStatus
  openedAt: string | null
}

export type GuardianStateLabel =
  | 'ARMED'
  | 'CAUTION'
  | 'TRIPPED'
  | typeof DATA_UNAVAILABLE

export type CircuitBreakerLabel = 'ENGAGED' | 'ARMED' | typeof DATA_UNAVAILABLE

export interface GuardianStatus {
  state: GuardianStateLabel
  circuitBreaker: CircuitBreakerLabel
  dailyDrawdownPct: number | null
  maxDailyDrawdownPct: number | null
  activeRiskPct: number | null
  openPositionCount: number | null
  maxPositionCount: number | null
  portfolioExposurePct: number | null
}

export interface TradeJournalEntry {
  tradeId: string
  symbol: string
  direction: PositionSide
  entry: number | null
  exit: number | null
  pnl: number | null
  riskReward: number | null
  status: PositionStatus
  openTime: string | null
  closeTime: string | null
}

export type GuardianDecisionLabel =
  | 'APPROVED FOR PAPER'
  | 'REJECTED'
  | 'PENDING'
  | typeof DATA_UNAVAILABLE

export interface IntelligenceReport {
  regime: MarketRegimeLabel
  marketSummary: string | null
  setupExplanation: string | null
  supportingFactors: string[]
  riskFactors: string[]
  guardianDecision: GuardianDecisionLabel
}

export interface SystemHealth {
  components: {
    scanner: ComponentHealth
    marketData: ComponentHealth
    api: ComponentHealth
    riskGuardian: ComponentHealth
    paperEngine: ComponentHealth
  }
  lastScan: string | null
  lastUpdate: string | null
}

export interface PerformanceSummary {
  period: string
  netPnl: number | null
  netPnlPct: number | null
  winRate: number | null
  realizedPnl: number | null
  unrealizedPnl: number | null
  dayDrawdownPct: number | null
}

/** Static app constants that are safe to display without backend data. */
export interface StaticSystemMeta {
  engineName: 'AEGIS ALPHA'
  subtitle: 'INTELLIGENT MARKET DEFENSE'
  tradingMode: 'PAPER MODE'
  liveTradingEnabled: false
}