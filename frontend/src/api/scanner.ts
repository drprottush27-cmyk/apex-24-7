import { get } from './client'
import type { ScannerRow } from '../types'

export const SCANNER_ENDPOINT = '/scanner'

/** Scanner snapshot (planned GET /api/v1/scanner). */
export function fetchScanner(): Promise<ScannerRow[]> {
  return get<ScannerRow[]>(SCANNER_ENDPOINT)
}