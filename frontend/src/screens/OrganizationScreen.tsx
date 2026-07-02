import { useCallback, useEffect, useMemo, useState, type FormEvent, type MouseEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Building2, Pencil, Play, Plus, Save, Send, ShieldAlert, Trash2, UserMinus, Users } from 'lucide-react'

import { createOrganization, type ConsoleContext, type ConsoleContextOption } from '../api/context'
import {
  currentOrganizationTemplatesQueryKey,
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
  updateOrganizationTemplate,
  updateOrganizationMemberRole,
  type CurrentOrganizationPayload,
  type CurrentOrganizationTemplatesPayload,
  type OrganizationInvite,
  type OrganizationMember,
  type OrganizationTemplate,
  type OrganizationTemplateEditorPayload,
} from '../api/organization'
import { HttpError } from '../api/http'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { ActionConfirmDialog } from '../components/common/ActionConfirmDialog'
import { AgentIntelligenceSlider } from '../components/common/AgentIntelligenceSlider'
import { ModalForm } from '../components/common/ModalForm'
import { CustomInstructionsSection } from '../components/settings/CustomInstructionsSection'
import type { IntelligenceTierKey, LlmIntelligenceConfig } from '../types/llmIntelligence'
import { useConsoleContextSwitcher } from '../hooks/useConsoleContextSwitcher'
import { navigateWithinApp } from '../util/appNavigation'
import { storeConsoleContext } from '../util/consoleContextStorage'

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
const FALLBACK_INTELLIGENCE_TIER: IntelligenceTierKey = 'standard'

const dateFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

function formatDate(value: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date)
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

function resolveTemplateDefaultTier(config?: LlmIntelligenceConfig | null): IntelligenceTierKey {
  const options = config?.options ?? []
  const systemDefault = config?.systemDefaultTier
  return systemDefault && options.some((option) => option.key === systemDefault)
    ? systemDefault
    : options[0]?.key ?? FALLBACK_INTELLIGENCE_TIER
}

function buildBlankTemplateDraft(config?: LlmIntelligenceConfig | null): OrganizationTemplateEditorPayload {
  return { name: '', tagline: '', charter: '', preferredLlmTier: resolveTemplateDefaultTier(config) }
}

function formatTemplateTier(template: OrganizationTemplate, config?: LlmIntelligenceConfig | null): string {
  const tier = config?.options.find((option) => option.key === template.preferredLlmTier)
  return tier?.label ?? template.preferredLlmTier
}

function publishOrganizationContext(data: CurrentOrganizationPayload) {
  publishConsoleContext({ type: 'organization', id: data.organization.id, name: data.organization.name })
}

function publishConsoleContext(context: ConsoleContext) {
  if (typeof window === 'undefined') return
  storeConsoleContext(context)
  window.dispatchEvent(new CustomEvent('gobii:console-context-updated', { detail: context }))
}

function isNoOrganizationContextError(error: unknown): boolean {
  const body = error instanceof HttpError && error.status === 404 ? error.body : null
  return Boolean(body && typeof body === 'object' && (body as { error?: unknown }).error === 'Switch to an organization context first.')
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

type ConfirmOrganizationActionModalProps = { action: ConfirmAction; onClose: () => void; onConfirm: (action: ConfirmAction) => Promise<void> }

function ConfirmOrganizationActionModal({ action, onClose, onConfirm }: ConfirmOrganizationActionModalProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isRemove = action.kind === 'remove-member'
  const isTemplateDeactivate = action.kind === 'deactivate-template'
  const title = isTemplateDeactivate ? 'Deactivate Template' : isRemove ? 'Remove Member' : 'Revoke Invite'
  const subject = isRemove ? action.member.email : isTemplateDeactivate ? action.template.name : action.invite.email
  const subtitle = isTemplateDeactivate
    ? `${subject} will no longer appear for this team.`
    : isRemove
      ? `${subject} will lose access to this team.`
      : `${subject} will no longer be able to accept this invitation.`

  const handleConfirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm(action)
      onClose()
    } catch (err) {
      setError(formatErrors(err, 'Unable to update team membership.')[0] ?? 'Unable to update team membership.')
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

type CreateOrganizationModalProps = { name: string; errors: string[]; busy: boolean; onNameChange: (name: string) => void; onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void }

function CreateOrganizationModal({ name, errors, busy, onNameChange, onClose, onSubmit }: CreateOrganizationModalProps) {
  return (
    <ModalForm
      id="organization-create-team-form" title="Create Team" onClose={onClose} onSubmit={onSubmit} widthClass="sm:max-w-lg"
      dismissible={!busy} submitLabel="Create Team" submittingLabel="Creating..." submitting={busy} errorMessages={errors}
    >
      <label htmlFor="organization-create-team-name" className="block text-sm font-medium text-slate-700">
        Team Name
        <input
          id="organization-create-team-name"
          type="text"
          required
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
          placeholder="Acme Operations"
          autoFocus
          disabled={busy}
        />
      </label>
    </ModalForm>
  )
}

type AddMemberModalProps = { roles: CurrentOrganizationPayload['roles']; email: string; role: string; seatsAvailable: number | null; errors: string[]; busy: boolean; onEmailChange: (email: string) => void; onRoleChange: (role: string) => void; onClose: () => void; onSubmit: (event: FormEvent) => void }

function AddMemberModal({ roles, email, role, seatsAvailable, errors, busy, onEmailChange, onRoleChange, onClose, onSubmit }: AddMemberModalProps) {
  const noSeatsAvailable = seatsAvailable !== null && seatsAvailable <= 0
  const selectedRoleRequiresSeat = role !== SOLUTIONS_PARTNER_ROLE
  const submitDisabled = !role || (noSeatsAvailable && selectedRoleRequiresSeat)

  return (
    <ModalForm
      id="organization-add-member-form"
      title="Add Member"
      subtitle="Send an invitation to join this team."
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

type CreateTemplateModalProps = { sourceAgents: CurrentOrganizationTemplatesPayload['sourceAgents']; sourceAgentId: string; errors: string[]; busy: boolean; onSourceAgentChange: (agentId: string) => void; onClose: () => void; onSubmit: (event: FormEvent) => void }

function CreateTemplateModal({ sourceAgents, sourceAgentId, errors, busy, onSourceAgentChange, onClose, onSubmit }: CreateTemplateModalProps) {
  const hasSourceAgents = sourceAgents.length > 0

  return (
    <ModalForm
      id="organization-create-template-form"
      title="Create Template"
      subtitle="Clone one of this team's agents into a private template."
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
        <p className="text-sm text-slate-600">Create a team-owned agent before turning it into a template.</p>
      )}
    </ModalForm>
  )
}

type TemplateEditorModalProps = { mode: 'create' | 'edit'; draft: OrganizationTemplateEditorPayload; intelligenceConfig: LlmIntelligenceConfig | null; errors: string[]; busy: boolean; onDraftChange: (draft: OrganizationTemplateEditorPayload) => void; onClose: () => void; onSubmit: (event: FormEvent) => void }

function TemplateEditorModal({ mode, draft, intelligenceConfig, errors, busy, onDraftChange, onClose, onSubmit }: TemplateEditorModalProps) {
  const updateDraft = <K extends keyof OrganizationTemplateEditorPayload>(
    key: K,
    value: OrganizationTemplateEditorPayload[K],
  ) => {
    onDraftChange({ ...draft, [key]: value })
  }

  return (
    <ModalForm
      id="organization-template-editor-form"
      title={mode === 'create' ? 'New Template' : 'Edit Template'}
      onClose={onClose}
      onSubmit={onSubmit}
      widthClass="sm:max-w-3xl"
      icon={Bot}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
      dismissible={!busy}
      submitLabel={mode === 'create' ? 'Create Template' : 'Save Template'}
      submittingLabel={mode === 'create' ? 'Creating...' : 'Saving...'}
      submitting={busy}
      submitDisabled={!draft.name.trim() || !draft.tagline.trim() || !draft.charter.trim()}
      errorMessages={errors}
      formClassName="space-y-5"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label htmlFor="organization-template-name" className="block text-sm font-medium text-slate-700">
          Name
          <input
            id="organization-template-name"
            type="text"
            value={draft.name}
            maxLength={255}
            placeholder="Customer Escalation Brief"
            onChange={(event) => updateDraft('name', event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            disabled={busy}
          />
        </label>
        <label htmlFor="organization-template-tagline" className="block text-sm font-medium text-slate-700">
          Short Description
          <input
            id="organization-template-tagline"
            type="text"
            value={draft.tagline}
            maxLength={255}
            placeholder="Drafts next actions."
            onChange={(event) => updateDraft('tagline', event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            disabled={busy}
          />
        </label>
      </div>
      <label htmlFor="organization-template-charter" className="block text-sm font-medium text-slate-700">
        Instructions
        <textarea
          id="organization-template-charter"
          value={draft.charter}
          placeholder="Monitor priority accounts, summarize recent customer activity, identify stalled escalations, and recommend the next owner and action."
          onChange={(event) => updateDraft('charter', event.target.value)}
          className="mt-1 block min-h-52 w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
          disabled={busy}
        />
      </label>
      {intelligenceConfig ? (
        <div>
          <span className="mb-2 block text-sm font-medium text-slate-700">Intelligence</span>
          <AgentIntelligenceSlider
            currentTier={draft.preferredLlmTier}
            config={intelligenceConfig}
            onTierChange={(tier) => updateDraft('preferredLlmTier', tier)}
            disabled={busy}
          />
        </div>
      ) : null}
    </ModalForm>
  )
}

type OrganizationEmptyStateProps = { organizations: ConsoleContextOption[]; organizationsLoading: boolean; switchingOrganizationId: string | null; switchError: string | null; onSwitchOrganization: (organization: ConsoleContextOption) => void; onCreateOrganization: () => void }

function OrganizationEmptyState({ organizations, organizationsLoading, switchingOrganizationId, switchError, onSwitchOrganization, onCreateOrganization }: OrganizationEmptyStateProps) {
  const hasOrganizations = organizations.length > 0
  const title = hasOrganizations || organizationsLoading ? 'Choose a team workspace' : 'Create your team workspace'
  const description = hasOrganizations
    ? 'You are in your personal workspace. Switch to a team to manage members, templates, setup, and pooled task credits.'
    : 'Shared agents, templates, setup, members, and pooled task credits can live together in one place.'

  return (
    <div className="profile-screen profile-screen--embedded organization-screen organization-screen--empty">
      <header className="profile-screen__header organization-screen__empty-hero">
        <div className="profile-screen__title-icon" aria-hidden="true"><Users className="h-5 w-5" /></div>
        <div className="organization-screen__empty-copy">
          <p className="profile-screen__eyebrow">Teams</p>
          <h1>{title}</h1>
          <p className="profile-screen__muted">
            {organizationsLoading && !hasOrganizations ? 'You are in your personal workspace. Looking for teams you can manage.' : description}
          </p>
        </div>
        <button type="button" className="profile-screen__button profile-screen__button--primary organization-screen__empty-action" onClick={onCreateOrganization}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Create Team
        </button>
      </header>

      <section className="profile-screen__section organization-screen__empty-section">
        {organizationsLoading ? (
          <p className="profile-screen__muted">Loading teams...</p>
        ) : hasOrganizations ? (
          organizations.map((organization) => (
            <div key={organization.id} className="organization-screen__empty-row">
              <h2>{organization.name}</h2>
              <button
                type="button"
                className="profile-screen__button profile-screen__button--secondary"
                onClick={() => onSwitchOrganization(organization)}
                disabled={switchingOrganizationId === organization.id}
              >
                {switchingOrganizationId === organization.id ? 'Opening...' : 'Open Team'}
              </button>
            </div>
          ))
        ) : (
          <p className="profile-screen__muted">Create a team to start sharing agents, templates, setup, members, and pooled task credits.</p>
        )}
        {switchError ? <p className="profile-screen__feedback profile-screen__feedback--error">{switchError}</p> : null}
      </section>
    </div>
  )
}

export function OrganizationScreen() {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['current-organization'] as const, [])
  const { data, error, isLoading } = useQuery({
    queryKey,
    queryFn: ({ signal }) => fetchCurrentOrganization(signal),
    retry: (failureCount, err) => !isNoOrganizationContextError(err) && failureCount < 3,
  })
  const noOrganizationContext = !data && isNoOrganizationContextError(error)
  const templateQueryKey = useMemo(
    () => currentOrganizationTemplatesQueryKey(data?.organization.id),
    [data?.organization.id],
  )
  const refreshOrganizationQueries = useCallback(() => {
    queryClient.setQueryData(queryKey, undefined)
    queryClient.setQueryData(templateQueryKey, undefined)
    void queryClient.invalidateQueries({ queryKey })
    void queryClient.invalidateQueries({ queryKey: templateQueryKey })
  }, [queryClient, queryKey, templateQueryKey])
  const teamContextSwitcher = useConsoleContextSwitcher({
    enabled: noOrganizationContext,
    onSwitched: refreshOrganizationQueries,
  })
  const {
    data: templateData,
    error: templateQueryError,
    isLoading: templatesLoading,
  } = useQuery({
    queryKey: templateQueryKey,
    queryFn: ({ signal }) => fetchCurrentOrganizationTemplates(signal, data?.organization.id),
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
  const [templateEditorMode, setTemplateEditorMode] = useState<'create' | 'edit' | null>(null)
  const [templateEditorId, setTemplateEditorId] = useState<string | null>(null)
  const [templateEditorDraft, setTemplateEditorDraft] = useState<OrganizationTemplateEditorPayload>(
    buildBlankTemplateDraft(),
  )
  const [templateSourceAgentId, setTemplateSourceAgentId] = useState('')
  const [templateErrors, setTemplateErrors] = useState<string[]>([])
  const [templateMessage, setTemplateMessage] = useState<string | null>(null)
  const [templateBusy, setTemplateBusy] = useState(false)
  const [templateLaunchBusyId, setTemplateLaunchBusyId] = useState<string | null>(null)
  const [createOrganizationOpen, setCreateOrganizationOpen] = useState(false)
  const [createOrganizationName, setCreateOrganizationName] = useState('')
  const [createOrganizationErrors, setCreateOrganizationErrors] = useState<string[]>([])
  const [creatingOrganization, setCreatingOrganization] = useState(false)

  const openCreateOrganizationModal = () => {
    setCreateOrganizationName('')
    setCreateOrganizationErrors([])
    setCreateOrganizationOpen(true)
  }

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
        refreshOrganizationQueries()
      }
    }

    window.addEventListener('gobii:console-context-updated', handleContextUpdated)
    return () => {
      window.removeEventListener('gobii:console-context-updated', handleContextUpdated)
    }
  }, [data?.organization.id, refreshOrganizationQueries])

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
      setNameErrors(['Team name is required.'])
      return
    }
    setSavingName(true)
    setNameErrors([])
    setNameMessage(null)
    try {
      const nextData = await updateCurrentOrganizationName(nextName)
      updateCachedData(nextData)
      setNameMessage('Team updated.')
    } catch (err) {
      setNameErrors(formatErrors(err, 'Unable to update team.'))
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
      const nextData = await deactivateOrganizationTemplate(action.template.id, data?.organization.id)
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
      const nextData = await createOrganizationTemplate(templateSourceAgentId, data?.organization.id)
      updateCachedTemplateData(nextData)
      setCreateTemplateOpen(false)
      setTemplateMessage(nextData.created ? 'Template created.' : 'Template already exists for that agent.')
    } catch (err) {
      setTemplateErrors(formatErrors(err, 'Unable to create template.'))
    } finally {
      setTemplateBusy(false)
    }
  }

  const openNewTemplateEditor = () => {
    setTemplateErrors([])
    setTemplateMessage(null)
    setTemplateEditorId(null)
    setTemplateEditorDraft(buildBlankTemplateDraft(templateData?.llmIntelligence ?? null))
    setTemplateEditorMode('create')
  }

  const handleEditTemplate = (template: OrganizationTemplate) => {
    setTemplateErrors([])
    setTemplateMessage(null)
    setTemplateEditorId(template.id)
    setTemplateEditorDraft({
      name: template.name,
      tagline: template.tagline,
      charter: template.charter,
      preferredLlmTier: template.preferredLlmTier,
    })
    setTemplateEditorMode('edit')
  }

  const handleTemplateEditorSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const draft = {
      ...templateEditorDraft,
      name: templateEditorDraft.name.trim(),
      tagline: templateEditorDraft.tagline.trim(),
      charter: templateEditorDraft.charter.trim(),
    }
    const errors: string[] = []
    if (!draft.name) {
      errors.push('Name is required.')
    }
    if (!draft.tagline) {
      errors.push('Short description is required.')
    }
    if (!draft.charter) {
      errors.push('Instructions are required.')
    }
    if (errors.length > 0) {
      setTemplateErrors(errors)
      return
    }

    setTemplateBusy(true)
    setTemplateErrors([])
    setTemplateMessage(null)
    try {
      const nextData = templateEditorMode === 'edit' && templateEditorId
        ? await updateOrganizationTemplate(templateEditorId, draft)
        : await createOrganizationTemplate(draft, data?.organization.id)
      updateCachedTemplateData(nextData)
      setTemplateEditorMode(null)
      setTemplateEditorId(null)
      setTemplateEditorDraft(buildBlankTemplateDraft(templateData?.llmIntelligence ?? null))
      setTemplateMessage(templateEditorMode === 'edit' ? 'Template saved.' : 'Template created.')
    } catch (err) {
      setTemplateErrors(formatErrors(err, 'Unable to save template.'))
    } finally {
      setTemplateBusy(false)
    }
  }

  const handleCreateOrganizationSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextName = createOrganizationName.trim()
    if (!nextName) {
      setCreateOrganizationErrors(['Team name is required.'])
      return
    }
    setCreatingOrganization(true)
    setCreateOrganizationErrors([])
    try {
      const created = await createOrganization(nextName)
      publishConsoleContext(created.context)
      setCreateOrganizationOpen(false)
      setCreateOrganizationName('')
      refreshOrganizationQueries()
    } catch (err) {
      setCreateOrganizationErrors(formatErrors(err, 'Unable to create team.'))
    } finally {
      setCreatingOrganization(false)
    }
  }

  const handleLaunchTemplate = async (template: OrganizationTemplate) => {
    setTemplateLaunchBusyId(template.id)
    setTemplateErrors([])
    setTemplateMessage(null)
    try {
      const payload = await launchOrganizationTemplate(template.id, data?.organization.id)
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
          <p className="profile-screen__muted">Loading team...</p>
        </section>
      </div>
    )
  }

  const createOrganizationModal = createOrganizationOpen ? (
    <CreateOrganizationModal
      name={createOrganizationName}
      errors={createOrganizationErrors}
      busy={creatingOrganization}
      onNameChange={setCreateOrganizationName}
      onClose={() => {
        if (!creatingOrganization) {
          setCreateOrganizationOpen(false)
        }
      }}
      onSubmit={handleCreateOrganizationSubmit}
    />
  ) : null

  if (!data && (!error || noOrganizationContext)) {
    return (
      <>
        <OrganizationEmptyState
          organizations={teamContextSwitcher.data?.organizations ?? []}
          organizationsLoading={
            teamContextSwitcher.isLoading
            || (noOrganizationContext && !teamContextSwitcher.data && !teamContextSwitcher.error)
          }
          switchingOrganizationId={
            teamContextSwitcher.isSwitching && teamContextSwitcher.data?.context.type === 'organization'
              ? teamContextSwitcher.data.context.id
              : null
          }
          switchError={teamContextSwitcher.error}
          onSwitchOrganization={(organization) => {
            void teamContextSwitcher.switchContext(organization)
          }}
          onCreateOrganization={openCreateOrganizationModal}
        />
        {createOrganizationModal}
      </>
    )
  }

  if (error || !data) {
    return (
      <SettingsBanner
        variant="embedded"
        title="Team Unavailable"
        subtitle={formatErrors(error, 'Switch to a team context to manage team settings.')[0]}
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
    ? formatErrors(templateQueryError, 'Unable to load team templates.')[0]
    : null

  return (
    <div className="profile-screen profile-screen--embedded organization-screen">
      <header className="profile-screen__header">
        <div className="profile-screen__title-icon" aria-hidden="true">
          <Building2 className="h-5 w-5" />
        </div>
        <div>
          <p className="profile-screen__eyebrow">Team</p>
          <h1>{data.organization.name}</h1>
        </div>
      </header>

      {!canEditOrganization && !canManageMembers ? (
        <SettingsBanner
          variant="embedded"
          title="Read-Only Access"
          subtitle={`Your ${data.viewer.roleLabel} role can view this team, but cannot edit settings or membership.`}
        />
      ) : null}

      <section className="profile-screen__section">
        <div className="profile-screen__section-header">
          <div className="profile-screen__section-icon" aria-hidden="true">
            <Building2 className="h-4 w-4" />
          </div>
          <div>
            <h2>Team Details</h2>
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
        {canEditMemberAgentCreation ? (
          <>
            <div className="organization-screen__setting-group">
              <label className="organization-screen__setting-row">
                <span className="organization-screen__setting-copy">
                  <span className="organization-screen__setting-title">Member Agent Creation</span>
                  <span className="organization-screen__setting-description">Members may create team agents</span>
                </span>
                <span className="organization-screen__setting-toggle">
                  <input
                    type="checkbox"
                    checked={data.organization.membersCanCreateAgents}
                    disabled={savingAgentCreation}
                    onChange={(event) => void handleMemberAgentCreationChange(event.currentTarget.checked)}
                  />
                  <span className="organization-screen__setting-switch" aria-hidden="true" />
                </span>
              </label>
              {agentCreationErrors.map((message) => (
                <em key={message} className="organization-screen__setting-error">{message}</em>
              ))}
            </div>
            <div className="profile-screen__actions">
              {agentCreationMessage ? (
                <p className="profile-screen__feedback profile-screen__feedback--success">{agentCreationMessage}</p>
              ) : null}
            </div>
          </>
        ) : null}
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

      <section className="profile-screen__section">
        <div className="profile-screen__section-header organization-screen__section-header">
          <div className="organization-screen__section-title">
            <div className="profile-screen__section-icon" aria-hidden="true">
              <Bot className="h-4 w-4" />
            </div>
            <div>
              <h2>Templates</h2>
              <p>{templates.length} team template{templates.length === 1 ? '' : 's'}</p>
            </div>
          </div>
          {canManageTemplates ? (
            <div className="organization-screen__member-actions">
              {sourceAgents.length ? (
                <button
                  type="button"
                  className="profile-screen__button profile-screen__button--secondary"
                  onClick={() => {
                    setTemplateErrors([])
                    setTemplateMessage(null)
                    setTemplateSourceAgentId(sourceAgents[0]?.id ?? '')
                    setCreateTemplateOpen(true)
                  }}
                  disabled={templateBusy}
                >
                  <Bot className="h-4 w-4" aria-hidden="true" />
                  Clone Agent
                </button>
              ) : null}
              <button
                type="button"
                className="profile-screen__button profile-screen__button--primary"
                onClick={openNewTemplateEditor}
                disabled={templateBusy}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                New Template
              </button>
            </div>
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
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {templates.map((template) => (
                  <tr key={template.id}>
                    <td>
                      <p className="organization-screen__primary-text">{template.name}</p>
                      <p className="profile-screen__muted">{template.tagline}</p>
                      <p className="profile-screen__muted">Intelligence: {formatTemplateTier(template, templateData?.llmIntelligence ?? null)}</p>
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
                          <>
                            <button
                              type="button"
                              className="profile-screen__button profile-screen__button--secondary"
                              onClick={() => handleEditTemplate(template)}
                            >
                              <Pencil className="h-4 w-4" aria-hidden="true" />
                              Edit
                            </button>
                            <button
                              type="button"
                              className="profile-screen__icon-button profile-screen__icon-button--danger"
                              onClick={() => setConfirmAction({ kind: 'deactivate-template', template })}
                              aria-label={`Deactivate ${template.name}`}
                              title="Deactivate"
                            >
                              <Trash2 className="h-4 w-4" aria-hidden="true" />
                            </button>
                          </>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="profile-screen__muted">No team templates yet.</p>
        )}
      </section>

      <CustomInstructionsSection
        value={data.organization.customInstructions}
        maxChars={data.organization.customInstructionsMaxChars}
        canEdit={canEditCustomInstructions}
        placeholder="Follow the team's tone, policies, and operating preferences."
        successMessage="Custom instructions updated."
        onSave={handleCustomInstructionsSave}
        formatErrorMessages={(err) => formatErrors(err, 'Unable to update custom instructions.')}
      />

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
      {templateEditorMode ? (
        <TemplateEditorModal
          mode={templateEditorMode}
          draft={templateEditorDraft}
          intelligenceConfig={templateData?.llmIntelligence ?? null}
          errors={templateErrors}
          busy={templateBusy}
          onDraftChange={setTemplateEditorDraft}
          onClose={() => {
            if (!templateBusy) {
              setTemplateEditorMode(null)
              setTemplateEditorId(null)
            }
          }}
          onSubmit={handleTemplateEditorSubmit}
        />
      ) : null}
    </div>
  )
}
