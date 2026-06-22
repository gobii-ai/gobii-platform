import { useCallback, useEffect, useMemo, useState, type FormEvent, type MouseEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Building2, Play, Plus, Save, Send, ShieldAlert, Trash2, UserMinus, Users } from 'lucide-react'

import {
  createOrganizationTemplate,
  deactivateOrganizationTemplate,
  fetchCurrentOrganization,
  fetchCurrentOrganizationTemplates,
  inviteOrganizationMember,
  launchOrganizationTemplate,
  removeOrganizationMember,
  resendOrganizationInvite,
  revokeOrganizationInvite,
  updateCurrentOrganizationCustomInstructions,
  updateCurrentOrganizationMemberAgentCreation,
  updateCurrentOrganizationName,
  updateOrganizationMemberRole,
  type CurrentOrganizationPayload,
  type CurrentOrganizationTemplatesPayload,
  type OrganizationInvite,
  type OrganizationMember,
  type OrganizationTemplate,
} from '../api/organization'
import { HttpError } from '../api/http'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { ActionConfirmDialog } from '../components/common/ActionConfirmDialog'
import { ModalForm } from '../components/common/ModalForm'
import { CustomInstructionsSection } from '../components/settings/CustomInstructionsSection'
import { navigateWithinApp } from '../util/appNavigation'

type ConfirmAction = {
  kind: 'remove-member'
  member: OrganizationMember
} | {
  kind: 'revoke-invite'
  invite: OrganizationInvite
} | {
  kind: 'deactivate-template'
  template: OrganizationTemplate
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
  const isTemplateDeactivate = action.kind === 'deactivate-template'
  const title = isTemplateDeactivate ? 'Deactivate Template' : isRemove ? 'Remove Member' : 'Revoke Invite'
  const subject = isRemove ? action.member.email : isTemplateDeactivate ? action.template.name : action.invite.email
  const subtitle = isTemplateDeactivate
    ? `${subject} will no longer appear for this organization.`
    : isRemove
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
      icon={isTemplateDeactivate ? Bot : isRemove ? UserMinus : ShieldAlert}
      confirmLabel={isTemplateDeactivate ? 'Deactivate Template' : isRemove ? 'Remove Member' : 'Revoke Invite'}
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

function CreateTemplateModal({
  sourceAgents,
  sourceAgentId,
  errors,
  busy,
  onSourceAgentChange,
  onClose,
  onSubmit,
}: {
  sourceAgents: CurrentOrganizationTemplatesPayload['sourceAgents']
  sourceAgentId: string
  errors: string[]
  busy: boolean
  onSourceAgentChange: (agentId: string) => void
  onClose: () => void
  onSubmit: (event: FormEvent) => void
}) {
  const hasSourceAgents = sourceAgents.length > 0

  return (
    <ModalForm
      id="organization-create-template-form"
      title="Create Template"
      subtitle="Clone one of this organization's agents into a private template."
      onClose={onClose}
      onSubmit={onSubmit}
      widthClass="sm:max-w-lg"
      icon={Bot}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
      dismissible={!busy}
      submitLabel="Create Template"
      submittingLabel="Creating..."
      submitting={busy}
      submitDisabled={!hasSourceAgents || !sourceAgentId}
      errorMessages={errors}
    >
      {hasSourceAgents ? (
        <label htmlFor="organization-template-source-agent" className="block text-sm font-medium text-slate-700">
          Source Agent
          <select
            id="organization-template-source-agent"
            value={sourceAgentId}
            onChange={(event) => onSourceAgentChange(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            disabled={busy}
          >
            {sourceAgents.map((agent) => (
              <option key={agent.id} value={agent.id}>{agent.name}</option>
            ))}
          </select>
          <span className="mt-2 block text-xs font-normal text-slate-500">
            {busy
              ? 'Generating the template from this agent. This can take up to a minute.'
              : 'Template generation can take up to a minute.'}
          </span>
        </label>
      ) : (
        <p className="text-sm text-slate-600">Create an organization-owned agent before turning it into a template.</p>
      )}
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
  const templateQueryKey = useMemo(() => ['current-organization-templates'] as const, [])
  const {
    data: templateData,
    error: templateQueryError,
    isLoading: templatesLoading,
  } = useQuery({
    queryKey: templateQueryKey,
    queryFn: ({ signal }) => fetchCurrentOrganizationTemplates(signal),
    enabled: Boolean(data),
  })

  const [nameDraft, setNameDraft] = useState('')
  const [nameMessage, setNameMessage] = useState<string | null>(null)
  const [nameErrors, setNameErrors] = useState<string[]>([])
  const [savingName, setSavingName] = useState(false)
  const [agentCreationMessage, setAgentCreationMessage] = useState<string | null>(null)
  const [agentCreationErrors, setAgentCreationErrors] = useState<string[]>([])
  const [savingAgentCreation, setSavingAgentCreation] = useState(false)
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
  const [createTemplateOpen, setCreateTemplateOpen] = useState(false)
  const [templateSourceAgentId, setTemplateSourceAgentId] = useState('')
  const [templateErrors, setTemplateErrors] = useState<string[]>([])
  const [templateMessage, setTemplateMessage] = useState<string | null>(null)
  const [templateBusy, setTemplateBusy] = useState(false)
  const [templateLaunchBusyId, setTemplateLaunchBusyId] = useState<string | null>(null)

  useEffect(() => {
    if (data) {
      setNameDraft(data.organization.name)
    }
  }, [data?.organization.name])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined
    }

    const handleContextUpdated = (event: Event) => {
      const detail = (event as CustomEvent<{ type?: string; id?: string }>).detail
      if (!detail?.type || !detail.id) {
        return
      }
      if (detail.type !== 'organization' || detail.id !== data?.organization.id) {
        queryClient.setQueryData(queryKey, undefined)
        queryClient.setQueryData(templateQueryKey, undefined)
        void queryClient.invalidateQueries({ queryKey })
        void queryClient.invalidateQueries({ queryKey: templateQueryKey })
      }
    }

    window.addEventListener('gobii:console-context-updated', handleContextUpdated)
    return () => {
      window.removeEventListener('gobii:console-context-updated', handleContextUpdated)
    }
  }, [data?.organization.id, queryClient, queryKey, templateQueryKey])

  useEffect(() => {
    if (!inviteRole && data?.roles[0]) {
      setInviteRole(data.roles[0].value)
    }
  }, [data?.roles, inviteRole])

  useEffect(() => {
    const firstSourceAgent = templateData?.sourceAgents[0]
    if (!firstSourceAgent || templateSourceAgentId) {
      return
    }
    setTemplateSourceAgentId(firstSourceAgent.id)
  }, [templateData, templateSourceAgentId])

  const updateCachedData = (nextData: CurrentOrganizationPayload) => {
    queryClient.setQueryData(queryKey, nextData)
    publishOrganizationContext(nextData)
  }

  const updateCachedTemplateData = (nextData: CurrentOrganizationTemplatesPayload) => {
    queryClient.setQueryData(templateQueryKey, nextData)
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

  const handleCustomInstructionsSave = async (normalizedInstructions: string) => {
    const nextData = await updateCurrentOrganizationCustomInstructions(normalizedInstructions)
    updateCachedData(nextData)
    return nextData.organization.customInstructions
  }

  const handleMemberAgentCreationChange = async (enabled: boolean) => {
    setSavingAgentCreation(true)
    setAgentCreationErrors([])
    setAgentCreationMessage(null)
    try {
      const nextData = await updateCurrentOrganizationMemberAgentCreation(enabled)
      updateCachedData(nextData)
      setAgentCreationMessage('Agent creation setting updated.')
    } catch (err) {
      setAgentCreationErrors(formatErrors(err, 'Unable to update agent creation setting.'))
    } finally {
      setSavingAgentCreation(false)
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
    if (action.kind === 'deactivate-template') {
      const nextData = await deactivateOrganizationTemplate(action.template.id)
      updateCachedTemplateData(nextData)
      setTemplateMessage(`${action.template.name} deactivated.`)
      return
    }
    const nextData = await revokeOrganizationInvite(action.invite.token)
    updateCachedData(nextData)
  }

  const handleCreateTemplateSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!templateSourceAgentId) {
      setTemplateErrors(['Choose an agent to clone.'])
      return
    }
    setTemplateBusy(true)
    setTemplateErrors([])
    setTemplateMessage(null)
    try {
      const nextData = await createOrganizationTemplate(templateSourceAgentId)
      updateCachedTemplateData(nextData)
      setCreateTemplateOpen(false)
      setTemplateMessage(nextData.created ? 'Template created.' : 'Template already exists for that agent.')
    } catch (err) {
      setTemplateErrors(formatErrors(err, 'Unable to create template.'))
    } finally {
      setTemplateBusy(false)
    }
  }

  const handleLaunchTemplate = async (template: OrganizationTemplate) => {
    setTemplateLaunchBusyId(template.id)
    setTemplateErrors([])
    setTemplateMessage(null)
    try {
      const payload = await launchOrganizationTemplate(template.id)
      if (!navigateWithinApp(payload.redirectUrl)) {
        window.location.assign(payload.redirectUrl)
      }
    } catch (err) {
      setTemplateErrors(formatErrors(err, 'Unable to use template.'))
    } finally {
      setTemplateLaunchBusyId(null)
    }
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
  const canEditCustomInstructions = data.viewer.canEditCustomInstructions
  const canEditMemberAgentCreation = data.viewer.canEditMemberAgentCreation
  const availableSeats = data.billing?.seatsAvailable ?? null
  const noSeatsAvailable = availableSeats !== null && availableSeats <= 0
  const canInviteSolutionsPartnerWithoutSeats = data.roles.some((roleOption) => roleOption.value === SOLUTIONS_PARTNER_ROLE)
  const addMemberDisabled = noSeatsAvailable && !canInviteSolutionsPartnerWithoutSeats
  const addMemberDisabledLabel = addMemberDisabled ? 'No seats available' : undefined
  const templates = templateData?.templates ?? []
  const sourceAgents = templateData?.sourceAgents ?? []
  const canManageTemplates = Boolean(templateData?.viewer.canManageTemplates)
  const templateQueryErrorMessage = templateQueryError
    ? formatErrors(templateQueryError, 'Unable to load organization templates.')[0]
    : null

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
        <div className="profile-screen__form-grid">
          <label className="profile-screen__field profile-screen__field--wide">
            <span>Member Agent Creation</span>
            <span className="inline-flex items-center gap-3 text-sm font-normal text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                checked={data.organization.membersCanCreateAgents}
                disabled={!canEditMemberAgentCreation || savingAgentCreation}
                onChange={(event) => void handleMemberAgentCreationChange(event.target.checked)}
              />
              <span>Members may create organization agents</span>
            </span>
            {agentCreationErrors.map((message) => (
              <em key={message}>{message}</em>
            ))}
          </label>
        </div>
        <div className="profile-screen__actions">
          {agentCreationMessage ? (
            <p className="profile-screen__feedback profile-screen__feedback--success">{agentCreationMessage}</p>
          ) : null}
        </div>
      </section>

      <CustomInstructionsSection
        value={data.organization.customInstructions}
        maxChars={data.organization.customInstructionsMaxChars}
        canEdit={canEditCustomInstructions}
        placeholder="Follow the organization's tone, policies, and operating preferences."
        successMessage="Custom instructions updated."
        onSave={handleCustomInstructionsSave}
        formatErrorMessages={(err) => formatErrors(err, 'Unable to update custom instructions.')}
      />

      <section className="profile-screen__section">
        <div className="profile-screen__section-header organization-screen__section-header">
          <div className="organization-screen__section-title">
            <div className="profile-screen__section-icon" aria-hidden="true">
              <Bot className="h-4 w-4" />
            </div>
            <div>
              <h2>Templates</h2>
              <p>{templates.length} organization template{templates.length === 1 ? '' : 's'}</p>
            </div>
          </div>
          {canManageTemplates ? (
            <button
              type="button"
              className="profile-screen__button profile-screen__button--primary"
              onClick={() => {
                setTemplateErrors([])
                setTemplateMessage(null)
                setTemplateSourceAgentId(sourceAgents[0]?.id ?? '')
                setCreateTemplateOpen(true)
              }}
              disabled={!sourceAgents.length || templateBusy}
              title={!sourceAgents.length ? 'Create an organization agent first' : undefined}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create Template
            </button>
          ) : null}
        </div>
        {templateMessage ? <p className="profile-screen__feedback profile-screen__feedback--success">{templateMessage}</p> : null}
        {templateQueryErrorMessage ? <p className="profile-screen__feedback profile-screen__feedback--error">{templateQueryErrorMessage}</p> : null}
        {templateErrors.map((message) => (
          <p key={message} className="profile-screen__feedback profile-screen__feedback--error">{message}</p>
        ))}
        {templatesLoading ? (
          <p className="profile-screen__muted">Loading templates...</p>
        ) : templates.length > 0 ? (
          <div className="organization-screen__table-wrap">
            <table className="organization-screen__table">
              <thead>
                <tr>
                  <th>Template</th>
                  <th>Source</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {templates.map((template) => (
                  <tr key={template.id}>
                    <td>
                      <p className="organization-screen__primary-text">{template.name}</p>
                      <p className="profile-screen__muted">{template.category} - {template.tagline}</p>
                      {template.scheduleDescription ? (
                        <p className="profile-screen__muted">{template.scheduleDescription}</p>
                      ) : null}
                    </td>
                    <td>
                      <p className="organization-screen__primary-text">{template.sourceAgentName ?? '-'}</p>
                      {template.createdBy ? <p className="profile-screen__muted">Created by {template.createdBy}</p> : null}
                    </td>
                    <td>
                      <div className="flex flex-wrap justify-end gap-2">
                        <button
                          type="button"
                          className="profile-screen__button profile-screen__button--primary"
                          onClick={() => void handleLaunchTemplate(template)}
                          disabled={templateLaunchBusyId === template.id}
                        >
                          <Play className="h-4 w-4" aria-hidden="true" />
                          {templateLaunchBusyId === template.id ? 'Opening...' : 'Use Template'}
                        </button>
                        {canManageTemplates ? (
                          <button
                            type="button"
                            className="profile-screen__button profile-screen__button--danger"
                            onClick={() => setConfirmAction({ kind: 'deactivate-template', template })}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                            Deactivate
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="profile-screen__muted">No organization templates yet.</p>
        )}
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
      {createTemplateOpen ? (
        <CreateTemplateModal
          sourceAgents={sourceAgents}
          sourceAgentId={templateSourceAgentId}
          errors={templateErrors}
          busy={templateBusy}
          onSourceAgentChange={setTemplateSourceAgentId}
          onClose={() => {
            if (!templateBusy) {
              setCreateTemplateOpen(false)
            }
          }}
          onSubmit={handleCreateTemplateSubmit}
        />
      ) : null}
    </div>
  )
}
