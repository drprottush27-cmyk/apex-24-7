/** Number/date formatting helpers. Never fabricate values: null → DATA UNAVAILABLE. */

export const NA = 'DATA UNAVAILABLE'

export function fmtPrice(value: number | null | undefined, digits = 2): string {
  if (value == null) return NA
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtSignedUsd(value: number | null | undefined, digits = 2): string {
  if (value == null) return NA
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}$${Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null) return NA
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}${value.toFixed(digits)}%`
}

export function fmtRatio(value: number | null | undefined): string {
  if (value == null) return NA
  return `1 : ${value.toFixed(1)}`
}

export function fmtDate(value: string | null | undefined): string {
  if (value == null) return NA
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return NA
  return d.toISOString()
}

export function clamp0to1(value: number | null | undefined): number {
  if (value == null) return 0
  return Math.min(1, Math.max(0, value))
}