import type { BriefingPayload } from '../../types/agentChat'

/**
 * A briefing is an input artifact, not a chat message — it renders as a
 * structured card on the timeline (no author bubble, no message chrome).
 * The underlying message body is the model-facing input and stays backstage.
 */
export function BriefingEventCard({
  briefing,
  relativeTimestamp,
}: {
  briefing: BriefingPayload
  relativeTimestamp?: string | null
}) {
  return (
    <div className="briefing-card" data-testid="briefing-card">
      <div className="briefing-card__head">
        <span className="briefing-card__eyebrow">
          {briefing.template || 'Briefing'} · Briefing
        </span>
        {relativeTimestamp ? (
          <span className="briefing-card__time">{relativeTimestamp}</span>
        ) : null}
      </div>
      {briefing.title ? <div className="briefing-card__title">{briefing.title}</div> : null}
      <div className="briefing-card__rows">
        {briefing.rows.map((row) => (
          <div className="briefing-card__row" key={row.label}>
            <span className="briefing-card__label">{row.label}</span>
            <span className={`briefing-card__value${row.open ? ' briefing-card__value--open' : ''}`}>
              {row.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
