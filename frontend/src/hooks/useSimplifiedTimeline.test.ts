import { describe, expect, it } from 'vitest'

import { collapseDetailedStatusRuns } from './useSimplifiedTimeline'
import type { PlanEvent, TimelineEvent, ToolClusterEvent } from '../types/agentChat'

function stepCluster(cursor: string, toolNames: string[]): ToolClusterEvent {
  return {
    kind: 'steps',
    cursor,
    entryCount: toolNames.length,
    collapsible: toolNames.length >= 3,
    collapseThreshold: 3,
    earliestTimestamp: null,
    latestTimestamp: null,
    entries: toolNames.map((toolName, index) => ({
      id: `${cursor}:entry:${index}`,
      cursor: `${cursor}.${index}`,
      toolName,
      timestamp: null,
      caption: toolName,
      meta: {
        label: toolName,
        iconPaths: [],
        iconBg: '',
        iconColor: '',
      },
      parameters: {},
      result: null,
      status: 'complete',
    })),
  }
}

function planEvent(cursor: string): PlanEvent {
  return {
    kind: 'plan',
    cursor,
    timestamp: null,
    agentName: 'Agent',
    displayText: 'Agent updated the plan',
    primaryAction: 'updated',
    changes: [],
    snapshot: {
      todoCount: 0,
      doingCount: 0,
      doneCount: 1,
      todoTitles: [],
      doingTitles: [],
      doneTitles: ['Done'],
    },
  }
}

describe('collapseDetailedStatusRuns', () => {
  it('collapses adjacent action clusters into one group across hidden plan events', () => {
    const events: TimelineEvent[] = [
      {
        kind: 'thinking',
        cursor: '1:thinking:first',
        reasoning: 'Working',
      },
      stepCluster('2:step:first', ['search_web', 'mcp_brightdata_search_engine']),
      planEvent('3:plan:first'),
      {
        kind: 'thinking',
        cursor: '4:thinking:second',
        reasoning: 'Still working',
      },
      stepCluster('5:step:second', ['mcp_brightdata_scrape_as_markdown']),
    ]

    const result = collapseDetailedStatusRuns(events, {
      latestPlanCursor: null,
      latestScheduleEntryId: null,
    })

    expect(result).toHaveLength(1)
    expect(result[0].kind).toBe('collapsed-group')
    if (result[0].kind === 'collapsed-group') {
      expect(result[0].events.map((event) => event.cursor)).toEqual([
        '1:thinking:first',
        '2:step:first',
        '4:thinking:second',
        '5:step:second',
      ])
      expect(result[0].summary.label).toBe('5 actions')
    }
  })
})
