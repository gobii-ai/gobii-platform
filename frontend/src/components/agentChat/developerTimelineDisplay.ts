import type {
  DeveloperCompletionEvent,
  DeveloperStepEvent,
  DeveloperToolCallEvent,
  TimelineEvent,
  ToolCallEntry,
  ToolClusterEvent,
} from '../../types/agentChat'
import type { AuditCompletionEvent, AuditStepEvent, AuditToolCallEvent } from '../../types/agentAudit'

type DeveloperActivityEvent = DeveloperCompletionEvent | DeveloperToolCallEvent | DeveloperStepEvent
type AuditActivityEvent = AuditCompletionEvent | AuditToolCallEvent | AuditStepEvent

export function asAuditEvent<T extends { kind: string }, K extends string>(
  event: T,
  kind: K,
): Omit<T, 'kind' | 'cursor'> & { kind: K } {
  const { cursor: _cursor, kind: _kind, ...payload } = event as T & { cursor: string }
  return { ...payload, kind } as Omit<T, 'kind' | 'cursor'> & { kind: K }
}

function developerActivityEntry(event: DeveloperActivityEvent): ToolCallEntry {
  let developerEvent: AuditActivityEvent
  let toolName: string
  let label: string

  if (event.kind === 'developer_completion') {
    developerEvent = asAuditEvent(event, 'completion')
    toolName = '__developer_completion__'
    label = 'Completion'
  } else if (event.kind === 'developer_step') {
    developerEvent = asAuditEvent(event, 'step')
    toolName = '__developer_step__'
    label = developerEvent.is_system ? developerEvent.system_code || 'System step' : 'Step'
  } else {
    developerEvent = asAuditEvent(event, 'tool_call')
    toolName = developerEvent.tool_name || 'tool'
    label = developerEvent.tool_name || 'Tool call'
  }

  return {
    id: event.kind === 'developer_completion' ? `completion:${event.id}` : event.id,
    cursor: event.cursor,
    meta: { label },
    toolName,
    timestamp: event.timestamp,
    parameters: developerEvent.kind === 'tool_call' ? developerEvent.parameters : undefined,
    result: developerEvent.kind === 'tool_call' ? developerEvent.result : undefined,
    status: 'complete',
    developerEvent,
  }
}

function isDeveloperActivity(event: TimelineEvent): event is DeveloperActivityEvent {
  return event.kind === 'developer_completion'
    || event.kind === 'developer_tool_call'
    || event.kind === 'developer_step'
}

export function groupDeveloperActivityEvents(events: TimelineEvent[]): TimelineEvent[] {
  const grouped: TimelineEvent[] = []
  let activeCluster: ToolClusterEvent | null = null

  for (const event of events) {
    if (!isDeveloperActivity(event)) {
      activeCluster = null
      grouped.push(event)
      continue
    }

    if (!activeCluster) {
      activeCluster = {
        kind: 'steps',
        cursor: event.cursor,
        entryCount: 0,
        collapsible: false,
        collapseThreshold: Number.POSITIVE_INFINITY,
        earliestTimestamp: event.timestamp,
        latestTimestamp: event.timestamp,
        entries: [],
      }
      grouped.push(activeCluster)
    }

    activeCluster.entries.push(developerActivityEntry(event))
    activeCluster.entryCount = activeCluster.entries.length
    activeCluster.latestTimestamp = event.timestamp
  }

  return grouped
}
