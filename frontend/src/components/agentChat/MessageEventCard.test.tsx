import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

describe('MessageEventCard Debug Mode timestamp', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-21T19:11:09Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows an exact viewer-timezone timestamp while retaining the raw datetime', () => {
    render(
      <MessageEventCard
        eventCursor="cursor-1"
        message={emailMessage({ relativeTimestamp: 'a few seconds ago' })}
        agentFirstName="Alpha"
        exactTimestamp
        timeZone="America/New_York"
      />,
    )

    const timestamp = screen.getByText('Jul 21, 2026, 3:10:39 PM EDT')
    expect(timestamp.tagName).toBe('TIME')
    expect(timestamp).toHaveAttribute('datetime', '2026-07-21T19:10:39Z')
    expect(timestamp).toHaveAttribute('title', '2026-07-21T19:10:39Z')
  })

  it('keeps the relative label outside Debug Mode', () => {
    renderCard(emailMessage({ relativeTimestamp: 'a few seconds ago' }))
    expect(screen.getByText(/ago$/)).toBeInTheDocument()
    expect(screen.queryByText('Jul 21, 2026, 3:10:39 PM EDT')).not.toBeInTheDocument()
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

  it('shows embed, attachment, and unavailable reply fallbacks supplied by the timeline', () => {
    const { rerender } = renderCard(emailMessage({
      channel: 'discord',
      isOutbound: false,
      bodyText: 'what do you think?',
      subject: null,
      recipientAddress: null,
      replyTo: {
        authorName: 'Release Bot',
        bodyText: '',
        embeds: [{
          title: 'Deployment',
          description: 'Production is healthy.',
          color: '#22C55E',
          fields: [{ name: 'Version', value: 'v42', inline: true }],
        }],
        attachmentFilenames: ['report.pdf'],
      },
    }))

    expect(screen.getByText('Release Bot')).toBeInTheDocument()
    expect(screen.getByTestId('discord-embed')).toBeInTheDocument()
    expect(screen.getByText('Deployment')).toBeInTheDocument()
    expect(screen.getByText('Production is healthy.')).toBeInTheDocument()
    expect(screen.getByText('Version')).toBeInTheDocument()
    expect(screen.getByText('v42')).toBeInTheDocument()
    expect(screen.getByText(/Attachments: report.pdf/)).toBeInTheDocument()

    rerender(
      <MessageEventCard
        eventCursor="cursor-1"
        message={emailMessage({
          channel: 'discord',
          isOutbound: false,
          bodyText: 'following up',
          subject: null,
          recipientAddress: null,
          replyTo: { authorName: null, bodyText: 'Original Discord message is unavailable.' },
        })}
        agentFirstName="Alpha"
      />,
    )

    expect(screen.getByText('Original Discord message is unavailable.')).toBeInTheDocument()
  })

  it('renders no quote block for a plain message', () => {
    renderCard(emailMessage({ channel: 'discord', isOutbound: false, subject: null, recipientAddress: null }))

    expect(screen.queryByTestId('reply-context')).not.toBeInTheDocument()
  })

  it('renders top-level embeds even when a Discord message has no text', () => {
    renderCard(emailMessage({
      channel: 'discord',
      isOutbound: true,
      bodyText: '',
      subject: null,
      recipientAddress: null,
      discordEmbeds: [{
        author: { name: 'Release Bot', iconUrl: 'https://example.test/bot.png' },
        title: 'Embed Demo',
        url: 'https://example.test/demo',
        description: 'A clean status card.',
        thumbnailUrl: 'https://example.test/thumb.png',
        footer: { text: 'Updated now' },
      }],
    }))

    expect(screen.queryByText('No content provided.')).not.toBeInTheDocument()
    expect(screen.getByText('Release Bot')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Embed Demo' })).toHaveAttribute('href', 'https://example.test/demo')
    expect(screen.getByText('A clean status card.')).toBeInTheDocument()
    expect(screen.getByText('Updated now')).toBeInTheDocument()
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
