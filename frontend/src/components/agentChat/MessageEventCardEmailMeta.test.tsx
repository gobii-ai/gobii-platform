/**
 * Email cards must name the sending mailbox and any cc recipients. Agents send from several
 * custom-domain mailboxes, so "which mailbox sent this" cannot be read off the agent name -- and
 * lead-gen work depends on knowing it. Bcc is never persisted, so it is deliberately not shown.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MessageEventCard } from './MessageEventCard'
import type { AgentMessage } from '../../types/agentChat'

function emailMessage(overrides: Partial<AgentMessage> = {}): AgentMessage {
  return {
    id: 'msg-1',
    cursor: '1:message:msg-1',
    bodyText: 'Following up on the pilot scope.',
    bodyHtml: '',
    subject: 'Pilot scope follow-up',
    isOutbound: true,
    channel: 'email',
    attachments: [],
    timestamp: '2026-07-25T20:00:00Z',
    relativeTimestamp: 'just now',
    senderAddress: 'alpha@primeforge-outbound.test',
    recipientAddress: 'dana@northwind.test',
    recipientName: 'Dana Whitfield',
    ...overrides,
  } as unknown as AgentMessage
}

function renderCard(message: AgentMessage) {
  return render(<MessageEventCard message={message} eventCursor={message.cursor ?? 'cursor-1'} agentFirstName="Alpha" />)
}

describe('email card sender and cc', () => {
  it('names the mailbox the email was actually sent from', () => {
    renderCard(emailMessage())

    expect(screen.getByText('alpha@primeforge-outbound.test')).toBeInTheDocument()
    expect(screen.getByText('From')).toBeInTheDocument()
  })

  it('lists cc recipients when there are any', () => {
    renderCard(emailMessage({ ccAddresses: ['priya@northwind.test', 'sam@northwind.test'] } as Partial<AgentMessage>))

    expect(screen.getByText('Cc')).toBeInTheDocument()
    expect(screen.getByText('priya@northwind.test, sam@northwind.test')).toBeInTheDocument()
  })

  it('omits the cc line entirely when there are none', () => {
    renderCard(emailMessage({ ccAddresses: [] } as Partial<AgentMessage>))

    expect(screen.queryByText('Cc')).not.toBeInTheDocument()
  })

  it('does not add a sender line to a non-email message', () => {
    renderCard(emailMessage({ channel: 'web', subject: null } as Partial<AgentMessage>))

    expect(screen.queryByText('From')).not.toBeInTheDocument()
  })
})
