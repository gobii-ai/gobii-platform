import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AgentTimelinePane } from './AgentTimelinePane'
import type { CreditForecast, TimelineEvent } from '../../types/agentChat'

vi.mock('./TimelineEventItem', () => ({
  TimelineEventItem: ({ event }: { event: { kind: string, message?: { id?: string }, forecast?: CreditForecast } }) => {
    if (event.kind === 'credit_forecast') {
      return <div data-testid="timeline-rendered-event">forecast</div>
    }
    if (event.kind === 'message') {
      return <div data-testid="timeline-rendered-event">{event.message?.id}</div>
    }
    return <div data-testid="timeline-rendered-event">{event.kind}</div>
  },
}))

vi.mock('./ScheduledResumeCard', () => ({
  ScheduledResumeCard: () => <div data-testid="timeline-rendered-event">schedule</div>,
}))

vi.mock('./StreamingReplyCard', () => ({
  StreamingReplyCard: () => null,
}))

vi.mock('./StreamingThinkingCard', () => ({
  StreamingThinkingCard: () => null,
}))

vi.mock('./TypingIndicator', () => ({
  TypingIndicator: () => null,
}))

vi.mock('./HardLimitCalloutCard', () => ({
  HardLimitCalloutCard: () => null,
}))

vi.mock('./ContactCapCalloutCard', () => ({
  ContactCapCalloutCard: () => null,
}))

vi.mock('./TaskCreditsCalloutCard', () => ({
  TaskCreditsCalloutCard: () => null,
}))

vi.mock('./StarterPromptSuggestions', () => ({
  StarterPromptSuggestions: () => null,
}))

function messageEvent(id: string, timestamp: string): TimelineEvent {
  return {
    kind: 'message',
    cursor: id,
    message: {
      id,
      timestamp,
      bodyText: id,
    },
  }
}

const baseForecast: CreditForecast = {
  perRunCredits: 8,
  dailyCredits: 8,
  monthlyCredits: 240,
  warningLevel: 'none',
  estimatedAt: '2026-06-30T12:05:00Z',
}

describe('AgentTimelinePane', () => {
  it('renders credit forecast timeline events before pinned schedule events', () => {
    render(
      <AgentTimelinePane
        agentFirstName="Agent"
        autoScrollPinned
        events={[
          messageEvent('before', '2026-06-30T12:00:00Z'),
          {
            kind: 'credit_forecast',
            cursor: 'forecast',
            timestamp: '2026-06-30T12:05:00Z',
            forecast: baseForecast,
          },
          messageEvent('after', '2026-06-30T12:10:00Z'),
        ]}
        onHardLimitOpenSettings={vi.fn()}
        showScheduledResumeEvent
        nextScheduledAt="2026-07-01T12:00:00Z"
        starterPromptCount={0}
        starterPrompts={[]}
        typingStatusText=""
      />,
    )

    expect(screen.getAllByTestId('timeline-rendered-event').map((node) => node.textContent)).toEqual([
      'before',
      'forecast',
      'after',
      'schedule',
    ])
  })
})
