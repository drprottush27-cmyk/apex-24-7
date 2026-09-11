import { get } from './client'
import type { GuardianStatus } from '../types'

export const GUARDIAN_ENDPOINT = '/guardian'

/** Risk Guardian status snapshot (planned GET /api/v1/guardian). */
export function fetchGuardian(): Promise<GuardianStatus> {
  return get<GuardianStatus>(GUARDIAN_ENDPOINT)
}