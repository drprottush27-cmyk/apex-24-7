/**
 * DEVELOPMENT MOCK FIXTURES — NOT REAL PRODUCTION DATA.
 *
 * This file exists ONLY so the UI can be developed against a known shape.
 * Every value here is fabricated and must never be mistaken for live data.
 * Fixtures are surfaced ONLY when DEVELOPMENT MOCK mode is explicitly enabled
 * in the UI, where they are persistently labelled as mock.
 *
 * The production app renders DATA UNAVAILABLE whenever mock mode is off.
 */
import type {
  GuardianStatus,
  HealthResponse,
  IntelligenceReport,
  PaperPosition,
  PerformanceSummary,
  ScannerRow,
  SystemHealth,
  TradeJournalEntry,
} from '../types'

export const DEVELOPMENT_MOCK_LABEL = 'DEVELOPMENT MOCK'

const clone = <T,>(value: T): T => structuredClone(value)

export const MOCK_HEALTH: HealthResponse = {
  status: 'healthy',
  service: 'aegis-webhook',
  tradingMode: 'PAPER',
  liveTradingEnabled: false,
  circuitBreakerTripped: false,
  openPositionsCount: 0,
}

export const MOCK_SCANNER: ScannerRow[] = [
  { symbol: 'BTC-USDT', venue: 'BINANCE', price: 66482.5, marketRegime: 'BULLISH TREND', trend: 'UP', adx: 34, rsi: 61, rvol: 1.48, liquidity: 'HIGH', setupGrade: 7.2, riskStatus: 'CLEAR' },
  { symbol: 'ETH-USDT', venue: 'BINANCE', price: 3452.1, marketRegime: 'RANGING', trend: 'FLAT', adx: 18, rsi: 52, rvol: 0.94, liquidity: 'HIGH', setupGrade: 0, riskStatus: 'CLEAR' },
  { symbol: 'SOL-USDT', venue: 'OKX', price: 146.4, marketRegime: 'BREAKOUT', trend: 'UP', adx: 38, rsi: 66, rvol: 2.4, liquidity: 'HIGH', setupGrade: 8.2, riskStatus: 'WATCH' },
  { symbol: 'INJ-USDT', venue: 'BINANCE', price: 23.46, marketRegime: 'BULLISH TREND', trend: 'UP', adx: 30, rsi: 58, rvol: 1.8, liquidity: 'MEDIUM', setupGrade: 7.0, riskStatus: 'WATCH' },
  { symbol: 'SUI-USDT', venue: 'OKX', price: 2.04, marketRegime: 'BREAKOUT', trend: 'UP', adx: 40, rsi: 69, rvol: 3.2, liquidity: 'MEDIUM', setupGrade: 8.0, riskStatus: 'LIMITED' },
]

export const MOCK_POSITIONS: PaperPosition[] = [
  { id: 'PAPER-0001', symbol: 'BTC-USDT', side: 'LONG', entry: 64220, currentPrice: 66482.5, stopLoss: 63100, takeProfit: 69000, riskReward: 2.4, unrealizedPnl: 226.25, riskPct: 0.9, status: 'OPEN', openedAt: null },
  { id: 'PAPER-0002', symbol: 'ETH-USDT', side: 'SHORT', entry: 3510, currentPrice: 3452.1, stopLoss: 3580, takeProfit: 3320, riskReward: 1.9, unrealizedPnl: 121.4, riskPct: 0.6, status: 'OPEN', openedAt: null },
]

export const MOCK_GUARDIAN: GuardianStatus = {
  state: 'ARMED',
  circuitBreaker: 'ARMED',
  dailyDrawdownPct: -0.42,
  maxDailyDrawdownPct: -3.0,
  activeRiskPct: 1.5,
  openPositionCount: 2,
  maxPositionCount: 10,
  portfolioExposurePct: 18.4,
}

export const MOCK_JOURNAL: TradeJournalEntry[] = [
  { tradeId: 'PAPER-0001', symbol: 'BTC-USDT', direction: 'LONG', entry: 64220, exit: null, pnl: null, riskReward: 2.4, status: 'OPEN', openTime: null, closeTime: null },
  { tradeId: 'PAPER-0002', symbol: 'ETH-USDT', direction: 'SHORT', entry: 3510, exit: null, pnl: null, riskReward: 1.9, status: 'OPEN', openTime: null, closeTime: null },
]

export const MOCK_INTELLIGENCE: IntelligenceReport = {
  regime: 'BULLISH TREND',
  marketSummary: null,
  setupExplanation: null,
  supportingFactors: ['Trend alignment', 'Momentum', 'Volume confirmation'],
  riskFactors: ['Nearby resistance'],
  guardianDecision: 'PENDING',
}

export const MOCK_SYSTEM_HEALTH: SystemHealth = {
  components: {
    scanner: 'HEALTHY',
    marketData: 'HEALTHY',
    api: 'HEALTHY',
    riskGuardian: 'HEALTHY',
    paperEngine: 'HEALTHY',
  },
  lastScan: null,
  lastUpdate: null,
}

export const MOCK_PERFORMANCE: PerformanceSummary = {
  period: 'DEV MOCK — NO REAL TRADES',
  netPnl: null,
  netPnlPct: null,
  winRate: null,
  realizedPnl: null,
  unrealizedPnl: null,
  dayDrawdownPct: null,
}

export function createMockHealth(): HealthResponse {
  return clone(MOCK_HEALTH)
}

export function createMockScanner(): ScannerRow[] {
  return clone(MOCK_SCANNER)
}

export function createMockPositions(): PaperPosition[] {
  return clone(MOCK_POSITIONS)
}

export function createMockGuardian(): GuardianStatus {
  return clone(MOCK_GUARDIAN)
}

export function createMockJournal(): TradeJournalEntry[] {
  return clone(MOCK_JOURNAL)
}

export function createMockIntelligence(): IntelligenceReport {
  return clone(MOCK_INTELLIGENCE)
}

export function createMockSystemHealth(): SystemHealth {
  return clone(MOCK_SYSTEM_HEALTH)
}

export function createMockPerformance(): PerformanceSummary {
  return clone(MOCK_PERFORMANCE)
}