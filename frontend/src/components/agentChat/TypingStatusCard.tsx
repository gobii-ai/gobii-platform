type TypingStatusCardProps = {
  label: string
}

export function TypingStatusCard({ label }: TypingStatusCardProps) {
  return (
    <div className="typing-status-card" role="status" aria-live="polite">
      <div className="typing-status-card__bubble" aria-hidden="true">
        <span className="typing-status-card__dot" />
        <span className="typing-status-card__dot" />
        <span className="typing-status-card__dot" />
      </div>
      <p className="typing-status-card__label">{label}</p>
    </div>
  )
}
