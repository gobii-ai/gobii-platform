import { Check, X } from 'lucide-react'

import type { AgentTransferInvite } from '../../types/agentRoster'
import { AgentChatAvatar } from './uiPrimitives'

export type TransferInviteAction = 'accept' | 'decline'

export type TransferInviteDialogState = {
  invite: AgentTransferInvite
  action: TransferInviteAction
}

function inviteSenderLabel(invite: AgentTransferInvite): string {
  return invite.initiated_by_name || invite.initiated_by_email || 'Someone'
}

function formatInviteSentAt(invite: AgentTransferInvite): string {
  if (!invite.created_at) {
    return ''
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(invite.created_at))
  } catch {
    return invite.created_at
  }
}

export function TransferInviteDetails({ invite }: { invite: AgentTransferInvite }) {
  const sentAt = formatInviteSentAt(invite)
  return (
    <dl className="grid gap-3 text-sm text-slate-700">
      <div className="grid gap-1">
        <dt className="text-xs font-bold uppercase tracking-wide text-slate-500">From</dt>
        <dd className="m-0 break-words">{inviteSenderLabel(invite)}{invite.initiated_by_email ? ` <${invite.initiated_by_email}>` : ''}</dd>
      </div>
      <div className="grid gap-1">
        <dt className="text-xs font-bold uppercase tracking-wide text-slate-500">To</dt>
        <dd className="m-0 break-words">{invite.recipient_email || 'Your account'}</dd>
      </div>
      {sentAt ? (
        <div className="grid gap-1">
          <dt className="text-xs font-bold uppercase tracking-wide text-slate-500">Sent</dt>
          <dd className="m-0 break-words">{sentAt}</dd>
        </div>
      ) : null}
      {invite.message.trim() ? (
        <div className="grid gap-1">
          <dt className="text-xs font-bold uppercase tracking-wide text-slate-500">Message</dt>
          <dd className="m-0 break-words">{invite.message.trim()}</dd>
        </div>
      ) : null}
    </dl>
  )
}

export function TransferInviteSidebarItem({
  invite,
  variant,
  disabled,
  onRespond,
}: {
  invite: AgentTransferInvite
  variant: 'drawer' | 'sidebar'
  disabled: boolean
  onRespond: (invite: AgentTransferInvite, action: TransferInviteAction) => void
}) {
  const agentName = invite.agent_name || 'Agent'
  return (
    <div
      className="agent-roster-item agent-transfer-invite"
      data-variant={variant}
      role="listitem"
    >
      <span className="agent-roster-item__leading">
        <span className="agent-roster-item__unread-slot" />
        <span className="agent-roster-item__avatar-wrap">
          <AgentChatAvatar
            name={agentName}
            avatarUrl={invite.agent_avatar_url}
            className="agent-roster-item__avatar"
            imageClassName="agent-roster-item__avatar-image"
            textClassName="agent-roster-item__avatar-text"
          />
        </span>
      </span>
      <span className="agent-roster-item__meta">
        <span className="agent-roster-item__name">{agentName}</span>
        <span className="agent-roster-item__desc">Invitation</span>
      </span>
      <span className="agent-roster-item__trailing agent-transfer-invite__actions">
        <button
          type="button"
          className="agent-transfer-invite__action agent-transfer-invite__action--decline"
          onClick={() => onRespond(invite, 'decline')}
          disabled={disabled}
          aria-label={`Decline transfer invite for ${agentName}`}
          title="Decline"
        >
          <X className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="agent-transfer-invite__action agent-transfer-invite__action--accept"
          onClick={() => onRespond(invite, 'accept')}
          disabled={disabled}
          aria-label={`Accept transfer invite for ${agentName}`}
          title="Accept"
        >
          <Check className="h-3.5 w-3.5" />
        </button>
      </span>
    </div>
  )
}
