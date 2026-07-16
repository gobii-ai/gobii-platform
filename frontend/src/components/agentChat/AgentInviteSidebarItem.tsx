import { Check, X } from 'lucide-react'

import type { AgentSidebarInvite } from '../../types/agentRoster'
import { AgentChatAvatar } from './uiPrimitives'

export type AgentInviteAction = 'accept' | 'decline'

export type AgentInviteDialogState = {
  invite: AgentSidebarInvite
  action: AgentInviteAction
}

export function AgentInviteDetails({ invite }: { invite: AgentSidebarInvite }) {
  const senderName = invite.sender_name || invite.sender_email || 'Someone'
  const message = invite.message.trim()

  return (
    <dl className="grid gap-3 text-sm text-slate-700">
      <div className="grid gap-1">
        <dt className="text-xs font-bold uppercase tracking-wide text-slate-500">From</dt>
        <dd className="m-0 break-words">{senderName}{invite.sender_email && senderName !== invite.sender_email ? ` (${invite.sender_email})` : ''}</dd>
      </div>
      {message ? (
        <div className="grid gap-1">
          <dt className="text-xs font-bold uppercase tracking-wide text-slate-500">Message</dt>
          <dd className="m-0 break-words">{message}</dd>
        </div>
      ) : null}
    </dl>
  )
}

export function AgentInviteSidebarItem({
  invite,
  variant,
  disabled,
  onRespond,
}: {
  invite: AgentSidebarInvite
  variant: 'drawer' | 'sidebar'
  disabled: boolean
  onRespond: (invite: AgentSidebarInvite, action: AgentInviteAction) => void
}) {
  const agentName = invite.agent_name || 'Agent'
  const inviteLabel = invite.kind === 'transfer' ? 'Transfer invitation' : 'Collaboration invitation'
  return (
    <div
      className="agent-roster-item agent-invite"
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
        <span className="agent-roster-item__desc">{inviteLabel}</span>
      </span>
      <span className="agent-roster-item__trailing agent-invite__actions">
        <button
          type="button"
          className="agent-invite__action agent-invite__action--decline"
          onClick={() => onRespond(invite, 'decline')}
          disabled={disabled}
          aria-label={`Decline ${inviteLabel.toLowerCase()} for ${agentName}`}
          title="Decline"
        >
          <X className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="agent-invite__action agent-invite__action--accept"
          onClick={() => onRespond(invite, 'accept')}
          disabled={disabled}
          aria-label={`Accept ${inviteLabel.toLowerCase()} for ${agentName}`}
          title="Accept"
        >
          <Check className="h-3.5 w-3.5" />
        </button>
      </span>
    </div>
  )
}
