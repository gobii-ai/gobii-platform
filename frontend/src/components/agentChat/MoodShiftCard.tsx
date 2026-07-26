import { memo } from 'react'

import { formatRelativeTimestamp } from '../../util/time'
import { selectActiveChatSession } from '../../store/chatSlice'
import { useAppSelector } from '../../store/hooks'
import type { ToolEntryDisplay } from './tooling/types'

/**
 * A mood change, shown as a mood.
 *
 * Agents set how they are feeling by writing to their own config table, so this arrived looking
 * like every other row update and rendered as "Database query, 1 statement". The feeling is the
 * whole content of the event, so it gets the emoji at full size and a halo tinted to match,
 * rather than an icon, a label and a SQL statement.
 */

/**
 * Emoji carry their own colour, so the halo is sampled from the character itself rather than from
 * a lookup of moods we would have to keep in step with whatever an agent decides to feel. The hue
 * is derived from the codepoint, which is stable per emoji: the same feeling always glows the same
 * colour, and a different one always looks different.
 */
function haloHue(emoji: string): number {
  const codepoint = emoji.codePointAt(0) ?? 0
  // Emoji sit in dense contiguous blocks, so a plain modulo maps neighbours onto nearly the same
  // hue and every mood glows the same colour. Stepping by the golden angle spreads adjacent
  // codepoints right around the wheel while staying perfectly stable per emoji.
  return Math.round((codepoint * 137.508) % 360)
}

function describeDuration(seconds: number | null | undefined): string | null {
  if (typeof seconds !== 'number' || seconds <= 0) {
    return null
  }
  if (seconds < 90) {
    return `for ${seconds} seconds`
  }
  const minutes = Math.round(seconds / 60)
  if (minutes === 1) {
    return 'for a minute'
  }
  if (minutes < 60) {
    return `for ${minutes} minutes`
  }
  const hours = Math.round(minutes / 60)
  return hours === 1 ? 'for an hour' : `for ${hours} hours`
}

export const MoodShiftCard = memo(function MoodShiftCard({ entry }: { entry: ToolEntryDisplay }) {
  const agentName = useAppSelector(selectActiveChatSession).identity.agentName
  const emoji = entry.emotion?.trim() || ''
  const cleared = !emoji
  const who = agentName?.trim().split(/\s+/)[0] || 'The agent'
  const relativeTime = formatRelativeTimestamp(entry.timestamp)
  const duration = describeDuration(entry.emotionTimeoutSeconds)
  const hue = emoji ? haloHue(emoji) : 240

  return (
    <div
      className="mood-shift"
      data-cleared={cleared ? 'true' : 'false'}
      style={{ ['--mood-hue' as string]: String(hue) }}
    >
      <span className="mood-shift__text">
        <span className="mood-shift__title">
          {cleared ? `${who} let their mood settle` : `${who} is feeling`}
        </span>
        {duration && !cleared ? <span className="mood-shift__meta">{duration}</span> : null}
      </span>
      {/* The feeling itself, sitting where the sentence lands so it reads as the object of it.
          A cleared mood has no face to show, and a placeholder glyph just reads as a stray mark. */}
      {cleared ? null : (
        <span className="mood-shift__face" role="img" aria-label={`feeling ${emoji}`}>
          <span className="mood-shift__halo" aria-hidden="true" />
          <span className="mood-shift__glyph" aria-hidden="true">{emoji}</span>
        </span>
      )}
      {relativeTime ? <span className="mood-shift__time">{relativeTime}</span> : null}
    </div>
  )
})
