/**
 * useData — data gate hook.
 *
 * Phase UI-1: no backend wiring. Data is only AVAILABLE when the operator
 * enables DEVELOPMENT MOCK mode; otherwise it is honestly UNAVAILABLE.
 * `env` passes typed data, `lazyMock` defers generation.
 */
import { useMemo } from 'react'
import { useMockMode } from '../state/mockMode'
import type { DataEnvelope } from '../types'

export interface UseDataOptions<T> {
  /** Sync — used when mock mode is enabled. */
  mock: () => T
  /** Prepared for a future phase: real backend fetcher. Ignored until wired. */
  fetcher?: never
}

export interface DataResult<T> extends DataEnvelope<T> {
  isMock: boolean
}

export function useData<T>({ mock, fetcher }: UseDataOptions<T>): DataResult<T> {
  const { mockEnabled } = useMockMode()

  return useMemo<DataResult<T>>(() => {
    void fetcher
    if (mockEnabled) {
      const data = mock()
      return {
        status: 'AVAILABLE',
        source: 'devmock',
        data,
        updatedAt: new Date().toISOString(),
        isMock: true,
      }
    }
    return {
      status: 'UNAVAILABLE',
      source: 'none',
      data: null,
      updatedAt: null,
      isMock: false,
    }
  }, [mock, mockEnabled, fetcher])
}