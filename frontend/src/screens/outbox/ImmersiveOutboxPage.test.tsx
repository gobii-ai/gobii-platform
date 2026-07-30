/**
 * #427 (frontend contributor): "Save & send" approved an edited body that had never been
 * rendered — the reviewer saw a raw textarea, not the HTML that would ship. Editing must
 * be saved (which refreshes the rendered preview) before approve becomes available.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ImmersiveOutboxPage } from './ImmersiveOutboxPage'

const outboxItem = {
  id: 'item-1',
  version: 3,
  status: 'needs_review',
  sender: 'agent@my.gobii.ai',
  to: 'lead@example.com',
  cc: [],
  bcc: ['compliance@example.com'],
  subject: 'Pricing follow-up',
  body: 'Plain text body',
  bodyHtml: '<html><body>Plain text body</body></html>',
  bodyPreview: 'Plain text body',
  createdAt: '2026-07-28T00:00:00Z',
  agent: { id: 'agent-1', name: 'Scout' },
  attachments: [],
  threadChanged: false,
  reviewStatus: 'pending',
  queuedAt: '2026-07-28T00:00:00Z',
  warnings: [],
  allowedActions: { edit: true, approve: true, discard: true, retry: false },
}

vi.mock('../../api/outbox', () => ({
  fetchOutbox: vi.fn(async () => ({
    featureEnabled: true,
    available: true,
    items: [outboxItem],
    counts: { needs_review: 1, sending: 0, failed: 0, sent: 0, discarded: 0, expired: 0 },
  })),
  fetchOutboxItem: vi.fn(async () => outboxItem),
  fetchOutboxAgentFiles: vi.fn(async () => []),
  updateOutboxItem: vi.fn(async () => outboxItem),
  decideOutboxItem: vi.fn(async () => outboxItem),
  bulkDiscardOutbox: vi.fn(async () => ({ discardedIds: [] })),
  fetchEmailSendingPolicy: vi.fn(async () => ({ enabled: true, mode: 'review_all' })),
  updateEmailSendingPolicy: vi.fn(async () => ({ enabled: true, mode: 'review_all' })),
}))

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ImmersiveOutboxPage />
    </QueryClientProvider>,
  )
}

describe('outbox review edit flow', () => {
  it('hides approve while editing so unrendered bodies cannot be sent', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('Pricing follow-up'))
    await screen.findByTitle('Email preview')
    expect(screen.getByDisplayValue('compliance@example.com')).toBeInTheDocument()
    expect(screen.getByText('Approve & send')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Edit'))

    expect(screen.queryByText('Approve & send')).not.toBeInTheDocument()
    expect(screen.queryByText('Save & send')).not.toBeInTheDocument()
    expect(screen.getByText('Save changes')).toBeInTheDocument()

    // Saving exits edit mode and restores the rendered preview + approve.
    fireEvent.click(screen.getByText('Save changes'))
    await waitFor(() => expect(screen.getByText('Approve & send')).toBeInTheDocument())
    expect(screen.getByTitle('Email preview')).toBeInTheDocument()
  })
})
