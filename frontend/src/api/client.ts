/**
 * AEGIS ALPHA — HTTP API client.
 *
 * Phase UI-1 boundary: the backend is NOT integrated. This client is a
 * prepared, typed transport for future wiring. Nothing calls it yet.
 *
 * Backend base = VITE_API_BASE_URL (default `/api/v1`), configured per
 * environment in a future phase. Never read secrets here.
 */

/** Flip to true only during a future phase that explicitly wires the backend. */
export const BACKEND_WIRED = false

const DEFAULT_BASE_URL = '/api/v1'

const baseUrl = (): string => {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured && configured.length > 0) {
    return configured.replace(/\/$/, '')
  }
  return DEFAULT_BASE_URL
}

export interface ApiError {
  kind: 'network' | 'http' | 'not-wired' | 'malformed'
  message: string
  status?: number
}

export class AegisApiError extends Error {
  readonly kind: ApiError['kind']
  readonly status?: number

  constructor(kind: ApiError['kind'], message: string, status?: number) {
    super(message)
    this.name = 'AegisApiError'
    this.kind = kind
    this.status = status
  }
}

function assertWired(path: string): void {
  if (!BACKEND_WIRED) {
    throw new AegisApiError(
      'not-wired',
      `GET ${path} is not wired yet: backend integration is disabled in Phase UI-1.`,
    )
  }
}

/** Typed GET request against the future backend. */
export async function get<T>(path: string): Promise<T> {
  assertWired(path)

  let response: Response
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      // Authentication + authorization are intentionally absent in Phase UI-1.
    })
  } catch {
    throw new AegisApiError('network', `Network failure for ${path}.`, undefined)
  }

  if (!response.ok) {
    throw new AegisApiError('http', `HTTP ${response.status} for ${path}.`, response.status)
  }

  try {
    return (await response.json()) as T
  } catch {
    throw new AegisApiError('malformed', `Malformed JSON for ${path}.`)
  }
}