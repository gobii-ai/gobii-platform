import { useCallback, useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { updateSystemMessage } from '../../api/agentAudit'
import type { DeveloperSystemMessageEvent, DeveloperTimelineEvent } from '../../types/agentChat'
import type { AuditErrorEvent, AuditSystemMessageEvent } from '../../types/agentAudit'
import { ErrorRow } from '../agentAudit/EventRows'
import { SystemMessageCard } from '../agentAudit/SystemMessageCard'
import { ModalForm } from '../common/ModalForm'
import { asAuditEvent } from './developerTimelineDisplay'

type DeveloperTimelineEventCardProps = {
  agentId: string
  event: DeveloperTimelineEvent
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

  let card = null
  if (event.kind === 'developer_error') {
    card = <ErrorRow error={asAuditEvent(event, 'error') as AuditErrorEvent} />
  } else if (event.kind === 'developer_system_message') {
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
