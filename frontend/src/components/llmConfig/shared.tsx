import { Crown, Layers, ShieldCheck, Sparkles, Star } from 'lucide-react'
import { useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'

import * as llmApi from '../../api/llmConfig'
import { HttpError } from '../../api/http'

export const button = {
  primary:
    'inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500/40 disabled:opacity-50 disabled:cursor-not-allowed',
  secondary:
    'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-200/60 disabled:opacity-50 disabled:cursor-not-allowed',
  muted:
    'inline-flex items-center justify-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-200/60 disabled:opacity-50 disabled:cursor-not-allowed',
  danger:
    'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-sm font-medium text-rose-600 transition hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-200/60 disabled:opacity-50 disabled:cursor-not-allowed',
  icon: 'p-2 text-slate-500 hover:bg-slate-100 rounded-full transition',
  iconDanger: 'p-2 text-slate-500 hover:bg-rose-50 hover:text-rose-600 rounded-full transition',
}

export const addEndpointOptions: Array<{ id: llmApi.ProviderEndpoint['type']; label: string }> = [
  { id: 'persistent', label: 'Persistent' },
  { id: 'browser', label: 'Browser' },
  { id: 'embedding', label: 'Embedding' },
  { id: 'file_handler', label: 'File handler' },
  { id: 'image_generation', label: 'Image generation' },
  { id: 'video_generation', label: 'Video generation' },
]

export const reasoningEffortOptions = [
  { value: '', label: 'Use endpoint default' },
  { value: 'minimal', label: 'Minimal' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]

export const DEFAULT_INTELLIGENCE_TIERS: llmApi.IntelligenceTier[] = [
  { key: 'standard', display_name: 'Standard', rank: 0, credit_multiplier: '1.00' },
  { key: 'premium', display_name: 'Premium', rank: 1, credit_multiplier: '2.00' },
  { key: 'max', display_name: 'Max', rank: 2, credit_multiplier: '5.00' },
  { key: 'ultra', display_name: 'Ultra', rank: 3, credit_multiplier: '20.00' },
  { key: 'ultra_max', display_name: 'Ultra Max', rank: 4, credit_multiplier: '50.00' },
]

export type TierStyle = {
  icon: ReactNode
  borderClass: string
  sectionClass: string
  headingClass: string
  emptyClass: string
}

export const TIER_STYLE_MAP: Record<string, TierStyle> = {
  standard: {
    icon: <Layers className="size-4 text-sky-700" />,
    borderClass: 'border-sky-200',
    sectionClass: 'bg-sky-50/70',
    headingClass: 'text-sky-800',
    emptyClass: 'text-sky-600',
  },
  premium: {
    icon: <ShieldCheck className="size-4 text-emerald-700" />,
    borderClass: 'border-emerald-200',
    sectionClass: 'bg-emerald-50/60',
    headingClass: 'text-emerald-800',
    emptyClass: 'text-emerald-600',
  },
  max: {
    icon: <Crown className="size-4 text-indigo-700" />,
    borderClass: 'border-indigo-200',
    sectionClass: 'bg-indigo-50/60',
    headingClass: 'text-indigo-800',
    emptyClass: 'text-indigo-600',
  },
  ultra: {
    icon: <Sparkles className="size-4 text-amber-700" />,
    borderClass: 'border-amber-200',
    sectionClass: 'bg-amber-50/60',
    headingClass: 'text-amber-800',
    emptyClass: 'text-amber-600',
  },
  ultra_max: {
    icon: <Star className="size-4 text-rose-700" />,
    borderClass: 'border-rose-200',
    sectionClass: 'bg-rose-50/60',
    headingClass: 'text-rose-800',
    emptyClass: 'text-rose-600',
  },
}

export type TierEndpoint = {
  id: string
  endpointId: string
  label: string
  weight: number
  supportsReasoning?: boolean
  reasoningEffortOverride?: string | null
  endpointReasoningEffort?: string | null
  extractionEndpointId?: string | null
  extractionEndpointKey?: string | null
  extractionLabel?: string | null
}

export type Tier = {
  id: string
  name: string
  order: number
  rangeId: string
  imageUseCase?: 'create_image' | 'avatar'
  intelligenceTier?: llmApi.IntelligenceTier | null
  endpoints: TierEndpoint[]
}

export type TierGroup = {
  key: string
  label: string
  rank: number
  creditMultiplier: string | null
  tiers: Tier[]
  style: TierStyle
}

export type TokenRange = {
  id: string
  name: string
  min_tokens: number
  max_tokens: number | null
}

export type ProviderEndpointCard = {
  id: string
  key: string
  name: string
  enabled: boolean
  litellm_pricing_model?: string | null
  api_base?: string
  browser_base_url?: string
  max_output_tokens?: number | null
  max_input_tokens?: number | null
  temperature?: number | null
  supports_temperature?: boolean
  supports_vision?: boolean
  supports_image_to_image?: boolean
  supports_image_to_video?: boolean
  supports_tool_choice?: boolean
  use_parallel_tool_calls?: boolean
  allow_implied_send?: boolean
  supports_reasoning?: boolean
  reasoning_effort?: string | null
  openrouter_preset?: string | null
  low_latency?: boolean
  type: llmApi.ProviderEndpoint['type']
  tierUsage: llmApi.EndpointTierUsage[]
}

export type ProviderCardData = {
  id: string
  name: string
  key: string
  status: string
  backend: string
  fallback: string
  enabled: boolean
  envVar?: string
  modelPrefix: string
  supportsSafety: boolean
  vertexProject: string
  vertexLocation: string
  endpoints: ProviderEndpointCard[]
}

export type ImageGenerationUseCase = 'create_image' | 'avatar'
export type VideoGenerationUseCase = 'create_video'
export type TierScope = 'persistent' | 'browser' | 'embedding' | 'file_handler' | 'image_generation' | 'video_generation'
export type ProfileTierScope = Exclude<TierScope, 'file_handler' | 'image_generation' | 'video_generation'>
export type EndpointKind = Extract<llmApi.ProviderEndpoint['type'], TierScope>
export type TierEndpointWeightPayload = { weight: number }
export type TierEndpointCreatePayload = { endpoint_id: string; weight: number }

export const ENDPOINT_KIND_MAP: Record<llmApi.ProviderEndpoint['type'], EndpointKind> = {
  persistent: 'persistent',
  browser: 'browser',
  embedding: 'embedding',
  file_handler: 'file_handler',
  image_generation: 'image_generation',
  video_generation: 'video_generation',
}

export const endpointKindFromType = (type: llmApi.ProviderEndpoint['type']): EndpointKind => ENDPOINT_KIND_MAP[type]

export const updateTierEndpointByScope: Record<TierScope, (tierEndpointId: string, payload: TierEndpointWeightPayload) => Promise<unknown>> = {
  persistent: (tierEndpointId, payload) => llmApi.updatePersistentTierEndpoint(tierEndpointId, payload),
  browser: (tierEndpointId, payload) => llmApi.updateBrowserTierEndpoint(tierEndpointId, payload),
  embedding: (tierEndpointId, payload) => llmApi.updateEmbeddingTierEndpoint(tierEndpointId, payload),
  file_handler: (tierEndpointId, payload) => llmApi.updateFileHandlerTierEndpoint(tierEndpointId, payload),
  image_generation: (tierEndpointId, payload) => llmApi.updateImageGenerationTierEndpoint(tierEndpointId, payload),
  video_generation: (tierEndpointId, payload) => llmApi.updateVideoGenerationTierEndpoint(tierEndpointId, payload),
}

export const deleteTierEndpointByScope: Record<TierScope, (tierEndpointId: string) => Promise<unknown>> = {
  persistent: (tierEndpointId) => llmApi.deletePersistentTierEndpoint(tierEndpointId),
  browser: (tierEndpointId) => llmApi.deleteBrowserTierEndpoint(tierEndpointId),
  embedding: (tierEndpointId) => llmApi.deleteEmbeddingTierEndpoint(tierEndpointId),
  file_handler: (tierEndpointId) => llmApi.deleteFileHandlerTierEndpoint(tierEndpointId),
  image_generation: (tierEndpointId) => llmApi.deleteImageGenerationTierEndpoint(tierEndpointId),
  video_generation: (tierEndpointId) => llmApi.deleteVideoGenerationTierEndpoint(tierEndpointId),
}

export const addTierEndpointByScope: Record<TierScope, (
  tierId: string,
  payload: TierEndpointCreatePayload,
  extractionEndpointId?: string | null,
) => Promise<{ tier_endpoint_id?: string }>> = {
  persistent: async (tierId, payload) => llmApi.addPersistentTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
  browser: async (tierId, payload, extractionEndpointId) => {
    const browserPayload: { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null } = {
      ...payload,
    }
    if (typeof extractionEndpointId !== 'undefined') {
      browserPayload.extraction_endpoint_id = extractionEndpointId || null
    }
    return llmApi.addBrowserTierEndpoint(tierId, browserPayload) as { tier_endpoint_id?: string }
  },
  embedding: async (tierId, payload) => llmApi.addEmbeddingTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
  file_handler: async (tierId, payload) => llmApi.addFileHandlerTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
  image_generation: async (tierId, payload) => llmApi.addImageGenerationTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
  video_generation: async (tierId, payload) => llmApi.addVideoGenerationTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
}

export const updateProfileTierEndpointByScope: Record<ProfileTierScope, (tierEndpointId: string, payload: TierEndpointWeightPayload) => Promise<unknown>> = {
  persistent: (tierEndpointId, payload) => llmApi.updateProfilePersistentTierEndpoint(tierEndpointId, payload),
  browser: (tierEndpointId, payload) => llmApi.updateProfileBrowserTierEndpoint(tierEndpointId, payload),
  embedding: (tierEndpointId, payload) => llmApi.updateProfileEmbeddingTierEndpoint(tierEndpointId, payload),
}

export const deleteProfileTierEndpointByScope: Record<ProfileTierScope, (tierEndpointId: string) => Promise<unknown>> = {
  persistent: (tierEndpointId) => llmApi.deleteProfilePersistentTierEndpoint(tierEndpointId),
  browser: (tierEndpointId) => llmApi.deleteProfileBrowserTierEndpoint(tierEndpointId),
  embedding: (tierEndpointId) => llmApi.deleteProfileEmbeddingTierEndpoint(tierEndpointId),
}

export const addProfileTierEndpointByScope: Record<ProfileTierScope, (
  tierId: string,
  payload: TierEndpointCreatePayload,
  extractionEndpointId?: string | null,
) => Promise<{ tier_endpoint_id?: string }>> = {
  persistent: async (tierId, payload) => llmApi.addProfilePersistentTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
  browser: async (tierId, payload, extractionEndpointId) => {
    const browserPayload: { endpoint_id: string; weight: number; extraction_endpoint_id?: string | null } = {
      ...payload,
    }
    if (typeof extractionEndpointId !== 'undefined') {
      browserPayload.extraction_endpoint_id = extractionEndpointId || null
    }
    return llmApi.addProfileBrowserTierEndpoint(tierId, browserPayload) as { tier_endpoint_id?: string }
  },
  embedding: async (tierId, payload) => llmApi.addProfileEmbeddingTierEndpoint(tierId, payload) as { tier_endpoint_id?: string },
}

export const getTierStyle = (tierKey?: string | null) => TIER_STYLE_MAP[tierKey ?? 'standard'] ?? TIER_STYLE_MAP.standard

export const getTierKey = (tier: Tier) => tier.intelligenceTier?.key ?? 'standard'

export const buildTierGroups = (tiers: Tier[], intelligenceTiers: llmApi.IntelligenceTier[]): TierGroup[] => {
  const parseMultiplier = (value: string | null) => {
    if (!value) return null
    const parsed = Number(value)
    return Number.isNaN(parsed) ? null : parsed
  }
  const tiersByKey: Record<string, Tier[]> = {}
  tiers.forEach((tier) => {
    const key = getTierKey(tier)
    if (!tiersByKey[key]) tiersByKey[key] = []
    tiersByKey[key].push(tier)
  })

  const groups: TierGroup[] = intelligenceTiers.map((tier) => ({
    key: tier.key,
    label: tier.display_name,
    rank: tier.rank,
    creditMultiplier: tier.credit_multiplier,
    tiers: tiersByKey[tier.key] ?? [],
    style: getTierStyle(tier.key),
  }))

  const knownKeys = new Set(intelligenceTiers.map((tier) => tier.key))
  Object.entries(tiersByKey).forEach(([key, values]) => {
    if (knownKeys.has(key)) return
    const meta = values[0]?.intelligenceTier
    groups.push({
      key,
      label: meta?.display_name ?? key,
      rank: meta?.rank ?? Number.MAX_SAFE_INTEGER,
      creditMultiplier: meta?.credit_multiplier ?? null,
      tiers: values,
      style: getTierStyle(key),
    })
  })

  groups.forEach((group) => {
    group.tiers.sort((a, b) => a.order - b.order)
  })
  groups.sort((a, b) => {
    const aMultiplier = parseMultiplier(a.creditMultiplier)
    const bMultiplier = parseMultiplier(b.creditMultiplier)
    if (aMultiplier !== null && bMultiplier !== null && aMultiplier !== bMultiplier) {
      return aMultiplier - bMultiplier
    }
    if (aMultiplier !== null && bMultiplier === null) return -1
    if (aMultiplier === null && bMultiplier !== null) return 1
    if (a.rank !== b.rank) return a.rank - b.rank
    return a.label.localeCompare(b.label)
  })
  return groups
}

export type EndpointTestStatus = {
  state: 'pending' | 'success' | 'error'
  message: string
  preview?: string
  latencyMs?: number | null
  totalTokens?: number | null
  promptTokens?: number | null
  completionTokens?: number | null
  updatedAt: number
}

export type EndpointFormValues = {
  model: string
  litellm_pricing_model?: string
  temperature?: string
  supportsTemperature?: boolean
  api_base?: string
  browser_base_url?: string
  max_output_tokens?: string
  max_input_tokens?: string
  supportsToolChoice?: boolean
  useParallelToolCalls?: boolean
  allowImpliedSend?: boolean
  supportsVision?: boolean
  supportsImageToImage?: boolean
  supportsImageToVideo?: boolean
  supportsReasoning?: boolean
  reasoningEffort?: string | null
  openrouterPreset?: string
  lowLatency?: boolean
}

export const actionKey = (...parts: Array<string | number | null | undefined>) => parts.filter(Boolean).join(':')

export type ImageGenerationSectionConfig = {
  useCase: ImageGenerationUseCase
  title: string
  description: string
  emptyText: string
  addSuccessMessage: string
  addLabel: string
  addContext: string
  removeMessage: string
  removeSuccessMessage: string
  removeLabel: string
  moveUpLabel: string
  moveDownLabel: string
  moveContext: string
}

export type VideoGenerationSectionConfig = {
  useCase: VideoGenerationUseCase
  title: string
  description: string
  emptyText: string
  addSuccessMessage: string
  addLabel: string
  addContext: string
  removeMessage: string
  removeSuccessMessage: string
  removeLabel: string
  moveUpLabel: string
  moveDownLabel: string
  moveContext: string
}

export const IMAGE_GENERATION_SECTION_CONFIG: Record<ImageGenerationUseCase, ImageGenerationSectionConfig> = {
  create_image: {
    useCase: 'create_image',
    title: 'Create image tiers',
    description: 'Fallback order for image generation models used by the create_image tool.',
    emptyText: 'No create_image tiers configured.',
    addSuccessMessage: 'Image generation tier added',
    addLabel: 'Creating image generation tier…',
    addContext: 'Image generation tiers',
    removeMessage: 'Any weighting rules tied to this tier will be lost.',
    removeSuccessMessage: 'Image generation tier removed',
    removeLabel: 'Removing image generation tier…',
    moveUpLabel: 'Moving image generation tier up…',
    moveDownLabel: 'Moving image generation tier down…',
    moveContext: 'Image generation tiers',
  },
  avatar: {
    useCase: 'avatar',
    title: 'Avatar image tiers',
    description: 'Fallback order for agent avatar rendering. If no avatar tiers are configured, avatar generation falls back to create_image tiers.',
    emptyText: 'No avatar image tiers configured. Avatar generation will use create_image tiers.',
    addSuccessMessage: 'Avatar image tier added',
    addLabel: 'Creating avatar image tier…',
    addContext: 'Avatar image tiers',
    removeMessage: 'Avatar generation will fall back to create_image tiers when no avatar tiers remain.',
    removeSuccessMessage: 'Avatar image tier removed',
    removeLabel: 'Removing avatar image tier…',
    moveUpLabel: 'Moving avatar image tier up…',
    moveDownLabel: 'Moving avatar image tier down…',
    moveContext: 'Avatar image tiers',
  },
}

export const VIDEO_GENERATION_SECTION_CONFIG: Record<VideoGenerationUseCase, VideoGenerationSectionConfig> = {
  create_video: {
    useCase: 'create_video',
    title: 'Create video tiers',
    description: 'Fallback order for video generation models used by the create_video tool.',
    emptyText: 'No create_video tiers configured.',
    addSuccessMessage: 'Video generation tier added',
    addLabel: 'Creating video generation tier…',
    addContext: 'Video generation tiers',
    removeMessage: 'Any weighting rules tied to this tier will be lost.',
    removeSuccessMessage: 'Video generation tier removed',
    removeLabel: 'Removing video generation tier…',
    moveUpLabel: 'Moving video generation tier up…',
    moveDownLabel: 'Moving video generation tier down…',
    moveContext: 'Video generation tiers',
  },
}

export type ActivityNotice = {
  id: string
  intent: 'success' | 'error'
  message: string
  context?: string
}

export type MutationOptions = {
  label?: string
  successMessage?: string
  context?: string
  busyKey?: string
  busyKeys?: string[]
  rethrow?: boolean
}

export type ConfirmDialogConfig = {
  title: string
  message: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  intent?: 'danger' | 'primary'
  onConfirm: () => Promise<void> | void
}

export type AsyncFeedback = {
  runWithFeedback: <T>(operation: () => Promise<T>, options?: MutationOptions) => Promise<T>
  isBusy: (key: string) => boolean
  activeLabels: string[]
  notices: ActivityNotice[]
  dismissNotice: (id: string) => void
}

function feedbackErrorMessage(error: unknown) {
  if (error instanceof HttpError) {
    if (typeof error.body === 'string' && error.body.trim()) {
      return error.body
    }
    if (typeof error.body === 'object' && error.body && 'message' in error.body) {
      return String((error.body as { message?: unknown }).message || error.message)
    }
    if (typeof error.body === 'object' && error.body && 'error' in error.body) {
      return String((error.body as { error?: unknown }).error || error.message)
    }
    return error.message
  }
  return error instanceof Error ? error.message : 'Request failed'
}

export function useAsyncFeedback(): AsyncFeedback {
  const [busyCounts, setBusyCounts] = useState<Record<string, number>>({})
  const [labelCounts, setLabelCounts] = useState<Record<string, number>>({})
  const [notices, setNotices] = useState<ActivityNotice[]>([])
  const noticeSeqRef = useRef(0)

  const adjustCounter = (setter: Dispatch<SetStateAction<Record<string, number>>>, key: string, delta: number) => {
    if (!key) return
    setter((prev) => {
      const next = { ...prev }
      next[key] = (next[key] ?? 0) + delta
      if (next[key] <= 0) {
        delete next[key]
      }
      return next
    })
  }

  const pushNotice = (notice: ActivityNotice) => {
    setNotices((prev) => [...prev, notice])
    if (notice.intent === 'success' && typeof window !== 'undefined') {
      window.setTimeout(() => {
        setNotices((current) => current.filter((entry) => entry.id !== notice.id))
      }, 4000)
    }
  }

  const runWithFeedback = async <T,>(operation: () => Promise<T>, options: MutationOptions = {}) => {
    const { label, successMessage, context, busyKey, busyKeys = [] } = options
    const activeBusyKeys = [busyKey, ...busyKeys].filter((key): key is string => Boolean(key))
    activeBusyKeys.forEach((key) => adjustCounter(setBusyCounts, key, 1))
    if (label) adjustCounter(setLabelCounts, label, 1)
    try {
      const result = await operation()
      if (successMessage) {
        const notice: ActivityNotice = {
          id: `notice-${noticeSeqRef.current += 1}`,
          intent: 'success',
          message: successMessage,
          context,
        }
        pushNotice(notice)
      }
      return result
    } catch (error) {
      const message = feedbackErrorMessage(error)
      const notice: ActivityNotice = {
        id: `notice-${noticeSeqRef.current += 1}`,
        intent: 'error',
        message,
        context,
      }
      pushNotice(notice)
      throw error
    } finally {
      if (label) adjustCounter(setLabelCounts, label, -1)
      activeBusyKeys.forEach((key) => adjustCounter(setBusyCounts, key, -1))
    }
  }

  return {
    runWithFeedback,
    isBusy: (key: string) => Boolean(key && busyCounts[key]),
    activeLabels: Object.keys(labelCounts),
    notices,
    dismissNotice: (id: string) => setNotices((prev) => prev.filter((notice) => notice.id !== id)),
  }
}

export const UNIT_SCALE = 10000
export const MIN_SERVER_UNIT = 1 / UNIT_SCALE
export const clampUnit = (value: number) => Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0))
export const roundToDisplayUnit = (value: number) => Math.round(clampUnit(value) * 100) / 100
export const parseUnitInput = (value: number) => clampUnit(Number.isFinite(value) ? value : 0)

export type WeightEntry = { id: string; unit: number }

export const normalizeServerWeight = (weight?: number | null) => {
  if (typeof weight !== 'number' || Number.isNaN(weight)) return 0
  if (!Number.isFinite(weight)) return 0
  if (weight > 1 + 1e-6) return clampUnit(weight / 100)
  if (weight < 0) return 0
  return clampUnit(weight)
}

export const normalizeWeightEntries = (entries: WeightEntry[]): WeightEntry[] => {
  if (!entries.length) return []
  const sanitized = entries.map((entry) => ({ id: entry.id, unit: clampUnit(entry.unit) }))
  const total = sanitized.reduce((sum, entry) => sum + entry.unit, 0)
  let normalized = sanitized
  if (total <= 0) {
    const evenShare = 1 / sanitized.length
    normalized = sanitized.map((entry) => ({ id: entry.id, unit: evenShare }))
  } else {
    normalized = sanitized.map((entry) => ({ id: entry.id, unit: entry.unit / total }))
  }

  const scaled = normalized.map((entry, index) => {
    const scaledValue = entry.unit * UNIT_SCALE
    const base = Math.floor(scaledValue)
    return {
      id: entry.id,
      order: index,
      base,
      fraction: scaledValue - base,
    }
  })
  let remainder = UNIT_SCALE - scaled.reduce((sum, entry) => sum + entry.base, 0)
  const allocationOrder = [...scaled].sort((a, b) => b.fraction - a.fraction)
  let idx = 0
  while (remainder > 0 && idx < allocationOrder.length) {
    allocationOrder[idx].base += 1
    remainder -= 1
    idx += 1
  }
  allocationOrder.sort((a, b) => a.order - b.order)
  return allocationOrder.map((entry) => ({ id: entry.id, unit: entry.base / UNIT_SCALE }))
}

export const entriesToMap = (entries: WeightEntry[]) => {
  const map: Record<string, number> = {}
  entries.forEach((entry) => {
    map[entry.id] = entry.unit
  })
  return map
}

export const normalizeTierEndpointWeights = (endpoints: llmApi.TierEndpoint[]) =>
  entriesToMap(
    normalizeWeightEntries(
      endpoints.map((endpoint) => ({ id: endpoint.id, unit: normalizeServerWeight(endpoint.weight) })),
    ),
  )

export const evenWeightMap = (endpointIds: string[]) => {
  if (!endpointIds.length) return {}
  const baseEntries = endpointIds.map((id) => ({ id, unit: 1 / endpointIds.length }))
  return entriesToMap(normalizeWeightEntries(baseEntries))
}

export const resolveTierUnits = (tier: Tier, pendingWeights: Record<string, number>) =>
  tier.endpoints.map((endpoint) => ({
    id: endpoint.id,
    unit: clampUnit(pendingWeights[endpoint.id] ?? endpoint.weight ?? 0),
  }))

export const rebalanceTierWeights = (
  tier: Tier,
  tierEndpointId: string,
  desiredUnit: number,
  pendingWeights: Record<string, number>,
) => {
  const entries = resolveTierUnits(tier, pendingWeights)
  if (!entries.length) return []
  const targetIndex = entries.findIndex((entry) => entry.id === tierEndpointId)
  if (targetIndex === -1) return []

  const targetUnit = clampUnit(desiredUnit)
  const others = entries.filter((entry) => entry.id !== tierEndpointId)
  const remainder = clampUnit(1 - targetUnit)

  let redistributed: WeightEntry[] = []
  if (others.length) {
    const otherTotal = others.reduce((sum, entry) => sum + entry.unit, 0)
    if (remainder <= 0) {
      redistributed = others.map((entry) => ({ id: entry.id, unit: 0 }))
    } else if (otherTotal > 0) {
      redistributed = others.map((entry) => ({ id: entry.id, unit: (entry.unit / otherTotal) * remainder }))
    } else {
      const share = remainder / others.length
      redistributed = others.map((entry) => ({ id: entry.id, unit: share }))
    }
  }

  const normalized = normalizeWeightEntries([{ id: tierEndpointId, unit: targetUnit }, ...redistributed])
  return normalized.map((entry) => ({ id: entry.id, weight: entry.unit }))
}

export const ensureServerUnits = (entries: WeightEntry[]): Record<string, number> => {
  if (!entries.length) return {}
  const ints = entries.map((entry) => ({ id: entry.id, value: Math.round(entry.unit * UNIT_SCALE) }))
  let total = ints.reduce((sum, entry) => sum + entry.value, 0)

  if (total !== UNIT_SCALE) {
    const diff = UNIT_SCALE - total
    if (diff > 0) {
      // allocate missing units
      const allocationOrder = [...ints].sort((a, b) => a.value - b.value)
      let remaining = diff
      allocationOrder.forEach((entry) => {
        if (remaining <= 0) return
        entry.value += 1
        remaining -= 1
      })
    } else {
      let remaining = Math.abs(diff)
      const reducibleOrder = [...ints].sort((a, b) => b.value - a.value)
      reducibleOrder.forEach((entry) => {
        if (remaining <= 0) return
        const reducible = entry.value
        if (reducible <= 0) return
        const delta = Math.min(reducible, remaining)
        entry.value -= delta
        remaining -= delta
      })
    }
    total = ints.reduce((sum, entry) => sum + entry.value, 0)
  }

  const minUnits = Math.round(MIN_SERVER_UNIT * UNIT_SCALE) || 1
  ints.forEach((entry) => {
    if (entry.value < minUnits) entry.value = minUnits
  })

  let surplus = ints.reduce((sum, entry) => sum + entry.value, 0) - UNIT_SCALE
  if (surplus > 0) {
    const donors = [...ints].sort((a, b) => b.value - a.value)
    donors.forEach((entry) => {
      if (surplus <= 0) return
      const reducible = entry.value - minUnits
      if (reducible <= 0) return
      const delta = Math.min(reducible, surplus)
      entry.value -= delta
      surplus -= delta
    })
  }

  const map: Record<string, number> = {}
  ints.forEach((entry) => {
    map[entry.id] = entry.value / UNIT_SCALE
  })
  return map
}

export const encodeServerWeight = (unit: number) => Number(clampUnit(unit).toFixed(4))

export function distributeEvenWeights(endpointIds: string[]): Record<string, number> {
  return evenWeightMap(endpointIds)
}

export const parseNumber = (value?: string) => {
  if (value === undefined) return undefined
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number(trimmed)
  return Number.isNaN(parsed) ? undefined : parsed
}

export const formatNullableNumber = (value?: number | null, suffix = '') => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—'
  return `${Number.isInteger(value) ? value.toString() : value.toFixed(2)}${suffix}`
}

export function mapProviders(input: llmApi.Provider[] = []): ProviderCardData[] {
  return input.map((provider) => ({
    id: provider.id,
    name: provider.name,
    key: provider.key,
    status: provider.status,
    backend: provider.browser_backend,
    fallback: provider.env_var || 'Not configured',
    envVar: provider.env_var,
    modelPrefix: provider.model_prefix,
    supportsSafety: provider.supports_safety_identifier,
    vertexProject: provider.vertex_project,
    vertexLocation: provider.vertex_location,
    enabled: provider.enabled,
    endpoints: provider.endpoints.map((endpoint) => ({
      id: endpoint.id,
      key: endpoint.key,
      name: endpoint.model,
      enabled: endpoint.enabled,
      litellm_pricing_model: endpoint.litellm_pricing_model ?? null,
      api_base: endpoint.api_base,
      browser_base_url: endpoint.browser_base_url,
      max_output_tokens: endpoint.max_output_tokens ?? null,
      max_input_tokens: endpoint.max_input_tokens ?? null,
      temperature: endpoint.temperature_override ?? null,
      supports_temperature: endpoint.supports_temperature ?? true,
      supports_vision: endpoint.supports_vision,
      supports_image_to_image: endpoint.supports_image_to_image,
      supports_image_to_video: endpoint.supports_image_to_video,
      supports_tool_choice: endpoint.supports_tool_choice,
      use_parallel_tool_calls: endpoint.use_parallel_tool_calls,
      allow_implied_send: endpoint.allow_implied_send,
      supports_reasoning: endpoint.supports_reasoning,
      reasoning_effort: endpoint.reasoning_effort ?? null,
      openrouter_preset: endpoint.openrouter_preset ?? null,
      low_latency: endpoint.low_latency,
      type: endpoint.type,
      tierUsage: endpoint.tier_usage ?? [],
    })),
  }))
}

export const shouldResetName = (name?: string) => {
  if (!name) return true
  const trimmed = name.trim()
  if (!trimmed) return true
  return /^tier\s+\d+$/i.test(trimmed)
}

export const applySequentialFallbackNames = (tiers: Tier[], keySelector: (tier: Tier) => string) => {
  const groups: Record<string, Tier[]> = {}
  tiers.forEach((tier) => {
    const key = keySelector(tier)
    if (!groups[key]) groups[key] = []
    groups[key].push(tier)
  })
  Object.values(groups).forEach((group) => {
    group.sort((a, b) => a.order - b.order)
    group.forEach((tier, index) => {
      if (shouldResetName(tier.name)) {
        tier.name = `Tier ${index + 1}`
      }
    })
  })
}

export function mapPersistentData(ranges: llmApi.TokenRange[] = []): { ranges: TokenRange[]; tiers: Tier[] } {
  const mappedRanges: TokenRange[] = ranges.map((range) => ({
    id: range.id,
    name: range.name,
    min_tokens: range.min_tokens,
    max_tokens: range.max_tokens,
  }))
  const mappedTiers: Tier[] = []
  ranges.forEach((range) => {
    range.tiers.forEach((tier) => {
      const normalized = normalizeTierEndpointWeights(tier.endpoints)
      mappedTiers.push({
        id: tier.id,
        name: (tier.description || '').trim(),
        order: tier.order,
        rangeId: range.id,
        intelligenceTier: tier.intelligence_tier,
        endpoints: tier.endpoints.map((endpoint) => ({
          id: endpoint.id,
          endpointId: endpoint.endpoint_id,
          label: endpoint.label,
          weight: normalized[endpoint.id] ?? 0,
          supportsReasoning: endpoint.supports_reasoning,
          reasoningEffortOverride: endpoint.reasoning_effort_override ?? null,
          endpointReasoningEffort: endpoint.endpoint_reasoning_effort ?? null,
        })),
      })
    })
  })
  applySequentialFallbackNames(mappedTiers, (tier) => `${tier.rangeId}:${getTierKey(tier)}`)
  return { ranges: mappedRanges, tiers: mappedTiers }
}

type WeightedEndpointSource = {
  id: string
  endpoint_id: string
  endpoint_key: string
  label: string
  weight: number
  extraction_endpoint_id?: string | null
  extraction_endpoint_key?: string | null
  extraction_label?: string | null
}

type WeightedTierSource<E extends WeightedEndpointSource = WeightedEndpointSource> = {
  id: string
  description?: string | null
  order: number
  endpoints: E[]
  intelligence_tier?: llmApi.IntelligenceTier | null
}

function mapWeightedEndpoints<E extends WeightedEndpointSource>(
  endpoints: E[],
  extraEndpoint?: (endpoint: E) => Partial<TierEndpoint>,
): TierEndpoint[] {
  const normalized = normalizeTierEndpointWeights(endpoints)
  return endpoints.map((endpoint) => ({
    id: endpoint.id,
    endpointId: endpoint.endpoint_id,
    label: endpoint.label,
    weight: normalized[endpoint.id] ?? 0,
    ...extraEndpoint?.(endpoint),
  }))
}

function mapWeightedTiers<E extends WeightedEndpointSource, T extends WeightedTierSource<E>>(
  tiers: T[],
  {
    rangeId,
    fallbackKey,
    intelligenceTier = () => null,
    extraTier,
    extraEndpoint,
  }: {
    rangeId: string
    fallbackKey: (tier: Tier) => string
    intelligenceTier?: (tier: T) => llmApi.IntelligenceTier | null
    extraTier?: (tier: T) => Partial<Tier>
    extraEndpoint?: (endpoint: E) => Partial<TierEndpoint>
  },
): Tier[] {
  const mapped = tiers.map((tier) => ({
    id: tier.id,
    name: (tier.description || '').trim(),
    order: tier.order,
    rangeId,
    intelligenceTier: intelligenceTier(tier),
    endpoints: mapWeightedEndpoints(tier.endpoints, extraEndpoint),
    ...extraTier?.(tier),
  }))
  applySequentialFallbackNames(mapped, fallbackKey)
  return mapped
}

export function mapBrowserTiers(policy: llmApi.BrowserPolicy | null): Tier[] {
  if (!policy) return []
  return mapWeightedTiers(policy.tiers, {
    rangeId: 'browser',
    fallbackKey: (tier) => `${tier.rangeId}:${getTierKey(tier)}`,
    intelligenceTier: (tier) => tier.intelligence_tier,
    extraEndpoint: (endpoint) => ({
      extractionEndpointId: endpoint.extraction_endpoint_id ?? null,
      extractionEndpointKey: endpoint.extraction_endpoint_key ?? null,
      extractionLabel: endpoint.extraction_label ?? null,
    }),
  })
}

export function mapEmbeddingTiers(tiers: llmApi.EmbeddingTier[] = []): Tier[] {
  return mapWeightedTiers(tiers, {
    rangeId: 'embedding',
    fallbackKey: () => 'embedding',
  })
}

export function mapFileHandlerTiers(tiers: llmApi.FileHandlerTier[] = []): Tier[] {
  return mapWeightedTiers(tiers, {
    rangeId: 'file_handler',
    fallbackKey: () => 'file_handler',
  })
}

export function mapImageGenerationTiers(
  tiers: llmApi.ImageGenerationTier[] = [],
  useCase: ImageGenerationUseCase,
): Tier[] {
  return mapWeightedTiers(tiers, {
    rangeId: 'image_generation',
    fallbackKey: () => `image_generation:${useCase}`,
    extraTier: () => ({ imageUseCase: useCase }),
  })
}

export function mapVideoGenerationTiers(
  tiers: llmApi.VideoGenerationTier[] = [],
  useCase: VideoGenerationUseCase,
): Tier[] {
  return mapWeightedTiers(tiers, {
    rangeId: 'video_generation',
    fallbackKey: () => `video_generation:${useCase}`,
  })
}

// Profile-based mapping functions
export function mapBrowserTiersFromProfile(tiers: llmApi.ProfileBrowserTier[] = []): Tier[] {
  return mapWeightedTiers(tiers, {
    rangeId: 'browser',
    fallbackKey: (tier) => `browser:${getTierKey(tier)}`,
    intelligenceTier: (tier) => tier.intelligence_tier,
    extraEndpoint: (endpoint) => ({
      extractionEndpointId: endpoint.extraction_endpoint_id ?? null,
      extractionEndpointKey: endpoint.extraction_endpoint_key ?? null,
      extractionLabel: endpoint.extraction_label ?? null,
    }),
  })
}

export function mapEmbeddingTiersFromProfile(tiers: llmApi.ProfileEmbeddingTier[] = []): Tier[] {
  return mapWeightedTiers(tiers, {
    rangeId: 'embedding',
    fallbackKey: () => 'embedding',
  })
}

export const useLlmConfigFeedback = useAsyncFeedback
