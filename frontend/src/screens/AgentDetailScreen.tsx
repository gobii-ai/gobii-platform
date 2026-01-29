import type { FormEvent, ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowLeft,
  ArrowUpFromLine,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  Folder,
  Info,
  Lock,
  KeyRound,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  ServerCog,
  ShieldAlert,
  Trash2,
  UserPlus,
  Users,
  XCircle,
  Zap,
} from 'lucide-react'
import {
  Checkbox as AriaCheckbox,
  ColorSwatch,
  ColorSwatchPicker,
  ColorSwatchPickerItem,
  Slider as AriaSlider,
  SliderThumb,
  SliderTrack,
  Switch as AriaSwitch,
} from 'react-aria-components'
import { Modal } from '../components/common/Modal'
import { useModal } from '../hooks/useModal'

type IntelligenceTierKey = 'standard' | 'premium' | 'max'

type LlmIntelligenceOption = {
  key: IntelligenceTierKey
  label: string
  description: string
  multiplier: number
}

type LlmIntelligenceConfig = {
  options: LlmIntelligenceOption[]
  canEdit: boolean
  disabledReason: string | null
  upgradeUrl: string | null
}

type PrimaryEndpoint = {
  address: string
}

type PendingTransfer = {
  toEmail: string
  createdAtIso: string
  createdAtDisplay: string
}

type AgentOrganization = {
  id: string
  name: string
} | null

type AgentSummary = {
  id: string
  name: string
  avatarUrl: string | null
  charter: string
  isActive: boolean
  createdAtDisplay: string
  pendingTransfer: PendingTransfer | null
  whitelistPolicy: string
  organization: AgentOrganization
  preferredLlmTier: IntelligenceTierKey
  agentColorHex: string
}

type AgentColorOption = {
  id: string
  name: string
  hex: string
}

function resolveAgentColorHex(agentColorHex: string | null | undefined, palette: AgentColorOption[]): string {
  if (!palette.length) {
    return agentColorHex || ''
  }
  const normalized = (agentColorHex || '').toUpperCase()
  const match = palette.find((color) => color.hex.toUpperCase() === normalized)
  return match ? match.hex : palette[0].hex
}

type DailyCreditsInfo = {
  limit: number | null
  hardLimit: number | null
  usage: number
  remaining: number | null
  softRemaining: number | null
  unlimited: boolean
  percentUsed: number | null
  softPercentUsed: number | null
  nextResetIso: string | null
  nextResetLabel: string | null
  low: boolean
  sliderMin: number
  sliderMax: number
  sliderLimitMax: number
  sliderStep: number
  sliderValue: number
  sliderEmptyValue: number
  standardSliderLimit: number
}

type DedicatedIpOption = {
  id: string
  label: string
  inUseElsewhere: boolean
  disabled: boolean
  assignedNames: string[]
}

type DedicatedIpInfo = {
  total: number
  available: number
  multiAssign: boolean
  ownerType: 'organization' | 'user'
  selectedId: string | null
  options: DedicatedIpOption[]
  organizationName: string | null
}

type AllowlistEntry = {
  id: string
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
}

type AllowlistInvite = {
  id: string
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
}

type AllowlistState = {
  show: boolean
  ownerEmail: string | null
  ownerPhone: string | null
  entries: AllowlistEntry[]
  pendingInvites: AllowlistInvite[]
  activeCount: number
  maxContacts: number | null
  pendingContactRequests: number
  emailVerified: boolean
}

type CollaboratorEntry = {
  id: string
  userId: string
  email: string
  name: string
}

type CollaboratorInvite = {
  id: string
  email: string
  invitedAtIso: string | null
  expiresAtIso: string | null
}

type CollaboratorState = {
  entries: CollaboratorEntry[]
  pendingInvites: CollaboratorInvite[]
  activeCount: number
  pendingCount: number
  totalCount: number
  maxContacts: number | null
  canManage: boolean
}

type McpServer = {
  id: string
  displayName: string
  description: string | null
  scope: string
  inherited: boolean
  assigned: boolean
}

type PersonalMcpServer = {
  id: string
  displayName: string
  description: string | null
  assigned: boolean
}

type McpServersInfo = {
  inherited: McpServer[]
  organization: McpServer[]
  personal: PersonalMcpServer[]
  showPersonalForm: boolean
  canManage: boolean
  manageUrl: string | null
}

type PeerLinkCandidate = {
  id: string
  name: string
}

type PeerLinkState = {
  creditsRemaining: number | null
  windowResetLabel: string | null
}

type PeerLinkEntry = {
  id: string
  counterpartId: string | null
  counterpartName: string | null
  isEnabled: boolean
  messagesPerWindow: number
  windowHours: number
  featureFlag: string | null
  createdOnLabel: string
  state: PeerLinkState | null
}

type PeerLinksInfo = {
  entries: PeerLinkEntry[]
  candidates: PeerLinkCandidate[]
  defaults: {
    messagesPerWindow: number
    windowHours: number
  }
}

type AgentWebhook = {
  id: string
  name: string
  url: string
}

type PendingWebhookAction =
  | { type: 'create'; tempId: string; name: string; url: string }
  | { type: 'update'; id: string; name: string; url: string }
  | { type: 'delete'; id: string }

type DisplayWebhook = AgentWebhook & {
  pendingType?: PendingWebhookAction['type']
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

type ReassignmentInfo = {
  enabled: boolean
  canReassign: boolean
  organizations: { id: string; name: string }[]
  assignedOrg: AgentOrganization
}

type AgentDetailPageData = {
  csrfToken: string
  urls: {
    detail: string
    list: string
    chat: string
    secrets: string
    emailSettings: string
    manageFiles: string
    smsEnable: string | null
    contactRequests: string
    delete: string
    mcpServersManage: string | null
  }
  agent: AgentSummary
  agentColors: AgentColorOption[]
  primaryEmail: PrimaryEndpoint | null
  primarySms: PrimaryEndpoint | null
  dailyCredits: DailyCreditsInfo
  dedicatedIps: DedicatedIpInfo
  allowlist: AllowlistState
  collaborators: CollaboratorState
  mcpServers: McpServersInfo
  peerLinks: PeerLinksInfo
  webhooks: AgentWebhook[]
  features: {
    organizations: boolean
  }
  reassignment: ReassignmentInfo
  llmIntelligence: LlmIntelligenceConfig | null
}

export type AgentDetailScreenProps = {
  initialData: AgentDetailPageData
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

type AllowlistInput = {
  address: string
  channel: string
  allowInbound: boolean
  allowOutbound: boolean
}

const generateTempId = () =>
  typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `temp-${Date.now()}-${Math.random().toString(36).slice(2)}`

const normalizeWebhooks = (hooks: AgentWebhook[]): DisplayWebhook[] => hooks.map((hook) => ({ ...hook }))

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

export function AgentDetailScreen({ initialData }: AgentDetailScreenProps) {
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
      preferredTier: initialData.agent.preferredLlmTier ?? 'standard',
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
  const [savedWebhooks, setSavedWebhooks] = useState<AgentWebhook[]>(initialData.webhooks)
  const [webhooksState, setWebhooksState] = useState<DisplayWebhook[]>(() => normalizeWebhooks(initialData.webhooks))
  const [pendingWebhookActions, setPendingWebhookActions] = useState<PendingWebhookAction[]>([])
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
  const currentMultiplier = hasTierMultipliers ? getTierMultiplier(formState.preferredTier) : 1
  const sliderLimitMax = hasTierMultipliers
    ? Math.max(sliderMin, Math.round(standardSliderLimit * currentMultiplier))
    : fallbackSliderLimitMax
  const sliderMax = hasTierMultipliers ? sliderLimitMax + sliderStep : fallbackSliderMax
  const sliderEmptyValue = hasTierMultipliers ? sliderMax : fallbackSliderEmptyValue

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

  const webhooksDirty = pendingWebhookActions.length > 0
  const peerLinksDirty = pendingPeerActions.length > 0
  const hasAnyChanges = generalHasChanges || mcpHasChanges || webhooksDirty || peerLinksDirty

  const applyPeerLinkPayload = useCallback((payload: PeerLinksInfo) => {
    setSavedPeerLinks(payload)
    setPeerLinksState(payload.entries)
    setPeerLinkCandidates(payload.candidates)
    setPeerLinkDefaults(payload.defaults)
  }, [])

  const resetForm = useCallback(() => {
    setFormState(savedFormState)
  }, [savedFormState])

  const handleResetAll = useCallback(() => {
    resetForm()
    setSelectedOrgServers(new Set(savedOrgServers))
    setSelectedPersonalServers(new Set(savedPersonalServers))
    setPendingWebhookActions([])
    setWebhooksState(normalizeWebhooks(savedWebhooks))
    setPendingPeerActions([])
    setPeerLinksState(savedPeerLinks.entries)
    setPeerLinkCandidates(savedPeerLinks.candidates)
    setPeerLinkDefaults(savedPeerLinks.defaults)
    setSaveError(null)
    resetAvatarState()
  }, [resetAvatarState, resetForm, savedOrgServers, savedPeerLinks, savedPersonalServers, savedWebhooks])

  const handleSaveAll = useCallback(async () => {
    if (!hasAnyChanges) {
      return
    }
    setSaving(true)
    setSaveError(null)
    let processedWebhookActions = 0
    let processedPeerActions = 0
    try {
      if (generalHasChanges && generalFormRef.current) {
        const data = await submitFormData(new FormData(generalFormRef.current))
        setSavedFormState({ ...formState })
        const nextAvatar = (data?.avatarUrl as string | null | undefined) ?? savedAvatarUrl
        clearAvatarPreviewUrl()
        setSavedAvatarUrl(nextAvatar ?? null)
        setAvatarPreviewUrl(nextAvatar ?? null)
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

      if (pendingWebhookActions.length) {
        for (const action of pendingWebhookActions) {
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
          processedWebhookActions += 1
        }
        setPendingWebhookActions([])
      }

      if (pendingPeerActions.length) {
        for (const action of pendingPeerActions) {
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
          processedPeerActions += 1
        }
        setPendingPeerActions([])
      }

      setSaveError(null)
    } catch (error) {
      if (processedWebhookActions > 0) {
        setPendingWebhookActions((prev) => prev.slice(processedWebhookActions))
      }
      if (processedPeerActions > 0) {
        setPendingPeerActions((prev) => prev.slice(processedPeerActions))
      }
      setSaveError(error instanceof Error ? error.message : 'Failed to save changes. Please try again.')
    } finally {
      setSaving(false)
    }
  }, [
    applyPeerLinkPayload,
    avatarInputRef,
    clearAvatarPreviewUrl,
    formState,
    generalFormRef,
    generalHasChanges,
    hasAnyChanges,
    mcpHasChanges,
    pendingPeerActions,
    pendingWebhookActions,
    savedAvatarUrl,
    selectedOrgServers,
    selectedPersonalServers,
    submitFormData,
  ])
  const [allowlistState, setAllowlistState] = useState(initialData.allowlist)
  const [allowlistError, setAllowlistError] = useState<string | null>(null)
  const [allowlistBusy, setAllowlistBusy] = useState(false)
  const [collaboratorState, setCollaboratorState] = useState(initialData.collaborators)
  const [collaboratorError, setCollaboratorError] = useState<string | null>(null)
  const [collaboratorBusy, setCollaboratorBusy] = useState(false)
  const [selectedOrgId, setSelectedOrgId] = useState(initialData.reassignment.assignedOrg?.id ?? '')
  const [reassignError, setReassignError] = useState<string | null>(null)
  const [reassigning, setReassigning] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [modal, showModal] = useModal()
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
        const nextSliderLimitMax = hasTierMultipliers
          ? Math.max(sliderMin, Math.round(standardSliderLimit * nextMultiplier))
          : fallbackSliderLimitMax
        const nextSliderMax = hasTierMultipliers ? nextSliderLimitMax + sliderStep : fallbackSliderMax
        const nextSliderEmptyValue = hasTierMultipliers ? nextSliderMax : fallbackSliderEmptyValue
        const isUnlimited = prev.sliderValue >= sliderEmptyValue || !prev.dailyCreditInput.trim()

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
      fallbackSliderEmptyValue,
      fallbackSliderLimitMax,
      fallbackSliderMax,
      getTierMultiplier,
      hasTierMultipliers,
      sliderEmptyValue,
      sliderMin,
      sliderStep,
      standardSliderLimit,
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

  const applyAllowlistPatch = useCallback((payload?: Partial<AllowlistState>) => {
    if (!payload) {
      return
    }
    setAllowlistState((prev) => ({
      ...prev,
      entries: payload.entries ?? prev.entries,
      pendingInvites: payload.pendingInvites ?? prev.pendingInvites,
      ownerEmail: payload.ownerEmail ?? prev.ownerEmail,
      ownerPhone: payload.ownerPhone ?? prev.ownerPhone,
      activeCount: typeof payload.activeCount === 'number' ? payload.activeCount : prev.activeCount,
    }))
  }, [])

  const applyCollaboratorPatch = useCallback((payload?: Partial<CollaboratorState>) => {
    if (!payload) {
      return
    }
    setCollaboratorState((prev) => ({
      ...prev,
      entries: payload.entries ?? prev.entries,
      pendingInvites: payload.pendingInvites ?? prev.pendingInvites,
      activeCount: typeof payload.activeCount === 'number' ? payload.activeCount : prev.activeCount,
      pendingCount: typeof payload.pendingCount === 'number' ? payload.pendingCount : prev.pendingCount,
      totalCount: typeof payload.totalCount === 'number' ? payload.totalCount : prev.totalCount,
      maxContacts: payload.maxContacts ?? prev.maxContacts,
      canManage: typeof payload.canManage === 'boolean' ? payload.canManage : prev.canManage,
    }))
  }, [])

  const postAllowlistAction = useCallback(
    async (body: Record<string, string | Blob>) => {
      setAllowlistBusy(true)
      setAllowlistError(null)
      try {
        const formData = new FormData()
        formData.append('csrfmiddlewaretoken', initialData.csrfToken)
        for (const [key, value] of Object.entries(body)) {
          formData.append(key, value)
        }
        const response = await fetch(initialData.urls.detail, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData,
        })
        const data = await response.json()
        if (data.allowlist) {
          applyAllowlistPatch(data.allowlist as Partial<AllowlistState>)
        }
        if (data.collaborators) {
          applyCollaboratorPatch(data.collaborators as Partial<CollaboratorState>)
        }
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Request failed. Please try again.')
        }
      } catch (error) {
        setAllowlistError(error instanceof Error ? error.message : 'Request failed. Please try again.')
        throw error
      } finally {
        setAllowlistBusy(false)
      }
    },
    [applyAllowlistPatch, initialData.csrfToken, initialData.urls.detail],
  )

  const handleAllowlistAdd = useCallback(
    async (input: AllowlistInput) => {
      await postAllowlistAction({
        action: 'add_allowlist',
        channel: input.channel,
        address: input.address,
        allow_inbound: String(input.allowInbound),
        allow_outbound: String(input.allowOutbound),
      })
    },
    [postAllowlistAction],
  )

  const handleAllowlistRemove = useCallback(
    async (entryId: string) => {
      await postAllowlistAction({
        action: 'remove_allowlist',
        entry_id: entryId,
      })
    },
    [postAllowlistAction],
  )

  const handleCancelInvite = useCallback(
    async (inviteId: string) => {
      await postAllowlistAction({
        action: 'cancel_invite',
        invite_id: inviteId,
      })
    },
    [postAllowlistAction],
  )

  const postCollaboratorAction = useCallback(
    async (body: Record<string, string | Blob>) => {
      setCollaboratorBusy(true)
      setCollaboratorError(null)
      try {
        const formData = new FormData()
        formData.append('csrfmiddlewaretoken', initialData.csrfToken)
        for (const [key, value] of Object.entries(body)) {
          formData.append(key, value)
        }
        const response = await fetch(initialData.urls.detail, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData,
        })
        const data = await response.json()
        if (data.collaborators) {
          applyCollaboratorPatch(data.collaborators as Partial<CollaboratorState>)
        }
        if (data.allowlist) {
          applyAllowlistPatch(data.allowlist as Partial<AllowlistState>)
        }
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Request failed. Please try again.')
        }
      } catch (error) {
        setCollaboratorError(error instanceof Error ? error.message : 'Request failed. Please try again.')
        throw error
      } finally {
        setCollaboratorBusy(false)
      }
    },
    [applyAllowlistPatch, applyCollaboratorPatch, initialData.csrfToken, initialData.urls.detail],
  )

  const handleCollaboratorAdd = useCallback(
    async (email: string) => {
      await postCollaboratorAction({
        action: 'add_collaborator',
        email,
      })
    },
    [postCollaboratorAction],
  )

  const handleCollaboratorRemove = useCallback(
    async (collaboratorId: string) => {
      await postCollaboratorAction({
        action: 'remove_collaborator',
        collaborator_id: collaboratorId,
      })
    },
    [postCollaboratorAction],
  )

  const handleCollaboratorCancelInvite = useCallback(
    async (inviteId: string) => {
      await postCollaboratorAction({
        action: 'cancel_collaborator_invite',
        invite_id: inviteId,
      })
    },
    [postCollaboratorAction],
  )

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
    [initialData.csrfToken, initialData.urls.detail],
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
      const redirectTarget = response.headers.get('HX-Redirect') || initialData.urls.list
      window.location.assign(redirectTarget)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to delete agent. Please try again.'
      setDeleteError(message)
      throw error
    }
  }, [initialData.csrfToken, initialData.urls.delete, initialData.urls.list])

  const confirmDeleteAgent = useCallback(() => {
    openConfirmAction({
      title: 'Delete agent',
      body: 'Are you sure you want to delete this agent? This action cannot be undone and will permanently remove all agent data and stop any running tasks.',
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

  return (
    <div className="space-y-6 pb-6">
      <header className="bg-white/80 backdrop-blur-sm shadow-xl rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200/70 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-800" id="agent-name-heading">
              {(formState.name || 'Agent').trim()} Settings
            </h1>
            <p className="text-sm text-gray-500 mt-1">Manage your agent settings and preferences</p>
            <a
              href={initialData.urls.list}
              className="group inline-flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 transition-colors mt-3"
            >
              <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" aria-hidden="true" />
              Back to Agents
            </a>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={initialData.urls.chat}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-blue-50 transition-colors"
            >
              <MessageSquare className="w-4 h-4" aria-hidden="true" />
              Web Chat
            </a>
            <a
              href={initialData.urls.secrets}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-gray-50 transition-colors"
            >
              <KeyRound className="w-4 h-4" aria-hidden="true" />
              Secrets
            </a>
            <a
              href={initialData.urls.emailSettings}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-gray-50 transition-colors"
            >
              <Mail className="w-4 h-4" aria-hidden="true" />
              Email Settings
            </a>
            <a
              href={initialData.urls.manageFiles}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-gray-50 transition-colors"
            >
              <Folder className="w-4 h-4" aria-hidden="true" />
              Manage Files
            </a>
          </div>
        </div>
      </header>

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
        <details className="gobii-card-base group" id="agent-identity" open>
          <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
            <div>
              <h2 className="text-lg font-semibold text-gray-800">General Settings</h2>
              <p className="text-sm text-gray-500">Core configuration and runtime controls.</p>
            </div>
            <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
          </summary>
          <div className="p-6 sm:p-8">
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
                        className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-800 shadow-sm transition-colors hover:border-blue-300 hover:text-blue-700"
                      >
                        <ArrowUpFromLine className="h-4 w-4" aria-hidden="true" />
                        Upload
                      </button>
                      {(avatarPreviewUrl || savedAvatarUrl || avatarFile) && (
                        <button
                          type="button"
                          onClick={handleAvatarRemove}
                          className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 shadow-sm transition-colors hover:border-red-300"
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
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between lg:gap-6 p-4 border border-gray-200 rounded-lg bg-gray-50/60">
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
                <DailyCreditSummary dailyCredits={initialData.dailyCredits} formatNumber={formatNumber} />
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

      <SaveBar visible={hasAnyChanges} onCancel={handleResetAll} onSave={handleSaveAll} busy={saving} error={saveError} />

      <details className="gobii-card-base group" id="agent-contact-controls">
        <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
          <div>
            <h2 className="text-lg font-semibold text-gray-800">Contacts &amp; Access</h2>
            <p className="text-sm text-gray-500">Contact endpoints and allowlist management.</p>
          </div>
          <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
        </summary>
        <div className="p-6 sm:p-8 space-y-6">
          <PrimaryContacts
            primaryEmail={initialData.primaryEmail}
            primarySms={initialData.primarySms}
            emailSettingsUrl={initialData.urls.emailSettings}
            smsEnableUrl={initialData.urls.smsEnable}
          />

          {initialData.allowlist.show && (
            <AllowlistManager
              state={allowlistState}
              error={allowlistError}
              busy={allowlistBusy}
              onAdd={handleAllowlistAdd}
              onRemove={handleAllowlistRemove}
              onCancelInvite={handleCancelInvite}
              contactRequestsUrl={initialData.urls.contactRequests}
              onConfirmAction={openConfirmAction}
            />
          )}

          <CollaboratorManager
            state={collaboratorState}
            error={collaboratorError}
            busy={collaboratorBusy}
            onAdd={handleCollaboratorAdd}
            onRemove={handleCollaboratorRemove}
            onCancelInvite={handleCollaboratorCancelInvite}
            onConfirmAction={openConfirmAction}
          />
        </div>
      </details>

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
        onDeleteAgent={confirmDeleteAgent}
        deleteError={deleteError}
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
    <div className="p-4 border border-gray-200 rounded-lg bg-white/70 space-y-4">
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

type AgentColorPickerProps = {
  colors: AgentColorOption[]
  selectedHex: string
  onChange: (hex: string) => void
}

function AgentColorPicker({ colors, selectedHex, onChange }: AgentColorPickerProps) {
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
              isSelected ? 'border-blue-500 bg-blue-50/60' : 'border-gray-200 bg-white',
              isHovered && !isSelected ? 'border-blue-300' : '',
              isDisabled ? 'opacity-60' : '',
              isFocusVisible ? 'ring-2 ring-blue-300 ring-offset-2 ring-offset-white' : '',
            ]
              .filter(Boolean)
              .join(' ')
          }
        >
          {({ isSelected }) => (
            <>
              <ColorSwatch className="h-5 w-5 rounded-full border border-slate-300 sm:h-6 sm:w-6" />
              {isSelected && (
                <span className="absolute right-1 top-1 rounded-full bg-white/80 p-0.5 text-blue-600">
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

function DedicatedIpSummary({ dedicatedIps, organizationName, selectedValue, onChange }: DedicatedIpSummaryProps) {
  return (
    <div className="text-sm text-gray-600 space-y-4" data-dedicated-ip-total={dedicatedIps.total}>
      <p className="text-sm text-gray-500">Monitor and assign dedicated IP addresses reserved for this account.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="border border-gray-200 rounded-lg bg-gray-50 p-4">
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
        <div className="border border-gray-200 rounded-lg bg-gray-50 p-4">
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

type AgentIntelligenceSliderProps = {
  currentTier: IntelligenceTierKey
  config: LlmIntelligenceConfig
  onTierChange: (tier: IntelligenceTierKey) => void
}

function AgentIntelligenceSlider({ currentTier, config, onTierChange }: AgentIntelligenceSliderProps) {
  const isDisabled = !config.canEdit

  const handleSelect = (tier: IntelligenceTierKey) => {
    if (isDisabled || tier === currentTier) {
      return
    }
    onTierChange(tier)
  }

  const renderMultiplier = (value: number) => {
    if (!Number.isFinite(value)) {
      return '× credits'
    }
    const formatted = value % 1 === 0 ? value.toFixed(0) : value.toFixed(1)
    return `${formatted}× credits`
  }

  return (
    <div className="space-y-2">
      {!config.canEdit && config.disabledReason && (
        <p className="flex items-center gap-1 text-xs text-gray-500">
          <Lock className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />
          <span>
            {config.disabledReason}
            {config.upgradeUrl && (
              <>
                {' '}
                <a href={config.upgradeUrl} className="text-indigo-600 underline">
                  Upgrade
                </a>
              </>
            )}
          </span>
        </p>
      )}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {config.options.map((option) => {
          const selected = currentTier === option.key
          return (
            <button
              type="button"
              key={option.key}
              onClick={() => handleSelect(option.key)}
              disabled={isDisabled}
              className={`flex flex-col rounded-lg border p-3 text-left transition focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                selected ? 'border-indigo-500 bg-indigo-50 shadow-sm' : 'border-gray-200 bg-white hover:border-indigo-300'
              } ${isDisabled ? 'cursor-not-allowed opacity-60' : ''}`}
            >
              <span className="text-sm font-semibold text-gray-800">{option.label}</span>
              <span className="mt-1 text-xs text-gray-500">{option.description}</span>
              <span className="mt-2 text-xs font-medium text-gray-500">{renderMultiplier(option.multiplier)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

type SaveBarProps = {
  visible: boolean
  onCancel: () => void
  onSave: () => Promise<void> | void
  busy?: boolean
  error?: string | null
}

function SaveBar({ visible, onCancel, onSave, busy, error }: SaveBarProps) {
  if (!visible) {
    return null
  }
  return (
    <div id="agent-save-bar" className="fixed inset-x-0 bottom-0 z-40 pointer-events-none">
      <div className="pointer-events-auto mx-auto w-full max-w-5xl px-4 pb-4">
        <div className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-[0_8px_30px_rgba(15,23,42,0.25)] sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-1 text-sm text-gray-700">
            <div className="flex items-center gap-2">
            <Info className="h-4 w-4 text-blue-600" aria-hidden="true" />
            <span>You have unsaved changes</span>
          </div>
            {error && (
              <div className="flex items-center gap-2 text-xs text-red-600">
                <XCircle className="h-4 w-4" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60"
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              {busy ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
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
}

function PrimaryContacts({ primaryEmail, primarySms, emailSettingsUrl, smsEnableUrl }: PrimaryContactsProps) {
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
              className="py-2 px-3 block w-full border-gray-200 bg-gray-100 shadow-sm rounded-lg text-sm"
            />
            <p className="mt-2 text-xs text-gray-500">The agent's primary email address for communication.</p>
            <div className="mt-2 space-y-1">
              <a href={emailSettingsUrl} className="text-sm text-blue-600 hover:text-blue-800">
                Manage Email Settings
              </a>
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
          <div className="py-2 px-3 text-sm text-gray-600 bg-gray-50 border border-dashed border-gray-300 rounded">
            Not configured.{' '}
            <a href={emailSettingsUrl} className="text-blue-600 hover:text-blue-800">
              Set up email
            </a>
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
              className="py-2 px-3 block w-full border-gray-200 bg-gray-100 shadow-sm rounded-lg text-sm"
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
  error: string | null
  busy: boolean
  onAdd: (input: AllowlistInput) => Promise<void>
  onRemove: (entryId: string) => Promise<void>
  onCancelInvite: (inviteId: string) => Promise<void>
  contactRequestsUrl: string
  onConfirmAction: (config: ConfirmActionConfig) => void
}

function AllowlistManager({ state, error, busy, onAdd, onRemove, onCancelInvite, contactRequestsUrl, onConfirmAction }: AllowlistManagerProps) {
  const [channel, setChannel] = useState('email')
  const [address, setAddress] = useState('')
  const [allowInbound, setAllowInbound] = useState(true)
  const [allowOutbound, setAllowOutbound] = useState(true)

  const handleSubmit = async () => {
    if (!address.trim()) {
      return
    }
    try {
      await onAdd({ channel, address: address.trim(), allowInbound, allowOutbound })
      setAddress('')
      setAllowInbound(true)
      setAllowOutbound(true)
    } catch (error) {
      // Errors are surfaced via allowlistError state
      console.error(error)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        By default, the agent owner and organization members can communicate with this agent. You can add additional contacts below. Note: Multi-recipient messaging is limited to email only.
      </p>
      <p className="text-xs text-slate-600">
        Contact slots include allowlist entries and collaborators.
      </p>

      {!state.emailVerified && (
        <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg flex items-start gap-2">
          <Mail className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <div className="text-sm text-amber-800">
            <span className="font-medium">Email verification required.</span>{' '}
            External contacts won't be able to reach your agent until you{' '}
            <a href="/accounts/email/" className="underline hover:text-amber-900">verify your email address</a>.
          </div>
        </div>
      )}

      <div className="space-y-4">
        <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg space-y-2">
          <h4 className="text-sm font-medium text-gray-700">Add Allowed Contact</h4>
          <div className="flex gap-2">
            <select
              id="allowlist-channel"
              name="channel"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
              className="py-1.5 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="email">Email</option>
            </select>
            <input
              type="email"
              id="allowlist-address"
              name="address"
              placeholder="email@example.com"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              className="flex-1 py-1.5 px-2 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
            />
          </div>
          <div className="flex gap-4 items-center">
            <AriaCheckbox
              isSelected={allowInbound}
              onChange={setAllowInbound}
              className="group inline-flex items-center gap-2 text-sm text-gray-700"
            >
              {({ isSelected }) => (
                <>
                  <span
                    aria-hidden="true"
                    className={`flex h-4 w-4 items-center justify-center rounded border transition ${
                      isSelected ? 'border-blue-600 bg-blue-600 text-white' : 'border-gray-300 bg-white text-transparent'
                    }`}
                  >
                    <Check className="h-3 w-3" aria-hidden="true" />
                  </span>
                  <span className="flex flex-col leading-tight">
                    <span>Allow Inbound</span>
                    <span className="text-xs text-gray-500">(can send to agent)</span>
                  </span>
                </>
              )}
            </AriaCheckbox>
            <AriaCheckbox
              isSelected={allowOutbound}
              onChange={setAllowOutbound}
              className="group inline-flex items-center gap-2 text-sm text-gray-700"
            >
              {({ isSelected }) => (
                <>
                  <span
                    aria-hidden="true"
                    className={`flex h-4 w-4 items-center justify-center rounded border transition ${
                      isSelected ? 'border-blue-600 bg-blue-600 text-white' : 'border-gray-300 bg-white text-transparent'
                    }`}
                  >
                    <Check className="h-3 w-3" aria-hidden="true" />
                  </span>
                  <span className="flex flex-col leading-tight">
                    <span>Allow Outbound</span>
                    <span className="text-xs text-gray-500">(agent can send to)</span>
                  </span>
                </>
              )}
            </AriaCheckbox>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={busy || !address.trim()}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              Add
            </button>
            <button
              type="button"
              onClick={() => {
                setAddress('')
                setAllowInbound(true)
                setAllowOutbound(true)
              }}
              className="px-3 py-1.5 text-sm bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400"
            >
              Cancel
            </button>
          </div>
          {error && <div className="text-xs text-red-600">{error}</div>}
        </div>

        {state.pendingContactRequests > 0 && (
          <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-yellow-600" aria-hidden="true" />
                <span className="text-sm font-medium text-yellow-800">
                  {state.pendingContactRequests} Contact Request{state.pendingContactRequests === 1 ? '' : 's'} Pending
                </span>
              </div>
              <a href={contactRequestsUrl} className="text-sm font-medium text-yellow-700 hover:text-yellow-900 underline">
                Review
              </a>
            </div>
          </div>
        )}

        <div className="p-3 bg-gray-50 rounded-lg">
          <div className="flex justify-between items-center mb-2">
            <h4 className="text-sm font-medium text-gray-700">Contact slots used</h4>
            <span className="text-xs text-gray-500">
              {state.activeCount} / {state.maxContacts ?? 'Unlimited'} contacts
            </span>
          </div>
          <AllowlistEntries state={state} onRemove={onRemove} onCancelInvite={onCancelInvite} onConfirmAction={onConfirmAction} />
        </div>
      </div>
    </div>
  )
}

type AllowlistEntriesProps = {
  state: AllowlistState
  onRemove: (entryId: string) => Promise<void>
  onCancelInvite: (inviteId: string) => Promise<void>
  onConfirmAction: (config: ConfirmActionConfig) => void
}

function AllowlistEntries({ state, onRemove, onCancelInvite, onConfirmAction }: AllowlistEntriesProps) {
  const hasContacts = state.entries.length > 0 || state.pendingInvites.length > 0
  const renderChannelIcon = (channel: string, className = 'w-4 h-4 text-gray-400') =>
    channel?.toLowerCase() === 'sms' ? (
      <Phone className={className} aria-hidden="true" />
    ) : (
      <Mail className={className} aria-hidden="true" />
    )

  return (
    <div className="space-y-2">
      {(state.ownerEmail || state.ownerPhone) && (
        <div className="text-xs text-gray-500 mb-2">
          <div className="font-medium">Owner (always allowed in Default mode):</div>
          {state.ownerEmail && (
            <div className="flex items-center justify-between py-1 px-2">
              <span className="flex items-center gap-2">
                <Mail className="w-3 h-3 text-gray-400" aria-hidden="true" />
                {state.ownerEmail}
              </span>
            </div>
          )}
          {state.ownerPhone && (
            <div className="flex items-center justify-between py-1 px-2">
              <span className="flex items-center gap-2">
                <Phone className="w-3 h-3 text-gray-400" aria-hidden="true" />
                {state.ownerPhone}
              </span>
            </div>
          )}
          <div className="border-t border-gray-200 my-2" />
        </div>
      )}

      {state.pendingInvites.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2 font-medium">Pending Invitations:</div>
          {state.pendingInvites.map((invite) => (
            <div key={invite.id} className="py-2 px-3 bg-yellow-50 rounded-lg border border-yellow-200 mb-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium flex items-center gap-2">
                  {renderChannelIcon(invite.channel, 'w-4 h-4 text-gray-400')}
                  {invite.address}
                </span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-yellow-700 font-medium">Pending</span>
                  <button
                    type="button"
                    onClick={() =>
                      onConfirmAction({
                        title: 'Cancel invitation',
                        body: `Cancel the allowlist invitation for ${invite.address}?`,
                        confirmLabel: 'Cancel invitation',
                        tone: 'danger',
                        onConfirm: () => onCancelInvite(invite.id),
                      })
                    }
                    className="text-red-600 hover:text-red-800 text-xs font-medium"
                  >
                    Cancel
                  </button>
                </div>
              </div>
              <AllowlistDirectionFlags allowInbound={invite.allowInbound} allowOutbound={invite.allowOutbound} labelColor="text-yellow-700" />
            </div>
          ))}
          <div className="border-t border-gray-200 my-2" />
        </div>
      )}

      {state.entries.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2 font-medium">Allowed Contacts:</div>
          {state.entries.map((entry) => (
            <div key={entry.id} className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded-lg group hover:bg-gray-100 border border-gray-200 mb-2">
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium flex items-center gap-2">
                    {renderChannelIcon(entry.channel, 'w-4 h-4 text-gray-400')}
                    {entry.address}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      onConfirmAction({
                        title: 'Remove contact',
                        body: `Remove ${entry.address} from the allowlist?`,
                        confirmLabel: 'Remove contact',
                        tone: 'danger',
                        onConfirm: () => onRemove(entry.id),
                      })
                    }
                    className="text-xs text-red-600 hover:text-red-800 opacity-0 group-hover:opacity-100 transition-opacity ml-2"
                  >
                    Remove
                  </button>
                </div>
                <AllowlistDirectionFlags allowInbound={entry.allowInbound} allowOutbound={entry.allowOutbound} />
              </div>
            </div>
          ))}
        </div>
      )}

      {!hasContacts && <div className="text-sm text-gray-500 py-2">No additional contacts configured yet.</div>}
    </div>
  )
}

type AllowlistDirectionFlagsProps = {
  allowInbound: boolean
  allowOutbound: boolean
  labelColor?: string
}

function AllowlistDirectionFlags({ allowInbound, allowOutbound, labelColor }: AllowlistDirectionFlagsProps) {
  const inboundClass = allowInbound ? 'text-green-700' : 'text-gray-400 line-through'
  const outboundClass = allowOutbound ? 'text-blue-700' : 'text-gray-400 line-through'
  const colorClass = labelColor ?? 'text-gray-500'

  return (
    <div className="flex flex-wrap gap-3 mt-1 ml-6">
      <div className={`flex items-center gap-1 text-xs ${colorClass}`}>
        <ArrowDownToLine className={`w-4 h-4 ${allowInbound ? 'text-green-600' : 'text-gray-400'}`} aria-hidden="true" />
        <span className={`text-xs ${inboundClass}`}>Receives from contact</span>
      </div>
      <div className={`flex items-center gap-1 text-xs ${colorClass}`}>
        <ArrowUpFromLine className={`w-4 h-4 ${allowOutbound ? 'text-blue-600' : 'text-gray-400'}`} aria-hidden="true" />
        <span className={`text-xs ${outboundClass}`}>Sends to contact</span>
      </div>
    </div>
  )
}

type CollaboratorManagerProps = {
  state: CollaboratorState
  error: string | null
  busy: boolean
  onAdd: (email: string) => Promise<void>
  onRemove: (collaboratorId: string) => Promise<void>
  onCancelInvite: (inviteId: string) => Promise<void>
  onConfirmAction: (config: ConfirmActionConfig) => void
}

function CollaboratorManager({ state, error, busy, onAdd, onRemove, onCancelInvite, onConfirmAction }: CollaboratorManagerProps) {
  const [email, setEmail] = useState('')
  const canManage = state.canManage
  const totalLimit = state.maxContacts ?? 'Unlimited'

  const handleInvite = async () => {
    if (!email.trim()) {
      return
    }
    try {
      await onAdd(email.trim())
      setEmail('')
    } catch (inviteError) {
      console.error(inviteError)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-sky-100 bg-sky-50/60 p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg bg-sky-600 text-white">
            <Users className="h-4 w-4" aria-hidden="true" />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-slate-800">Collaborators</h4>
            <p className="text-xs text-slate-600">
              Invite coworkers to chat and exchange files. Collaborators can upload and download files only.
            </p>
            <p className="mt-2 text-xs text-slate-600">
              Contact slots used: {state.totalCount} / {totalLimit}
            </p>
          </div>
        </div>
      </div>

      {canManage ? (
        <div className="rounded-lg border border-emerald-100 bg-emerald-50/60 p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
            <UserPlus className="h-4 w-4 text-emerald-600" aria-hidden="true" />
            Invite a collaborator
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="email"
              placeholder="name@company.com"
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              className="flex-1 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-emerald-500 focus:ring-emerald-500"
            />
            <button
              type="button"
              onClick={handleInvite}
              disabled={busy || !email.trim()}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-50"
            >
              Send invite
            </button>
          </div>
          {error && <div className="text-xs text-rose-600">{error}</div>}
        </div>
      ) : (
        <p className="text-xs text-slate-600">Only owners and organization admins can invite collaborators.</p>
      )}

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h5 className="text-sm font-semibold text-slate-700">Active collaborators</h5>
          <span className="text-xs text-slate-500">{state.activeCount} active</span>
        </div>
        {state.entries.length > 0 ? (
          <div className="space-y-2">
            {state.entries.map((collaborator) => (
              <div key={collaborator.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2">
                <div>
                  <p className="text-sm font-medium text-slate-800">{collaborator.name || collaborator.email}</p>
                  <p className="text-xs text-slate-500">{collaborator.email}</p>
                </div>
                {canManage && (
                  <button
                    type="button"
                    onClick={() =>
                      onConfirmAction({
                        title: 'Remove collaborator',
                        body: `Remove ${collaborator.email} from this agent?`,
                        tone: 'danger',
                        confirmLabel: 'Remove',
                        onConfirm: () => onRemove(collaborator.id),
                      })
                    }
                    className="text-xs font-semibold text-rose-600 hover:text-rose-700"
                    disabled={busy}
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-slate-500">No collaborators yet.</p>
        )}
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h5 className="text-sm font-semibold text-slate-700">Pending invites</h5>
          <span className="text-xs text-slate-500">{state.pendingCount} pending</span>
        </div>
        {state.pendingInvites.length > 0 ? (
          <div className="space-y-2">
            {state.pendingInvites.map((invite) => (
              <div key={invite.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2">
                <div>
                  <p className="text-sm font-medium text-slate-800">{invite.email}</p>
                  <p className="text-xs text-slate-500">Invite pending</p>
                </div>
                {canManage && (
                  <button
                    type="button"
                    onClick={() =>
                      onConfirmAction({
                        title: 'Cancel invite',
                        body: `Cancel the invite for ${invite.email}?`,
                        tone: 'danger',
                        confirmLabel: 'Cancel invite',
                        onConfirm: () => onCancelInvite(invite.id),
                      })
                    }
                    className="text-xs font-semibold text-rose-600 hover:text-rose-700"
                    disabled={busy}
                  >
                    Cancel
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-slate-500">No pending invites.</p>
        )}
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
  onConfirmAction,
}: IntegrationsSectionProps) {
  return (
    <details className="gobii-card-base group" id="agent-integrations">
      <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Integrations</h2>
          <p className="text-sm text-gray-500">MCP servers, peer links, and webhooks.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className="divide-y divide-gray-200/70">
        <section className="p-6 sm:p-8 space-y-6">
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
                  <li key={server.id} className="flex items-start justify-between gap-3 border border-gray-200 bg-gray-50 rounded-lg px-4 py-3">
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
                      <label key={server.id} className="flex items-start gap-3 border border-gray-200 rounded-lg px-3 py-3">
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
              <div className="border border-gray-200 rounded-xl bg-white p-4 space-y-4">
                <div className="grid gap-3 md:grid-cols-2">
                  {mcpServers.personal.map((server) => {
                    const checked = selectedPersonalServers.has(server.id)
                    return (
                      <label key={server.id} className="flex items-start gap-3 border border-gray-200 rounded-lg px-3 py-3">
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
                      className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm transition-colors hover:bg-gray-50"
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

        <section className="p-6 sm:p-8 space-y-6">
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
            <div className="overflow-hidden border border-gray-200 rounded-xl">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Agent</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Quota</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Remaining</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Next Reset</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Feature Flag</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
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
                              className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
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
            <div className="p-4 bg-gray-50 border border-dashed border-gray-300 rounded-xl text-sm text-gray-600">
              No peer links yet. Use the button above to connect this agent with another agent you control.
            </div>
          )}
        </section>

        <section className="p-6 sm:p-8 space-y-6">
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
              Add Webhook
            </button>
          </div>

          {webhooks.length > 0 ? (
            <div className="overflow-hidden border border-gray-200 rounded-xl">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">URL</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
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
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50"
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
            <div className="p-4 bg-gray-50 border border-dashed border-gray-300 rounded-xl text-sm text-gray-600">
              No webhooks yet. Add one to let your agent notify external systems.
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
          <p className="mt-2 text-xs text-gray-500">
            We send a POST request with JSON payload including <code className="bg-gray-100 px-1 py-0.5 rounded">agent_id</code> and your provided{' '}
            <code className="bg-gray-100 px-1 py-0.5 rounded">payload</code>.
          </p>
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

type ActionsSectionProps = {
  csrfToken: string
  urls: AgentDetailPageData['urls']
  agent: AgentSummary
  features: AgentDetailPageData['features']
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
  return (
    <details className="gobii-card-base group" id="agent-ownership">
      <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Actions</h2>
          <p className="text-sm text-gray-500">Ownership, transfer, and deletion tools.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className="divide-y divide-gray-200/70">
        {features.organizations && reassignment.enabled && (
          <section className="p-6 sm:p-8 space-y-4">
            <div>
              <h3 className="text-base font-semibold text-gray-800">Organization Assignment</h3>
              <p className="text-sm text-gray-500">Switch this agent between your personal workspace and an organization you manage.</p>
            </div>
            {agent.organization ? (
              <div className="space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
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

        <section className="p-6 sm:p-8 space-y-4">
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
                <button type="submit" className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50">
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

        <section className="p-6 sm:p-8">
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
