import { fetchScanner } from '../api/scanner'
import { fetchPositions } from '../api/positions'
import { fetchGuardian } from '../api/guardian'
import { fetchPerformance } from '../api/performance'
import { fetchHealth } from '../api/health'
/**
 * Convenience data hooks for each domain. All of them route through the honest
 * `useData` gate: UNAVAILABLE when no backend, clearly mocked when DEV MOCK is
 * toggled on.
 */
import { useData } from './useData'
import type { DataResult } from './useData'
import {
  createMockGuardian,
  createMockHealth,
  createMockIntelligence,
  createMockJournal,
  createMockPerformance,
  createMockPositions,
  createMockScanner,
  createMockSystemHealth,
} from '../mocks/devMock'
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

export function useHealthData(): DataResult<HealthResponse> {
  return useData<HealthResponse>({ mock: createMockHealth, fetcher: fetchHealth })
}

export function useScannerData(): DataResult<ScannerRow[]> {
  return useData<ScannerRow[]>({ mock: createMockScanner, fetcher: async () => (await fetchScanner() as any).rows ?? [] })
}

export function usePositionsData(): DataResult<PaperPosition[]> {
  return useData<PaperPosition[]>({ mock: createMockPositions, fetcher: fetchPositions })
}

export function useGuardianData(): DataResult<GuardianStatus> {
  return useData<GuardianStatus>({ mock: createMockGuardian, fetcher: fetchGuardian })
}

export function useJournalData(): DataResult<TradeJournalEntry[]> {
  return useData<TradeJournalEntry[]>({ mock: createMockJournal })
}

export function useIntelligenceData(): DataResult<IntelligenceReport> {
  return useData<IntelligenceReport>({ mock: createMockIntelligence })
}

export function useSystemHealthData(): DataResult<SystemHealth> {
  return useData<SystemHealth>({ mock: createMockSystemHealth })
}

export function usePerformanceData(): DataResult<PerformanceSummary> {
  return useData<PerformanceSummary>({ mock: createMockPerformance, fetcher: fetchPerformance })
}