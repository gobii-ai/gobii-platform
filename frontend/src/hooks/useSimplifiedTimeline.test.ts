import { describe, expect, it } from 'vitest'

import { collapseDetailedStatusRuns } from './useSimplifiedTimeline'
import type { MessageEvent, PlanEvent, TimelineEvent, ToolClusterEvent } from '../types/agentChat'

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

function messageEvent(cursor: string, bodyText = 'Message'): MessageEvent {
  return {
    kind: 'message',
    cursor,
    message: {
      id: cursor,
      bodyText,
    },
  }
}

describe('collapseDetailedStatusRuns', () => {
  it('renders a single visible action directly instead of collapsing it', () => {
    const action = stepCluster('1:step:first', ['sleep_until_next_trigger', 'search_web'])

    const result = collapseDetailedStatusRuns([action], {
      latestPlanCursor: null,
      latestScheduleEntryId: null,
    })

    expect(result).toHaveLength(1)
    expect(result[0].kind).toBe('steps')
    expect(result[0]).toBe(action)
  })

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

  it('keeps only the trailing action run expanded while the agent is working', () => {
    const historicalAction = stepCluster('2:step:historical', ['search_web', 'mcp_brightdata_search_engine'])
    const trailingAction = stepCluster('4:step:trailing', ['mcp_brightdata_scrape_as_markdown', 'search_web'])
    const events: TimelineEvent[] = [
      messageEvent('1:message:first', 'Start'),
      historicalAction,
      messageEvent('3:message:latest', 'Latest'),
      trailingAction,
    ]

    const result = collapseDetailedStatusRuns(
      events,
      {
        latestPlanCursor: null,
        latestScheduleEntryId: null,
      },
      { keepTrailingActivityExpanded: true },
    )

    expect(result.map((event) => event.kind)).toEqual(['message', 'collapsed-group', 'message', 'steps'])
    expect(result[1].kind).toBe('collapsed-group')
    expect(result[3].kind).toBe('steps')
    if (result[3].kind === 'steps') {
      expect(result[3].cursor).toBe(trailingAction.cursor)
      expect(result[3].collapsible).toBe(false)
      expect(result[3].collapseThreshold).toBe(Infinity)
    }
  })

  it('collapses actions before the latest schedule update even when keeping trailing activity expanded', () => {
    const firstAction = stepCluster('2:step:first', ['search_web'])
    const secondAction = stepCluster('3:step:second', ['sqlite_batch'])
    const thirdAction = stepCluster('4:step:third', ['create_pdf'])
    const scheduleUpdate = stepCluster('5:step:schedule', ['update_schedule'])
    const events: TimelineEvent[] = [
      messageEvent('1:message:latest', 'Latest'),
      firstAction,
      secondAction,
      thirdAction,
      scheduleUpdate,
    ]

    const result = collapseDetailedStatusRuns(
      events,
      {
        latestPlanCursor: null,
        latestScheduleEntryId: '5:step:schedule:entry:0',
      },
      { keepTrailingActivityExpanded: true },
    )

    expect(result.map((event) => event.kind)).toEqual(['message', 'collapsed-group', 'steps'])
    expect(result[1].kind).toBe('collapsed-group')
    if (result[1].kind === 'collapsed-group') {
      expect(result[1].events.map((event) => event.cursor)).toEqual([
        firstAction.cursor,
        secondAction.cursor,
        thirdAction.cursor,
      ])
      expect(result[1].summary.label).toBe('3 actions')
    }
    expect(result[2]).toBe(scheduleUpdate)
  })

  it('keeps assignment updates expanded between adjacent activity runs', () => {
    const assignmentUpdate = stepCluster('3:step:assignment', ['sqlite_batch'])
    assignmentUpdate.entries[0].parameters = {
      sql: "UPDATE __agent_config SET charter=patch_text(charter, 'Old', 'New') WHERE id=1",
    }
    assignmentUpdate.entries[0].result = {
      status: 'ok',
      agent_config_update: {
        updated_fields: ['charter'],
        unchanged_fields: [],
        errors: {},
      },
    }
    assignmentUpdate.entries[0].charterText = 'New'
    const result = collapseDetailedStatusRuns(
      [
        stepCluster('2:step:older', ['search_web']),
        assignmentUpdate,
        stepCluster('4:step:newer', ['create_pdf']),
      ],
      { latestPlanCursor: null, latestScheduleEntryId: null },
    )

    expect(result.map((event) => event.kind)).toEqual(['steps', 'steps', 'steps'])
    expect(result[1]).toMatchObject({
      cursor: assignmentUpdate.cursor,
      collapsible: false,
      collapseThreshold: Infinity,
    })
  })

  it('keeps plan updates as visible actions', () => {
    const result = collapseDetailedStatusRuns([stepCluster('1:step:hidden', ['update_plan'])], {
      latestPlanCursor: null,
      latestScheduleEntryId: null,
    })

    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({
      kind: 'steps',
      cursor: '1:step:hidden',
    })
  })
})
