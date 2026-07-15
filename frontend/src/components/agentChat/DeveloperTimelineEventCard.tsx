import { useCallback, useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { updateSystemMessage } from '../../api/agentAudit'
import type {
  DeveloperTimelineEvent,
  DeveloperSystemMessageEvent,
  ToolClusterEvent,
} from '../../types/agentChat'
import type {
  AuditErrorEvent,
  AuditSystemMessageEvent,
  AuditToolCallEvent,
} from '../../types/agentAudit'
import { ErrorRow } from '../agentAudit/EventRows'
import { SystemMessageCard } from '../agentAudit/SystemMessageCard'
import { ModalForm } from '../common/ModalForm'
import { ToolClusterCard } from './ToolClusterCard'

type DeveloperTimelineEventCardProps = {
  agentId: string
  event: DeveloperTimelineEvent
}

function asAuditEvent<T extends { kind: string }, K extends string>(event: T, kind: K): Omit<T, 'kind' | 'cursor'> & { kind: K } {
  const { cursor: _cursor, kind: _kind, ...payload } = event as T & { cursor: string }
  return { ...payload, kind } as Omit<T, 'kind' | 'cursor'> & { kind: K }
}

function buildDeveloperToolCluster(event: DeveloperTimelineEvent): ToolClusterEvent | null {
  if (event.kind === 'developer_completion') {
    const completion = asAuditEvent(event, 'completion')
    return {
      kind: 'steps',
      cursor: event.cursor,
      entryCount: 1,
      collapsible: false,
      collapseThreshold: Number.POSITIVE_INFINITY,
      earliestTimestamp: completion.timestamp,
      latestTimestamp: completion.timestamp,
      entries: [{
        id: `completion:${completion.id}`,
        cursor: event.cursor,
        meta: { label: 'Completion' },
        toolName: '__developer_completion__',
        timestamp: completion.timestamp,
        status: 'complete',
        developerRaw: true,
        developerCompletion: completion,
      }],
    }
  }
  if (event.kind === 'developer_tool_call') {
    const tool = asAuditEvent(event, 'tool_call') as AuditToolCallEvent
    return {
      kind: 'steps',
      cursor: event.cursor,
      entryCount: 1,
      collapsible: false,
      collapseThreshold: Number.POSITIVE_INFINITY,
      earliestTimestamp: tool.timestamp,
      latestTimestamp: tool.timestamp,
      entries: [{
        id: tool.id,
        cursor: event.cursor,
        meta: { label: tool.tool_name || 'Tool call' },
        toolName: tool.tool_name || 'tool',
        timestamp: tool.timestamp,
        parameters: tool.parameters,
        result: tool.result,
        status: 'complete',
        developerRaw: true,
        developerExecutionDurationMs: tool.execution_duration_ms,
      }],
    }
  }
  if (event.kind === 'developer_step') {
    const step = asAuditEvent(event, 'step')
    return {
      kind: 'steps',
      cursor: event.cursor,
      entryCount: 1,
      collapsible: false,
      collapseThreshold: Number.POSITIVE_INFINITY,
      earliestTimestamp: step.timestamp,
      latestTimestamp: step.timestamp,
      entries: [{
        id: step.id,
        cursor: event.cursor,
        meta: { label: step.is_system ? step.system_code || 'System step' : 'Step' },
        toolName: '__developer_step__',
        timestamp: step.timestamp,
        status: 'complete',
        developerRaw: true,
        developerStep: step,
      }],
    }
  }
  return null
}

export function DeveloperTimelineEventCard({ agentId, event }: DeveloperTimelineEventCardProps) {
  const queryClient = useQueryClient()
  const [editingMessage, setEditingMessage] = useState<DeveloperSystemMessageEvent | null>(null)
  const [editingBody, setEditingBody] = useState('')
  const [editingBusy, setEditingBusy] = useState(false)
  const [editingError, setEditingError] = useState<string | null>(null)

  const openEditor = useCallback((message: AuditSystemMessageEvent) => {
    setEditingMessage(event.kind === 'developer_system_message' ? event : null)
    setEditingBody(message.body)
    setEditingError(null)
  }, [event])

  const submitEdit = useCallback(async (submitEvent: FormEvent<HTMLFormElement>) => {
    submitEvent.preventDefault()
    const body = editingBody.trim()
    if (!editingMessage || !body) return
    setEditingBusy(true)
    setEditingError(null)
    try {
      await updateSystemMessage(agentId, editingMessage.id, { body })
      await queryClient.invalidateQueries({ queryKey: ['agent-timeline', agentId, 'developer'] })
      setEditingMessage(null)
    } catch {
      setEditingError('Unable to update the system message.')
    } finally {
      setEditingBusy(false)
    }
  }, [agentId, editingBody, editingMessage, queryClient])

  const developerCluster = buildDeveloperToolCluster(event)
  let card = null
  if (developerCluster) {
    card = <ToolClusterCard cluster={developerCluster} />
  } else if (event.kind === 'developer_error') {
    card = <ErrorRow error={asAuditEvent(event, 'error') as AuditErrorEvent} />
  } else {
    card = (
      <SystemMessageCard
        message={asAuditEvent(event, 'system_message') as AuditSystemMessageEvent}
        onEdit={openEditor}
      />
    )
  }

  return (
    <>
      {card}
      {editingMessage ? (
        <ModalForm
          id={`developer-system-message-${editingMessage.id}`}
          title="Edit system message"
          onClose={() => setEditingMessage(null)}
          onSubmit={submitEdit}
          submitLabel="Save"
          submitting={editingBusy}
          submitDisabled={!editingBody.trim()}
          errorMessages={editingError ? [editingError] : null}
        >
          <textarea
            value={editingBody}
            onChange={(changeEvent) => setEditingBody(changeEvent.target.value)}
            rows={8}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
          />
        </ModalForm>
      ) : null}
    </>
  )
}
