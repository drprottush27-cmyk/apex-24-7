/**
 * AegisMascot — placeholder mascot for AEGIS ALPHA.
 *
 * Deliberately NOT the Stitch penguin. This is a clean, geometric shield mark
 * whose visual state is changed subtly and deterministically.
 *
 * Contract:
 *  - No random animations, no fake reactions.
 *  - State is driven by a prop now; in future phases it must be driven by
 *    backend data.
 *  - Never used to imply execution authority.
 */

export type AegisMascotState =
  | 'CONFIDENT'
  | 'SCANNING'
  | 'CAUTION'
  | 'ALERT'
  | 'DANGER'
  | 'OFFLINE'

interface AegisMascotProps {
  state: AegisMascotState
  label?: string
}

const STATE_META: Record<
  AegisMascotState,
  { ring: string; accent: string; label: string }
> = {
  CONFIDENT: { ring: '#35C48C', accent: '#E7E4DD', label: 'CONFIDENT' },
  SCANNING: { ring: '#5B9BF2', accent: '#E7E4DD', label: 'SCANNING' },
  CAUTION: { ring: '#E5A835', accent: '#E5A835', label: 'CAUTION' },
  ALERT: { ring: '#F0A83C', accent: '#F0A83C', label: 'ALERT' },
  DANGER: { ring: '#E5484D', accent: '#E5484D', label: 'DANGER' },
  OFFLINE: { ring: '#5B6472', accent: '#5B6472', label: 'OFFLINE' },
}

export function AegisMascot({ state, label }: AegisMascotProps) {
  const meta = STATE_META[state]
  const displayLabel = label ?? meta.label
  const dimmedRing = state === 'OFFLINE' ? meta.ring : meta.ring

  return (
    <span className="inline-flex flex-col items-center gap-2" aria-label={`Aegis state: ${displayLabel}`}>
      <span className="inline-flex h-10 w-10 items-center justify-center" aria-hidden="true">
        <svg viewBox="0 0 48 48" className="h-full w-full" fill="none" stroke="none">
          <polygon
            points="24 3, 42 11.5, 42 24, 24 45, 6 24, 6 11.5"
            fill="#141821"
            stroke={dimmedRing}
            strokeWidth="1.6"
            opacity={state === 'OFFLINE' ? 0.5 : 1}
          />
          <path
            d="M24 27.5 a 7.5 7.5 0 1 0 0 -9 a 7.5 7.5 0 0 0 0 9 Z"
            stroke={meta.accent}
            strokeWidth="1.6"
            opacity={state === 'OFFLINE' ? 0.4 : 1}
            fill="none"
          />
          <circle cx="24" cy="23" r="2.4" fill={meta.accent} opacity={state === 'OFFLINE' ? 0.35 : 1} />
          <line x1="24" y1="26" x2="24" y2="15" stroke={meta.accent} strokeWidth="1.2" opacity={state === 'OFFLINE' ? 0.3 : 0.75} />
        </svg>
      </span>
      <span
        className="rounded border px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-[0.08em]"
        style={{ color: meta.accent, borderColor: `${meta.ring}55`, background: `${meta.ring}14` }}
      >
        {displayLabel}
      </span>
    </span>
  )
}