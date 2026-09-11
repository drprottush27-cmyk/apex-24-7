/* eslint-disable react-refresh/only-export-components -- context + hook belong together */
/**
 * DEVELOPMENT MOCK switch (context).
 *
 * Default OFF. When OFF the UI renders honest `DATA UNAVAILABLE` states and
 * never fabricates metrics. Only when the operator explicitly flips the switch
 * do clearly-labelled fixtures appear, tagged DEVELOPMENT MOCK.
 */
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

interface MockModeValue {
  mockEnabled: boolean
  setMockEnabled: (enabled: boolean) => void
}

const MockModeContext = createContext<MockModeValue | null>(null)

export function MockModeProvider({ children }: { children: ReactNode }) {
  const [mockEnabled, setMockEnabledState] = useState(false)

  const setMockEnabled = useCallback((enabled: boolean) => {
    setMockEnabledState(enabled)
  }, [])

  const value = useMemo<MockModeValue>(
    () => ({ mockEnabled, setMockEnabled }),
    [mockEnabled, setMockEnabled],
  )

  return <MockModeContext.Provider value={value}>{children}</MockModeContext.Provider>
}

export function useMockMode(): MockModeValue {
  const ctx = useContext(MockModeContext)
  if (!ctx) {
    throw new Error('useMockMode must be used within MockModeProvider')
  }
  return ctx
}