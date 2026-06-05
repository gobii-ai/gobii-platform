import { useCallback, useEffect, useMemo, useState, type FormEvent, type MouseEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Plus, Save, Send, ShieldAlert, Trash2, UserMinus, Users } from 'lucide-react'

import {
  fetchCurrentOrganization,
  inviteOrganizationMember,
  removeOrganizationMember,
  resendOrganizationInvite,
  revokeOrganizationInvite,
  updateCurrentOrganizationName,
  updateOrganizationMemberRole,
  type CurrentOrganizationPayload,
  type OrganizationInvite,
  type OrganizationMember,
} from '../api/organization'
import { HttpError } from '../api/http'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { ActionConfirmDialog } from '../components/common/ActionConfirmDialog'
import { ModalForm } from '../components/common/ModalForm'
import { navigateWithinApp } from '../util/appNavigation'

type ConfirmAction = {
  kind: 'remove-member'
  member: OrganizationMember
} | {
  kind: 'revoke-invite'
  invite: OrganizationInvite
}

const SOLUTIONS_PARTNER_ROLE = 'solutions_partner'

const dateFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

function formatDate(value: string | null): string {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return dateFormatter.format(date)
}

function formatErrors(error: unknown, fallback: string): string[] {
  if (error instanceof HttpError && typeof error.body === 'object' && error.body) {
    const body = error.body as Record<string, unknown>
    if (body.errors && typeof body.errors === 'object') {
      return Object.values(body.errors as Record<string, unknown>).flatMap((messages) => (
        Array.isArray(messages) ? messages.map(String) : [String(messages)]
      ))
    }
    if (body.error) {
      return [String(body.error)]
    }
  }
  if (error instanceof Error) {
    return [error.message]
  }
  return [fallback]
}

function publishOrganizationContext(data: CurrentOrganizationPayload) {
  if (typeof window === 'undefined') {
    return
  }
  window.dispatchEvent(new CustomEvent('gobii:console-context-updated', {
    detail: {
      type: 'organization',
      id: data.organization.id,
      name: data.organization.name,
    },
  }))
}

function buildBillingPathForCurrentAppRoute(): string {
  if (typeof window === 'undefined') {
    return '/app/billing'
  }
  const match = window.location.pathname.match(/^\/app\/agents\/([^/]+)$/)
  if (!match) {
    return '/app/billing'
  }
  const params = new URLSearchParams()
  const currentParams = new URLSearchParams(window.location.search)
  for (const key of ['embed', 'return_to']) {
    const value = currentParams.get(key)
    if (value !== null) {
      params.set(key, value)
    }
  }
  params.set('shell', 'billing')
  return `/app/agents/${match[1]}?${params.toString()}`
}

function ConfirmOrganizationActionModal({
  action,
  onClose,
  onConfirm,
}: {
  action: ConfirmAction
  onClose: () => void
  onConfirm: (action: ConfirmAction) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isRemove = action.kind === 'remove-member'
  const title = isRemove ? 'Remove Member' : 'Revoke Invite'
  const subject = isRemove ? action.member.email : action.invite.email
  const subtitle = isRemove
    ? `${subject} will lose access to this organization.`
    : `${subject} will no longer be able to accept this invitation.`

  const handleConfirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm(action)
      onClose()
    } catch (err) {
      setError(formatErrors(err, 'Unable to update organization membership.')[0] ?? 'Unable to update organization membership.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ActionConfirmDialog
      open
      title={title}
      description={subtitle}
      onClose={onClose}
      icon={isRemove ? UserMinus : ShieldAlert}
      confirmLabel={isRemove ? 'Remove Member' : 'Revoke Invite'}
      busy={busy}
      danger
      onConfirm={handleConfirm}
      localError={error}
    />
  )
}

function AddMemberModal({
  roles,
  email,
  role,
  seatsAvailable,
  errors,
  busy,
  onEmailChange,
  onRoleChange,
  onClose,
  onSubmit,
}: {
  roles: CurrentOrganizationPayload['roles']
  email: string
  role: string
  seatsAvailable: number | null
  errors: string[]
  busy: boolean
  onEmailChange: (email: string) => void
  onRoleChange: (role: string) => void
  onClose: () => void
  onSubmit: (event: FormEvent) => void
}) {
  const noSeatsAvailable = seatsAvailable !== null && seatsAvailable <= 0
  const selectedRoleRequiresSeat = role !== SOLUTIONS_PARTNER_ROLE
  const submitDisabled = !role || (noSeatsAvailable && selectedRoleRequiresSeat)

  return (
    <ModalForm
      id="organization-add-member-form"
      title="Add Member"
      subtitle="Send an invitation to join this organization."
      onClose={onClose}
      onSubmit={onSubmit}
      widthClass="sm:max-w-lg"
      icon={Users}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
      dismissible={!busy}
      submitLabel="Send Invite"
      submittingLabel="Sending..."
      submitting={busy}
      submitDisabled={submitDisabled && !busy}
      errorMessages={errors}
    >
        <div>
          <label htmlFor="organization-member-email" className="block text-sm font-medium text-slate-700">
            Email
          </label>
          <input
            id="organization-member-email"
            type="email"
            required
            value={email}
            onChange={(event) => onEmailChange(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder="teammate@example.com"
            autoFocus
          />
        </div>
        <div>
          <label htmlFor="organization-member-role" className="block text-sm font-medium text-slate-700">
            Role
          </label>
          <select
            id="organization-member-role"
            value={role}
            onChange={(event) => onRoleChange(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
          >
            {roles.map((roleOption) => (
              <option key={roleOption.value} value={roleOption.value}>{roleOption.label}</option>
            ))}
          </select>
        </div>
        {noSeatsAvailable ? (
          <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            No standard member seats are available. Select Solutions Partner or add seats first.
          </p>
        ) : null}
    </ModalForm>
  )
}

export function OrganizationScreen() {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['current-organization'] as const, [])
  const { data, error, isLoading } = useQuery({
    queryKey,
    queryFn: ({ signal }) => fetchCurrentOrganization(signal),
  })

  const [nameDraft, setNameDraft] = useState('')
  const [nameMessage, setNameMessage] = useState<string | null>(null)
  const [nameErrors, setNameErrors] = useState<string[]>([])
  const [savingName, setSavingName] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('')
  const [inviteErrors, setInviteErrors] = useState<string[]>([])
  const [inviting, setInviting] = useState(false)
  const [inviteBusyToken, setInviteBusyToken] = useState<string | null>(null)
  const [inviteMessage, setInviteMessage] = useState<string | null>(null)
  const [memberBusyId, setMemberBusyId] = useState<string | null>(null)
  const [memberError, setMemberError] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)
  const [addMemberOpen, setAddMemberOpen] = useState(false)

  useEffect(() => {
    if (data) {
      setNameDraft(data.organization.name)
      if (!inviteRole && data.roles[0]) {
        setInviteRole(data.roles[0].value)
      }
    }
  }, [data, inviteRole])

  const updateCachedData = (nextData: CurrentOrganizationPayload) => {
    queryClient.setQueryData(queryKey, nextData)
    publishOrganizationContext(nextData)
  }

  const handleNameSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!data) {
      return
    }
    const nextName = nameDraft.trim()
    if (!nextName) {
      setNameErrors(['Organization name is required.'])
      return
    }
    setSavingName(true)
    setNameErrors([])
    setNameMessage(null)
    try {
      const nextData = await updateCurrentOrganizationName(nextName)
      updateCachedData(nextData)
      setNameMessage('Organization updated.')
    } catch (err) {
      setNameErrors(formatErrors(err, 'Unable to update organization.'))
    } finally {
      setSavingName(false)
    }
  }

  const handleInviteSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setInviting(true)
    setInviteErrors([])
    setInviteMessage(null)
    try {
      const nextData = await inviteOrganizationMember(inviteEmail.trim(), inviteRole)
      updateCachedData(nextData)
      setInviteEmail('')
      setAddMemberOpen(false)
    } catch (err) {
      setInviteErrors(formatErrors(err, 'Unable to send invite.'))
    } finally {
      setInviting(false)
    }
  }

  const handleResendInvite = async (invite: OrganizationInvite) => {
    setInviteBusyToken(invite.token)
    setMemberError(null)
    setInviteMessage(null)
    try {
      const nextData = await resendOrganizationInvite(invite.token)
      updateCachedData(nextData)
      setInviteMessage(`Invitation resent to ${invite.email}.`)
    } catch (err) {
      setMemberError(formatErrors(err, 'Unable to resend invite.')[0] ?? 'Unable to resend invite.')
    } finally {
      setInviteBusyToken(null)
    }
  }

  const handleRoleChange = async (member: OrganizationMember, role: string) => {
    setMemberBusyId(member.userId)
    setMemberError(null)
    try {
      const nextData = await updateOrganizationMemberRole(member.userId, role)
      updateCachedData(nextData)
    } catch (err) {
      setMemberError(formatErrors(err, 'Unable to update member role.')[0] ?? 'Unable to update member role.')
    } finally {
      setMemberBusyId(null)
    }
  }

  const handleConfirmAction = async (action: ConfirmAction) => {
    if (action.kind === 'remove-member') {
      const nextData = await removeOrganizationMember(action.member.userId)
      updateCachedData(nextData)
      return
    }
    const nextData = await revokeOrganizationInvite(action.invite.token)
    updateCachedData(nextData)
  }

  const billingUrl = buildBillingPathForCurrentAppRoute()
  const handleAddSeatsClick = useCallback((event: MouseEvent<HTMLAnchorElement>) => {
    if (navigateWithinApp(billingUrl)) {
      event.preventDefault()
    }
  }, [billingUrl])

  if (isLoading) {
    return (
      <div className="profile-screen profile-screen--embedded">
        <section className="profile-screen__section">
          <p className="profile-screen__muted">Loading organization...</p>
        </section>
      </div>
    )
  }

  if (error || !data) {
    return (
      <SettingsBanner
        variant="embedded"
        title="Organization Context Required"
        subtitle={formatErrors(error, 'Switch to an organization context to manage organization settings.')[0]}
      />
    )
  }

  const canManageMembers = data.viewer.canManageMembers
  const canEditOrganization = data.viewer.canEditOrganization
  const availableSeats = data.billing?.seatsAvailable ?? null
  const noSeatsAvailable = availableSeats !== null && availableSeats <= 0
  const canInviteSolutionsPartnerWithoutSeats = data.roles.some((roleOption) => roleOption.value === SOLUTIONS_PARTNER_ROLE)
  const addMemberDisabled = noSeatsAvailable && !canInviteSolutionsPartnerWithoutSeats
  const addMemberDisabledLabel = addMemberDisabled ? 'No seats available' : undefined

  return (
    <div className="profile-screen profile-screen--embedded organization-screen">
      <header className="profile-screen__header">
        <div className="profile-screen__title-icon" aria-hidden="true">
          <Building2 className="h-5 w-5" />
        </div>
        <div>
          <p className="profile-screen__eyebrow">Organization</p>
          <h1>{data.organization.name}</h1>
        </div>
      </header>

      {!canEditOrganization && !canManageMembers ? (
        <SettingsBanner
          variant="embedded"
          title="Read-Only Access"
          subtitle={`Your ${data.viewer.roleLabel} role can view this organization, but cannot edit settings or membership.`}
        />
      ) : null}

      <section className="profile-screen__section">
        <div className="profile-screen__section-header">
          <div className="profile-screen__section-icon" aria-hidden="true">
            <Building2 className="h-4 w-4" />
          </div>
          <div>
            <h2>Organization Details</h2>
            <p>Current role: {data.viewer.roleLabel}</p>
          </div>
        </div>
        <form onSubmit={handleNameSubmit} className="organization-screen__name-form">
          <div className="profile-screen__form-grid organization-screen__name-grid">
            <label className="profile-screen__field">
              <span>Name</span>
              <input
                type="text"
                value={nameDraft}
                onChange={(event) => setNameDraft(event.target.value)}
                disabled={!canEditOrganization || savingName}
              />
              {nameErrors.map((message) => (
                <em key={message}>{message}</em>
              ))}
            </label>
          </div>
          <div className="profile-screen__actions">
            {canEditOrganization ? (
              <button
                type="submit"
                className="profile-screen__button profile-screen__button--primary"
                disabled={savingName || nameDraft.trim() === data.organization.name}
              >
                <Save className="h-4 w-4" aria-hidden="true" />
                {savingName ? 'Saving...' : 'Save Name'}
              </button>
            ) : null}
            {nameMessage ? <p className="profile-screen__feedback profile-screen__feedback--success">{nameMessage}</p> : null}
          </div>
        </form>
      </section>

      <section className="profile-screen__section">
        <div className="profile-screen__section-header organization-screen__section-header">
          <div className="organization-screen__section-title">
            <div className="profile-screen__section-icon" aria-hidden="true">
              <Users className="h-4 w-4" />
            </div>
            <div>
              <h2>Members</h2>
              <p>
                {data.members.length} member{data.members.length === 1 ? '' : 's'}
                {availableSeats !== null ? ` - ${availableSeats} seat${availableSeats === 1 ? '' : 's'} available` : ''}
              </p>
            </div>
          </div>
          <div className="organization-screen__member-actions">
            <a
              href={billingUrl}
              className="profile-screen__button profile-screen__button--secondary"
              onClick={handleAddSeatsClick}
            >
              Add Seats
            </a>
            {canManageMembers ? (
              <button
                type="button"
                className="profile-screen__button profile-screen__button--primary"
                onClick={() => {
                  setInviteEmail('')
                  setInviteErrors([])
                  const defaultRole = noSeatsAvailable && canInviteSolutionsPartnerWithoutSeats
                    ? SOLUTIONS_PARTNER_ROLE
                    : data.roles[0]?.value
                  if (defaultRole) {
                    setInviteRole(defaultRole)
                  }
                  setAddMemberOpen(true)
                }}
                disabled={addMemberDisabled}
                title={addMemberDisabledLabel}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                Add Member
              </button>
            ) : null}
          </div>
        </div>
        {memberError ? <p className="profile-screen__feedback profile-screen__feedback--error">{memberError}</p> : null}
        {inviteMessage ? <p className="profile-screen__feedback profile-screen__feedback--success">{inviteMessage}</p> : null}
        <div className="organization-screen__table-wrap">
          <table className="organization-screen__table">
            <thead>
              <tr>
                <th>Member</th>
                <th>Role</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.members.map((member) => (
                <tr key={member.userId}>
                  <td>
                    <p className="organization-screen__primary-text">{member.name}</p>
                    <p className="profile-screen__muted">{member.email}</p>
                  </td>
                  <td>
                    {canManageMembers && member.canUpdateRole ? (
                      <select
                        value={member.role}
                        onChange={(event) => void handleRoleChange(member, event.target.value)}
                        disabled={memberBusyId === member.userId}
                      >
                        {data.roles.map((role) => (
                          <option key={role.value} value={role.value}>{role.label}</option>
                        ))}
                      </select>
                    ) : (
                      <span className="profile-screen__status">{member.roleLabel}</span>
                    )}
                  </td>
                  <td>
                    {canManageMembers && member.canRemove ? (
                      <button
                        type="button"
                        className="profile-screen__button profile-screen__button--danger"
                        onClick={() => setConfirmAction({ kind: 'remove-member', member })}
                        disabled={memberBusyId === member.userId}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                        Remove
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

      {data.pendingInvites.length > 0 ? (
        <div className="organization-screen__pending">
          <div className="organization-screen__pending-header">
            <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            <span>{data.pendingInvites.length} pending invite{data.pendingInvites.length === 1 ? '' : 's'}</span>
          </div>
          <div className="organization-screen__invite-list">
            {data.pendingInvites.map((invite) => (
              <div key={invite.token} className="organization-screen__invite-row">
                <div>
                  <p className="organization-screen__primary-text">{invite.email}</p>
                  <p className="profile-screen__muted">
                    {invite.roleLabel} - invited by {invite.invitedBy} - expires {formatDate(invite.expiresAt)}
                  </p>
                </div>
                {canManageMembers ? (
                  <div className="flex flex-wrap justify-end gap-2">
                    <button
                      type="button"
                      className="profile-screen__button profile-screen__button--secondary"
                      onClick={() => void handleResendInvite(invite)}
                      disabled={inviteBusyToken === invite.token}
                    >
                      <Send className="h-4 w-4" aria-hidden="true" />
                      {inviteBusyToken === invite.token ? 'Sending...' : 'Resend'}
                    </button>
                    <button
                      type="button"
                      className="profile-screen__button profile-screen__button--danger"
                      onClick={() => setConfirmAction({ kind: 'revoke-invite', invite })}
                      disabled={inviteBusyToken === invite.token}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                      Revoke
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      </section>

      {confirmAction ? (
        <ConfirmOrganizationActionModal
          action={confirmAction}
          onClose={() => setConfirmAction(null)}
          onConfirm={handleConfirmAction}
        />
      ) : null}
      {addMemberOpen ? (
        <AddMemberModal
          roles={data.roles}
          email={inviteEmail}
          role={inviteRole}
          seatsAvailable={availableSeats}
          errors={inviteErrors}
          busy={inviting}
          onEmailChange={setInviteEmail}
          onRoleChange={setInviteRole}
          onClose={() => {
            if (!inviting) {
              setAddMemberOpen(false)
            }
          }}
          onSubmit={handleInviteSubmit}
        />
      ) : null}
    </div>
  )
}
