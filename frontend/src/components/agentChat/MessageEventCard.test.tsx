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

  it('labels the recipient so it is not just a bare address on the card', () => {
    renderCard(emailMessage())

    // The envelope pairs a visible To label with the value, which reads correctly to assistive
    // tech as well; the old sr-only "Sent to" existed only because the chip had no visible label.
    expect(screen.getByText('To')).toBeInTheDocument()
  })

  it('labels a received email with the mailbox that received it (#495)', () => {
    renderCard(emailMessage({ isOutbound: false, recipientAddress: 'scout@my.gobii.ai' }))

    expect(screen.getByText('To')).toBeInTheDocument()
    expect(screen.getByText('scout@my.gobii.ai')).toBeInTheDocument()
  })

  it('stays quiet on a received email with no recipient data', () => {
    renderCard(emailMessage({ isOutbound: false, recipientAddress: null }))

    expect(screen.queryByText('To')).not.toBeInTheDocument()
  })

  it('stays quiet when no recipient is available', () => {
    renderCard(emailMessage({ recipientAddress: null }))

    expect(screen.queryByText('To')).not.toBeInTheDocument()
  })
})

describe('MessageEventCard reply context', () => {
  // A Discord reply's meaning lives in what it answered; the card dropped it entirely and
  // "have you been there?" rendered with no indication of what "there" meant (bug #248).
  it('quotes the replied-to message above the body', () => {
    renderCard(emailMessage({
      channel: 'discord',
      isOutbound: false,
      bodyText: 'have you been there?',
      subject: null,
      recipientAddress: null,
      replyTo: { authorName: 'Alyssa Perkins', bodyText: 'honestly maybe like a 30.' },
    }))

    expect(screen.getByTestId('reply-context')).toBeInTheDocument()
    expect(screen.getByText('Alyssa Perkins')).toBeInTheDocument()
    expect(screen.getByText('honestly maybe like a 30.')).toBeInTheDocument()
  })

  it('renders no quote block for a plain message', () => {
    renderCard(emailMessage({ channel: 'discord', isOutbound: false, subject: null, recipientAddress: null }))

    expect(screen.queryByTestId('reply-context')).not.toBeInTheDocument()
  })
})

describe('MessageEventCard MCP direction', () => {
  function mcpMessage(overrides: Partial<AgentMessage> = {}): AgentMessage {
    return {
      id: 'mcp-message-1',
      cursor: 'cursor-mcp-1',
      bodyText: 'Completed.',
      isOutbound: false,
      channel: 'mcp',
      sourceKind: 'mcp',
      sourceLabel: 'Gobii MCP',
      timestamp: '2026-07-25T16:35:13Z',
      relativeTimestamp: null,
      ...overrides,
    } as AgentMessage
  }

  it('renders an inbound MCP message from Gobii MCP on the requester side', () => {
    const { container } = renderCard(mcpMessage())

    expect(screen.getByText('Gobii MCP')).toBeInTheDocument()
    expect(screen.getByText('MCP')).toBeInTheDocument()
    expect(container.querySelector('article')).toHaveClass('is-user')
  })

  it('renders an outbound MCP reply from the agent on the agent side', () => {
    const { container } = renderCard(mcpMessage({ isOutbound: true }))

    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.queryByText('Gobii MCP')).not.toBeInTheDocument()
    expect(screen.getByText('MCP')).toBeInTheDocument()
    expect(container.querySelector('article')).toHaveClass('is-agent')
  })
})
