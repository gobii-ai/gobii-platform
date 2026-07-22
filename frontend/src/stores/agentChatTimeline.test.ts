import { describe, expect, it } from 'vitest'

import type { ToolClusterEvent } from '../types/agentChat'
import { mergeTimelineEvents } from './agentChatTimeline'

function configEvent(status: 'pending' | 'complete', charterText?: string): ToolClusterEvent {
  return {
    kind: 'steps',
    cursor: '100:step:config-step',
    entryCount: 1,
    collapsible: false,
    collapseThreshold: 3,
    earliestTimestamp: '2026-07-22T12:00:00Z',
    latestTimestamp: '2026-07-22T12:00:00Z',
    entries: [{
      id: 'config-step',
      cursor: '100:step:config-step',
      timestamp: '2026-07-22T12:00:00Z',
      toolName: 'sqlite_batch',
      meta: { label: 'Database query' },
      parameters: { sql: "UPDATE __agent_config SET charter='Saved' WHERE id=1" },
      result: status === 'complete'
        ? {
          status: 'ok',
          agent_config_update: {
            updated_fields: ['charter'],
            unchanged_fields: [],
            errors: {},
          },
        }
        : '',
      status,
      charterText,
    }],
  }
}

describe('mergeTimelineEvents', () => {
  it('replaces a pending tool entry with finalized charter metadata without duplication', () => {
    const merged = mergeTimelineEvents(
      [configEvent('pending')],
      [configEvent('complete', 'Full persisted assignment')],
    )

    expect(merged).toHaveLength(1)
    expect(merged[0]).toMatchObject({
      kind: 'steps',
      entryCount: 1,
      entries: [{
        id: 'config-step',
        status: 'complete',
        charterText: 'Full persisted assignment',
      }],
    })
  })
})
