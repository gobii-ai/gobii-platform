import { describe, expect, it } from 'vitest'

import type { TimelineEvent } from '../../types/agentChat'
import { groupDeveloperActivityEvents } from './developerTimelineDisplay'

describe('groupDeveloperActivityEvents', () => {
  it('keeps a communication tool cluster separate from its message bubble', () => {
    const events: TimelineEvent[] = [
      {
        kind: 'developer_tool_call',
        id: 'tool-step-1',
        cursor: '1:tool_call:tool-step-1',
        timestamp: '2026-08-12T19:24:18Z',
        completion_id: 'completion-1',
        tool_name: 'send_chat_message',
        parameters: { body: 'Finished.' },
        result: { message_id: 'message-1' },
      },
      {
        kind: 'message',
        cursor: '2:message:message-1',
        message: {
          id: 'message-1',
          timestamp: '2026-08-12T19:24:19Z',
          bodyText: 'Finished.',
          isOutbound: true,
          channel: 'web',
        },
      },
    ]

    const grouped = groupDeveloperActivityEvents(events)

    expect(grouped).toHaveLength(2)
    expect(grouped[0]).toMatchObject({
      kind: 'steps',
      entries: [{
        toolName: 'send_chat_message',
        parameters: { body: 'Finished.' },
        result: { message_id: 'message-1' },
      }],
    })
    expect(grouped[1]).toBe(events[1])
  })
})
