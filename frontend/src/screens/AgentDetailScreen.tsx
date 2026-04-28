import type { FormEvent, ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpFromLine,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  Copy,
  Folder,
  Info,
  KeyRound,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  ServerCog,
  ShieldAlert,
  Trash2,
  UserPlus,
  XCircle,
  Zap,
} from 'lucide-react'
import {
  ColorSwatch,
  ColorSwatchPicker,
  ColorSwatchPickerItem,
  Slider as AriaSlider,
  SliderThumb,
  SliderTrack,
  Switch as AriaSwitch,
} from 'react-aria-components'
import { Modal } from '../components/common/Modal'
import { AddCollaboratorModal } from '../components/agentSettings/AddCollaboratorModal'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { AgentIntelligenceSlider } from '../components/common/AgentIntelligenceSlider'
import { SaveBar } from '../components/common/SaveBar'
import { AddContactModal } from '../components/agentSettings/AddContactModal'
import { AllowlistContactsTable } from '../components/agentSettings/AllowlistContactsTable'
import { CollaboratorsTable } from '../components/agentSettings/CollaboratorsTable'
import type {
  AllowlistInput,
  AllowlistTableRow,
  CollaboratorTableRow,
  PendingAllowlistAction,
  PendingCollaboratorAction,
} from '../components/agentSettings/contactTypes'
import { useModal } from '../hooks/useModal'
import type { IntelligenceTierKey } from '../types/llmIntelligence'
import type {
  AgentColorOption,
  AgentDailyCreditsInfo as DailyCreditsInfo,
  AgentInboundWebhook,
  AgentOrganization,
  AgentSettingsData,
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

function resolveAgentColorHex(agentColorHex: string | null | undefined, palette: AgentColorOption[]): string {
  if (!palette.length) {
    return agentColorHex || ''
  }
  const normalized = (agentColorHex || '').toUpperCase()
  const match = palette.find((color) => color.hex.toUpperCase() === normalized)
  return match ? match.hex : palette[0].hex
}

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

export type AgentDetailScreenProps = {
  initialData: AgentSettingsData
}

type AgentSettingsWorkspaceVariant = 'standalone' | 'embedded'

export type AgentSettingsWorkspaceSavePayload = {
  agentId: string
  agentName: string
  agentAvatarUrl: string | null
  agentColorHex: string
  preferredLlmTier: IntelligenceTierKey
  organization: AgentOrganization
}

export type AgentSettingsWorkspaceProps = {
  initialData: AgentSettingsData
  variant?: AgentSettingsWorkspaceVariant
  onBack?: () => void
  onSaved?: (payload: AgentSettingsWorkspaceSavePayload) => void
  onDeleted?: () => void
  onOpenSecrets?: () => void
  onOpenEmailSettings?: () => void
  onOpenFiles?: () => void
  onReassigned?: (payload: {
    context?: { type: string; id: string; name?: string | null }
    redirect?: string | null
    organization?: AgentOrganization
  }) => void
}

type FormState = {
  name: string
  charter: string
  isActive: boolean
  dailyCreditInput: string
  sliderValue: number
  dedicatedProxyId: string
  preferredTier: IntelligenceTierKey
  agentColorHex: string
}

const generateTempId = () =>
  typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `temp-${Date.now()}-${Math.random().toString(36).slice(2)}`

const normalizeAllowlistAddress = (value: string) => value.trim().toLowerCase()

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
  const persistedKeys = new Set(persistedActions.map((action) => `${action.type}:${action.id}`))

  const next = pendingActions.filter((action) => {
    if (isCreatePendingAction(action)) {
      return !tempIds.has(action.tempId)
    }

    if (!('id' in action)) {
      return true
    }

    return !persistedKeys.has(`${action.type}:${action.id}`)
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
  return buildStagedRows({
    baseRows: [
      ...state.entries.map<AllowlistTableRow>((entry) => ({
        id: entry.id,
        kind: 'entry',
        channel: entry.channel,
        address: entry.address,
        allowInbound: entry.allowInbound,
        allowOutbound: entry.allowOutbound,
      })),
      ...state.pendingInvites.map<AllowlistTableRow>((invite) => ({
        id: invite.id,
        kind: 'invite',
        channel: invite.channel,
        address: invite.address,
        allowInbound: invite.allowInbound,
        allowOutbound: invite.allowOutbound,
      })),
    ],
    pendingActions,
    createRow: (action) => ({
      id: action.tempId,
      kind: 'entry',
      channel: action.channel,
      address: action.address,
      allowInbound: action.allowInbound,
      allowOutbound: action.allowOutbound,
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

export function AgentSettingsWorkspace({
  initialData,
  variant = 'standalone',
  onBack,
  onSaved,
  onDeleted,
  onOpenSecrets,
  onOpenEmailSettings,
  onOpenFiles,
  onReassigned,
}: AgentSettingsWorkspaceProps) {
  const isEmbedded = variant === 'embedded'
  const fallbackSliderMax = initialData.dailyCredits.sliderMax
  const fallbackSliderEmptyValue = initialData.dailyCredits.sliderEmptyValue ?? fallbackSliderMax
  const fallbackSliderLimitMax = initialData.dailyCredits.sliderLimitMax ?? fallbackSliderMax
  const sliderMin = initialData.dailyCredits.sliderMin
  const sliderStep = initialData.dailyCredits.sliderStep
  const standardSliderLimit = Number.isFinite(initialData.dailyCredits.standardSliderLimit)
    ? initialData.dailyCredits.standardSliderLimit
    : 20

  const initialFormState = useMemo<FormState>(
    () => ({
      name: initialData.agent.name,
      charter: initialData.agent.charter,
      isActive: initialData.agent.isActive,
      dailyCreditInput:
        typeof initialData.dailyCredits.limit === 'number' && Number.isFinite(initialData.dailyCredits.limit)
          ? String(Math.round(initialData.dailyCredits.limit))
          : '',
      sliderValue: initialData.dailyCredits.sliderValue ?? fallbackSliderEmptyValue,
      dedicatedProxyId: initialData.dedicatedIps.selectedId ?? '',
      preferredTier: (initialData.agent.preferredLlmTier || 'standard') as IntelligenceTierKey,
      agentColorHex: resolveAgentColorHex(initialData.agent.agentColorHex, initialData.agentColors),
    }),
    [
      initialData.agent.name,
      initialData.agent.charter,
      initialData.agent.isActive,
      initialData.agent.preferredLlmTier,
      initialData.agent.agentColorHex,
      initialData.agentColors,
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
  const tierMultiplierByKey = useMemo(() => {
    const map = new Map<IntelligenceTierKey, number>()
    for (const option of initialData.llmIntelligence?.options ?? []) {
      map.set(option.key, option.multiplier)
    }
    return map
  }, [initialData.llmIntelligence?.options])
  const hasTierMultipliers = tierMultiplierByKey.size > 0
  const getTierMultiplier = useCallback(
    (tier: IntelligenceTierKey) => {
      const value = tierMultiplierByKey.get(tier)
      if (!Number.isFinite(value) || !value || value <= 0) {
        return 1
      }
      return value
    },
    [tierMultiplierByKey],
  )
  const getSliderMetrics = useCallback(
    (tier: IntelligenceTierKey) => {
      const multiplier = hasTierMultipliers ? getTierMultiplier(tier) : 1
      const limitMax = hasTierMultipliers
        ? Math.max(sliderMin, Math.round(standardSliderLimit * multiplier))
        : fallbackSliderLimitMax
      const max = hasTierMultipliers ? limitMax + sliderStep : fallbackSliderMax
      const emptyValue = hasTierMultipliers ? max : fallbackSliderEmptyValue
      return { limitMax, max, emptyValue }
    },
    [
      fallbackSliderEmptyValue,
      fallbackSliderLimitMax,
      fallbackSliderMax,
      getTierMultiplier,
      hasTierMultipliers,
      sliderMin,
      sliderStep,
      standardSliderLimit,
    ],
  )
  const { limitMax: sliderLimitMax, max: sliderMax, emptyValue: sliderEmptyValue } = getSliderMetrics(
    formState.preferredTier,
  )

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
      formState.isActive !== savedFormState.isActive ||
      formState.dailyCreditInput !== savedFormState.dailyCreditInput ||
      formState.sliderValue !== savedFormState.sliderValue ||
      formState.dedicatedProxyId !== savedFormState.dedicatedProxyId ||
      formState.preferredTier !== savedFormState.preferredTier ||
      formState.agentColorHex !== savedFormState.agentColorHex ||
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
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
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
      formData.append('action', action.type === 'cancel_invite' ? 'cancel_invite' : action.type === 'remove' ? 'remove_allowlist' : 'add_allowlist')
      if (action.type === 'create') {
        formData.append('channel', action.channel)
        formData.append('address', action.address)
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
        if (serverTierRaw && (initialData.llmIntelligence?.options ?? []).some((option) => option.key === serverTierRaw)) {
          const serverTier = serverTierRaw as IntelligenceTierKey
          if (serverTier !== nextFormState.preferredTier) {
            const wasUnlimited = nextFormState.sliderValue === sliderEmptyValue
            const { max: nextMax, emptyValue: nextEmptyValue } = getSliderMetrics(serverTier)
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
        agentColorHex: nextSavedFormState.agentColorHex,
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
    getSliderMetrics,
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

  const clampSlider = useCallback(
    (value: number) => {
      return Math.min(Math.max(Number.isFinite(value) ? value : sliderEmptyValue, sliderMin), sliderMax)
    },
    [sliderEmptyValue, sliderMax, sliderMin],
  )

  const updateSliderValue = useCallback(
    (value: number) => {
      const normalized = clampSlider(value)
      setFormState((prev) => ({
        ...prev,
        sliderValue: normalized,
        dailyCreditInput: normalized === sliderEmptyValue ? '' : String(Math.round(normalized)),
      }))
    },
    [clampSlider, sliderEmptyValue],
  )

  const handleTierChange = useCallback(
    (tier: IntelligenceTierKey) => {
      setFormState((prev) => {
        if (tier === prev.preferredTier) {
          return prev
        }
        const previousMultiplier = hasTierMultipliers ? getTierMultiplier(prev.preferredTier) : 1
        const nextMultiplier = hasTierMultipliers ? getTierMultiplier(tier) : 1
        const { emptyValue: currentEmptyValue } = getSliderMetrics(prev.preferredTier)
        const { limitMax: nextSliderLimitMax, emptyValue: nextSliderEmptyValue } = getSliderMetrics(tier)
        const isUnlimited = prev.sliderValue >= currentEmptyValue || !prev.dailyCreditInput.trim()

        if (isUnlimited) {
          return {
            ...prev,
            preferredTier: tier,
            sliderValue: nextSliderEmptyValue,
            dailyCreditInput: '',
          }
        }

        let scaledValue = prev.sliderValue
        if (previousMultiplier > 0 && nextMultiplier > 0 && Number.isFinite(prev.sliderValue)) {
          scaledValue = Math.round((prev.sliderValue * nextMultiplier) / previousMultiplier)
        }

        if (!Number.isFinite(scaledValue) || scaledValue <= 0 || scaledValue > nextSliderLimitMax) {
          return {
            ...prev,
            preferredTier: tier,
            sliderValue: nextSliderEmptyValue,
            dailyCreditInput: '',
          }
        }

        if (scaledValue < sliderMin) {
          scaledValue = sliderMin
        }

        return {
          ...prev,
          preferredTier: tier,
          sliderValue: scaledValue,
          dailyCreditInput: String(Math.round(scaledValue)),
        }
      })
    },
    [
      getSliderMetrics,
      getTierMultiplier,
      hasTierMultipliers,
      sliderMin,
    ],
  )

  const handleDailyCreditInputChange = useCallback(
    (value: string) => {
      setFormState((prev) => ({ ...prev, dailyCreditInput: value }))
      if (!value.trim()) {
        updateSliderValue(sliderEmptyValue)
        return
      }
      const numeric = Number(value)
      if (!Number.isFinite(numeric)) {
        updateSliderValue(sliderEmptyValue)
        return
      }
      const clamped = Math.min(Math.max(Math.round(numeric), sliderMin), sliderLimitMax)
      updateSliderValue(clamped)
    },
    [sliderEmptyValue, sliderLimitMax, sliderMin, updateSliderValue],
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
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData,
        })
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Reassignment failed. Please try again.')
        }
        if (variant === 'embedded') {
          onReassigned?.({
            context: data.context as { type: string; id: string; name?: string | null } | undefined,
            redirect: (data.redirect as string | null | undefined) ?? null,
            organization: (data.organization as AgentOrganization | undefined) ?? null,
          })
          return
        }
        if (data.redirect) {
          window.location.href = data.redirect as string
          return
        }
        window.location.reload()
      } catch (error) {
        setReassignError(error instanceof Error ? error.message : 'An unexpected error occurred.')
      } finally {
        setReassigning(false)
      }
    },
    [initialData.csrfToken, initialData.urls.detail, onReassigned, variant],
  )

  const deleteAgent = useCallback(async () => {
    setDeleteError(null)
    try {
      const response = await fetch(initialData.urls.delete, {
        method: 'DELETE',
        headers: {
          'X-CSRFToken': initialData.csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
      })
      if (!response.ok) {
        const message = (await response.text())?.trim()
        throw new Error(message || 'Failed to delete agent. Please try again.')
      }
      if (variant === 'embedded') {
        onDeleted?.()
        return
      }
      const redirectTarget = response.headers.get('HX-Redirect') || initialData.urls.list
      window.location.assign(redirectTarget)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to delete agent. Please try again.'
      setDeleteError(message)
      throw error
    }
  }, [initialData.csrfToken, initialData.urls.delete, initialData.urls.list, onDeleted, variant])

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

  const sectionClassName = isEmbedded
    ? 'group rounded-none border-0 bg-transparent shadow-none'
    : 'gobii-card-base group'
  const sectionSummaryClassName = isEmbedded
    ? 'flex cursor-pointer list-none items-center justify-between gap-3 border-b border-slate-200/70 px-0 pb-4'
    : 'flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none'
  const sectionBodyClassName = isEmbedded ? 'px-0 pt-5' : 'p-6 sm:p-8'
  const stackedSectionBodyClassName = isEmbedded ? 'px-0 pt-5 space-y-6' : 'p-6 sm:p-8 space-y-6'
  const embeddedUtilityLinkClassName = 'inline-flex items-center gap-2 rounded-xl border border-slate-200/25 bg-slate-900/35 px-3 py-2 text-sm font-medium text-slate-100 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white'
  const embeddedNeutralButtonClassName = 'inline-flex items-center gap-2 rounded-lg border border-slate-200/25 bg-slate-900/35 px-3 py-2 text-sm font-semibold text-slate-100 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white'
  const standaloneHeaderActionClassName = 'inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-800 shadow-sm transition-colors hover:bg-blue-50'
  const embeddedHeaderActions = (
    <>
      {onOpenSecrets ? (
        <button
          type="button"
          onClick={onOpenSecrets}
          className={embeddedUtilityLinkClassName}
        >
          <KeyRound className="h-4 w-4" aria-hidden="true" />
          Secrets
        </button>
      ) : (
        <a
          href={initialData.urls.secrets}
          className={embeddedUtilityLinkClassName}
        >
          <KeyRound className="h-4 w-4" aria-hidden="true" />
          Secrets
        </a>
      )}
      {onOpenEmailSettings ? (
        <button
          type="button"
          onClick={onOpenEmailSettings}
          className={embeddedUtilityLinkClassName}
        >
          <Mail className="h-4 w-4" aria-hidden="true" />
          Email Settings
        </button>
      ) : (
        <a
          href={initialData.urls.emailSettings}
          className={embeddedUtilityLinkClassName}
        >
          <Mail className="h-4 w-4" aria-hidden="true" />
          Email Settings
        </a>
      )}
      {onOpenFiles ? (
        <button
          type="button"
          onClick={onOpenFiles}
          className={embeddedUtilityLinkClassName}
        >
          <Folder className="h-4 w-4" aria-hidden="true" />
          Manage Files
        </button>
      ) : (
        <a
          href={initialData.urls.manageFiles}
          className={embeddedUtilityLinkClassName}
        >
          <Folder className="h-4 w-4" aria-hidden="true" />
          Manage Files
        </a>
      )}
    </>
  )
  const standaloneHeaderActions = (
    <>
      <a
        href={initialData.urls.chat}
        className={standaloneHeaderActionClassName}
      >
        <MessageSquare className="w-4 h-4" aria-hidden="true" />
        Web Chat
      </a>
      <a
        href={initialData.urls.secrets}
        className={standaloneHeaderActionClassName}
      >
        <KeyRound className="w-4 h-4" aria-hidden="true" />
        Secrets
      </a>
      <a
        href={initialData.urls.emailSettings}
        className={standaloneHeaderActionClassName}
      >
        <Mail className="w-4 h-4" aria-hidden="true" />
        Email Settings
      </a>
      <a
        href={initialData.urls.manageFiles}
        className={standaloneHeaderActionClassName}
      >
        <Folder className="w-4 h-4" aria-hidden="true" />
        Manage Files
      </a>
    </>
  )

  return (
    <div className={isEmbedded ? 'space-y-6 pb-24' : 'space-y-6 pb-6'}>
      {isEmbedded ? (
        <SettingsBanner
          variant="embedded"
          leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to gallery" />}
          eyebrow="Agent settings"
          title={(formState.name || 'Agent').trim()}
          headingId="agent-name-heading"
          actions={embeddedHeaderActions}
        />
      ) : (
        <SettingsBanner
          variant="standalone"
          title={`${(formState.name || 'Agent').trim()} Settings`}
          subtitle="Manage your agent settings and preferences"
          supportingContent={(
            <a
              href={initialData.urls.list}
              className="group inline-flex items-center gap-2 text-sm text-blue-600 transition-colors hover:text-blue-800"
            >
              <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" aria-hidden="true" />
              Back to Agents
            </a>
          )}
          headingId="agent-name-heading"
          actions={standaloneHeaderActions}
        />
      )}

      {initialData.agent.pendingTransfer && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl shadow-md px-5 py-4 flex flex-col gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Info className="w-4 h-4" aria-hidden="true" />
            Transfer pending
          </div>
          <p className="text-sm leading-5">
            This agent is awaiting acceptance from <strong>{initialData.agent.pendingTransfer.toEmail}</strong> (sent {initialData.agent.pendingTransfer.createdAtDisplay}).
            You can continue editing settings, but keep in mind the new owner will take control once they accept.
          </p>
        </div>
      )}

      {saveNotice && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl px-5 py-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 mt-0.5" aria-hidden="true" />
            <div className="text-sm leading-5">{saveNotice}</div>
          </div>
          <button
            type="button"
            onClick={() => setSaveNotice(null)}
            className="shrink-0 inline-flex items-center justify-center rounded-lg border border-amber-200 bg-white px-2 py-2 text-amber-900 hover:bg-amber-100/40 transition-colors"
            aria-label="Dismiss notice"
          >
            <XCircle className="w-5 h-5" aria-hidden="true" />
          </button>
        </div>
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
        <details className={sectionClassName} id="agent-identity" {...(isEmbedded ? {} : { open: true })}>
          <summary className={sectionSummaryClassName}>
            <div>
              <h2 className="text-lg font-semibold text-gray-800">General Settings</h2>
              <p className="text-sm text-gray-500">Core configuration and runtime controls.</p>
            </div>
            <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
          </summary>
          <div className={sectionBodyClassName}>
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
                      <button
                        type="button"
                        onClick={() => avatarInputRef.current?.click()}
                        className={isEmbedded ? embeddedNeutralButtonClassName : 'inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-800 shadow-sm transition-colors hover:border-blue-300 hover:text-blue-700'}
                      >
                        <ArrowUpFromLine className="h-4 w-4" aria-hidden="true" />
                        Upload
                      </button>
                      {(avatarPreviewUrl || savedAvatarUrl || avatarFile) && (
                        <button
                          type="button"
                          onClick={handleAvatarRemove}
                          className={isEmbedded ? 'inline-flex items-center gap-2 rounded-lg border border-red-300/70 bg-transparent px-3 py-2 text-sm font-semibold text-red-700 transition-colors hover:border-red-400' : 'inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 shadow-sm transition-colors hover:border-red-300'}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                          Remove
                        </button>
                      )}
                    </div>
                    <p className="text-xs text-gray-500">Use a square image (PNG, JPG, WebP, or GIF). Max 5 MB.</p>
                  </div>
                </div>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Theme color</span>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9">
                <input type="hidden" name="agent_color_hex" value={formState.agentColorHex} />
                <AgentColorPicker
                  colors={initialData.agentColors}
                  selectedHex={formState.agentColorHex}
                  embedded={isEmbedded}
                  onChange={(hex) => setFormState((prev) => ({ ...prev, agentColorHex: hex }))}
                />
                <p className="mt-2 text-xs text-gray-500">Choose the accent color used across agent chat and cards.</p>
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
                <div className={isEmbedded ? 'flex flex-col gap-4 rounded-lg border border-slate-200/70 bg-transparent p-4 lg:flex-row lg:items-center lg:justify-between lg:gap-6' : 'flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between lg:gap-6 p-4 border border-gray-200 rounded-lg bg-gray-50/60'}>
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
                  <AriaSwitch
                    name="is_active"
                    value="true"
                    aria-label="Toggle agent status"
                    isSelected={formState.isActive}
                    onChange={(isSelected) => setFormState((prev) => ({ ...prev, isActive: isSelected }))}
                    className="relative inline-flex h-6 w-11 cursor-pointer items-center focus:outline-none"
                  >
                    {({ isSelected, isFocusVisible }) => (
                      <>
                        <span
                          aria-hidden="true"
                          className={`h-6 w-11 rounded-full transition ${isSelected ? 'bg-blue-600' : 'bg-gray-200'}`}
                        />
                        <span
                          aria-hidden="true"
                          className={`absolute left-1 top-1 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                            isSelected ? 'translate-x-5' : 'translate-x-0'
                          }`}
                        />
                        {isFocusVisible && <span className="absolute -inset-1 rounded-full ring-2 ring-blue-300" aria-hidden="true" />}
                      </>
                    )}
                  </AriaSwitch>
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
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Daily Task Credits</span>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9 space-y-4">
                <DailyCreditSummary dailyCredits={initialData.dailyCredits} embedded={isEmbedded} formatNumber={formatNumber} />
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-3">
                    <label htmlFor="daily-credit-limit-slider" className="inline-block text-sm font-medium text-gray-700">
                      Soft target (credits/day)
                    </label>
                    <input type="hidden" name="daily_credit_limit_slider" value={formState.sliderValue} />
                    <AriaSlider
                      aria-label="Soft target slider"
                      id="daily-credit-limit-slider"
                      className="mt-2 space-y-3"
                      minValue={sliderMin}
                      maxValue={sliderMax}
                      step={sliderStep}
                      value={formState.sliderValue}
                      onChange={(value: number | number[]) => {
                        const numeric = Array.isArray(value) ? value[0] : value
                        if (typeof numeric === 'number') {
                          updateSliderValue(numeric)
                        }
                      }}
                    >
                      <SliderTrack className="relative h-2 rounded-full bg-gray-200">
                        {({ state }) => {
                          const percent = Math.min(Math.max(state.getThumbPercent(0) * 100, 0), 100)
                          return (
                            <>
                              <div className="absolute inset-y-0 left-0 rounded-full bg-indigo-500" style={{ width: `${percent}%` }} />
                              <SliderThumb
                                index={0}
                                className="absolute top-1/2 h-5 w-5 -translate-y-1/2 rounded-full border-2 border-white bg-indigo-600 shadow transition focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 data-[dragging]:scale-105"
                              />
                            </>
                          )
                        }}
                      </SliderTrack>
                    </AriaSlider>
                    <div className="flex items-center justify-between text-xs font-medium text-gray-600">
                      <span>
                        {formState.sliderValue === sliderEmptyValue
                          ? 'Unlimited'
                          : `${Math.round(formState.sliderValue).toLocaleString()} credits/day`}
                      </span>
                      <span>Unlimited</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        id="daily-credit-limit-input"
                        name="daily_credit_limit"
                        type="number"
                        step="1"
                        min={sliderMin}
                        max={sliderLimitMax}
                        value={formState.dailyCreditInput}
                        onChange={(event) => handleDailyCreditInputChange(event.target.value)}
                        className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                        placeholder="Unlimited"
                      />
                      <span className="text-sm text-gray-500">credits/day</span>
                    </div>
                    <p className="mt-1 text-xs text-gray-500">Soft target controls pacing for this agent. Leave the number blank for unlimited.</p>
                  </div>
                </div>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Dedicated IPs</span>
              </div>
              <div className="sm:col-span-9">
                <DedicatedIpSummary
                  dedicatedIps={initialData.dedicatedIps}
                  embedded={isEmbedded}
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
          </div>
        </details>
      </form>

      {variant === 'standalone' ? (
        <SaveBar
          id="agent-save-bar"
          visible={hasAnyChanges}
          onCancel={handleResetAll}
          onSave={handleSaveAll}
          busy={saving}
          error={saveError}
        />
      ) : null}

      <details className={sectionClassName} id="agent-contact-controls">
        <summary className={sectionSummaryClassName}>
          <div>
            <h2 className="text-lg font-semibold text-gray-800">Contacts &amp; Access</h2>
            <p className="text-sm text-gray-500">Contact endpoints and allowlist management.</p>
          </div>
          <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
        </summary>
        <div className={stackedSectionBodyClassName}>
          <PrimaryContacts
            primaryEmail={initialData.primaryEmail}
            primarySms={initialData.primarySms}
            emailSettingsUrl={initialData.urls.emailSettings}
            smsEnableUrl={initialData.urls.smsEnable}
            embedded={isEmbedded}
            onOpenEmailSettings={onOpenEmailSettings}
          />

          {initialData.allowlist.show && (
            <AllowlistManager
              embedded={isEmbedded}
              state={savedAllowlistState}
              rows={allowlistRows}
              projectedSlotsUsed={projectedContactSlots}
              saving={saving}
              onAddContact={openAddContactModal}
              onRemoveRows={confirmAllowlistRemoval}
              contactRequestsUrl={initialData.urls.contactRequests}
            />
          )}

          <CollaboratorManager
            embedded={isEmbedded}
            state={savedCollaboratorState}
            rows={collaboratorRows}
            projectedTotalCount={projectedCollaboratorTotalCount}
            error={collaboratorError}
            busy={saving}
            onAdd={openAddCollaboratorModal}
            onRemove={stageCollaboratorRemove}
            onConfirmAction={openConfirmAction}
          />
        </div>
      </details>

      <IntegrationsSection
        variant={variant}
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
        variant={variant}
        agent={initialData.agent}
        features={initialData.features}
        reassignment={initialData.reassignment}
        selectedOrgId={selectedOrgId}
        onOrgChange={setSelectedOrgId}
        onReassign={handleReassign}
        reassignError={reassignError}
        reassigning={reassigning}
        onDeleteAgent={confirmDeleteAgent}
        deleteError={deleteError}
      />

      {isEmbedded && hasAnyChanges ? (
        <div className="sticky bottom-4 z-20">
          <div className="rounded-2xl border border-slate-300/70 bg-transparent px-4 py-3 shadow-none">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-900">You have unsaved changes</p>
                {saveError ? (
                  <p className="mt-1 text-xs text-red-600">{saveError}</p>
                ) : (
                  <p className="mt-1 text-xs text-slate-500">Save now to update the chat shell and gallery immediately.</p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={handleResetAll}
                  className="inline-flex items-center gap-2 rounded-xl border border-slate-300/70 bg-transparent px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:border-slate-400 hover:text-slate-900"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveAll}
                  disabled={saving}
                  className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-800 disabled:opacity-60"
                >
                  <Check className="h-4 w-4" aria-hidden="true" />
                  {saving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {modal}
    </div>
  )
}

export function AgentDetailScreen({ initialData }: AgentDetailScreenProps) {
  return <AgentSettingsWorkspace initialData={initialData} variant="standalone" />
}

type DailyCreditSummaryProps = {
  dailyCredits: DailyCreditsInfo
  embedded?: boolean
  formatNumber: (value: number | null, fractionDigits?: number) => string | null
}

function DailyCreditSummary({ dailyCredits, embedded = false, formatNumber }: DailyCreditSummaryProps) {
  const usageDisplay = formatNumber(dailyCredits.usage, 2)
  const limitDisplay = dailyCredits.limit === null ? 'Unlimited' : formatNumber(dailyCredits.limit, 0)
  const softRemaining = formatNumber(dailyCredits.softRemaining, 2)
  const hardRemaining = formatNumber(dailyCredits.remaining, 2)

  return (
    <div className={embedded ? 'space-y-4 rounded-lg border border-slate-200/70 bg-transparent p-4' : 'p-4 border border-gray-200 rounded-lg bg-white/70 space-y-4'}>
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
  embedded?: boolean
  organizationName: string | null
  selectedValue: string
  onChange: (value: string) => void
}

type AgentColorPickerProps = {
  colors: AgentColorOption[]
  selectedHex: string
  embedded?: boolean
  onChange: (hex: string) => void
}

function AgentColorPicker({ colors, selectedHex, embedded = false, onChange }: AgentColorPickerProps) {
  if (!colors.length) {
    return <p className="text-xs text-gray-500">No theme colors are available right now.</p>
  }

  const normalizedSelected = selectedHex.toUpperCase()
  const resolvedHex = colors.some((color) => color.hex.toUpperCase() === normalizedSelected)
    ? selectedHex
    : colors[0].hex

  return (
    <ColorSwatchPicker
      value={resolvedHex}
      onChange={(color) => onChange(color.toString('hex'))}
      layout="grid"
      className="grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-8"
      aria-label="Agent theme color"
    >
      {colors.map((color) => (
        <ColorSwatchPickerItem
          key={color.id}
          color={color.hex}
          className={({ isSelected, isFocusVisible, isHovered, isDisabled }) =>
            [
              'relative flex items-center justify-center rounded-md border p-1 transition',
              'size-9 sm:size-10',
              isSelected
                ? `border-blue-500 ${embedded ? 'bg-transparent' : 'bg-blue-50/60'}`
                : `${embedded ? 'border-slate-200 bg-transparent' : 'border-gray-200 bg-white'}`,
              isHovered && !isSelected ? 'border-blue-300' : '',
              isDisabled ? 'opacity-60' : '',
              isFocusVisible ? `ring-2 ring-blue-300 ring-offset-2 ${embedded ? 'ring-offset-transparent' : 'ring-offset-white'}` : '',
            ]
              .filter(Boolean)
              .join(' ')
          }
        >
          {({ isSelected }) => (
            <>
              <ColorSwatch className="h-5 w-5 rounded-full border border-slate-300 sm:h-6 sm:w-6" />
              {isSelected && (
                <span className={embedded ? 'absolute right-1 top-1 rounded-full bg-slate-900 p-0.5 text-white' : 'absolute right-1 top-1 rounded-full bg-white/80 p-0.5 text-blue-600'}>
                  <Check className="h-3 w-3" aria-hidden="true" />
                </span>
              )}
            </>
          )}
        </ColorSwatchPickerItem>
      ))}
    </ColorSwatchPicker>
  )
}

function DedicatedIpSummary({ dedicatedIps, embedded = false, organizationName, selectedValue, onChange }: DedicatedIpSummaryProps) {
  return (
    <div className="text-sm text-gray-600 space-y-4" data-dedicated-ip-total={dedicatedIps.total}>
      <p className="text-sm text-gray-500">Monitor and assign dedicated IP addresses reserved for this account.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className={embedded ? 'rounded-lg border border-slate-200/70 bg-transparent p-4' : 'border border-gray-200 rounded-lg bg-gray-50 p-4'}>
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
        <div className={embedded ? 'rounded-lg border border-slate-200/70 bg-transparent p-4' : 'border border-gray-200 rounded-lg bg-gray-50 p-4'}>
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
  smsEnableUrl: string | null
  embedded?: boolean
  onOpenEmailSettings?: () => void
}

function PrimaryContacts({
  primaryEmail,
  primarySms,
  emailSettingsUrl,
  smsEnableUrl,
  embedded = false,
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
              className={embedded ? 'block w-full rounded-lg border border-slate-200/70 bg-transparent px-3 py-2 text-sm shadow-none' : 'py-2 px-3 block w-full border-gray-200 bg-gray-100 shadow-sm rounded-lg text-sm'}
            />
            <p className="mt-2 text-xs text-gray-500">The agent's primary email address for communication.</p>
            <div className="mt-2 space-y-1">
              {manageEmailSettingsLink}
              {!primarySms && smsEnableUrl && (
                <div>
                  <a href={smsEnableUrl} className="text-sm text-blue-600 hover:text-blue-800">
                    Enable SMS
                  </a>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className={embedded ? 'rounded border border-dashed border-slate-300 px-3 py-2 text-sm text-gray-600 bg-transparent' : 'py-2 px-3 text-sm text-gray-600 bg-gray-50 border border-dashed border-gray-300 rounded'}>
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
              className={embedded ? 'block w-full rounded-lg border border-slate-200/70 bg-transparent px-3 py-2 text-sm shadow-none' : 'py-2 px-3 block w-full border-gray-200 bg-gray-100 shadow-sm rounded-lg text-sm'}
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
  saving: boolean
  onAddContact: () => void
  onRemoveRows: (rows: AllowlistTableRow[]) => void
  contactRequestsUrl: string
  embedded?: boolean
}

function AllowlistManager({ state, rows, projectedSlotsUsed, saving, onAddContact, onRemoveRows, contactRequestsUrl, embedded = false }: AllowlistManagerProps) {
  const contactCapReached = typeof state.maxContacts === 'number' && state.maxContacts > 0 && projectedSlotsUsed >= state.maxContacts
  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="text-xs text-gray-500">
          By default, the agent owner and organization members can communicate with this agent. You can add additional contacts below.
          Note: Multi-recipient messaging is limited to email only.
        </p>
        <p className="text-xs text-slate-600">Contact slots include allowlist entries and collaborators.</p>
      </div>

      {!state.emailVerified && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <Mail className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <div className="text-sm text-amber-800">
            <span className="font-medium">Email verification required.</span>{' '}
            External contacts won't be able to reach your agent until you{' '}
            <a href="/accounts/email/" className="underline hover:text-amber-900">verify your email address</a>.
          </div>
        </div>
      )}

      {state.pendingContactRequests > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-amber-600" aria-hidden="true" />
              <span className="text-sm font-medium text-amber-800">
                {state.pendingContactRequests} Contact Request{state.pendingContactRequests === 1 ? '' : 's'} Pending
              </span>
            </div>
            <a href={contactRequestsUrl} className="text-sm font-medium text-amber-700 hover:text-amber-900 underline">
              Review
            </a>
          </div>
        </div>
      )}

      {(state.ownerEmail || state.ownerPhone) && (
        <div className={embedded ? 'rounded-xl border border-slate-200/70 bg-transparent px-4 py-4' : 'rounded-xl border border-slate-200 bg-white px-4 py-4'}>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Owner Endpoints</div>
          <p className="mt-1 text-xs text-slate-600">Owner endpoints are always allowed in default mode.</p>
          {state.ownerEmail && (
            <div className="mt-3 flex items-center gap-2 text-sm text-slate-700">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
                <Mail className="w-4 h-4" aria-hidden="true" />
              </span>
              <span className="font-medium">{state.ownerEmail}</span>
            </div>
          )}
          {state.ownerPhone && (
            <div className="mt-3 flex items-center gap-2 text-sm text-slate-700">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
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
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add Contact
            </button>
          </div>
        </div>

        <AllowlistContactsTable
          embedded={embedded}
          rows={rows}
          disabled={saving}
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
  embedded?: boolean
}

function CollaboratorManager({ state, rows, projectedTotalCount, error, busy, onAdd, onRemove, onConfirmAction, embedded = false }: CollaboratorManagerProps) {
  const canManage = state.canManage
  const totalLimit = state.maxContacts ?? 'Unlimited'

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="text-xs text-slate-600">
          Invite coworkers to chat and exchange files. Collaborators can upload and download files only.
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
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-50"
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
          embedded={embedded}
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
  variant?: AgentSettingsWorkspaceVariant
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
  variant = 'standalone',
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
  const embedded = variant === 'embedded'
  const sectionClassName = embedded ? 'group rounded-none border-0 bg-transparent shadow-none' : 'gobii-card-base group'
  const summaryClassName = embedded
    ? 'flex cursor-pointer list-none items-center justify-between gap-3 border-b border-slate-200/70 px-0 pb-4'
    : 'flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none'
  const wrapperClassName = embedded ? 'divide-y divide-slate-200/70' : 'divide-y divide-gray-200/70'
  const sectionBodyClassName = embedded ? 'space-y-6 px-0 py-5' : 'p-6 sm:p-8 space-y-6'
  const cardClassName = embedded ? 'rounded-xl border border-slate-200/70 bg-transparent p-4 space-y-4' : 'border border-gray-200 rounded-xl bg-white p-4 space-y-4'
  const tableWrapperClassName = embedded ? 'overflow-hidden rounded-xl border border-slate-200/70 bg-transparent' : 'overflow-hidden border border-gray-200 rounded-xl'
  const tableHeadClassName = embedded ? 'bg-transparent' : 'bg-gray-50'
  const tableBodyClassName = embedded ? 'bg-transparent divide-y divide-gray-200' : 'bg-white divide-y divide-gray-200'
  const neutralButtonClassName = embedded
    ? 'inline-flex items-center gap-2 rounded-md border border-slate-300/70 bg-transparent px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-slate-100/60 disabled:opacity-50'
    : 'inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50'
  const emptyStateClassName = embedded
    ? 'rounded-xl border border-dashed border-slate-300 px-4 py-4 text-sm text-gray-600 bg-transparent'
    : 'p-4 bg-gray-50 border border-dashed border-gray-300 rounded-xl text-sm text-gray-600'

  return (
    <details className={sectionClassName} id="agent-integrations">
      <summary className={summaryClassName}>
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Integrations</h2>
          <p className="text-sm text-gray-500">MCP servers, peer links, and webhooks.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className={wrapperClassName}>
        <section className={sectionBodyClassName}>
        <div>
          <h3 className="text-base font-semibold text-gray-800">MCP Servers</h3>
          <p className="text-sm text-gray-500">
            Platform MCP servers are always enabled. Enable or disable organization servers per agent, and configure optional personal
            servers when applicable.
          </p>
        </div>

        {mcpServers.inherited.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-700">Inherited Servers</h4>
              <ul className="space-y-2">
                {mcpServers.inherited.map((server) => (
                  <li key={server.id} className={embedded ? 'flex items-start justify-between gap-3 rounded-lg border border-slate-200/70 bg-transparent px-4 py-3' : 'flex items-start justify-between gap-3 border border-gray-200 bg-gray-50 rounded-lg px-4 py-3'}>
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
              <h4 className="text-sm font-semibold text-gray-700">Organization Servers</h4>
              {isOrgAgent ? (
                <div className="grid gap-3 md:grid-cols-2">
                  {mcpServers.organization.map((server) => {
                    const checked = selectedOrgServers.has(server.id)
                    return (
                      <label key={server.id} className={embedded ? 'flex items-start gap-3 rounded-lg border border-slate-200/70 bg-transparent px-3 py-3' : 'flex items-start gap-3 border border-gray-200 rounded-lg px-3 py-3'}>
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
                <p className="text-sm text-gray-500">Organization MCP servers can be managed when the agent belongs to an organization.</p>
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
                      <label key={server.id} className={embedded ? 'flex items-start gap-3 rounded-lg border border-slate-200/70 bg-transparent px-3 py-3' : 'flex items-start gap-3 border border-gray-200 rounded-lg px-3 py-3'}>
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
                    <a
                      href={mcpServers.manageUrl}
                      className={embedded ? 'inline-flex items-center gap-2 rounded-lg border border-slate-300/70 bg-transparent px-3 py-2 text-sm font-medium text-gray-800 transition-colors hover:border-slate-400 hover:text-slate-900' : 'inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm transition-colors hover:bg-gray-50'}
                    >
                      <ServerCog className="h-4 w-4" aria-hidden="true" />
                      Manage All Servers
                    </a>
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
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg shadow-sm hover:bg-blue-700 disabled:opacity-50"
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
                          {pendingLabel && <div className="text-xs text-amber-600">{pendingLabel}</div>}
                          <div className="text-xs mt-1">
                            Status:{' '}
                            <span className={entry.isEnabled ? 'text-green-600' : 'text-gray-500'}>
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
                              className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-red-600 border border-red-200 rounded-md hover:bg-red-50"
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
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg shadow-sm hover:bg-blue-700"
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
                            {pendingLabel && <span className="text-xs text-amber-600">{pendingLabel}</span>}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600 break-all">{webhook.url}</td>
                        <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => onWebhookEdit(webhook)}
                              className={embedded ? 'inline-flex items-center gap-1.5 rounded-md border border-slate-300/70 bg-transparent px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-slate-100/60' : 'inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50'}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-red-200 text-red-600 hover:bg-red-50"
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
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg shadow-sm hover:bg-blue-700"
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
                            {pendingLabel && <span className="text-xs text-amber-600">{pendingLabel}</span>}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600">
                          <div className={embedded ? 'flex min-w-0 items-stretch overflow-hidden rounded-lg border border-slate-200/70 bg-transparent' : 'flex min-w-0 items-stretch overflow-hidden rounded-lg border border-gray-200'}>
                            <input
                              type="text"
                              value={webhook.url ?? ''}
                              readOnly
                              placeholder="URL available after save"
                              aria-label={`Webhook URL for ${webhook.name}`}
                              onFocus={(event) => event.currentTarget.select()}
                              className={embedded ? 'min-w-0 flex-1 border-0 bg-transparent px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:ring-0' : 'min-w-0 flex-1 border-0 bg-gray-50 px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:ring-0'}
                            />
                            <button
                              type="button"
                              onClick={() => onInboundWebhookCopy(webhook)}
                              disabled={!webhook.url}
                              className={embedded ? 'inline-flex shrink-0 items-center gap-1.5 border-l border-slate-200/70 bg-transparent px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-slate-100/60 disabled:cursor-not-allowed disabled:text-gray-400' : 'inline-flex shrink-0 items-center gap-1.5 border-l border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400'}
                            >
                              {copiedInboundWebhookId === webhook.id ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : <Copy className="w-3.5 h-3.5" aria-hidden="true" />}
                              {copyLabel}
                            </button>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">
                          <span className={webhook.isActive ? 'text-green-600' : 'text-gray-500'}>
                            {webhook.isActive ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">{lastTriggeredLabel}</td>
                        <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => onInboundWebhookEdit(webhook)}
                              className={embedded ? 'inline-flex items-center gap-1.5 rounded-md border border-slate-300/70 bg-transparent px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-slate-100/60' : 'inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50'}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-amber-200 text-amber-700 hover:bg-amber-50 disabled:opacity-50"
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
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-red-200 text-red-600 hover:bg-red-50"
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
      </div>
    </details>
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
    <Modal
      title={isCreate ? 'Add Peer Link' : 'Edit Peer Link'}
      subtitle={isCreate ? 'Select an agent and quota limits.' : 'Adjust quota and feature flag controls for this link.'}
      onClose={onClose}
      widthClass="sm:max-w-lg"
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        {isCreate ? (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Agent</label>
            <select
              value={peerAgentId}
              onChange={(event) => setPeerAgentId(event.target.value)}
              className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
              disabled={candidates.length === 0}
            >
              <option value="">Select an agent...</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </select>
            {candidates.length === 0 && <p className="text-xs text-gray-500 mt-1">No additional eligible agents available.</p>}
          </div>
        ) : (
          <div>
            <span className="block text-sm font-medium text-gray-700">Agent</span>
            <p className="mt-1 text-sm text-gray-600">{entry?.counterpartName ?? '(Agent unavailable)'}</p>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Messages per Window</label>
          <input
            type="number"
            min="1"
            value={messagesInput}
            onChange={(event) => setMessagesInput(event.target.value)}
            className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Window Hours</label>
          <input
            type="number"
            min="1"
            value={windowInput}
            onChange={(event) => setWindowInput(event.target.value)}
            className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
          />
        </div>

        {!isCreate && (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Feature Flag</label>
              <input
                type="text"
                value={featureFlag}
                onChange={(event) => setFeatureFlag(event.target.value)}
                placeholder="optional"
                className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
              />
            </div>
            <label className="inline-flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={isEnabled}
                onChange={(event) => setIsEnabled(event.target.checked)}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span>Link enabled</span>
            </label>
          </>
        )}

        <div className="flex items-center justify-end gap-3 pt-2">
          <button type="button" className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={isCreate && !peerAgentId}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60"
          >
            Save Link
          </button>
        </div>
      </form>
    </Modal>
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
    <Modal
      title={mode === 'create' ? 'Add Webhook' : 'Edit Webhook'}
      subtitle="Provide a human-friendly name and destination URL."
      onClose={onClose}
      widthClass="sm:max-w-lg"
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        <div>
          <label htmlFor="webhook-name-field" className="block text-sm font-medium text-gray-700">
            Webhook Name
          </label>
          <input
            type="text"
            id="webhook-name-field"
            name="webhook_name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder="A descriptive name for this webhook"
          />
        </div>
        <div>
          <label htmlFor="webhook-url-field" className="block text-sm font-medium text-gray-700">
            Destination URL
          </label>
          <input
            type="url"
            id="webhook-url-field"
            name="webhook_url"
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder="https://example.com/webhooks/gobii"
          />
        </div>
        <div className="flex items-center justify-end gap-3 pt-2">
          <button type="button" className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2">
            Save Webhook
          </button>
        </div>
      </form>
    </Modal>
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
    <Modal
      title={mode === 'create' ? 'Add Inbound Webhook' : 'Edit Inbound Webhook'}
      subtitle="Save to generate or update the secret-bearing webhook URL."
      onClose={onClose}
      widthClass="sm:max-w-lg"
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        <div>
          <label htmlFor="inbound-webhook-name-field" className="block text-sm font-medium text-gray-700">
            Webhook Name
          </label>
          <input
            type="text"
            id="inbound-webhook-name-field"
            name="inbound_webhook_name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder="A descriptive name for this inbound webhook"
          />
        </div>
        <label className="inline-flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(event) => setIsActive(event.target.checked)}
            className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <span>Webhook active</span>
        </label>
        <div className="flex items-center justify-end gap-3 pt-2">
          <button type="button" className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2">
            Save Inbound Webhook
          </button>
        </div>
      </form>
    </Modal>
  )
}

type ActionsSectionProps = {
  csrfToken: string
  urls: AgentSettingsData['urls']
  variant?: AgentSettingsWorkspaceVariant
  agent: AgentSummary
  features: AgentSettingsData['features']
  reassignment: ReassignmentInfo
  selectedOrgId: string
  onOrgChange: (value: string) => void
  onReassign: (targetOrgId: string | null) => Promise<void>
  reassignError: string | null
  reassigning: boolean
  onDeleteAgent: () => void
  deleteError: string | null
}

function ActionsSection({
  csrfToken,
  variant = 'standalone',
  agent,
  features,
  reassignment,
  selectedOrgId,
  onOrgChange,
  onReassign,
  reassignError,
  reassigning,
  onDeleteAgent,
  deleteError,
}: ActionsSectionProps) {
  const embedded = variant === 'embedded'
  const sectionClassName = embedded ? 'group rounded-none border-0 bg-transparent shadow-none' : 'gobii-card-base group'
  const summaryClassName = embedded
    ? 'flex cursor-pointer list-none items-center justify-between gap-3 border-b border-slate-200/70 px-0 pb-4'
    : 'flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none'
  const wrapperClassName = embedded ? 'divide-y divide-slate-200/70' : 'divide-y divide-gray-200/70'
  const sectionBodyClassName = embedded ? 'space-y-4 px-0 py-5' : 'p-6 sm:p-8 space-y-4'

  return (
    <details className={sectionClassName} id="agent-ownership">
      <summary className={summaryClassName}>
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Actions</h2>
          <p className="text-sm text-gray-500">Ownership, transfer, and deletion tools.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className={wrapperClassName}>
        {features.organizations && reassignment.enabled && (
          <section className={sectionBodyClassName}>
            <div>
              <h3 className="text-base font-semibold text-gray-800">Organization Assignment</h3>
              <p className="text-sm text-gray-500">Switch this agent between your personal workspace and an organization you manage.</p>
            </div>
            {agent.organization ? (
              <div className="space-y-3">
                <div className={embedded ? 'flex flex-col gap-3 rounded-lg border border-slate-200/70 bg-transparent px-4 py-3 sm:flex-row sm:items-center sm:justify-between' : 'flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3'}>
                  <span className="text-sm text-gray-700">
                    Currently assigned to <strong>{agent.organization.name}</strong>
                  </span>
                  <button
                    type="button"
                    onClick={() => onReassign(null)}
                    className="px-3 py-1.5 text-sm bg-gray-600 text-white rounded-lg hover:bg-gray-700 disabled:opacity-50"
                    disabled={reassigning}
                  >
                    Move to Personal
                  </button>
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
                    <option value="">Select organization...</option>
                    {reassignment.organizations.map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => onReassign(selectedOrgId || null)}
                    className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                    disabled={!selectedOrgId || reassigning}
                  >
                    Assign to Organization
                  </button>
                </div>
                <p className="text-xs text-gray-500">Name must be unique within the selected organization.</p>
              </div>
            )}
            {reassignError && <div className="text-xs text-red-600">{reassignError}</div>}
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
              <form method="post" className="flex">
                <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
                <input type="hidden" name="action" value="cancel_transfer_invite" />
                <button type="submit" className={embedded ? 'inline-flex items-center gap-2 rounded-lg border border-slate-300/70 bg-transparent px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100/60' : 'inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50'}>
                  Cancel Invitation
                </button>
              </form>
            </div>
          ) : (
            <form method="post" className="space-y-4">
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
                <button type="submit" className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2">
                  Send Transfer Invite
                </button>
              </div>
            </form>
          )}
        </section>

        <section className={embedded ? 'px-0 py-5' : 'p-6 sm:p-8'}>
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <div className="flex items-center justify-center w-12 h-12 rounded-full bg-red-100 border-4 border-red-50">
                <ShieldAlert className="w-6 h-6 text-red-600" aria-hidden="true" />
              </div>
            </div>
            <div className="flex-grow space-y-4">
              <div>
                <h3 className="text-lg font-bold text-red-800">Danger Zone</h3>
                <p className="text-sm text-red-700">Permanently delete this agent and all of its data. This action cannot be undone and will immediately stop any running tasks.</p>
              </div>
              <button
                type="button"
                onClick={onDeleteAgent}
                className="py-2 px-4 inline-flex items-center gap-x-2 text-sm font-medium rounded-lg border border-red-300 bg-red-50 text-red-700 hover:bg-red-100 hover:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
              >
                <Trash2 className="w-4 h-4" aria-hidden="true" />
                Delete Agent
              </button>
              {deleteError && <p className="text-sm text-red-600">{deleteError}</p>}
            </div>
          </div>
        </section>
      </div>
    </details>
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
  const [busy, setBusy] = useState(false)
  const confirmClasses =
    tone === 'danger'
      ? 'inline-flex w-full justify-center rounded-md border border-transparent bg-red-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 sm:w-auto sm:text-sm disabled:opacity-60'
      : 'inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:w-auto sm:text-sm disabled:opacity-60'
  const cancelClasses =
    'inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:w-auto sm:text-sm disabled:opacity-60'

  const handleConfirm = async () => {
    if (!onConfirm) {
      onClose()
      return
    }
    setBusy(true)
    try {
      await onConfirm()
      onClose()
    } catch (error) {
      console.error(error)
      setBusy(false)
    }
  }

  const footer = (
    <>
      <button type="button" className={confirmClasses} onClick={handleConfirm} disabled={busy}>
        {busy ? 'Working…' : confirmLabel}
      </button>
      <button type="button" className={cancelClasses} onClick={onClose} disabled={busy}>
        {cancelLabel}
      </button>
    </>
  )

  return (
    <Modal
      title={title}
      onClose={() => {
        if (!busy) {
          onClose()
        }
      }}
      subtitle={typeof body === 'string' ? body : undefined}
      icon={tone === 'danger' ? Trash2 : Info}
      iconBgClass={tone === 'danger' ? 'bg-red-100' : 'bg-blue-100'}
      iconColorClass={tone === 'danger' ? 'text-red-600' : 'text-blue-600'}
      widthClass="sm:max-w-md"
      footer={footer}
    >
      {typeof body === 'string' ? null : <div className="text-sm text-gray-600">{body}</div>}
    </Modal>
  )
}
