import { useMemo } from 'react'

import { getCsrfToken } from '../../api/http'
import { CollaboratorInviteFormDialog } from '../agentSettings/CollaboratorInviteFormDialog'

type CollaboratorInviteDialogProps = {
  open: boolean
  agentName?: string | null
  inviteUrl?: string | null
  canManage?: boolean
  onClose: () => void
}

export function CollaboratorInviteDialog({
  open,
  agentName,
  inviteUrl,
  canManage = true,
  onClose,
}: CollaboratorInviteDialogProps) {
  const displayName = useMemo(() => (agentName || '').trim() || 'this agent', [agentName])
  const handleSubmit = async (email: string) => {
    if (!inviteUrl) {
      throw new Error('Collaboration invites are unavailable right now.')
    }
    if (!canManage) {
      throw new Error('Only owners and organization admins can invite collaborators.')
    }

    const csrfToken = getCsrfToken()
    const formData = new FormData()
    formData.append('action', 'add_collaborator')
    formData.append('email', email)
    if (csrfToken) {
      formData.append('csrfmiddlewaretoken', csrfToken)
    }
    const response = await fetch(inviteUrl, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
      },
      credentials: 'same-origin',
      body: formData,
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || 'Unable to send invite. Please try again.')
    }
    return `Invite sent to ${email}.`
  }

  return (
    <CollaboratorInviteFormDialog
      open={open}
      formId="collaborator-invite-form"
      emailFieldId="collaborator-email"
      title={`Invite someone to collaborate with ${displayName}`}
      subtitle="Collaborators can view and send messages with this agent."
      onClose={onClose}
      onSubmit={handleSubmit}
      submitLabel="Send invite"
      submittingLabel="Sending..."
      emptyEmailMessage="Enter an email address to continue."
      fallbackErrorMessage="Unable to send invite. Please try again."
      disabled={!inviteUrl || !canManage}
      disabledMessage={!canManage ? 'Only owners and organization admins can invite collaborators.' : undefined}
    />
  )
}
