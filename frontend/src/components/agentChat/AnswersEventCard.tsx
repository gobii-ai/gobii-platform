import type { AnswersPayload } from '../../types/agentChat'

/**
 * The user's replies to a structured question round render as an input
 * artifact mirroring the briefing card — question/answer rows, no bubble.
 */
export function AnswersEventCard({
  answers,
  relativeTimestamp,
}: {
  answers: AnswersPayload
  relativeTimestamp?: string | null
}) {
  return (
    <div className="answers-card" data-testid="answers-card">
      <div className="answers-card__head">
        <span className="answers-card__eyebrow">Your answers</span>
        {relativeTimestamp ? (
          <span className="answers-card__time">{relativeTimestamp}</span>
        ) : null}
      </div>
      <div className="answers-card__rows">
        {answers.rows.map((row, index) => (
          <div className="answers-card__row" key={`${index}-${row.question}`}>
            {row.question ? <span className="answers-card__question">{row.question}</span> : null}
            <span className="answers-card__answer">{row.answer}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
