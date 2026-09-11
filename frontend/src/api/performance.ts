import { get } from './client'
import type { PerformanceSummary } from '../types'

export const PERFORMANCE_ENDPOINT = '/performance'

/** Performance summary snapshot (planned GET /api/v1/performance). */
export function fetchPerformance(): Promise<PerformanceSummary> {
  return get<PerformanceSummary>(PERFORMANCE_ENDPOINT)
}