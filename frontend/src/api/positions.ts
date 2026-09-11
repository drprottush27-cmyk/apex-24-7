import { get } from './client'
import type { PaperPosition } from '../types'

export const POSITIONS_ENDPOINT = '/positions'

/** Paper positions snapshot (planned GET /api/v1/positions). */
export function fetchPositions(): Promise<PaperPosition[]> {
  return get<PaperPosition[]>(POSITIONS_ENDPOINT)
}