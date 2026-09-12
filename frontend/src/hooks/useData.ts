/**
 * useData — data gate hook.
 *
 * Provides real-time polling against backend API endpoints when available,
 * falls back cleanly to mock data when dev mock mode is enabled, or returns
 * UNAVAILABLE if disconnected.
 */
import { useState, useEffect } from 'react'
import { useMockMode } from '../state/mockMode'
import type { DataEnvelope, DataSource } from '../types'

export interface UseDataOptions<T> {
  mock: () => T
  fetcher?: () => Promise<T>
}

export interface DataResult<T> extends DataEnvelope<T> {
  isMock: boolean
}

export function useData<T>({ mock, fetcher }: UseDataOptions<T>): DataResult<T> {
  const { mockEnabled } = useMockMode()
  const [liveData, setLiveData] = useState<T | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  useEffect(() => {
    if (mockEnabled || typeof fetcher !== 'function') return
    const currentFetcher = fetcher
    let active = true

    const load = async () => {
      try {
        const res = await currentFetcher()
        if (active) {
          setLiveData(res)
          setUpdatedAt(new Date().toISOString())
        }
      } catch {
        // Retain unavailable on failure
      }
    }

    load()
    const timer = setInterval(load, 5000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [mockEnabled, fetcher])

  if (mockEnabled) {
    return {
      status: 'AVAILABLE',
      source: 'devmock',
      data: mock(),
      updatedAt: new Date().toISOString(),
      isMock: true,
    }
  }

  const source: DataSource = liveData !== null ? 'backend' : 'none'

  return {
    status: liveData !== null ? 'AVAILABLE' : 'UNAVAILABLE',
    source,
    data: liveData,
    updatedAt,
    isMock: false,
  }
}
