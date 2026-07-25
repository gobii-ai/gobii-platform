import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MessageEventCard } from './MessageEventCard'
import type { AgentMessage } from './types'

vi.mock('../../api/agentChat', () => ({ trackAgentMessageCopy: vi.fn() }))

function emailMessage(overrides: Partial<AgentMessage> = {}): AgentMessage {
  return {
    id: 'msg-1',
    cursor: 'cursor-1',
    bodyText: 'Derraleigh,\n\nLoved this.',
    isOutbound: true,
    channel: 'email',
    subject: 'loved this',
    timestamp: '2026-07-21T19:10:39Z',
    relativeTimestamp: null,
    recipientName: null,
    recipientAddress: 'derraleigh@example.com',
    ...overrides,
  } as AgentMessage
}

function renderCard(message: AgentMessage) {
  return render(
    <MessageEventCard
      eventCursor="cursor-1"
      message={message}
      agentFirstName="Alpha"
    />,
  )
}

describe('MessageEventCard email recipient', () => {
  it('names the recipient on a sent email so the card is auditable', () => {
    renderCard(emailMessage())

    expect(screen.getByText('derraleigh@example.com')).toBeInTheDocument()
  })

  it('prefers a display name and keeps the address available on hover', () => {
    renderCard(emailMessage({ recipientName: 'Derraleigh Vance' }))

    expect(screen.getByText('Derraleigh Vance')).toBeInTheDocument()
    expect(screen.getByTitle('Derraleigh Vance <derraleigh@example.com>')).toBeInTheDocument()
  })

  it('announces the recipient to assistive tech rather than relying on the visual label', () => {
    renderCard(emailMessage())

    expect(screen.getByText('Sent to')).toBeInTheDocument()
  })

  it('does not label a received email with a recipient', () => {
    renderCard(emailMessage({ isOutbound: false, recipientAddress: null }))

    expect(screen.queryByText('Sent to')).not.toBeInTheDocument()
  })

  it('stays quiet when no recipient is available', () => {
    renderCard(emailMessage({ recipientAddress: null }))

    expect(screen.queryByText('Sent to')).not.toBeInTheDocument()
  })
})
