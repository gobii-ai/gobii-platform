import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAuditEvents } from '../api/agentAudit'
import type { AuditEvent } from '../types/agentAudit'
import { createAppStore } from './appStore'
import { initializeAudit, loadMoreAudit } from './auditSlice'

vi.mock('../api/agentAudit', () => ({
  fetchAuditEvents: vi.fn(),
  fetchAuditTimeline: vi.fn(),
}))

function makeMessageEvent(id: string, timestamp: string): AuditEvent {
  return {
    kind: 'message',
    id,
    timestamp,
    is_outbound: false,
    channel: 'web',
    body_html: null,
    body_text: id,
    attachments: [],
  }
}

describe('auditSlice pagination', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads the next audit page when more events are available', async () => {
    vi.mocked(fetchAuditEvents)
      .mockResolvedValueOnce({
        events: [makeMessageEvent('first-page', '2026-01-02T00:00:00Z')],
        has_more: true,
        next_cursor: 'cursor-1',
        processing_active: false,
        agent: { id: 'agent-1', name: 'Agent', color: null },
      })
      .mockResolvedValueOnce({
        events: [makeMessageEvent('second-page', '2026-01-01T00:00:00Z')],
        has_more: false,
        next_cursor: null,
        processing_active: false,
        agent: { id: 'agent-1', name: 'Agent', color: null },
      })

    const store = createAppStore()
    await store.dispatch(initializeAudit('agent-1')).unwrap()
    await store.dispatch(loadMoreAudit()).unwrap()

    expect(fetchAuditEvents).toHaveBeenCalledTimes(2)
    expect(fetchAuditEvents).toHaveBeenLastCalledWith('agent-1', expect.objectContaining({
      cursor: 'cursor-1',
      limit: 40,
    }))
    expect(store.getState().audit.events.map((event) => (event as { id: string }).id)).toEqual([
      'first-page',
      'second-page',
    ])
    expect(store.getState().audit.hasMore).toBe(false)
  })
})
