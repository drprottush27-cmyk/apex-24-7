/** Always-visible PAPER MODE indicator. The UI must never imply live execution. */
export function PaperModeBadge() {
  return (
    <div className="paper-badge" role="status" aria-label="Paper mode active">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
      PAPER MODE
    </div>
  )
}