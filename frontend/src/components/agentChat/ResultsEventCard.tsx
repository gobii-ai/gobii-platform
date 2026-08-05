import type { ResultsPayload } from '../../types/agentChat'

/**
 * Structured result delivery (deliver_results) as a card: clear rows with
 * score pills, then locked rows. Locked rows arrive REDACTED from the server
 * (initials only) while the account is frozen at the signup wall — the blur
 * here is styling on already-redacted text, not the security boundary.
 */
export function ResultsEventCard({
  results,
  bodyText,
  relativeTimestamp,
}: {
  results: ResultsPayload
  bodyText?: string
  relativeTimestamp?: string | null
}) {
  const clearRows = results.rows.filter((row) => !row.locked)
  const lockedRows = results.rows.filter((row) => row.locked)
  // The delivery body is "**title**\n\nsummary" — the card renders the title
  // itself, so only the summary remainder is shown, markdown markers stripped.
  const summary = (bodyText || '')
    .replace(results.title ? `**${results.title}**` : /^\*\*[^*]+\*\*/, '')
    .replace(/\*\*/g, '')
    .trim()
  return (
    <div className="results-card" data-testid="results-card">
      <div className="results-card__head">
        <span className="results-card__eyebrow">Delivered results</span>
        {relativeTimestamp ? (
          <span className="results-card__time">{relativeTimestamp}</span>
        ) : null}
      </div>
      {results.title ? <div className="results-card__title">{results.title}</div> : null}
      {summary ? <div className="results-card__summary">{summary}</div> : null}
      <div className="results-card__rows">
        {clearRows.map((row, index) => (
          <div className="results-card__row" key={`${index}-${row.primary}`}>
            <div className="results-card__info">
              <b>
                {row.url ? (
                  <a href={row.url} target="_blank" rel="noreferrer">
                    {row.primary}
                  </a>
                ) : (
                  row.primary
                )}
              </b>
              {row.secondary ? <span>{row.secondary}</span> : null}
              {row.detail ? <small>{row.detail}</small> : null}
            </div>
            {row.score ? <em className="results-card__score">{row.score} fit</em> : null}
          </div>
        ))}
      </div>
      {lockedRows.length > 0 ? (
        <div className="results-card__locked" data-testid="results-card-locked">
          {lockedRows.map((row, index) => (
            <div className="results-card__lockrow" key={`locked-${index}`}>
              <span className="results-card__lockdot" aria-hidden="true" />
              <span className="results-card__lockname" aria-hidden="true">
                {row.primary}
                {row.secondary ? ` — ${row.secondary}` : ''}
              </span>
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="3" y="7" width="10" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
                <path d="M5 7V5a3 3 0 016 0v2" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </div>
          ))}
          <div className="results-card__unlock">
            <span>
              <b>
                {lockedRows.length} more found
              </b>{' '}
              · unlocked when your trial starts
            </span>
          </div>
        </div>
      ) : null}
    </div>
  )
}
