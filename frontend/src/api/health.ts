import { get } from './client'
import type { HealthResponse } from '../types'

export const HEALTH_ENDPOINT = '/health'

/** Read-only health readiness probe. Mirrors src/webhook_server.py. */
export function fetchHealth(): Promise<HealthResponse> {
  return get<HealthResponse>(HEALTH_ENDPOINT)
}