import type { FormEvent, ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowUpFromLine,
  Check,
  CheckCircle2,
  CircleHelp,
  Copy,
  Folder,
  Info,
  KeyRound,
  Mail,
  Phone,
  Plus,
  ServerCog,
  ShieldAlert,
  Trash2,
  UserPlus,
  XCircle,
  Zap,
} from 'lucide-react'
import { Switch as AriaSwitch, type SwitchProps } from 'react-aria-components'
import { AsyncActionConfirmDialog } from '../components/common/ActionConfirmDialog'
import { CheckboxField, FormField, SelectInput, TextInput } from '../components/common/FormControls'
import { ModalForm } from '../components/common/ModalForm'
import { AddCollaboratorModal } from '../components/agentSettings/AddCollaboratorModal'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { getSettingsSurfaceClassName } from '../components/common/SettingsSurface'
import { AgentIntelligenceSlider } from '../components/common/AgentIntelligenceSlider'
import { InlineStatusBanner } from '../components/common/InlineStatusBanner'
import { SaveBar } from '../components/common/SaveBar'
import { AddContactModal, EditContactModal } from '../components/agentSettings/AddContactModal'
import { AllowlistContactsTable } from '../components/agentSettings/AllowlistContactsTable'
import { CollaboratorsTable } from '../components/agentSettings/CollaboratorsTable'
import { CollapsibleSettingsSection } from '../components/agentSettings/CollapsibleSettingsSection'
import { DailyCreditLimitControl } from '../components/agentSettings/DailyCreditLimitControl'
import {
  getSettingsActionButtonClassName,
  getSettingsStatusBadgeClassName,
  SettingsActionButton,
} from '../components/agentSettings/SettingsControls'
import {
  getDailyCreditLimitConfig,
  getDailyCreditLimitMetrics,
  setDailyCreditInputValue,
  setDailyCreditSliderValue,
  setDailyCreditTier,
  type DailyCreditLimitValue,
} from '../components/agentSettings/dailyCreditLimit'
import type { AllowlistInput, AllowlistTableRow, CollaboratorTableRow, PendingAllowlistAction, PendingCollaboratorAction } from '../components/agentSettings/contactTypes'
import { useModal } from '../hooks/useModal'
import { HttpError } from '../api/http'
import { safeErrorMessage } from '../api/safeErrorMessage'
import { readStoredConsoleContext } from '../util/consoleContextStorage'
import type { IntelligenceTierKey } from '../types/llmIntelligence'
import type {
  AgentDailyCreditsInfo as DailyCreditsInfo,
  AgentInboundWebhook,
  AgentOrganization,
  AgentSettingsData,
  ContactApprovalMode,
  MiniDescriptionMode,
  AgentSettingsReassignmentInfo as ReassignmentInfo,
  AgentSummary,
  AgentWebhook,
  AllowlistState,
  CollaboratorState,
  DedicatedIpInfo,
  McpServersInfo,
  PeerLinkCandidate,
  PeerLinkEntry,
  PeerLinksInfo,
  PrimaryEndpoint,
} from '../types/agentSettings'

type PendingWebhookAction =
  | { type: 'create'; tempId: string; name: string; url: string }
  | { type: 'update'; id: string; name: string; url: string }
  | { type: 'delete'; id: string }

type PendingInboundWebhookAction =
  | { type: 'create'; tempId: string; name: string; isActive: boolean }
  | { type: 'update'; id: string; name: string; isActive: boolean }
  | { type: 'delete'; id: string }
  | { type: 'rotate_secret'; id: string }

type DisplayWebhook = AgentWebhook & {
  pendingType?: PendingWebhookAction['type']
  temp?: boolean
}

type DisplayInboundWebhook = AgentInboundWebhook & {
  pendingType?: PendingInboundWebhookAction['type']
  temp?: boolean
}

type PendingPeerLinkAction =
  | { type: 'create'; tempId: string; peerAgentId: string; peerAgentName: string; messagesPerWindow: number; windowHours: number }
  | { type: 'update'; id: string; messagesPerWindow: number; windowHours: number; featureFlag: string; isEnabled: boolean }
  | { type: 'delete'; id: string }

type PeerLinkEntryState = PeerLinkEntry & {
  pendingType?: PendingPeerLinkAction['type']
  temp?: boolean
}

type ConfirmActionConfig = {
  title: string
  body: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  onConfirm?: () => Promise<void> | void
}

export type AgentSettingsWorkspaceSavePayload = {
  agentId: string
  agentName: string
  agentAvatarUrl: string | null
  preferredLlmTier: IntelligenceTierKey
  organization: AgentOrganization
}

export type AgentSettingsWorkspaceProps = {
  initialData: AgentSettingsData
  onBack?: () => void
  onSaved?: (payload: AgentSettingsWorkspaceSavePayload) => void
  onDeleted?: () => void
  onOpenSecrets?: () => void
  onOpenEmailSettings?: () => void
  onOpenFiles?: () => void
  onOpenContactRequests?: () => void
  onReassigned?: (payload: {
    context?: { type: string; id: string; name?: string | null }
    redirect?: string | null
    organization?: AgentOrganization
  }) => void
}

type FormState = {
  name: string
  charter: string
  miniDescription: string
  miniDescriptionMode: MiniDescriptionMode
  isActive: boolean
  dailyCreditInput: string
  sliderValue: number
  dedicatedProxyId: string
  preferredTier: IntelligenceTierKey
  contactApprovalMode: ContactApprovalMode
}

function transitionDailyCreditState(
  state: FormState,
  transition: (value: DailyCreditLimitValue) => DailyCreditLimitValue,
): FormState {
  const next = transition({ tier: state.preferredTier, sliderValue: state.sliderValue, input: state.dailyCreditInput })
  return { ...state, preferredTier: next.tier, sliderValue: next.sliderValue, dailyCreditInput: next.input }
}

const CONTACT_APPROVAL_OPTIONS = [
  {
    value: 'require_approval',
    title: 'Ask before adding',
    description: 'Review each new email or SMS contact before the agent can reach them.',
    icon: ShieldAlert,
    badge: null,
  },
  {
    value: 'auto_approve_email',
    title: 'Automatically allow email contacts',
    description: 'Anyone your agent emails is added to its contacts.',
    icon: Mail,
    badge: Zap,
  },
] as const

const generateTempId = () =>
  typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `temp-${Date.now()}-${Math.random().toString(36).slice(2)}`

const normalizeAllowlistAddress = (value: string) => value.trim().toLowerCase()

function buildContextAwareHeaders(headersInit?: HeadersInit): Headers {
  const headers = new Headers(headersInit ?? undefined)
  const context = readStoredConsoleContext()
  if (context) {
    if (!headers.has('X-Gobii-Context-Type')) {
      headers.set('X-Gobii-Context-Type', context.type)
    }
    if (!headers.has('X-Gobii-Context-Id')) {
      headers.set('X-Gobii-Context-Id', context.id)
    }
  }
  return headers
}

function isCreatePendingAction<TAction extends { type: string }>(action: TAction): action is Extract<TAction, PendingCreateAction> {
  return action.type === 'create'
}

type PendingCreateAction = {
  type: 'create'
  tempId: string
}

type PendingIdAction = {
  type: string
  id: string
}

function isPendingRemoval(pendingType?: string): boolean {
  return pendingType === 'remove' || pendingType === 'cancel_invite' || pendingType === 'delete'
}

function buildStagedRows<
  TPendingAction extends PendingCreateAction | PendingIdAction,
  TRow extends { id: string; pendingType?: TPendingAction['type']; temp?: boolean },
>({
  baseRows,
  pendingActions,
  createRow,
  sortRows,
}: {
  baseRows: TRow[]
  pendingActions: TPendingAction[]
  createRow: (action: Extract<TPendingAction, PendingCreateAction>) => TRow
  sortRows: (left: TRow, right: TRow) => number
}): TRow[] {
  const rows = new Map(baseRows.map((row) => [row.id, row] as const))

  for (const action of pendingActions) {
    if (isCreatePendingAction(action)) {
      rows.set(action.tempId, createRow(action))
      continue
    }

    if (!('id' in action)) {
      continue
    }

    const row = rows.get(action.id)
    if (!row) {
      continue
    }

    rows.set(action.id, {
      ...row,
      pendingType: action.type,
    })
  }

  return Array.from(rows.values()).sort(sortRows)
}

function stagePersistedRowActions<
  TPendingAction extends PendingCreateAction | PendingIdAction,
  TRow extends { id: string; temp?: boolean },
>({
  pendingActions,
  rows,
  getPersistedAction,
}: {
  pendingActions: TPendingAction[]
  rows: TRow[]
  getPersistedAction: (row: TRow) => Extract<TPendingAction, PendingIdAction> | null
}): TPendingAction[] {
  const tempIds = new Set(rows.filter((row) => row.temp).map((row) => row.id))
  const persistedActions = rows
    .filter((row) => !row.temp)
    .map(getPersistedAction)
    .filter((action): action is Extract<TPendingAction, PendingIdAction> => action !== null)
  const persistedIds = new Set(persistedActions.map((action) => action.id))

  const next = pendingActions.filter((action) => {
    if (isCreatePendingAction(action)) {
      return !tempIds.has(action.tempId)
    }

    if (!('id' in action)) {
      return true
    }

    return !persistedIds.has(action.id)
  })

  return [...next, ...persistedActions] as TPendingAction[]
}

async function runPendingActionGroup<TAction>({
  actions,
  submitAction,
  clearActions,
  trimProcessedActions,
}: {
  actions: TAction[]
  submitAction: (action: TAction) => Promise<void>
  clearActions: () => void
  trimProcessedActions: (processedCount: number) => void
}) {
  if (!actions.length) {
    return
  }

  let processedCount = 0

  try {
    for (const action of actions) {
      await submitAction(action)
      processedCount += 1
    }

    clearActions()
  } catch (error) {
    if (processedCount > 0) {
      trimProcessedActions(processedCount)
    }
    throw error
  }
}

function buildAllowlistRows(state: AllowlistState, pendingActions: PendingAllowlistAction[]): AllowlistTableRow[] {
  const rows = buildStagedRows({
    baseRows: [
      ...state.entries.map<AllowlistTableRow>((entry) => ({
        id: entry.id,
        kind: 'entry',
        channel: entry.channel,
        address: entry.address,
        allowInbound: entry.allowInbound,
        allowOutbound: entry.allowOutbound,
        smsContactPurpose: entry.smsContactPurpose,
        smsContactPurposeDetails: entry.smsContactPurposeDetails,
        smsContactPermissionAttested: entry.smsContactPermissionAttested,
        smsContactPermissionAttestedAt: entry.smsContactPermissionAttestedAt,
      })),
      ...state.pendingInvites.map<AllowlistTableRow>((invite) => ({
        id: invite.id,
        kind: 'invite',
        channel: invite.channel,
        address: invite.address,
        allowInbound: invite.allowInbound,
        allowOutbound: invite.allowOutbound,
        smsContactPurpose: invite.smsContactPurpose,
        smsContactPurposeDetails: invite.smsContactPurposeDetails,
        smsContactPermissionAttested: invite.smsContactPermissionAttested,
        smsContactPermissionAttestedAt: invite.smsContactPermissionAttestedAt,
      })),
    ],
    pendingActions,
    createRow: (action): AllowlistTableRow => ({
      id: action.tempId,
      kind: 'entry',
      channel: action.channel,
      address: action.address,
      allowInbound: action.allowInbound,
      allowOutbound: action.allowOutbound,
      smsContactPurpose: action.smsContactPurpose,
      smsContactPurposeDetails: action.smsContactPurposeDetails,
      smsContactPermissionAttested: action.smsContactPermissionAttested,
      smsContactPermissionAttestedAt: action.smsContactPermissionAttestedAt,
      temp: true,
      pendingType: 'create',
    }),
    sortRows: (left, right) => {
      const addressCompare = left.address.localeCompare(right.address, undefined, { sensitivity: 'base' })
      if (addressCompare !== 0) {
        return addressCompare
      }
      if (left.kind !== right.kind) {
        return left.kind === 'entry' ? -1 : 1
      }
      return left.id.localeCompare(right.id)
    },
  })
  const updates = new Map(
    pendingActions
      .filter((action): action is Extract<PendingAllowlistAction, { type: 'update' }> => action.type === 'update')
      .map((action) => [action.id, action] as const),
  )
  return rows.map((row) => {
    const update = updates.get(row.id)
    return update
      ? { ...row, allowInbound: update.allowInbound, allowOutbound: update.allowOutbound }
      : row
  })
}

function buildCollaboratorRows(state: CollaboratorState, pendingActions: PendingCollaboratorAction[]): CollaboratorTableRow[] {
  return buildStagedRows({
    baseRows: [
      ...state.entries.map<CollaboratorTableRow>((entry) => ({
        id: entry.id,
        kind: 'active',
        email: entry.email,
        name: entry.name,
      })),
      ...state.pendingInvites.map<CollaboratorTableRow>((invite) => ({
        id: invite.id,
        kind: 'pending',
        email: invite.email,
        name: 'Invite pending',
      })),
    ],
    pendingActions,
    createRow: (action) => ({
      id: action.tempId,
      kind: 'pending',
      email: action.email,
      name: action.name,
      temp: true,
      pendingType: 'create',
    }),
    sortRows: (left, right) => {
      const emailCompare = left.email.localeCompare(right.email, undefined, { sensitivity: 'base' })
      if (emailCompare !== 0) {
        return emailCompare
      }
      if (left.kind !== right.kind) {
        return left.kind === 'active' ? -1 : 1
      }
      return left.id.localeCompare(right.id)
    },
  })
}

const normalizeWebhooks = (hooks: AgentWebhook[]): DisplayWebhook[] => hooks.map((hook) => ({ ...hook }))
const normalizeInboundWebhooks = (hooks: AgentInboundWebhook[]): DisplayInboundWebhook[] => hooks.map((hook) => ({ ...hook }))

function areSetsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) {
    return false
  }
  for (const value of a) {
    if (!b.has(value)) {
      return false
    }
  }
  return true
}

function SettingsSwitch(props: SwitchProps) {
  return (
    <AriaSwitch
      {...props}
      className="group relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
    >
      <span aria-hidden="true" className="h-6 w-11 rounded-full bg-violet-200 transition group-data-[selected]:bg-blue-600" />
      <span aria-hidden="true" className="absolute left-1 top-1 h-4 w-4 rounded-full bg-white shadow transition-transform group-data-[selected]:translate-x-5" />
    </AriaSwitch>
  )
}

export function AgentSettingsWorkspace({
  initialData,
  onBack,
  onSaved,
  onDeleted,
  onOpenSecrets,
  onOpenEmailSettings,
  onOpenFiles,
  onOpenContactRequests,
  onReassigned,
}: AgentSettingsWorkspaceProps) {
  const fallbackSliderMax = initialData.dailyCredits.sliderMax
  const fallbackSliderEmptyValue = initialData.dailyCredits.sliderEmptyValue ?? fallbackSliderMax
  const dailyCreditConfig = useMemo(
    () => getDailyCreditLimitConfig(initialData.dailyCredits, initialData.llmIntelligence, 20),
    [initialData.dailyCredits, initialData.llmIntelligence],
  )

  const initialFormState = useMemo<FormState>(
    () => ({
      name: initialData.agent.name,
      charter: initialData.agent.charter,
      miniDescription: initialData.agent.miniDescription,
      miniDescriptionMode: initialData.agent.miniDescriptionMode,
      isActive: initialData.agent.isActive,
      dailyCreditInput:
        typeof initialData.dailyCredits.limit === 'number' && Number.isFinite(initialData.dailyCredits.limit)
          ? String(Math.round(initialData.dailyCredits.limit))
          : '',
      sliderValue: initialData.dailyCredits.sliderValue ?? fallbackSliderEmptyValue,
      dedicatedProxyId: initialData.dedicatedIps.selectedId ?? '',
      preferredTier: (initialData.agent.preferredLlmTier || 'standard') as IntelligenceTierKey,
      contactApprovalMode: initialData.agent.contactApprovalMode,
    }),
    [
      initialData.agent.name,
      initialData.agent.charter,
      initialData.agent.miniDescription,
      initialData.agent.miniDescriptionMode,
      initialData.agent.isActive,
      initialData.agent.preferredLlmTier,
      initialData.agent.contactApprovalMode,
      initialData.dailyCredits.limit,
      initialData.dailyCredits.sliderValue,
      initialData.dedicatedIps.selectedId,
      fallbackSliderEmptyValue,
    ],
  )

  const [savedFormState, setSavedFormState] = useState<FormState>(initialFormState)
  const [formState, setFormState] = useState<FormState>(initialFormState)
  const [savedAvatarUrl, setSavedAvatarUrl] = useState<string | null>(initialData.agent.avatarUrl ?? null)
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(initialData.agent.avatarUrl ?? null)
  const avatarPreviewObjectUrlRef = useRef<string | null>(null)
  const [avatarFile, setAvatarFile] = useState<File | null>(null)
  const [removeAvatar, setRemoveAvatar] = useState(false)
  const avatarInputRef = useRef<HTMLInputElement | null>(null)
  const generalFormRef = useRef<HTMLFormElement | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveNotice, setSaveNotice] = useState<string | null>(null)
  const [savedWebhooks, setSavedWebhooks] = useState<AgentWebhook[]>(initialData.webhooks)
  const [webhooksState, setWebhooksState] = useState<DisplayWebhook[]>(() => normalizeWebhooks(initialData.webhooks))
  const [pendingWebhookActions, setPendingWebhookActions] = useState<PendingWebhookAction[]>([])
  const [savedInboundWebhooks, setSavedInboundWebhooks] = useState<AgentInboundWebhook[]>(initialData.inboundWebhooks)
  const [inboundWebhooksState, setInboundWebhooksState] = useState<DisplayInboundWebhook[]>(() => normalizeInboundWebhooks(initialData.inboundWebhooks))
  const [pendingInboundWebhookActions, setPendingInboundWebhookActions] = useState<PendingInboundWebhookAction[]>([])
  const [copiedInboundWebhookId, setCopiedInboundWebhookId] = useState<string | null>(null)
  const inboundWebhookCopyResetTimeoutRef = useRef<number | null>(null)
  const initialOrgServerSet = useMemo(() => {
    return new Set(initialData.mcpServers.organization.filter((server) => server.assigned).map((server) => server.id))
  }, [initialData.mcpServers.organization])
  const initialPersonalServerSet = useMemo(() => {
    return new Set(initialData.mcpServers.personal.filter((server) => server.assigned).map((server) => server.id))
  }, [initialData.mcpServers.personal])
  const [savedOrgServers, setSavedOrgServers] = useState<Set<string>>(() => new Set(initialOrgServerSet))
  const [savedPersonalServers, setSavedPersonalServers] = useState<Set<string>>(() => new Set(initialPersonalServerSet))
  const [selectedOrgServers, setSelectedOrgServers] = useState<Set<string>>(() => new Set(initialOrgServerSet))
  const [selectedPersonalServers, setSelectedPersonalServers] = useState<Set<string>>(() => new Set(initialPersonalServerSet))
  const [savedPeerLinks, setSavedPeerLinks] = useState(initialData.peerLinks)
  const [peerLinksState, setPeerLinksState] = useState<PeerLinkEntryState[]>(initialData.peerLinks.entries)
  const [peerLinkCandidates, setPeerLinkCandidates] = useState(initialData.peerLinks.candidates)
  const [peerLinkDefaults, setPeerLinkDefaults] = useState(initialData.peerLinks.defaults)
  const [pendingPeerActions, setPendingPeerActions] = useState<PendingPeerLinkAction[]>([])
  const [savedAllowlistState, setSavedAllowlistState] = useState(initialData.allowlist)
  const [pendingAllowlistActions, setPendingAllowlistActions] = useState<PendingAllowlistAction[]>([])
  const [savedCollaboratorState, setSavedCollaboratorState] = useState(initialData.collaborators)
  const [pendingCollaboratorActions, setPendingCollaboratorActions] = useState<PendingCollaboratorAction[]>([])
  const [collaboratorError, setCollaboratorError] = useState<string | null>(null)
  const [selectedOrgId, setSelectedOrgId] = useState(initialData.reassignment.assignedOrg?.id ?? '')
  const [reassignError, setReassignError] = useState<string | null>(null)
  const [reassigning, setReassigning] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [modal, showModal] = useModal()
  const dailyCreditValue = useMemo(() => ({
    tier: formState.preferredTier,
    sliderValue: formState.sliderValue,
    input: formState.dailyCreditInput,
  }), [formState.dailyCreditInput, formState.preferredTier, formState.sliderValue])
  const dailyCreditMetrics = useMemo(
    () => getDailyCreditLimitMetrics(dailyCreditConfig, formState.preferredTier),
    [dailyCreditConfig, formState.preferredTier],
  )
  const sliderEmptyValue = dailyCreditMetrics.emptyValue

  const clearAvatarPreviewUrl = useCallback(() => {
    if (avatarPreviewObjectUrlRef.current) {
      URL.revokeObjectURL(avatarPreviewObjectUrlRef.current)
      avatarPreviewObjectUrlRef.current = null
    }
  }, [])

  const generalHasChanges = useMemo(() => {
    return (
      formState.name !== savedFormState.name ||
      formState.charter !== savedFormState.charter ||
      formState.miniDescription !== savedFormState.miniDescription ||
      formState.miniDescriptionMode !== savedFormState.miniDescriptionMode ||
      formState.isActive !== savedFormState.isActive ||
      formState.dailyCreditInput !== savedFormState.dailyCreditInput ||
      formState.sliderValue !== savedFormState.sliderValue ||
      formState.dedicatedProxyId !== savedFormState.dedicatedProxyId ||
      formState.preferredTier !== savedFormState.preferredTier ||
      formState.contactApprovalMode !== savedFormState.contactApprovalMode ||
      avatarFile !== null ||
      (removeAvatar && Boolean(savedAvatarUrl))
    )
  }, [avatarFile, formState, removeAvatar, savedAvatarUrl, savedFormState])

  useEffect(() => {
    setSavedFormState(initialFormState)
    setFormState(initialFormState)
  }, [initialFormState])

  useEffect(() => {
    clearAvatarPreviewUrl()
    setSavedAvatarUrl(initialData.agent.avatarUrl ?? null)
    setAvatarPreviewUrl(initialData.agent.avatarUrl ?? null)
    setAvatarFile(null)
    setRemoveAvatar(false)
    if (avatarInputRef.current) {
      avatarInputRef.current.value = ''
    }
  }, [avatarInputRef, clearAvatarPreviewUrl, initialData.agent.avatarUrl])

  useEffect(() => {
    setSavedWebhooks(initialData.webhooks)
    setWebhooksState(normalizeWebhooks(initialData.webhooks))
    setPendingWebhookActions([])
  }, [initialData.webhooks])

  useEffect(() => {
    setSavedInboundWebhooks(initialData.inboundWebhooks)
    setInboundWebhooksState(normalizeInboundWebhooks(initialData.inboundWebhooks))
    setPendingInboundWebhookActions([])
  }, [initialData.inboundWebhooks])

  useEffect(() => {
    return () => {
      if (inboundWebhookCopyResetTimeoutRef.current !== null) {
        window.clearTimeout(inboundWebhookCopyResetTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    setSavedOrgServers(new Set(initialOrgServerSet))
    setSelectedOrgServers(new Set(initialOrgServerSet))
  }, [initialOrgServerSet])

  useEffect(() => {
    setSavedPersonalServers(new Set(initialPersonalServerSet))
    setSelectedPersonalServers(new Set(initialPersonalServerSet))
  }, [initialPersonalServerSet])

  useEffect(() => {
    setSavedPeerLinks(initialData.peerLinks)
    setPeerLinksState(initialData.peerLinks.entries)
    setPeerLinkCandidates(initialData.peerLinks.candidates)
    setPeerLinkDefaults(initialData.peerLinks.defaults)
    setPendingPeerActions([])
  }, [initialData.peerLinks])

  useEffect(() => {
    setSavedAllowlistState(initialData.allowlist)
    setPendingAllowlistActions([])
  }, [initialData.allowlist])

  useEffect(() => {
    setSavedCollaboratorState(initialData.collaborators)
    setPendingCollaboratorActions([])
  }, [initialData.collaborators])

  const mcpHasChanges = useMemo(
    () =>
      !areSetsEqual(selectedPersonalServers, savedPersonalServers) ||
      !areSetsEqual(selectedOrgServers, savedOrgServers),
    [selectedPersonalServers, savedPersonalServers, selectedOrgServers, savedOrgServers],
  )

const togglePersonalServer = useCallback((serverId: string) => {
  setSelectedPersonalServers((prev) => {
    const next = new Set(prev)
    if (next.has(serverId)) {
      next.delete(serverId)
    } else {
      next.add(serverId)
    }
    return next
  })
}, [])

const toggleOrganizationServer = useCallback((serverId: string) => {
  setSelectedOrgServers((prev) => {
    const next = new Set(prev)
    if (next.has(serverId)) {
      next.delete(serverId)
    } else {
      next.add(serverId)
    }
    return next
  })
}, [])

  const handleAvatarChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      if (!file) {
        return
      }
      clearAvatarPreviewUrl()
      setAvatarFile(file)
      setRemoveAvatar(false)
      const objectUrl = URL.createObjectURL(file)
      avatarPreviewObjectUrlRef.current = objectUrl
      setAvatarPreviewUrl(objectUrl)
    },
    [clearAvatarPreviewUrl],
  )

  const handleAvatarRemove = useCallback(() => {
    clearAvatarPreviewUrl()
    setAvatarFile(null)
    setRemoveAvatar(true)
    setAvatarPreviewUrl(null)
    if (avatarInputRef.current) {
      avatarInputRef.current.value = ''
    }
  }, [avatarInputRef, clearAvatarPreviewUrl])

  const resetAvatarState = useCallback(() => {
    clearAvatarPreviewUrl()
    setAvatarFile(null)
    setRemoveAvatar(false)
    setAvatarPreviewUrl(savedAvatarUrl)
    if (avatarInputRef.current) {
      avatarInputRef.current.value = ''
    }
  }, [avatarInputRef, clearAvatarPreviewUrl, savedAvatarUrl])

  useEffect(() => {
    return () => {
      if (avatarPreviewObjectUrlRef.current) {
        URL.revokeObjectURL(avatarPreviewObjectUrlRef.current)
        avatarPreviewObjectUrlRef.current = null
      }
    }
  }, [])

  const submitFormData = useCallback(
    async (formData: FormData) => {
      if (!formData.has('csrfmiddlewaretoken')) {
        formData.append('csrfmiddlewaretoken', initialData.csrfToken)
      }
      const response = await fetch(initialData.urls.detail, {
        method: 'POST',
        headers: buildContextAwareHeaders({ 'X-Requested-With': 'XMLHttpRequest' }),
        credentials: 'same-origin',
        body: formData,
      })
      let data: any = null
      try {
        data = await response.json()
      } catch (error) {
        data = null
      }
      if (!response.ok || !data?.success) {
        throw new Error(data?.error || 'Update failed. Please try again.')
      }
      return data
    },
    [initialData.csrfToken, initialData.urls.detail],
  )

  const handleWebhookDraft = useCallback(
    ({ id, name, url }: { id?: string; name: string; url: string }) => {
      if (id) {
        setWebhooksState((prev) => prev.map((hook) => (hook.id === id ? { ...hook, name, url, pendingType: 'update' } : hook)))
        setPendingWebhookActions((prev) => {
          const next = prev.filter((action) => !(action.type === 'update' && action.id === id))
          return [...next, { type: 'update', id, name, url }]
        })
        return
      }
      const tempId = generateTempId()
      setWebhooksState((prev) => [...prev, { id: tempId, name, url, temp: true, pendingType: 'create' }])
      setPendingWebhookActions((prev) => [...prev, { type: 'create', tempId, name, url }])
    },
    [],
  )

  const stageWebhookDelete = useCallback((hook: DisplayWebhook) => {
    if (hook.temp) {
      setWebhooksState((prev) => prev.filter((entry) => entry.id !== hook.id))
      setPendingWebhookActions((prev) => prev.filter((action) => !(action.type === 'create' && action.tempId === hook.id)))
      return
    }
    setWebhooksState((prev) => prev.map((entry) => (entry.id === hook.id ? { ...entry, pendingType: 'delete' } : entry)))
    setPendingWebhookActions((prev) => {
      const next = prev.filter((action) => !(action.type === 'delete' && action.id === hook.id) && !(action.type === 'update' && action.id === hook.id))
      return [...next, { type: 'delete', id: hook.id }]
    })
  }, [])

  const handleInboundWebhookDraft = useCallback(
    ({ id, name, isActive }: { id?: string; name: string; isActive: boolean }) => {
      if (id) {
        setInboundWebhooksState((prev) =>
          prev.map((hook) => (hook.id === id ? { ...hook, name, isActive, pendingType: 'update' } : hook)),
        )
        setPendingInboundWebhookActions((prev) => {
          const next = prev.filter((action) => !(action.type === 'update' && action.id === id))
          return [...next, { type: 'update', id, name, isActive }]
        })
        return
      }
      const tempId = generateTempId()
      setInboundWebhooksState((prev) => [
        ...prev,
        {
          id: tempId,
          name,
          url: '',
          isActive,
          lastTriggeredAt: null,
          temp: true,
          pendingType: 'create',
        },
      ])
      setPendingInboundWebhookActions((prev) => [...prev, { type: 'create', tempId, name, isActive }])
    },
    [],
  )

  const stageInboundWebhookDelete = useCallback((hook: DisplayInboundWebhook) => {
    if (hook.temp) {
      setInboundWebhooksState((prev) => prev.filter((entry) => entry.id !== hook.id))
      setPendingInboundWebhookActions((prev) => prev.filter((action) => !(action.type === 'create' && action.tempId === hook.id)))
      return
    }
    setInboundWebhooksState((prev) => prev.map((entry) => (entry.id === hook.id ? { ...entry, pendingType: 'delete' } : entry)))
    setPendingInboundWebhookActions((prev) => {
      const next = prev.filter(
        (action) =>
          !((action.type === 'delete' || action.type === 'update' || action.type === 'rotate_secret') && action.id === hook.id),
      )
      return [...next, { type: 'delete', id: hook.id }]
    })
  }, [])

  const stageInboundWebhookRotateSecret = useCallback((hook: DisplayInboundWebhook) => {
    if (hook.temp) {
      return
    }
    setInboundWebhooksState((prev) => prev.map((entry) => (entry.id === hook.id ? { ...entry, pendingType: 'rotate_secret' } : entry)))
    setPendingInboundWebhookActions((prev) => {
      const next = prev.filter((action) => !(action.type === 'rotate_secret' && action.id === hook.id))
      return [...next, { type: 'rotate_secret', id: hook.id }]
    })
  }, [])

  const copyInboundWebhookUrl = useCallback(async (hook: DisplayInboundWebhook) => {
    if (!hook.url || typeof navigator === 'undefined' || !navigator.clipboard) {
      return
    }
    try {
      await navigator.clipboard.writeText(hook.url)
      setCopiedInboundWebhookId(hook.id)
      if (inboundWebhookCopyResetTimeoutRef.current !== null) {
        window.clearTimeout(inboundWebhookCopyResetTimeoutRef.current)
      }
      inboundWebhookCopyResetTimeoutRef.current = window.setTimeout(() => {
        setCopiedInboundWebhookId(null)
      }, 1600)
    } catch (error) {
      console.error('Copy failed', error)
    }
  }, [])

  const stagePeerLinkCreate = useCallback(
    (payload: { peerAgentId: string; messagesPerWindow: number; windowHours: number }) => {
      const candidate = peerLinkCandidates.find((entry) => entry.id === payload.peerAgentId)
      if (!candidate) {
        setSaveError('Select a valid agent to link.')
        return
      }
      const tempId = generateTempId()
      setPeerLinksState((prev) => [
        ...prev,
        {
          id: tempId,
          counterpartId: candidate.id,
          counterpartName: candidate.name,
          isEnabled: true,
          messagesPerWindow: payload.messagesPerWindow,
          windowHours: payload.windowHours,
          featureFlag: '',
          createdOnLabel: 'Pending save',
          state: null,
          pendingType: 'create',
          temp: true,
        },
      ])
      setPendingPeerActions((prev) => [
        ...prev,
        {
          type: 'create',
          tempId,
          peerAgentId: candidate.id,
          peerAgentName: candidate.name,
          messagesPerWindow: payload.messagesPerWindow,
          windowHours: payload.windowHours,
        },
      ])
    },
    [peerLinkCandidates],
  )

  const stagePeerLinkUpdate = useCallback(
    (payload: { id: string; messagesPerWindow: number; windowHours: number; featureFlag: string; isEnabled: boolean }) => {
      setPeerLinksState((prev) =>
        prev.map((entry) =>
          entry.id === payload.id
            ? {
                ...entry,
                messagesPerWindow: payload.messagesPerWindow,
                windowHours: payload.windowHours,
                featureFlag: payload.featureFlag,
                isEnabled: payload.isEnabled,
                pendingType: entry.temp ? entry.pendingType : 'update',
              }
            : entry,
        ),
      )
      setPendingPeerActions((prev) => {
        const createIndex = prev.findIndex((action) => action.type === 'create' && action.tempId === payload.id)
        if (createIndex !== -1) {
          const next = [...prev]
          const existing = next[createIndex] as Extract<PendingPeerLinkAction, { type: 'create' }>
          next[createIndex] = {
            ...existing,
            messagesPerWindow: payload.messagesPerWindow,
            windowHours: payload.windowHours,
          }
          return next
        }
        const filtered = prev.filter((action) => !(action.type === 'update' && action.id === payload.id))
        return [
          ...filtered,
          {
            type: 'update',
            id: payload.id,
            messagesPerWindow: payload.messagesPerWindow,
            windowHours: payload.windowHours,
            featureFlag: payload.featureFlag,
            isEnabled: payload.isEnabled,
          },
        ]
      })
    },
    [],
  )

  const stagePeerLinkDelete = useCallback((entry: PeerLinkEntryState) => {
    if (entry.temp) {
      setPeerLinksState((prev) => prev.filter((item) => item.id !== entry.id))
      setPendingPeerActions((prev) => prev.filter((action) => !(action.type === 'create' && action.tempId === entry.id)))
      return
    }
    setPeerLinksState((prev) => prev.map((item) => (item.id === entry.id ? { ...item, pendingType: 'delete' } : item)))
    setPendingPeerActions((prev) => {
      const next = prev.filter(
        (action) => !(action.type === 'delete' && action.id === entry.id) && !(action.type === 'update' && action.id === entry.id),
      )
      return [...next, { type: 'delete', id: entry.id }]
    })
  }, [])

  const allowlistRows = useMemo(
    () => buildAllowlistRows(savedAllowlistState, pendingAllowlistActions),
    [pendingAllowlistActions, savedAllowlistState],
  )
  const collaboratorRows = useMemo(
    () => buildCollaboratorRows(savedCollaboratorState, pendingCollaboratorActions),
    [pendingCollaboratorActions, savedCollaboratorState],
  )
  const projectedAllowlistEntryCount = useMemo(
    () => allowlistRows.filter((row) => row.kind === 'entry' && row.pendingType !== 'remove').length,
    [allowlistRows],
  )
  const projectedAllowlistInviteCount = useMemo(
    () => allowlistRows.filter((row) => row.kind === 'invite' && row.pendingType !== 'cancel_invite').length,
    [allowlistRows],
  )
  const projectedCollaboratorActiveCount = useMemo(
    () => collaboratorRows.filter((row) => row.kind === 'active' && row.pendingType !== 'remove').length,
    [collaboratorRows],
  )
  const projectedCollaboratorPendingCount = useMemo(
    () => collaboratorRows.filter((row) => row.kind === 'pending' && row.pendingType !== 'cancel_invite').length,
    [collaboratorRows],
  )
  const projectedCollaboratorTotalCount = projectedCollaboratorActiveCount + projectedCollaboratorPendingCount
  const projectedContactSlots = useMemo(
    () => projectedCollaboratorActiveCount + projectedCollaboratorPendingCount + projectedAllowlistEntryCount + projectedAllowlistInviteCount,
    [projectedAllowlistEntryCount, projectedAllowlistInviteCount, projectedCollaboratorActiveCount, projectedCollaboratorPendingCount],
  )
  const allowlistDirty = pendingAllowlistActions.length > 0
  const collaboratorDirty = pendingCollaboratorActions.length > 0
  const webhooksDirty = pendingWebhookActions.length > 0
  const inboundWebhooksDirty = pendingInboundWebhookActions.length > 0
  const peerLinksDirty = pendingPeerActions.length > 0
  const hasAnyChanges = generalHasChanges || mcpHasChanges || allowlistDirty || collaboratorDirty || webhooksDirty || inboundWebhooksDirty || peerLinksDirty

  const applyPeerLinkPayload = useCallback((payload: PeerLinksInfo) => {
    setSavedPeerLinks(payload)
    setPeerLinksState(payload.entries)
    setPeerLinkCandidates(payload.candidates)
    setPeerLinkDefaults(payload.defaults)
  }, [])

  const submitAllowlistAction = useCallback(
    async (action: PendingAllowlistAction) => {
      const formData = new FormData()
      formData.append(
        'action',
        action.type === 'cancel_invite'
          ? 'cancel_invite'
          : action.type === 'remove'
            ? 'remove_allowlist'
            : action.type === 'update'
              ? 'update_allowlist'
              : 'add_allowlist',
      )
      if (action.type === 'create') {
        formData.append('channel', action.channel)
        formData.append('address', action.address)
        formData.append('allow_inbound', String(action.allowInbound))
        formData.append('allow_outbound', String(action.allowOutbound))
        if (action.smsContactPurpose) {
          formData.append('sms_contact_purpose', action.smsContactPurpose)
        }
        if (action.smsContactPurposeDetails) {
          formData.append('sms_contact_purpose_details', action.smsContactPurposeDetails)
        }
        if (action.smsContactPermissionAttested != null) {
          formData.append('sms_contact_permission_attested', String(action.smsContactPermissionAttested))
        }
      } else if (action.type === 'update') {
        formData.append('entry_id', action.id)
        formData.append('allow_inbound', String(action.allowInbound))
        formData.append('allow_outbound', String(action.allowOutbound))
      } else if (action.type === 'remove') {
        formData.append('entry_id', action.id)
      } else {
        formData.append('invite_id', action.id)
      }

      const data = await submitFormData(formData)
      if (data?.allowlist) {
        applyAllowlistPayload(data.allowlist as Partial<AllowlistState>)
      }
      if (data?.collaborators) {
        applyCollaboratorPatch(data.collaborators as Partial<CollaboratorState>)
      }
    },
    [applyAllowlistPayload, applyCollaboratorPatch, submitFormData],
  )

  const submitCollaboratorAction = useCallback(
    async (action: PendingCollaboratorAction) => {
      const formData = new FormData()
      formData.append(
        'action',
        action.type === 'cancel_invite'
          ? 'cancel_collaborator_invite'
          : action.type === 'remove'
            ? 'remove_collaborator'
            : 'add_collaborator',
      )
      if (action.type === 'create') {
        formData.append('email', action.email)
      } else if (action.type === 'remove') {
        formData.append('collaborator_id', action.id)
      } else {
        formData.append('invite_id', action.id)
      }

      const data = await submitFormData(formData)
      if (data?.collaborators) {
        applyCollaboratorPatch(data.collaborators as Partial<CollaboratorState>)
      }
      if (data?.allowlist) {
        applyAllowlistPayload(data.allowlist as Partial<AllowlistState>)
      }
    },
    [applyAllowlistPayload, applyCollaboratorPatch, submitFormData],
  )

  const submitWebhookAction = useCallback(
    async (action: PendingWebhookAction) => {
      const formData = new FormData()
      if (action.type === 'create') {
        formData.append('webhook_action', 'create')
        formData.append('webhook_name', action.name)
        formData.append('webhook_url', action.url)
      } else if (action.type === 'update') {
        formData.append('webhook_action', 'update')
        formData.append('webhook_id', action.id)
        formData.append('webhook_name', action.name)
        formData.append('webhook_url', action.url)
      } else {
        formData.append('webhook_action', 'delete')
        formData.append('webhook_id', action.id)
      }

      const data = await submitFormData(formData)
      if (data?.webhooks) {
        const normalized = normalizeWebhooks(data.webhooks as AgentWebhook[])
        setSavedWebhooks(data.webhooks as AgentWebhook[])
        setWebhooksState(normalized)
      }
    },
    [submitFormData],
  )

  const submitInboundWebhookAction = useCallback(
    async (action: PendingInboundWebhookAction) => {
      const formData = new FormData()
      if (action.type === 'create') {
        formData.append('inbound_webhook_action', 'create')
        formData.append('inbound_webhook_name', action.name)
        formData.append('inbound_webhook_is_active', String(action.isActive))
      } else if (action.type === 'update') {
        formData.append('inbound_webhook_action', 'update')
        formData.append('inbound_webhook_id', action.id)
        formData.append('inbound_webhook_name', action.name)
        formData.append('inbound_webhook_is_active', String(action.isActive))
      } else if (action.type === 'rotate_secret') {
        formData.append('inbound_webhook_action', 'rotate_secret')
        formData.append('inbound_webhook_id', action.id)
      } else {
        formData.append('inbound_webhook_action', 'delete')
        formData.append('inbound_webhook_id', action.id)
      }

      const data = await submitFormData(formData)
      if (data?.inboundWebhooks) {
        const nextHooks = data.inboundWebhooks as AgentInboundWebhook[]
        setSavedInboundWebhooks(nextHooks)
        setInboundWebhooksState(normalizeInboundWebhooks(nextHooks))
      }
    },
    [submitFormData],
  )

  const submitPeerAction = useCallback(
    async (action: PendingPeerLinkAction) => {
      const formData = new FormData()
      if (action.type === 'create') {
        formData.append('peer_link_action', 'create')
        formData.append('peer_agent_id', action.peerAgentId)
        formData.append('messages_per_window', String(action.messagesPerWindow))
        formData.append('window_hours', String(action.windowHours))
      } else if (action.type === 'update') {
        formData.append('peer_link_action', 'update')
        formData.append('link_id', action.id)
        formData.append('messages_per_window', String(action.messagesPerWindow))
        formData.append('window_hours', String(action.windowHours))
        formData.append('feature_flag', action.featureFlag)
        if (action.isEnabled) {
          formData.append('is_enabled', 'on')
        }
      } else {
        formData.append('peer_link_action', 'delete')
        formData.append('link_id', action.id)
      }

      const data = await submitFormData(formData)
      if (data?.peerLinks) {
        applyPeerLinkPayload(data.peerLinks as PeerLinksInfo)
      }
    },
    [applyPeerLinkPayload, submitFormData],
  )

  const submitTransferForm = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      setSaving(true)
      setSaveError(null)
      setSaveNotice(null)
      try {
        await submitFormData(new FormData(event.currentTarget))
        window.location.reload()
      } catch (error) {
        setSaveError(error instanceof Error ? error.message : 'Failed to update transfer invitation. Please try again.')
      } finally {
        setSaving(false)
      }
    },
    [submitFormData],
  )

  const resetForm = useCallback(() => {
    setFormState(savedFormState)
  }, [savedFormState])

  const handleResetAll = useCallback(() => {
    resetForm()
    setSelectedOrgServers(new Set(savedOrgServers))
    setSelectedPersonalServers(new Set(savedPersonalServers))
    setPendingAllowlistActions([])
    setPendingCollaboratorActions([])
    setPendingWebhookActions([])
    setWebhooksState(normalizeWebhooks(savedWebhooks))
    setPendingInboundWebhookActions([])
    setInboundWebhooksState(normalizeInboundWebhooks(savedInboundWebhooks))
    setPendingPeerActions([])
    setPeerLinksState(savedPeerLinks.entries)
    setPeerLinkCandidates(savedPeerLinks.candidates)
    setPeerLinkDefaults(savedPeerLinks.defaults)
    setCollaboratorError(null)
    setSaveError(null)
    setSaveNotice(null)
    resetAvatarState()
  }, [resetAvatarState, resetForm, savedInboundWebhooks, savedOrgServers, savedPeerLinks, savedPersonalServers, savedWebhooks])

  const handleSaveAll = useCallback(async () => {
    if (!hasAnyChanges) {
      return
    }
    setSaving(true)
    setSaveError(null)
    setSaveNotice(null)
    try {
      let nextSavedAvatarUrl = savedAvatarUrl
      let nextSavedFormState = formState
      if (generalHasChanges && generalFormRef.current) {
        const data = await submitFormData(new FormData(generalFormRef.current))
        const warning = typeof data?.warning === 'string' && data.warning.trim() ? String(data.warning) : null
        const serverTierRaw =
          typeof data?.preferredLlmTier === 'string' && data.preferredLlmTier.trim() ? String(data.preferredLlmTier) : null

        const nextFormState: FormState = { ...formState }
        if (typeof data?.miniDescription === 'string') {
          nextFormState.miniDescription = data.miniDescription
        }
        if (data?.miniDescriptionMode === 'auto' || data?.miniDescriptionMode === 'manual') {
          nextFormState.miniDescriptionMode = data.miniDescriptionMode
        }
        if (data?.contactApprovalMode === 'require_approval' || data?.contactApprovalMode === 'auto_approve_email') {
          nextFormState.contactApprovalMode = data.contactApprovalMode
        }
        if (serverTierRaw && (initialData.llmIntelligence?.options ?? []).some((option) => option.key === serverTierRaw)) {
          const serverTier = serverTierRaw as IntelligenceTierKey
          if (serverTier !== nextFormState.preferredTier) {
            const wasUnlimited = nextFormState.sliderValue === sliderEmptyValue
            const { max: nextMax, emptyValue: nextEmptyValue } = getDailyCreditLimitMetrics(dailyCreditConfig, serverTier)
            nextFormState.preferredTier = serverTier
            nextFormState.sliderValue = wasUnlimited ? nextEmptyValue : Math.min(nextFormState.sliderValue, nextMax)
          }
        }

        setFormState(nextFormState)
        setSavedFormState(nextFormState)
        nextSavedFormState = nextFormState
        if (warning) {
          setSaveNotice(warning)
        }
        const nextAvatar = (data?.avatarUrl as string | null | undefined) ?? savedAvatarUrl
        clearAvatarPreviewUrl()
        setSavedAvatarUrl(nextAvatar ?? null)
        setAvatarPreviewUrl(nextAvatar ?? null)
        nextSavedAvatarUrl = nextAvatar ?? null
        setAvatarFile(null)
        setRemoveAvatar(false)
        if (avatarInputRef.current) {
          avatarInputRef.current.value = ''
        }
      }

      if (mcpHasChanges) {
        if (initialData.agent.organization) {
          const formData = new FormData()
          formData.append('mcp_server_action', 'update_org')
          selectedOrgServers.forEach((id) => formData.append('org_servers', id))
          await submitFormData(formData)
          setSavedOrgServers(new Set(selectedOrgServers))
          setSavedPersonalServers(new Set(selectedPersonalServers))
        } else {
          const formData = new FormData()
          formData.append('mcp_server_action', 'update_personal')
          selectedPersonalServers.forEach((id) => formData.append('personal_servers', id))
          await submitFormData(formData)
          setSavedPersonalServers(new Set(selectedPersonalServers))
        }
      }

      await runPendingActionGroup({
        actions: pendingAllowlistActions,
        submitAction: submitAllowlistAction,
        clearActions: () => setPendingAllowlistActions([]),
        trimProcessedActions: (processedCount) => setPendingAllowlistActions((prev) => prev.slice(processedCount)),
      })

      await runPendingActionGroup({
        actions: pendingCollaboratorActions,
        submitAction: submitCollaboratorAction,
        clearActions: () => setPendingCollaboratorActions([]),
        trimProcessedActions: (processedCount) => setPendingCollaboratorActions((prev) => prev.slice(processedCount)),
      })

      await runPendingActionGroup({
        actions: pendingWebhookActions,
        submitAction: submitWebhookAction,
        clearActions: () => setPendingWebhookActions([]),
        trimProcessedActions: (processedCount) => setPendingWebhookActions((prev) => prev.slice(processedCount)),
      })

      await runPendingActionGroup({
        actions: pendingInboundWebhookActions,
        submitAction: submitInboundWebhookAction,
        clearActions: () => setPendingInboundWebhookActions([]),
        trimProcessedActions: (processedCount) => setPendingInboundWebhookActions((prev) => prev.slice(processedCount)),
      })

      await runPendingActionGroup({
        actions: pendingPeerActions,
        submitAction: submitPeerAction,
        clearActions: () => setPendingPeerActions([]),
        trimProcessedActions: (processedCount) => setPendingPeerActions((prev) => prev.slice(processedCount)),
      })

      setSaveError(null)
      onSaved?.({
        agentId: initialData.agent.id,
        agentName: nextSavedFormState.name,
        agentAvatarUrl: nextSavedAvatarUrl,
        preferredLlmTier: nextSavedFormState.preferredTier,
        organization: initialData.agent.organization,
      })
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Failed to save changes. Please try again.')
    } finally {
      setSaving(false)
    }
  }, [
    applyAllowlistPayload,
    applyCollaboratorPatch,
    applyPeerLinkPayload,
    avatarInputRef,
    clearAvatarPreviewUrl,
    formState,
    generalFormRef,
    generalHasChanges,
    dailyCreditConfig,
    hasAnyChanges,
    initialData.llmIntelligence?.options,
    mcpHasChanges,
    pendingAllowlistActions,
    pendingCollaboratorActions,
    pendingInboundWebhookActions,
    pendingPeerActions,
    pendingWebhookActions,
    savedAvatarUrl,
    selectedOrgServers,
    selectedPersonalServers,
    sliderEmptyValue,
    submitAllowlistAction,
    submitCollaboratorAction,
    submitInboundWebhookAction,
    submitPeerAction,
    submitFormData,
    submitWebhookAction,
    onSaved,
  ])

  const openConfirmAction = useCallback(
    (config: ConfirmActionConfig) => {
      showModal((onClose) => <ConfirmActionDialog {...config} onClose={onClose} />)
    },
    [showModal],
  )

  const updateSliderValue = useCallback(
    (value: number) => {
      setFormState((prev) => transitionDailyCreditState(prev, (current) =>
        setDailyCreditSliderValue(current, value, getDailyCreditLimitMetrics(dailyCreditConfig, current.tier))))
    },
    [dailyCreditConfig],
  )

  const handleTierChange = useCallback(
    (tier: IntelligenceTierKey) => {
      setFormState((prev) => transitionDailyCreditState(prev, (current) =>
        setDailyCreditTier(current, tier, dailyCreditConfig)))
    },
    [dailyCreditConfig],
  )

  const handleDailyCreditInputChange = useCallback(
    (value: string) => {
      setFormState((prev) => transitionDailyCreditState(prev, (current) =>
        setDailyCreditInputValue(current, value, getDailyCreditLimitMetrics(dailyCreditConfig, current.tier))))
    },
    [dailyCreditConfig],
  )

  const formatNumber = useCallback((value: number | null, fractionDigits = 0) => {
    if (value === null || !Number.isFinite(value)) {
      return null
    }
    return value.toLocaleString(undefined, {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    })
  }, [])

  function applyAllowlistPayload(payload?: Partial<AllowlistState>) {
    if (!payload) {
      return
    }
    setSavedAllowlistState((prev) => ({
      ...prev,
      show: typeof payload.show === 'boolean' ? payload.show : prev.show,
      ownerEmail: payload.ownerEmail ?? prev.ownerEmail,
      ownerPhone: payload.ownerPhone ?? prev.ownerPhone,
      entries: payload.entries ?? prev.entries,
      pendingInvites: payload.pendingInvites ?? prev.pendingInvites,
      activeCount: typeof payload.activeCount === 'number' ? payload.activeCount : prev.activeCount,
      maxContacts: payload.maxContacts ?? prev.maxContacts,
      pendingContactRequests:
        typeof payload.pendingContactRequests === 'number' ? payload.pendingContactRequests : prev.pendingContactRequests,
      emailVerified: typeof payload.emailVerified === 'boolean' ? payload.emailVerified : prev.emailVerified,
    }))
  }

  function applyCollaboratorPatch(payload?: Partial<CollaboratorState>) {
    if (!payload) {
      return
    }
    setSavedCollaboratorState((prev) => ({
      ...prev,
      entries: payload.entries ?? prev.entries,
      pendingInvites: payload.pendingInvites ?? prev.pendingInvites,
      activeCount: typeof payload.activeCount === 'number' ? payload.activeCount : prev.activeCount,
      pendingCount: typeof payload.pendingCount === 'number' ? payload.pendingCount : prev.pendingCount,
      totalCount: typeof payload.totalCount === 'number' ? payload.totalCount : prev.totalCount,
      maxContacts: payload.maxContacts ?? prev.maxContacts,
      canManage: typeof payload.canManage === 'boolean' ? payload.canManage : prev.canManage,
    }))
  }

  const stageAllowlistAdd = useCallback(
    async (input: AllowlistInput) => {
      const normalizedAddress = normalizeAllowlistAddress(input.address)
      const hasDuplicate = allowlistRows.some(
        (row) =>
          row.channel === input.channel
          && normalizeAllowlistAddress(row.address) === normalizedAddress
          && !isPendingRemoval(row.pendingType),
      )

      if (hasDuplicate) {
        throw new Error('This address is already listed for this agent.')
      }

      if (typeof savedAllowlistState.maxContacts === 'number' && savedAllowlistState.maxContacts > 0 && projectedContactSlots >= savedAllowlistState.maxContacts) {
        throw new Error(`Contact limit reached. Maximum ${savedAllowlistState.maxContacts} contacts allowed.`)
      }

      const tempId = generateTempId()
      setPendingAllowlistActions((prev) => [
        ...prev,
        {
          type: 'create',
          tempId,
          channel: input.channel,
          address: input.address.trim(),
          allowInbound: input.allowInbound,
          allowOutbound: input.allowOutbound,
          smsContactPurpose: input.smsContactPurpose ?? null,
          smsContactPurposeDetails: input.smsContactPurposeDetails ?? null,
          smsContactPermissionAttested: input.smsContactPermissionAttested ?? null,
          smsContactPermissionAttestedAt: input.smsContactPermissionAttestedAt ?? null,
        },
      ])
    },
    [allowlistRows, projectedContactSlots, savedAllowlistState.maxContacts],
  )

  const stageAllowlistRemoveRows = useCallback((rows: AllowlistTableRow[]) => {
    if (!rows.length) {
      return
    }

    setPendingAllowlistActions((prev) =>
      stagePersistedRowActions({
        pendingActions: prev,
        rows,
        getPersistedAction: (row) => {
          if (row.kind === 'entry' && row.pendingType !== 'remove') {
            return { type: 'remove', id: row.id }
          }
          if (row.kind === 'invite' && row.pendingType !== 'cancel_invite') {
            return { type: 'cancel_invite', id: row.id }
          }
          return null
        },
      }),
    )
  }, [])

  const openAddContactModal = useCallback(() => {
    showModal((onClose) => (
      <AddContactModal
        onSubmit={stageAllowlistAdd}
        onClose={onClose}
      />
    ))
  }, [showModal, stageAllowlistAdd])

  const stageAllowlistUpdate = useCallback(
    async (row: AllowlistTableRow, input: AllowlistInput) => {
      if (row.temp) {
        setPendingAllowlistActions((prev) => prev.map((action) => (
          action.type === 'create' && action.tempId === row.id
            ? { ...action, allowInbound: input.allowInbound, allowOutbound: input.allowOutbound }
            : action
        )))
        return
      }

      const savedEntry = savedAllowlistState.entries.find((entry) => entry.id === row.id)
      setPendingAllowlistActions((prev) => {
        const withoutCurrentUpdate = prev.filter((action) => !(action.type === 'update' && action.id === row.id))
        if (
          savedEntry
          && savedEntry.allowInbound === input.allowInbound
          && savedEntry.allowOutbound === input.allowOutbound
        ) {
          return withoutCurrentUpdate
        }
        return [
          ...withoutCurrentUpdate,
          {
            type: 'update',
            id: row.id,
            allowInbound: input.allowInbound,
            allowOutbound: input.allowOutbound,
          },
        ]
      })
    },
    [savedAllowlistState.entries],
  )

  const openEditContactModal = useCallback(
    (row: AllowlistTableRow) => {
      showModal((onClose) => (
        <EditContactModal
          contact={row}
          onSubmit={(input) => stageAllowlistUpdate(row, input)}
          onClose={onClose}
        />
      ))
    },
    [showModal, stageAllowlistUpdate],
  )

  const confirmAllowlistRemoval = useCallback(
    (rows: AllowlistTableRow[]) => {
      if (!rows.length) {
        return
      }

      const removableCount = rows.filter((row) => row.kind === 'entry').length
      const cancellableCount = rows.filter((row) => row.kind === 'invite').length
      const label =
        rows.length === 1
          ? rows[0].kind === 'invite'
            ? 'Cancel invite'
            : 'Remove contact'
          : 'Remove selected'

      let body: ReactNode
      if (rows.length === 1) {
        body =
          rows[0].kind === 'invite'
            ? `Cancel the pending invite for ${rows[0].address}?`
            : `Remove ${rows[0].address} from the allowlist?`
      } else {
        const parts = []
        if (removableCount > 0) {
          parts.push(`${removableCount} contact${removableCount === 1 ? '' : 's'}`)
        }
        if (cancellableCount > 0) {
          parts.push(`${cancellableCount} invite${cancellableCount === 1 ? '' : 's'}`)
        }
        body = `Remove ${parts.join(' and ')} from this agent?`
      }

      openConfirmAction({
        title: label,
        body,
        confirmLabel: label,
        tone: 'danger',
        onConfirm: () => stageAllowlistRemoveRows(rows),
      })
    },
    [openConfirmAction, stageAllowlistRemoveRows],
  )

  const stageCollaboratorAdd = useCallback(
    async (email: string) => {
      const normalizedEmail = email.trim().toLowerCase()
      const hasDuplicate = collaboratorRows.some(
        (row) =>
          row.email.trim().toLowerCase() === normalizedEmail
          && !isPendingRemoval(row.pendingType),
      )

      if (hasDuplicate) {
        throw new Error('This collaborator already has access or a pending invite.')
      }

      if (
        typeof savedCollaboratorState.maxContacts === 'number'
        && savedCollaboratorState.maxContacts > 0
        && projectedContactSlots >= savedCollaboratorState.maxContacts
      ) {
        throw new Error(`Contact limit reached. Maximum ${savedCollaboratorState.maxContacts} contacts allowed.`)
      }

      setCollaboratorError(null)
      const tempId = generateTempId()
      setPendingCollaboratorActions((prev) => [
        ...prev,
        {
          type: 'create',
          tempId,
          email: normalizedEmail,
          name: 'Invite pending',
        },
      ])
    },
    [collaboratorRows, projectedContactSlots, savedCollaboratorState.maxContacts],
  )

  const stageCollaboratorRemove = useCallback((row: CollaboratorTableRow) => {
    setCollaboratorError(null)
    setPendingCollaboratorActions((prev) =>
      stagePersistedRowActions({
        pendingActions: prev,
        rows: [row],
        getPersistedAction: (currentRow) => {
          if (currentRow.kind === 'active' && currentRow.pendingType !== 'remove') {
            return { type: 'remove', id: currentRow.id }
          }
          if (currentRow.kind === 'pending' && currentRow.pendingType !== 'cancel_invite') {
            return { type: 'cancel_invite', id: currentRow.id }
          }
          return null
        },
      }),
    )
  }, [])

  const openAddCollaboratorModal = useCallback(() => {
    showModal((onClose) => (
      <AddCollaboratorModal
        onSubmit={stageCollaboratorAdd}
        onClose={onClose}
      />
    ))
  }, [showModal, stageCollaboratorAdd])

  const handleReassign = useCallback(
    async (targetOrgId: string | null) => {
      setReassigning(true)
      setReassignError(null)
      try {
        const formData = new FormData()
        formData.append('csrfmiddlewaretoken', initialData.csrfToken)
        formData.append('action', 'reassign_org')
        if (targetOrgId) {
          formData.append('target_org_id', targetOrgId)
        }
        const response = await fetch(initialData.urls.detail, {
          method: 'POST',
          headers: buildContextAwareHeaders({ 'X-Requested-With': 'XMLHttpRequest' }),
          credentials: 'same-origin',
          body: formData,
        })
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Reassignment failed. Please try again.')
        }
        onReassigned?.({
          context: data.context as { type: string; id: string; name?: string | null } | undefined,
          redirect: (data.redirect as string | null | undefined) ?? null,
          organization: (data.organization as AgentOrganization | undefined) ?? null,
        })
      } catch (error) {
        setReassignError(error instanceof Error ? error.message : 'An unexpected error occurred.')
      } finally {
        setReassigning(false)
      }
    },
    [initialData.csrfToken, initialData.urls.detail, onReassigned],
  )

  const deleteAgent = useCallback(async () => {
    setDeleteError(null)
    try {
      const response = await fetch(initialData.urls.delete, {
        method: 'DELETE',
        headers: buildContextAwareHeaders({
          'X-CSRFToken': initialData.csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        }),
        credentials: 'same-origin',
      })
      if (!response.ok) {
        const body = await response.text().catch(() => null)
        throw new HttpError(response.status, response.statusText, body)
      }
      onDeleted?.()
    } catch (error) {
      const message = safeErrorMessage(error, 'Failed to delete agent. Please try again.')
      setDeleteError(message)
      throw error
    }
  }, [initialData.csrfToken, initialData.urls.delete, onDeleted])

  const confirmDeleteAgent = useCallback(() => {
    openConfirmAction({
      title: 'Delete agent',
      body: 'Are you sure you want to delete this agent? This action cannot be undone.',
      confirmLabel: 'Delete agent',
      tone: 'danger',
      onConfirm: deleteAgent,
    })
  }, [deleteAgent, openConfirmAction])

  const openWebhookModal = useCallback(
    (mode: 'create' | 'edit', webhook: DisplayWebhook | null = null) => {
      showModal((onClose) => (
        <WebhookModal
          mode={mode}
          webhook={webhook}
          onSubmit={(draft) => {
            handleWebhookDraft(draft)
            onClose()
          }}
          onClose={onClose}
        />
      ))
    },
    [handleWebhookDraft, showModal],
  )

  const openInboundWebhookModal = useCallback(
    (mode: 'create' | 'edit', webhook: DisplayInboundWebhook | null = null) => {
      showModal((onClose) => (
        <InboundWebhookModal
          mode={mode}
          webhook={webhook}
          onSubmit={(draft) => {
            handleInboundWebhookDraft(draft)
            onClose()
          }}
          onClose={onClose}
        />
      ))
    },
    [handleInboundWebhookDraft, showModal],
  )

  const openPeerLinkModal = useCallback(
    (mode: 'create' | 'edit', entry: PeerLinkEntryState | null = null) => {
      showModal((onClose) => (
        <PeerLinkModal
          mode={mode}
          entry={entry}
          candidates={peerLinkCandidates}
          defaults={peerLinkDefaults}
          onSubmit={(values) => {
            if (mode === 'create' && values.peerAgentId) {
              stagePeerLinkCreate({
                peerAgentId: values.peerAgentId,
                messagesPerWindow: values.messagesPerWindow,
                windowHours: values.windowHours,
              })
            } else if (mode === 'edit' && entry) {
              stagePeerLinkUpdate({
                id: entry.id,
                messagesPerWindow: values.messagesPerWindow,
                windowHours: values.windowHours,
                featureFlag: values.featureFlag,
                isEnabled: values.isEnabled,
              })
            }
            onClose()
          }}
          onClose={onClose}
        />
      ))
    },
    [peerLinkCandidates, peerLinkDefaults, showModal, stagePeerLinkCreate, stagePeerLinkUpdate],
  )

  const embeddedHeaderActions = (
    <>
      {onOpenSecrets ? (
        <SettingsActionButton onClick={onOpenSecrets} responsive>
          <KeyRound className="h-4 w-4" aria-hidden="true" />
          Secrets
        </SettingsActionButton>
      ) : (
        <SettingsActionButton as="a" href={initialData.urls.secrets} responsive>
          <KeyRound className="h-4 w-4" aria-hidden="true" />
          Secrets
        </SettingsActionButton>
      )}
      {onOpenEmailSettings ? (
        <SettingsActionButton onClick={onOpenEmailSettings} responsive>
          <Mail className="h-4 w-4" aria-hidden="true" />
          Email Settings
        </SettingsActionButton>
      ) : (
        <SettingsActionButton as="a" href={initialData.urls.emailSettings} responsive>
          <Mail className="h-4 w-4" aria-hidden="true" />
          Email Settings
        </SettingsActionButton>
      )}
      {onOpenFiles ? (
        <SettingsActionButton onClick={onOpenFiles} responsive>
          <Folder className="h-4 w-4" aria-hidden="true" />
          Manage Files
        </SettingsActionButton>
      ) : (
        <SettingsActionButton as="a" href={initialData.urls.manageFiles} responsive>
          <Folder className="h-4 w-4" aria-hidden="true" />
          Manage Files
        </SettingsActionButton>
      )}
    </>
  )

  return (
    <div className="space-y-6 pb-24">
      <SettingsBanner
        variant="embedded"
        leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to gallery" />}
        eyebrow="Agent settings"
        title={(formState.name || 'Agent').trim()}
        headingId="agent-name-heading"
        actions={embeddedHeaderActions}
      />

      {initialData.agent.pendingTransfer && (
        <InlineStatusBanner variant="warning" surface="embedded" icon={Info}>
          <div className="flex items-center gap-2 text-sm font-semibold">
            Transfer pending
          </div>
          <p className="text-sm leading-5">
            This agent is awaiting acceptance from <strong>{initialData.agent.pendingTransfer.toEmail}</strong> (sent {initialData.agent.pendingTransfer.createdAtDisplay}).
            You can continue editing settings, but keep in mind the new owner will take control once they accept.
          </p>
        </InlineStatusBanner>
      )}

      {saveNotice && (
        <InlineStatusBanner variant="warning" surface="embedded" icon={AlertTriangle}>
          <div className="flex items-start justify-between gap-4">
            <div className="text-sm leading-5">{saveNotice}</div>
            <button
              type="button"
              onClick={() => setSaveNotice(null)}
              className="shrink-0 rounded-lg p-1 text-amber-100 transition hover:bg-amber-900/40"
              aria-label="Dismiss notice"
            >
              <XCircle className="w-5 h-5" aria-hidden="true" />
            </button>
          </div>
        </InlineStatusBanner>
      )}

      <form
        method="post"
        action={initialData.urls.detail}
        id="general-settings-form"
        ref={generalFormRef}
      onSubmit={(event) => {
        event.preventDefault()
        handleSaveAll()
      }}
      encType="multipart/form-data"
    >
      <input type="hidden" name="csrfmiddlewaretoken" value={initialData.csrfToken} />
      <input type="hidden" name="clear_avatar" value={removeAvatar ? 'true' : ''} />
      <input
        ref={avatarInputRef}
        type="file"
        name="avatar"
        accept="image/*"
        className="sr-only"
        onChange={handleAvatarChange}
      />
      {initialData.allowlist.show && (
        <input type="hidden" name="whitelist_policy" value={initialData.agent.whitelistPolicy} />
      )}
      <input type="hidden" name="contact_approval_mode" value={formState.contactApprovalMode} />
        <CollapsibleSettingsSection
          id="agent-identity"
          title="General Settings"
          subtitle="Core configuration and runtime controls."
        >
            <div className="grid sm:grid-cols-12 gap-4 sm:gap-6">
              <div className="sm:col-span-3">
                <label htmlFor="agent-name" className="inline-block text-sm font-medium text-gray-800 mt-2.5">
                  Agent Name
                </label>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9">
                <input
                  id="agent-name"
                  name="name"
                  type="text"
                  maxLength={255}
                  value={formState.name}
                  onChange={(event) => setFormState((prev) => ({ ...prev, name: event.target.value }))}
                  className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                />
                <p className="mt-2 text-xs text-gray-500">Choose a memorable name that describes this agent's purpose.</p>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Avatar</span>
              </div>
              <div className="sm:col-span-9">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
                  <div className="relative flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-full border border-gray-200 shadow-sm">
                    {(!removeAvatar && (avatarPreviewUrl || savedAvatarUrl)) ? (
                      <img
                        src={(removeAvatar ? null : avatarPreviewUrl || savedAvatarUrl) ?? undefined}
                        alt={`${formState.name || 'Agent'} avatar`}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <Zap className="h-7 w-7 text-gray-500" aria-hidden="true" />
                    )}
                  </div>
                  <div className="flex flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <SettingsActionButton onClick={() => avatarInputRef.current?.click()}>
                        <ArrowUpFromLine className="h-4 w-4" aria-hidden="true" />
                        Upload
                      </SettingsActionButton>
                      {(avatarPreviewUrl || savedAvatarUrl || avatarFile) && (
                        <SettingsActionButton tone="danger" onClick={handleAvatarRemove}>
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                          Remove
                        </SettingsActionButton>
                      )}
                    </div>
                    <p className="text-xs text-gray-500">Use a square image (PNG, JPG, WebP, or GIF). Max 5 MB.</p>
                  </div>
                </div>
              </div>

              {initialData.llmIntelligence && (
                <>
                  <div className="sm:col-span-3">
                    <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Intelligence</span>
                    <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
                  </div>
                  <div className="sm:col-span-9">
                    <input type="hidden" name="preferred_llm_tier" value={formState.preferredTier} />
                    <AgentIntelligenceSlider
                      currentTier={formState.preferredTier}
                      config={initialData.llmIntelligence}
                      onTierChange={handleTierChange}
                    />
                  </div>
                </>
              )}

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Status</span>
              </div>
              <div className="sm:col-span-9">
                <div className="flex flex-col gap-4 rounded-lg border border-slate-200/70 bg-transparent p-4 lg:flex-row lg:items-center lg:justify-between lg:gap-6">
                  <div className="flex items-center gap-3">
                    <div className={`flex items-center justify-center w-10 h-10 rounded-full ${formState.isActive ? 'bg-green-100' : 'bg-gray-100'}`}>
                      {formState.isActive ? (
                        <CheckCircle2 className="w-5 h-5 text-green-600" aria-hidden="true" />
                      ) : (
                        <XCircle className="w-5 h-5 text-gray-500" aria-hidden="true" />
                      )}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-gray-800">{formState.isActive ? 'Active' : 'Inactive'}</p>
                      <p className="text-xs text-gray-500">
                        {formState.isActive
                          ? 'This agent is currently running and accepting tasks.'
                          : 'This agent is paused and not accepting tasks.'}
                      </p>
                    </div>
                  </div>
                  <SettingsSwitch
                    name="is_active"
                    value="true"
                    aria-label="Toggle agent status"
                    isSelected={formState.isActive}
                    onChange={(isSelected) => setFormState((prev) => ({ ...prev, isActive: isSelected }))}
                  />
                </div>
                <p className="mt-2 text-xs text-gray-500">Toggle the switch and click "Save Changes" to activate or pause the agent.</p>
              </div>

              <div className="sm:col-span-3">
                <label htmlFor="agent-charter" className="inline-block text-sm font-medium text-gray-800 mt-2.5">
                  Assignment
                </label>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9">
                <textarea
                  id="agent-charter"
                  name="charter"
                  rows={4}
                  value={formState.charter}
                  onChange={(event) => setFormState((prev) => ({ ...prev, charter: event.target.value }))}
                  className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                  placeholder="Describe what you want your agent to do..."
                />
                <p className="mt-2 text-xs text-gray-500">Share goals, responsibilities, and key guardrails for this agent.</p>
              </div>

              <div className="sm:col-span-3">
                <label htmlFor="agent-mini-description" className="inline-block text-sm font-medium text-gray-800 mt-2.5">
                  Mini Description
                </label>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9 space-y-3">
                <input
                  type="hidden"
                  name="mini_description_mode"
                  value={formState.miniDescriptionMode}
                />
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm font-medium text-gray-800">Generate automatically</p>
                    <p className="text-xs text-gray-500">Update this label when the assignment changes.</p>
                  </div>
                  <SettingsSwitch
                    aria-label="Generate mini description automatically"
                    isSelected={formState.miniDescriptionMode === 'auto'}
                    onChange={(isSelected) => setFormState((prev) => ({
                      ...prev,
                      miniDescriptionMode: isSelected ? 'auto' : 'manual',
                    }))}
                  />
                </div>
                <input
                  id="agent-mini-description"
                  name="mini_description"
                  type="text"
                  maxLength={80}
                  disabled={formState.miniDescriptionMode === 'auto'}
                  required={formState.miniDescriptionMode === 'manual'}
                  value={formState.miniDescription}
                  onChange={(event) => setFormState((prev) => ({ ...prev, miniDescription: event.target.value }))}
                  className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="e.g. Executive Talent Researcher"
                />
                <div className="flex items-center justify-between gap-4 text-xs text-gray-500">
                  <span>
                    {formState.miniDescriptionMode === 'auto'
                      ? 'The current label remains visible until automatic generation replaces it.'
                      : 'Enter the compact label shown beneath the agent name.'}
                  </span>
                  {formState.miniDescriptionMode === 'manual' ? (
                    <span className="shrink-0 tabular-nums">{formState.miniDescription.length}/80</span>
                  ) : null}
                </div>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Daily Task Credits</span>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9 space-y-4">
                <DailyCreditSummary dailyCredits={initialData.dailyCredits} formatNumber={formatNumber} />
                <div className="grid gap-4 sm:grid-cols-2">
                  <DailyCreditLimitControl
                    id="daily-credit-limit-slider"
                    value={dailyCreditValue}
                    metrics={dailyCreditMetrics}
                    onSliderChange={updateSliderValue}
                    onInputChange={handleDailyCreditInputChange}
                    surface="embedded"
                    label="Soft target (credits/day)"
                    helperText="Soft target controls pacing for this agent. Leave the number blank for unlimited."
                    inputName="daily_credit_limit"
                    sliderInputName="daily_credit_limit_slider"
                  />
                </div>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Dedicated IPs</span>
              </div>
              <div className="sm:col-span-9">
                <DedicatedIpSummary
                  dedicatedIps={initialData.dedicatedIps}
                  organizationName={initialData.agent.organization?.name ?? null}
                  selectedValue={formState.dedicatedProxyId}
                  onChange={(value) => setFormState((prev) => ({ ...prev, dedicatedProxyId: value }))}
                />
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Created</span>
              </div>
              <div className="sm:col-span-9">
                <div className="py-2 px-3 text-sm text-gray-600">{initialData.agent.createdAtDisplay}</div>
              </div>
            </div>
        </CollapsibleSettingsSection>
      </form>

      <CollapsibleSettingsSection
        id="agent-contact-controls"
        title="Contacts & Access"
        subtitle="Contact endpoints and allowlist management."
        bodyClassName="space-y-6 px-5 py-5"
      >
          <PrimaryContacts
            primaryEmail={initialData.primaryEmail}
            primarySms={initialData.primarySms}
            emailSettingsUrl={initialData.urls.emailSettings}
            onOpenEmailSettings={onOpenEmailSettings}
          />

          {initialData.allowlist.show && (
            <AllowlistManager
              state={savedAllowlistState}
              rows={allowlistRows}
              projectedSlotsUsed={projectedContactSlots}
              contactAutoApproveEmailEnabled={
                initialData.features.contactAutoApproveEmail
                || savedFormState.contactApprovalMode === 'auto_approve_email'
              }
              contactApprovalMode={formState.contactApprovalMode}
              saving={saving}
              onAddContact={openAddContactModal}
              onEditContact={openEditContactModal}
              onRemoveRows={confirmAllowlistRemoval}
              onContactApprovalModeChange={(contactApprovalMode) => setFormState((prev) => ({
                ...prev,
                contactApprovalMode,
              }))}
              contactRequestsUrl={initialData.urls.contactRequests}
              onOpenContactRequests={onOpenContactRequests}
            />
          )}

          <CollaboratorManager
            state={savedCollaboratorState}
            rows={collaboratorRows}
            projectedTotalCount={projectedCollaboratorTotalCount}
            error={collaboratorError}
            busy={saving}
            onAdd={openAddCollaboratorModal}
            onRemove={stageCollaboratorRemove}
            onConfirmAction={openConfirmAction}
          />
      </CollapsibleSettingsSection>

      <IntegrationsSection
        mcpServers={initialData.mcpServers}
        isOrgAgent={Boolean(initialData.agent.organization)}
        selectedOrgServers={selectedOrgServers}
        selectedPersonalServers={selectedPersonalServers}
        onToggleOrganizationServer={toggleOrganizationServer}
        onTogglePersonalServer={togglePersonalServer}
        peerLinks={{ entries: peerLinksState, candidates: peerLinkCandidates, defaults: peerLinkDefaults }}
        onPeerLinkAdd={() => openPeerLinkModal('create')}
        onPeerLinkEdit={(entry) => openPeerLinkModal('edit', entry)}
        onPeerLinkDelete={stagePeerLinkDelete}
        webhooks={webhooksState}
        onWebhookCreate={() => openWebhookModal('create')}
        onWebhookEdit={(hook) => openWebhookModal('edit', hook)}
        onWebhookDelete={stageWebhookDelete}
        inboundWebhooks={inboundWebhooksState}
        copiedInboundWebhookId={copiedInboundWebhookId}
        onInboundWebhookCreate={() => openInboundWebhookModal('create')}
        onInboundWebhookEdit={(hook) => openInboundWebhookModal('edit', hook)}
        onInboundWebhookDelete={stageInboundWebhookDelete}
        onInboundWebhookRotateSecret={stageInboundWebhookRotateSecret}
        onInboundWebhookCopy={copyInboundWebhookUrl}
        onConfirmAction={openConfirmAction}
      />

      <ActionsSection
        csrfToken={initialData.csrfToken}
        urls={initialData.urls}
        agent={initialData.agent}
        features={initialData.features}
        reassignment={initialData.reassignment}
        selectedOrgId={selectedOrgId}
        onOrgChange={setSelectedOrgId}
        onReassign={handleReassign}
        reassignError={reassignError}
        reassigning={reassigning}
        onSubmitTransferForm={submitTransferForm}
        onDeleteAgent={confirmDeleteAgent}
        deleteError={deleteError}
      />

      <SaveBar
        visible={hasAnyChanges}
        onCancel={handleResetAll}
        onSave={handleSaveAll}
        busy={saving}
        error={saveError}
        helperText="Save now to update the chat shell and gallery immediately."
        variant="embedded"
        placement="sticky"
      />

      {modal}
    </div>
  )
}

type DailyCreditSummaryProps = {
  dailyCredits: DailyCreditsInfo
  formatNumber: (value: number | null, fractionDigits?: number) => string | null
}

function DailyCreditSummary({ dailyCredits, formatNumber }: DailyCreditSummaryProps) {
  const usageDisplay = formatNumber(dailyCredits.usage, 2)
  const limitDisplay = dailyCredits.limit === null ? 'Unlimited' : formatNumber(dailyCredits.limit, 0)
  const softRemaining = formatNumber(dailyCredits.softRemaining, 2)
  const hardRemaining = formatNumber(dailyCredits.remaining, 2)

  return (
    <div className="space-y-4 rounded-lg border border-slate-200/70 bg-transparent p-4">
      {dailyCredits.unlimited ? (
        <div>
          <p className="text-sm text-gray-700">Soft target is currently Unlimited, so this agent will keep running until your overall credits run out.</p>
          {dailyCredits.nextResetLabel && <p className="text-xs text-gray-500 mt-1">Daily usage still resets at {dailyCredits.nextResetLabel}.</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm text-gray-700">
            <span>Soft target progress</span>
            <span className="font-medium">
              {usageDisplay} / {limitDisplay} credits
            </span>
          </div>
          <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 rounded-full transition-all"
              style={{ width: `${Math.min(dailyCredits.softPercentUsed ?? 0, 100)}%` }}
            />
          </div>
          {softRemaining && <p className="text-xs text-gray-500">Remaining before soft target: {softRemaining} credits.</p>}
          {dailyCredits.nextResetLabel && <p className="text-xs text-gray-500">Daily usage resets at {dailyCredits.nextResetLabel}.</p>}
        </div>
      )}
      {hardRemaining && <p className="text-xs text-gray-500">Hard limit remaining: {hardRemaining} credits.</p>}
    </div>
  )
}

type DedicatedIpSummaryProps = {
  dedicatedIps: DedicatedIpInfo
  organizationName: string | null
  selectedValue: string
  onChange: (value: string) => void
}

function DedicatedIpSummary({ dedicatedIps, organizationName, selectedValue, onChange }: DedicatedIpSummaryProps) {
  return (
    <div className="text-sm text-gray-600 space-y-4" data-dedicated-ip-total={dedicatedIps.total}>
      <p className="text-sm text-gray-500">Monitor and assign dedicated IP addresses reserved for this account.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-slate-200/70 bg-transparent p-4">
          <p className="text-xs uppercase tracking-wide text-gray-500">Total Reserved</p>
          <p className="text-2xl font-semibold text-gray-800 mt-1">{dedicatedIps.total}</p>
          <p className="text-xs text-gray-500 mt-3">
            {dedicatedIps.ownerType === 'organization' && organizationName
              ? `Dedicated IPs owned by ${organizationName}.`
              : 'Dedicated IPs owned by your account.'}
          </p>
          {!dedicatedIps.multiAssign && <p className="text-xs text-amber-600 mt-1">Each dedicated IP can be assigned to only one agent at a time.</p>}
          {dedicatedIps.total === 0 && <p className="text-xs text-gray-500 mt-1">Purchase dedicated IPs in Billing to make them available here.</p>}
        </div>
        <div className="rounded-lg border border-slate-200/70 bg-transparent p-4">
          <p className="text-xs uppercase tracking-wide text-gray-500">Available to Assign</p>
          <p className="text-2xl font-semibold text-gray-800 mt-1">{dedicatedIps.available}</p>
          {dedicatedIps.options.length > 0 ? (
            <div className="mt-4 space-y-2">
              <label htmlFor="dedicated-proxy-id" className="inline-block text-sm font-medium text-gray-800">
                Assigned Dedicated IP
              </label>
              <select
                id="dedicated-proxy-id"
                name="dedicated_proxy_id"
                value={selectedValue}
                onChange={(event) => onChange(event.target.value)}
                className="mt-1 py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
              >
                <option value="">Use shared proxy pool</option>
                {dedicatedIps.options.map((option) => (
                  <option key={option.id} value={option.id} disabled={option.disabled}>
                    {option.label}
                    {option.inUseElsewhere ? ' (In use)' : ''}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-gray-500">
                Selecting a dedicated IP locks this agent to that address. Leave it on "Use shared proxy pool" to continue using shared proxies.
              </p>
              {!dedicatedIps.multiAssign && <p className="mt-1 text-xs text-amber-600">IPs already assigned to other agents are disabled.</p>}
            </div>
          ) : (
            <p className="text-xs text-gray-500 mt-4">No dedicated IPs are currently available to assign.</p>
          )}
        </div>
      </div>
    </div>
  )
}

type PrimaryContactsProps = {
  primaryEmail: PrimaryEndpoint | null
  primarySms: PrimaryEndpoint | null
  emailSettingsUrl: string
  onOpenEmailSettings?: () => void
}

function PrimaryContacts({
  primaryEmail,
  primarySms,
  emailSettingsUrl,
  onOpenEmailSettings,
}: PrimaryContactsProps) {
  const manageEmailSettingsLink = onOpenEmailSettings ? (
    <button type="button" onClick={onOpenEmailSettings} className="text-sm text-blue-600 hover:text-blue-800">
      Manage Email Settings
    </button>
  ) : (
    <a href={emailSettingsUrl} className="text-sm text-blue-600 hover:text-blue-800">
      Manage Email Settings
    </a>
  )
  const setupEmailLink = onOpenEmailSettings ? (
    <button type="button" onClick={onOpenEmailSettings} className="text-blue-600 hover:text-blue-800">
      Set up email
    </button>
  ) : (
    <a href={emailSettingsUrl} className="text-blue-600 hover:text-blue-800">
      Set up email
    </a>
  )

  return (
    <div className="grid sm:grid-cols-12 gap-4 sm:gap-6">
      <div className="sm:col-span-3">
        <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Primary Email</span>
      </div>
      <div className="sm:col-span-9">
        {primaryEmail ? (
          <>
            <input
              id="agent-email"
              type="text"
              value={primaryEmail.address}
              readOnly
              className="block w-full rounded-lg border border-slate-200/70 bg-transparent px-3 py-2 text-sm shadow-none"
            />
            <p className="mt-2 text-xs text-gray-500">The agent's primary email address for communication.</p>
            <div className="mt-2 space-y-1">
              {manageEmailSettingsLink}
            </div>
          </>
        ) : (
          <div className="rounded border border-dashed border-slate-300 px-3 py-2 text-sm text-gray-600 bg-transparent">
            Not configured. {setupEmailLink}
          </div>
        )}
      </div>

      {primarySms && (
        <>
          <div className="sm:col-span-3">
            <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Primary SMS</span>
          </div>
          <div className="sm:col-span-9">
            <input
              id="agent-sms"
              type="text"
              value={primarySms.address}
              readOnly
              className="block w-full rounded-lg border border-slate-200/70 bg-transparent px-3 py-2 text-sm shadow-none"
            />
            <p className="mt-2 text-xs text-gray-500">The agent's primary SMS address for communication. This cannot be changed.</p>
          </div>
        </>
      )}
    </div>
  )
}

type AllowlistManagerProps = {
  state: AllowlistState
  rows: AllowlistTableRow[]
  projectedSlotsUsed: number
  contactAutoApproveEmailEnabled: boolean
  contactApprovalMode: ContactApprovalMode
  saving: boolean
  onAddContact: () => void
  onEditContact: (row: AllowlistTableRow) => void
  onRemoveRows: (rows: AllowlistTableRow[]) => void
  onContactApprovalModeChange: (mode: ContactApprovalMode) => void
  contactRequestsUrl: string
  onOpenContactRequests?: () => void
}

function AllowlistManager({
  state,
  rows,
  projectedSlotsUsed,
  contactAutoApproveEmailEnabled,
  contactApprovalMode,
  saving,
  onAddContact,
  onEditContact,
  onRemoveRows,
  onContactApprovalModeChange,
  contactRequestsUrl,
  onOpenContactRequests,
}: AllowlistManagerProps) {
  const contactCapReached = typeof state.maxContacts === 'number' && state.maxContacts > 0 && projectedSlotsUsed >= state.maxContacts
  const embeddedInfoBannerClassName = 'flex items-start gap-2 rounded-lg border border-amber-300/20 bg-amber-950/30 px-4 py-3'
  const embeddedInfoCardClassName = 'rounded-xl border border-slate-200/20 bg-slate-950/35 px-4 py-4'
  const embeddedInfoIconClassName = 'flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200/20 bg-slate-900/45 text-slate-300'
  const embeddedPrimaryActionClassName = getSettingsActionButtonClassName({ tone: 'primary' })

  return (
    <div className="space-y-5">
      {contactAutoApproveEmailEnabled && (
        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-slate-700">New contact approval</legend>
          <p className="text-xs text-slate-500">Choose how this agent handles email addresses that are not already listed.</p>
          <div className="grid gap-3 lg:grid-cols-2">
            {CONTACT_APPROVAL_OPTIONS.map((option) => {
              const Icon = option.icon
              const Badge = option.badge
              const selected = contactApprovalMode === option.value
              return (
                <label
                  key={option.value}
                  className={`flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-4 text-left transition-colors ${
                    selected
                      ? 'border-blue-400/60 bg-blue-950/30'
                      : 'border-slate-200/20 bg-transparent hover:border-slate-300/40'
                  } ${saving ? 'cursor-not-allowed opacity-60' : ''}`}
                >
                  <span className="relative flex size-9 shrink-0 items-center justify-center rounded-lg border border-slate-200/20 bg-slate-900/45 text-slate-300">
                    <Icon className="size-4" aria-hidden="true" />
                    {Badge && <Badge className="absolute -right-1 -top-1 size-3 text-amber-300" aria-hidden="true" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-slate-100">{option.title}</span>
                    <span className="mt-1 block text-xs leading-5 text-slate-400">{option.description}</span>
                  </span>
                  <input
                    type="radio"
                    name="contact-approval-mode-choice"
                    value={option.value}
                    checked={selected}
                    disabled={saving}
                    onChange={() => onContactApprovalModeChange(option.value)}
                    className="mt-2 size-4 shrink-0 accent-blue-500"
                  />
                </label>
              )
            })}
          </div>
          {contactApprovalMode === 'auto_approve_email' && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300/20 bg-amber-950/30 px-4 py-3 text-xs leading-5 text-amber-100">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-400" aria-hidden="true" />
              <p>
                This agent can add email contacts without asking you first. New contacts remain visible and removable below. SMS contacts always require approval.
              </p>
            </div>
          )}
        </fieldset>
      )}

      {!state.emailVerified && (
        <div className={embeddedInfoBannerClassName}>
          <Mail className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <div className="text-sm text-amber-100">
            <span className="font-medium">Email verification required.</span>{' '}
            External contacts won't be able to reach your agent until you{' '}
            <a href="/accounts/email/" className="underline hover:text-amber-900">verify your email address</a>.
          </div>
        </div>
      )}

      {state.pendingContactRequests > 0 && (
        <div className="rounded-lg border border-amber-300/20 bg-amber-950/30 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-amber-600" aria-hidden="true" />
              <span className="text-sm font-medium text-amber-100">
                {state.pendingContactRequests} Contact Request{state.pendingContactRequests === 1 ? '' : 's'} Pending
              </span>
            </div>
            {onOpenContactRequests ? (
              <button
                type="button"
                onClick={onOpenContactRequests}
                className="text-sm font-medium text-amber-100 underline transition-colors hover:text-white"
              >
                Review
              </button>
            ) : (
              <a href={contactRequestsUrl} className="text-sm font-medium text-amber-700 hover:text-amber-900 underline">
                Review
              </a>
            )}
          </div>
          {contactApprovalMode === 'auto_approve_email' && (
            <p className="mt-2 pl-7 text-xs text-amber-200/80">
              Email requests shown here predate automatic approval; SMS requests always need your review.
            </p>
          )}
        </div>
      )}

      {(state.ownerEmail || state.ownerPhone) && (
        <div className={embeddedInfoCardClassName}>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Owner Endpoints</div>
          <p className="mt-1 text-xs text-slate-600">Owner endpoints can always be contacted by the agent.</p>
          {state.ownerEmail && (
            <div className="mt-3 flex items-center gap-2 text-sm text-slate-700">
              <span className={embeddedInfoIconClassName}>
                <Mail className="w-4 h-4" aria-hidden="true" />
              </span>
              <span className="font-medium">{state.ownerEmail}</span>
            </div>
          )}
          {state.ownerPhone && (
            <div className="mt-3 flex items-center gap-2 text-sm text-slate-700">
              <span className={embeddedInfoIconClassName}>
                <Phone className="w-4 h-4" aria-hidden="true" />
              </span>
              <span className="font-medium">{state.ownerPhone}</span>
            </div>
          )}
        </div>
      )}

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-semibold text-slate-700">Contacts</h4>
            <p className="text-xs text-slate-500">
              {projectedSlotsUsed} / {state.maxContacts ?? 'Unlimited'} contact slots
              {projectedSlotsUsed !== state.activeCount ? ` (currently ${state.activeCount})` : ''}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {contactCapReached && (
              <span className="text-xs font-medium text-amber-700">
                Remove contacts or collaborators before adding more.
              </span>
            )}
            <button
              type="button"
              onClick={onAddContact}
              disabled={saving || contactCapReached}
              className={embeddedPrimaryActionClassName}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add Contact
            </button>
          </div>
        </div>

        <AllowlistContactsTable
          rows={rows}
          disabled={saving}
          onEditRow={onEditContact}
          onRemoveRow={(row) => onRemoveRows([row])}
          onRemoveRows={onRemoveRows}
        />
      </div>
    </div>
  )
}

type CollaboratorManagerProps = {
  state: CollaboratorState
  rows: CollaboratorTableRow[]
  projectedTotalCount: number
  error: string | null
  busy: boolean
  onAdd: () => void
  onRemove: (row: CollaboratorTableRow) => void
  onConfirmAction: (config: ConfirmActionConfig) => void
}

function CollaboratorManager({ state, rows, projectedTotalCount, error, busy, onAdd, onRemove, onConfirmAction }: CollaboratorManagerProps) {
  const canManage = state.canManage
  const totalLimit = state.maxContacts ?? 'Unlimited'
  const embeddedPrimaryActionClassName = getSettingsActionButtonClassName({ tone: 'success' })

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="text-xs text-slate-600">
          Invite employees to chat and exchange files. Collaborators can upload and download files only.
        </p>
        <p className="text-xs text-slate-600">Contact slots used: {state.totalCount} / {totalLimit}</p>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-semibold text-slate-700">Collaborators</h4>
            <p className="text-xs text-slate-500">
              {projectedTotalCount} total
              {projectedTotalCount !== state.totalCount ? ` (currently ${state.totalCount})` : ''}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {!canManage && <span className="text-xs text-slate-500">Managed by owner/admin</span>}
            <button
              type="button"
              onClick={onAdd}
              disabled={busy || !canManage}
              className={embeddedPrimaryActionClassName}
            >
              <span className="inline-flex items-center gap-2">
                <UserPlus className="h-4 w-4" aria-hidden="true" />
                Add Collaborator
              </span>
            </button>
          </div>
        </div>

        {error && <div className="text-xs text-rose-600">{error}</div>}

        <CollaboratorsTable
          rows={rows}
          disabled={busy}
          canManage={canManage}
          onRemove={(row) =>
            onConfirmAction({
              title: row.kind === 'active' ? 'Remove collaborator' : 'Cancel invite',
              body: row.kind === 'active' ? 'Remove this collaborator from this agent?' : 'Cancel this collaborator invite?',
              tone: 'danger',
              confirmLabel: row.kind === 'active' ? 'Remove' : 'Cancel invite',
              onConfirm: () => onRemove(row),
            })
          }
        />
      </div>
    </div>
  )
}

type IntegrationsSectionProps = {
  mcpServers: McpServersInfo
  isOrgAgent: boolean
  selectedOrgServers: Set<string>
  selectedPersonalServers: Set<string>
  onToggleOrganizationServer: (id: string) => void
  onTogglePersonalServer: (id: string) => void
  peerLinks: {
    entries: PeerLinkEntryState[]
    candidates: PeerLinkCandidate[]
    defaults: PeerLinksInfo['defaults']
  }
  onPeerLinkAdd: () => void
  onPeerLinkEdit: (entry: PeerLinkEntryState) => void
  onPeerLinkDelete: (entry: PeerLinkEntryState) => void
  webhooks: DisplayWebhook[]
  onWebhookCreate: () => void
  onWebhookEdit: (webhook: DisplayWebhook) => void
  onWebhookDelete: (webhook: DisplayWebhook) => void
  inboundWebhooks: DisplayInboundWebhook[]
  copiedInboundWebhookId: string | null
  onInboundWebhookCreate: () => void
  onInboundWebhookEdit: (webhook: DisplayInboundWebhook) => void
  onInboundWebhookDelete: (webhook: DisplayInboundWebhook) => void
  onInboundWebhookRotateSecret: (webhook: DisplayInboundWebhook) => void
  onInboundWebhookCopy: (webhook: DisplayInboundWebhook) => void
  onConfirmAction: (config: ConfirmActionConfig) => void
}

function IntegrationsSection({
  mcpServers,
  isOrgAgent,
  selectedOrgServers,
  selectedPersonalServers,
  onToggleOrganizationServer,
  onTogglePersonalServer,
  peerLinks,
  onPeerLinkAdd,
  onPeerLinkEdit,
  onPeerLinkDelete,
  webhooks,
  onWebhookCreate,
  onWebhookEdit,
  onWebhookDelete,
  inboundWebhooks,
  copiedInboundWebhookId,
  onInboundWebhookCreate,
  onInboundWebhookEdit,
  onInboundWebhookDelete,
  onInboundWebhookRotateSecret,
  onInboundWebhookCopy,
  onConfirmAction,
}: IntegrationsSectionProps) {
  const sectionBodyClassName = 'space-y-6 px-5 py-5'
  const cardClassName = getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'p-4 space-y-4' })
  const tableWrapperClassName = getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none' })
  const tableHeadClassName = 'bg-slate-950/45'
  const tableBodyClassName = 'bg-transparent divide-y divide-slate-200/15'
  const primaryActionButtonClassName = getSettingsActionButtonClassName({ tone: 'primary' })
  const neutralButtonClassName = getSettingsActionButtonClassName({ size: 'sm' })
  const destructiveButtonClassName = getSettingsActionButtonClassName({ tone: 'danger', size: 'sm' })
  const warningButtonClassName = getSettingsActionButtonClassName({ tone: 'warning', size: 'sm' })
  const pendingBadgeClassName = getSettingsStatusBadgeClassName({ tone: 'warning', className: 'px-2 py-0.5 text-[11px]' })
  const activeStatusClassName = getSettingsStatusBadgeClassName({ tone: 'success', className: 'px-2 py-0.5 text-[11px]' })
  const inactiveStatusClassName = getSettingsStatusBadgeClassName({ className: 'px-2 py-0.5 text-[11px]' })
  const emptyStateClassName = 'rounded-xl border border-dashed border-slate-200/25 bg-slate-950/20 px-4 py-4 text-sm text-slate-300'

  return (
    <CollapsibleSettingsSection
      id="agent-integrations"
      title="Integrations"
      subtitle="MCP servers, peer links, and webhooks."
      bodyClassName="divide-y divide-slate-200/15 p-0"
    >
        <section className={sectionBodyClassName}>
        <div>
          <h3 className="text-base font-semibold text-gray-800">MCP Servers</h3>
          <p className="text-sm text-gray-500">
            Platform MCP servers are always enabled. Enable or disable team servers per agent, and configure optional personal
            servers when applicable.
          </p>
        </div>

        {mcpServers.inherited.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-700">Inherited Servers</h4>
              <ul className="space-y-2">
                {mcpServers.inherited.map((server) => (
                  <li key={server.id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-200/20 bg-slate-950/25 px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-gray-800">{server.displayName}</p>
                      {server.description && <p className="text-sm text-gray-600">{server.description}</p>}
                    </div>
                    <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{server.scope}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {mcpServers.organization.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-sm font-semibold text-gray-700">Team Servers</h4>
              {isOrgAgent ? (
                <div className="grid gap-3 md:grid-cols-2">
                  {mcpServers.organization.map((server) => {
                    const checked = selectedOrgServers.has(server.id)
                    return (
                      <label key={server.id} className="flex items-start gap-3 rounded-lg border border-slate-200/20 bg-slate-950/25 px-3 py-3">
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 text-blue-600 border-gray-300 rounded"
                          checked={checked}
                          onChange={() => onToggleOrganizationServer(server.id)}
                        />
                        <div>
                          <p className="text-sm font-medium text-gray-800">{server.displayName}</p>
                          {server.description && <p className="text-sm text-gray-600">{server.description}</p>}
                        </div>
                      </label>
                    )
                  })}
                </div>
              ) : (
                <p className="text-sm text-gray-500">Team MCP servers can be managed when the agent belongs to a team.</p>
              )}
            </div>
          )}

          {mcpServers.personal.length > 0 ? (
            mcpServers.showPersonalForm ? (
              <div className={cardClassName}>
                <div className="grid gap-3 md:grid-cols-2">
                  {mcpServers.personal.map((server) => {
                    const checked = selectedPersonalServers.has(server.id)
                    return (
                      <label key={server.id} className="flex items-start gap-3 rounded-lg border border-slate-200/20 bg-slate-950/25 px-3 py-3">
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 text-blue-600 border-gray-300 rounded"
                          checked={checked}
                          onChange={() => onTogglePersonalServer(server.id)}
                        />
                        <div>
                          <p className="text-sm font-medium text-gray-800">{server.displayName}</p>
                          {server.description && <p className="text-sm text-gray-600">{server.description}</p>}
                        </div>
                      </label>
                    )
                  })}
                </div>
                {mcpServers.canManage && mcpServers.manageUrl && (
                  <div className="flex justify-end">
                    <SettingsActionButton
                      as="a"
                      href={mcpServers.manageUrl}
                    >
                      <ServerCog className="h-4 w-4" aria-hidden="true" />
                      Manage All Servers
                    </SettingsActionButton>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-gray-500">Personal MCP servers are managed on personal agents. Switch to a personal agent to configure access.</p>
            )
          ) : (
            mcpServers.inherited.length === 0 && <p className="text-sm text-gray-500">No MCP servers are available for this agent yet.</p>
          )}
        </section>

        <section className={sectionBodyClassName}>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-gray-800">Agent Contacts (Peer Links)</h3>
              <p className="text-sm text-gray-500">Create direct channels between this agent and other agents you control.</p>
            </div>
            <button
              type="button"
              className={primaryActionButtonClassName}
              onClick={onPeerLinkAdd}
              disabled={peerLinks.candidates.length === 0}
            >
              <Plus className="w-4 h-4" aria-hidden="true" />
              Add Peer Link
            </button>
          </div>
          {peerLinks.candidates.length === 0 && (
            <p className="text-xs text-gray-500">No additional eligible agents available right now.</p>
          )}

          {peerLinks.entries.length > 0 ? (
            <div className={tableWrapperClassName}>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className={tableHeadClassName}>
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Agent</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Quota</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Remaining</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Next Reset</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Feature Flag</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className={tableBodyClassName}>
                  {peerLinks.entries.map((entry) => {
                    const pendingLabel =
                      entry.pendingType === 'delete'
                        ? 'Pending removal'
                        : entry.pendingType === 'update'
                          ? 'Pending update'
                          : entry.pendingType === 'create'
                            ? 'Pending create'
                            : null
                    const rowClasses = entry.pendingType === 'delete' ? 'opacity-60' : ''
                    return (
                      <tr key={entry.id} className={`align-top ${rowClasses}`}>
                        <td className="px-4 py-3 text-sm text-gray-800">
                          <div className="font-medium">{entry.counterpartName ?? '(Agent unavailable)'}</div>
                          <div className="text-xs text-gray-500 mt-1">Linked {entry.createdOnLabel}</div>
                          {pendingLabel && <div className="mt-1"><span className={pendingBadgeClassName}>{pendingLabel}</span></div>}
                          <div className="text-xs mt-1">
                            Status:{' '}
                            <span className={entry.isEnabled ? activeStatusClassName : inactiveStatusClassName}>
                              {entry.isEnabled ? 'Enabled' : 'Disabled'}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">
                          {entry.messagesPerWindow} / {entry.windowHours} h
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">{entry.state?.creditsRemaining ?? '--'}</td>
                        <td className="px-4 py-3 text-sm text-gray-700">{entry.state?.windowResetLabel ?? '--'}</td>
                        <td className="px-4 py-3 text-sm text-gray-700">{entry.featureFlag ?? '--'}</td>
                        <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              className={neutralButtonClassName}
                              onClick={() => onPeerLinkEdit(entry)}
                              disabled={entry.pendingType === 'delete'}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className={destructiveButtonClassName}
                              onClick={() => {
                                onConfirmAction({
                                  title: 'Remove peer link',
                                  body: 'Remove this link? This cannot be undone.',
                                  confirmLabel: 'Remove link',
                                  tone: 'danger',
                                  onConfirm: () => onPeerLinkDelete(entry),
                                })
                              }}
                            >
                              <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                              Remove
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className={emptyStateClassName}>
              No peer links yet. Use the button above to connect this agent with another agent you control.
            </div>
          )}
        </section>

        <section className={sectionBodyClassName}>
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
            <div>
              <h3 className="text-base font-semibold text-gray-800">Outbound Webhooks</h3>
              <p className="text-sm text-gray-500">Webhooks notify your systems when the agent completes important actions.</p>
            </div>
            <button
              type="button"
              onClick={onWebhookCreate}
              className={primaryActionButtonClassName}
            >
              <Plus className="w-4 h-4" aria-hidden="true" />
              Add Outbound Webhook
            </button>
          </div>

          {webhooks.length > 0 ? (
            <div className={tableWrapperClassName}>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className={tableHeadClassName}>
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">URL</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className={tableBodyClassName}>
                  {webhooks.map((webhook) => {
                    const pendingLabel =
                      webhook.pendingType === 'delete'
                        ? 'Pending removal'
                        : webhook.pendingType === 'update'
                          ? 'Pending update'
                          : webhook.pendingType === 'create'
                            ? 'Pending create'
                            : null
                    const rowClasses = webhook.pendingType === 'delete' ? 'opacity-60' : ''
                    return (
                      <tr key={webhook.id} className={rowClasses}>
                        <td className="px-4 py-3 text-sm text-gray-800">
                          <div className="flex flex-col">
                            <span>{webhook.name}</span>
                            {pendingLabel && <span className={`mt-1 w-fit ${pendingBadgeClassName}`}>{pendingLabel}</span>}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600 break-all">{webhook.url}</td>
                        <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => onWebhookEdit(webhook)}
                              className={neutralButtonClassName}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className={destructiveButtonClassName}
                              onClick={() =>
                                onConfirmAction({
                                  title: 'Delete webhook',
                                  body: `Remove the webhook "${webhook.name}"? This cannot be undone.`,
                                  confirmLabel: 'Delete webhook',
                                  tone: 'danger',
                                  onConfirm: () => onWebhookDelete(webhook),
                                })
                              }
                            >
                              <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className={emptyStateClassName}>
              No webhooks yet. Add one to let your agent notify external systems.
            </div>
          )}
        </section>

        <section className={sectionBodyClassName}>
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
            <div>
              <h3 className="text-base font-semibold text-gray-800">Inbound Webhooks</h3>
              <p className="text-sm text-gray-500">Webhook URLs trigger the agent and show up in live chat as inbound events.</p>
            </div>
            <button
              type="button"
              onClick={onInboundWebhookCreate}
              className={primaryActionButtonClassName}
            >
              <Plus className="w-4 h-4" aria-hidden="true" />
              Add Inbound Webhook
            </button>
          </div>

          {inboundWebhooks.length > 0 ? (
            <div className={tableWrapperClassName}>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className={tableHeadClassName}>
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Webhook URL</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Last Triggered</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className={tableBodyClassName}>
                  {inboundWebhooks.map((webhook) => {
                    const pendingLabel =
                      webhook.pendingType === 'delete'
                        ? 'Pending removal'
                        : webhook.pendingType === 'update'
                          ? 'Pending update'
                          : webhook.pendingType === 'create'
                            ? 'Pending create'
                            : webhook.pendingType === 'rotate_secret'
                              ? 'Pending secret rotation'
                              : null
                    const rowClasses = webhook.pendingType === 'delete' ? 'opacity-60' : ''
                    const lastTriggeredLabel = webhook.lastTriggeredAt ? new Date(webhook.lastTriggeredAt).toLocaleString() : 'Never'
                    const copyLabel = copiedInboundWebhookId === webhook.id ? 'Copied' : 'Copy'
                    return (
                      <tr key={webhook.id} className={rowClasses}>
                        <td className="px-4 py-3 text-sm text-gray-800">
                          <div className="flex flex-col">
                            <span>{webhook.name}</span>
                            {pendingLabel && <span className={`mt-1 w-fit ${pendingBadgeClassName}`}>{pendingLabel}</span>}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600">
                          <div className="flex min-w-0 items-stretch overflow-hidden rounded-lg border border-slate-200/20 bg-slate-950/25">
                            <input
                              type="text"
                              value={webhook.url ?? ''}
                              readOnly
                              placeholder="URL available after save"
                              aria-label={`Webhook URL for ${webhook.name}`}
                              onFocus={(event) => event.currentTarget.select()}
                              className="min-w-0 flex-1 border-0 bg-transparent px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:ring-0"
                            />
                            <button
                              type="button"
                              onClick={() => onInboundWebhookCopy(webhook)}
                              disabled={!webhook.url}
                              className="inline-flex shrink-0 items-center gap-1.5 border-l border-slate-200/20 bg-slate-950/10 px-3 py-2 text-xs font-medium text-slate-100 transition hover:bg-slate-900/45 disabled:cursor-not-allowed disabled:text-slate-500"
                            >
                              {copiedInboundWebhookId === webhook.id ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : <Copy className="w-3.5 h-3.5" aria-hidden="true" />}
                              {copyLabel}
                            </button>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">
                          <span className={webhook.isActive ? activeStatusClassName : inactiveStatusClassName}>
                            {webhook.isActive ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">{lastTriggeredLabel}</td>
                        <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => onInboundWebhookEdit(webhook)}
                              className={neutralButtonClassName}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className={warningButtonClassName}
                              disabled={webhook.temp}
                              onClick={() =>
                                onConfirmAction({
                                  title: 'Rotate inbound webhook secret',
                                  body: `Rotate the secret for "${webhook.name}"? Existing callers will need the new URL after you save changes.`,
                                  confirmLabel: 'Rotate secret',
                                  onConfirm: () => onInboundWebhookRotateSecret(webhook),
                                })
                              }
                            >
                              <KeyRound className="w-3.5 h-3.5" aria-hidden="true" />
                              Rotate Secret
                            </button>
                            <button
                              type="button"
                              className={destructiveButtonClassName}
                              onClick={() =>
                                onConfirmAction({
                                  title: 'Delete inbound webhook',
                                  body: `Remove the inbound webhook "${webhook.name}"? This cannot be undone.`,
                                  confirmLabel: 'Delete inbound webhook',
                                  tone: 'danger',
                                  onConfirm: () => onInboundWebhookDelete(webhook),
                                })
                              }
                            >
                              <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className={emptyStateClassName}>
              No inbound webhooks yet. Add one to let external systems trigger this agent.
            </div>
          )}
        </section>
    </CollapsibleSettingsSection>
  )
}

type PeerLinkModalProps = {
  mode: 'create' | 'edit'
  entry: PeerLinkEntryState | null
  candidates: PeerLinkCandidate[]
  defaults: PeerLinksInfo['defaults']
  onSubmit: (values: { peerAgentId?: string; messagesPerWindow: number; windowHours: number; featureFlag: string; isEnabled: boolean }) => void
  onClose: () => void
}

function PeerLinkModal({ mode, entry, candidates, defaults, onSubmit, onClose }: PeerLinkModalProps) {
  const isCreate = mode === 'create'
  const [peerAgentId, setPeerAgentId] = useState(entry?.counterpartId ?? candidates[0]?.id ?? '')
  const [messagesInput, setMessagesInput] = useState(String(entry?.messagesPerWindow ?? defaults.messagesPerWindow))
  const [windowInput, setWindowInput] = useState(String(entry?.windowHours ?? defaults.windowHours))
  const [featureFlag, setFeatureFlag] = useState(entry?.featureFlag ?? '')
  const [isEnabled, setIsEnabled] = useState(entry?.isEnabled ?? true)

  const parseNumber = (value: string, fallback: number) => {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) {
      return fallback
    }
    return Math.round(numeric)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isCreate && !peerAgentId) {
      return
    }
    onSubmit({
      peerAgentId: isCreate ? peerAgentId : entry?.counterpartId ?? undefined,
      messagesPerWindow: parseNumber(messagesInput, defaults.messagesPerWindow),
      windowHours: parseNumber(windowInput, defaults.windowHours),
      featureFlag: featureFlag.trim(),
      isEnabled,
    })
  }

  return (
    <ModalForm
      id="peer-link-form"
      title={isCreate ? 'Add Peer Link' : 'Edit Peer Link'}
      subtitle={isCreate ? 'Select an agent and quota limits.' : 'Adjust quota and feature flag controls for this link.'}
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      submitLabel="Save Link"
      submitDisabled={isCreate && !peerAgentId}
      formClassName="space-y-5"
    >
        {isCreate ? (
          <FormField id="peer-link-agent" label="Agent">
            <SelectInput
              id="peer-link-agent"
              value={peerAgentId}
              onChange={(event) => setPeerAgentId(event.target.value)}
              disabled={candidates.length === 0}
            >
              <option value="">Select an agent...</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </SelectInput>
            {candidates.length === 0 && <p className="text-xs text-gray-500 mt-1">No additional eligible agents available.</p>}
          </FormField>
        ) : (
          <div>
            <span className="block text-sm font-medium text-gray-700">Agent</span>
            <p className="mt-1 text-sm text-gray-600">{entry?.counterpartName ?? '(Agent unavailable)'}</p>
          </div>
        )}

        <FormField id="peer-link-messages" label="Messages per Window">
          <TextInput
            id="peer-link-messages"
            type="number"
            min="1"
            value={messagesInput}
            onChange={(event) => setMessagesInput(event.target.value)}
          />
        </FormField>
        <FormField id="peer-link-window" label="Window Hours">
          <TextInput
            id="peer-link-window"
            type="number"
            min="1"
            value={windowInput}
            onChange={(event) => setWindowInput(event.target.value)}
          />
        </FormField>

        {!isCreate && (
          <>
            <FormField id="peer-link-feature-flag" label="Feature Flag">
              <TextInput
                id="peer-link-feature-flag"
                type="text"
                value={featureFlag}
                onChange={(event) => setFeatureFlag(event.target.value)}
                placeholder="optional"
              />
            </FormField>
            <CheckboxField
              id="peer-link-enabled"
              checked={isEnabled}
              onChange={(event) => setIsEnabled(event.target.checked)}
              label="Link enabled"
              containerClassName="inline-flex items-center gap-2"
            />
          </>
        )}
    </ModalForm>
  )
}

type WebhookModalProps = {
  mode: 'create' | 'edit'
  webhook: DisplayWebhook | null
  onSubmit: (draft: { id?: string; name: string; url: string }) => void
  onClose: () => void
}

function WebhookModal({ mode, webhook, onSubmit, onClose }: WebhookModalProps) {
  const [name, setName] = useState(webhook?.name ?? '')
  const [url, setUrl] = useState(webhook?.url ?? '')

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSubmit({ id: webhook?.id, name, url })
  }

  return (
    <ModalForm
      id="webhook-form"
      title={mode === 'create' ? 'Add Webhook' : 'Edit Webhook'}
      subtitle="Provide a human-friendly name and destination URL."
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      submitLabel="Save Webhook"
      formClassName="space-y-5"
    >
        <FormField id="webhook-name-field" label="Webhook Name">
          <TextInput
            type="text"
            id="webhook-name-field"
            name="webhook_name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="A descriptive name for this webhook"
          />
        </FormField>
        <FormField id="webhook-url-field" label="Destination URL">
          <TextInput
            type="url"
            id="webhook-url-field"
            name="webhook_url"
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/webhooks/gobii"
          />
        </FormField>
    </ModalForm>
  )
}

type InboundWebhookModalProps = {
  mode: 'create' | 'edit'
  webhook: DisplayInboundWebhook | null
  onSubmit: (draft: { id?: string; name: string; isActive: boolean }) => void
  onClose: () => void
}

function InboundWebhookModal({ mode, webhook, onSubmit, onClose }: InboundWebhookModalProps) {
  const [name, setName] = useState(webhook?.name ?? '')
  const [isActive, setIsActive] = useState(webhook?.isActive ?? true)

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSubmit({ id: webhook?.id, name, isActive })
  }

  return (
    <ModalForm
      id="inbound-webhook-form"
      title={mode === 'create' ? 'Add Inbound Webhook' : 'Edit Inbound Webhook'}
      subtitle="Save to generate or update the secret-bearing webhook URL."
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      submitLabel="Save Inbound Webhook"
      formClassName="space-y-5"
    >
        <FormField id="inbound-webhook-name-field" label="Webhook Name">
          <TextInput
            type="text"
            id="inbound-webhook-name-field"
            name="inbound_webhook_name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="A descriptive name for this inbound webhook"
          />
        </FormField>
        <CheckboxField
          id="inbound-webhook-active"
          checked={isActive}
          onChange={(event) => setIsActive(event.target.checked)}
          label="Webhook active"
          containerClassName="inline-flex items-center gap-2"
        />
    </ModalForm>
  )
}

type ActionsSectionProps = {
  csrfToken: string
  urls: AgentSettingsData['urls']
  agent: AgentSummary
  features: AgentSettingsData['features']
  reassignment: ReassignmentInfo
  selectedOrgId: string
  onOrgChange: (value: string) => void
  onReassign: (targetOrgId: string | null) => Promise<void>
  reassignError: string | null
  reassigning: boolean
  onSubmitTransferForm: (event: FormEvent<HTMLFormElement>) => void
  onDeleteAgent: () => void
  deleteError: string | null
}

function ActionsSection({
  csrfToken,
  urls,
  agent,
  features,
  reassignment,
  selectedOrgId,
  onOrgChange,
  onReassign,
  reassignError,
  reassigning,
  onSubmitTransferForm,
  onDeleteAgent,
  deleteError,
}: ActionsSectionProps) {
  const sectionBodyClassName = 'space-y-4 px-5 py-5'

  return (
    <CollapsibleSettingsSection
      id="agent-ownership"
      title="Actions"
      subtitle="Ownership, transfer, and deletion tools."
      bodyClassName="divide-y divide-slate-200/15 p-0"
    >
        {features.organizations && reassignment.enabled && (
          <section className={sectionBodyClassName}>
            <div>
              <h3 className="text-base font-semibold text-gray-800">Team Assignment</h3>
              <p className="text-sm text-gray-500">Switch this agent between your personal workspace and a team you manage.</p>
            </div>
            {agent.organization ? (
              <div className="space-y-3">
                <div className="flex flex-col gap-3 rounded-lg border border-slate-200/70 bg-transparent px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <span className="text-sm text-gray-700">
                    Currently assigned to <strong>{agent.organization.name}</strong>
                  </span>
                  <SettingsActionButton
                    onClick={() => onReassign(null)}
                    disabled={reassigning}
                  >
                    Move to Personal
                  </SettingsActionButton>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:gap-3">
                  <select
                    id="target-org-id"
                    value={selectedOrgId}
                    onChange={(event) => onOrgChange(event.target.value)}
                    className="py-2 border-gray-200 rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                  >
                    <option value="">Select team...</option>
                    {reassignment.organizations.map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                  <SettingsActionButton
                    tone="primary"
                    onClick={() => onReassign(selectedOrgId || null)}
                    disabled={!selectedOrgId || reassigning}
                  >
                    Assign to Team
                  </SettingsActionButton>
                </div>
                <p className="text-xs text-gray-500">Name must be unique within the selected team.</p>
              </div>
            )}
            {reassignError ? <InlineStatusBanner variant="error" surface="embedded" density="compact">{reassignError}</InlineStatusBanner> : null}
          </section>
        )}

        <section className={sectionBodyClassName}>
          <div>
            <h3 className="text-base font-semibold text-gray-800">Transfer Ownership</h3>
            <p className="text-sm text-gray-500">Send this agent to someone else. They can accept or decline from their dashboard.</p>
          </div>

          {agent.pendingTransfer ? (
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 bg-indigo-50 border border-indigo-100 rounded-lg p-4">
              <div>
                <p className="text-sm text-indigo-800">
                  Transfer invitation sent to <strong>{agent.pendingTransfer.toEmail}</strong> on {agent.pendingTransfer.createdAtDisplay}.
                </p>
                <p className="text-xs text-indigo-700 mt-1">They'll need to sign in with that email to accept.</p>
              </div>
              <form method="post" action={urls.detail} className="flex" onSubmit={onSubmitTransferForm}>
                <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
                <input type="hidden" name="action" value="cancel_transfer_invite" />
                <SettingsActionButton type="submit">
                  Cancel Invitation
                </SettingsActionButton>
              </form>
            </div>
          ) : (
            <form method="post" action={urls.detail} className="space-y-4" onSubmit={onSubmitTransferForm}>
              <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
              <input type="hidden" name="action" value="transfer_agent" />
              <div>
                <label htmlFor="transfer-email" className="text-sm font-medium text-gray-700">
                  Recipient email
                </label>
                <input
                  id="transfer-email"
                  name="transfer_email"
                  type="email"
                  required
                  placeholder="user@example.com"
                  className="mt-1 block w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
                />
              </div>
              <div>
                <label htmlFor="transfer-message" className="text-sm font-medium text-gray-700">
                  Message <span className="text-gray-400">(optional)</span>
                </label>
                <textarea
                  id="transfer-message"
                  name="transfer_message"
                  rows={2}
                  className="mt-1 block w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
                  placeholder="Share any context you'd like them to know."
                />
              </div>
              <div className="flex justify-end">
                <SettingsActionButton type="submit" tone="primary">
                  Send Transfer Invite
                </SettingsActionButton>
              </div>
            </form>
          )}
        </section>

        <section className="px-4 py-5 sm:px-5">
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <div className="flex h-12 w-12 items-center justify-center rounded-full border border-rose-300/25 bg-rose-950/35 text-rose-200">
                <ShieldAlert className="w-6 h-6 text-red-600" aria-hidden="true" />
              </div>
            </div>
            <div className="flex-grow space-y-4">
              <div>
                <h3 className="text-lg font-bold text-red-800">Danger Zone</h3>
                <p className="text-sm text-red-700">Permanently delete this agent and all of its data. This action cannot be undone and will immediately stop any running tasks.</p>
              </div>
              <SettingsActionButton
                tone="danger"
                onClick={onDeleteAgent}
              >
                <Trash2 className="w-4 h-4" aria-hidden="true" />
                Delete Agent
              </SettingsActionButton>
              {deleteError ? <InlineStatusBanner variant="error" surface="embedded" density="compact">{deleteError}</InlineStatusBanner> : null}
            </div>
          </div>
        </section>
    </CollapsibleSettingsSection>
  )
}

type ConfirmActionDialogProps = ConfirmActionConfig & {
  onClose: () => void
}

function ConfirmActionDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'primary',
  onConfirm,
  onClose,
}: ConfirmActionDialogProps) {
  return (
    <AsyncActionConfirmDialog
      open
      title={title}
      onClose={onClose}
      description={typeof body === 'string' ? body : undefined}
      icon={tone === 'danger' ? Trash2 : Info}
      confirmLabel={confirmLabel}
      cancelLabel={cancelLabel}
      danger={tone === 'danger'}
      onConfirm={onConfirm ?? (() => {})}
      widthClass="sm:max-w-md"
      getErrorMessage={(error) => {
        console.error(error)
        return null
      }}
    >
      {typeof body === 'string' ? null : <div className="text-sm text-gray-600">{body}</div>}
    </AsyncActionConfirmDialog>
  )
}
