import {
  AlertCircle,
  Atom,
  Globe,
  PlugZap,
  Shield,
  X,
  Plus,
  PlusCircle,
  Trash,
  Trash2,
  ChevronUp,
  ChevronDown,
  KeyRound,
  ShieldCheck,
  LoaderCircle,
  Loader2,
  Clock3,
  BookText,
  Search,
  Layers,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { SectionCard } from '../components/llmConfig/SectionCard'
import { StatCard } from '../components/llmConfig/StatCard'
import * as llmApi from '../api/llmConfig'
import { HttpError } from '../api/http'

const button = {
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

type TierEndpoint = {
  id: string
  endpointId: string
  label: string
  weight: number
}

type Tier = {
  id: string
  name: string
  order: number
  rangeId: string
  premium: boolean
  endpoints: TierEndpoint[]
}

type TokenRange = {
  id: string
  name: string
  min_tokens: number
  max_tokens: number | null
}

type ProviderEndpointCard = {
  id: string
  name: string
  enabled: boolean
  api_base?: string
  browser_base_url?: string
  max_output_tokens?: number | null
  temperature?: number | null
  supports_vision?: boolean
  supports_tool_choice?: boolean
  use_parallel_tool_calls?: boolean
  type: llmApi.ProviderEndpoint['type']
}

type ProviderCardData = {
  id: string
  name: string
  status: string
  backend: string
  fallback: string
  enabled: boolean
  envVar?: string
  supportsSafety: boolean
  vertexProject: string
  vertexLocation: string
  endpoints: ProviderEndpointCard[]
}

type TierScope = 'persistent' | 'browser' | 'embedding'

type EndpointTestStatus = {
  state: 'pending' | 'success' | 'error'
  message: string
  preview?: string
  latencyMs?: number | null
  totalTokens?: number | null
  promptTokens?: number | null
  completionTokens?: number | null
  updatedAt: number
}

type EndpointFormValues = {
  model: string
  temperature?: string
  api_base?: string
  browser_base_url?: string
  max_output_tokens?: string
  supportsToolChoice?: boolean
  useParallelToolCalls?: boolean
  supportsVision?: boolean
}

const actionKey = (...parts: Array<string | number | null | undefined>) => parts.filter(Boolean).join(':')

type ActivityNotice = {
  id: string
  intent: 'success' | 'error'
  message: string
  context?: string
}

type MutationOptions = {
  label?: string
  successMessage?: string
  context?: string
  busyKey?: string
  busyKeys?: string[]
  rethrow?: boolean
}

type ConfirmDialogConfig = {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  intent?: 'danger' | 'primary'
  onConfirm: () => Promise<void> | void
}

type ActiveConfirmDialog = {
  title: string
  message: string
  confirmLabel: string
  cancelLabel: string
  intent: 'danger' | 'primary'
  onConfirm: () => Promise<void> | void
}

type AsyncFeedback = {
  runWithFeedback: <T>(operation: () => Promise<T>, options?: MutationOptions) => Promise<T>
  isBusy: (key: string) => boolean
  activeLabels: string[]
  notices: ActivityNotice[]
  dismissNotice: (id: string) => void
}

function useAsyncFeedback(): AsyncFeedback {
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
      const message = error instanceof Error ? error.message : 'Request failed'
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

const modalRoot = typeof document !== 'undefined' ? document.body : null

function ModalPortal({ children }: { children: ReactNode }) {
  if (!modalRoot) return null
  return createPortal(children, modalRoot)
}

const UNIT_SCALE = 10000
const MIN_SERVER_UNIT = 1 / UNIT_SCALE
const clampUnit = (value: number) => Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0))
const roundToDisplayUnit = (value: number) => Math.round(clampUnit(value) * 100) / 100
const parseUnitInput = (value: number) => clampUnit(Number.isFinite(value) ? value : 0)

type WeightEntry = { id: string; unit: number }

const normalizeServerWeight = (weight?: number | null) => {
  if (typeof weight !== 'number' || Number.isNaN(weight)) return 0
  if (!Number.isFinite(weight)) return 0
  if (weight > 1 + 1e-6) return clampUnit(weight / 100)
  if (weight < 0) return 0
  return clampUnit(weight)
}

const normalizeWeightEntries = (entries: WeightEntry[]): WeightEntry[] => {
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

const entriesToMap = (entries: WeightEntry[]) => {
  const map: Record<string, number> = {}
  entries.forEach((entry) => {
    map[entry.id] = entry.unit
  })
  return map
}

const normalizeTierEndpointWeights = (endpoints: llmApi.TierEndpoint[]) =>
  entriesToMap(
    normalizeWeightEntries(
      endpoints.map((endpoint) => ({ id: endpoint.id, unit: normalizeServerWeight(endpoint.weight) })),
    ),
  )

const evenWeightMap = (endpointIds: string[]) => {
  if (!endpointIds.length) return {}
  const baseEntries = endpointIds.map((id) => ({ id, unit: 1 / endpointIds.length }))
  return entriesToMap(normalizeWeightEntries(baseEntries))
}

const resolveTierUnits = (tier: Tier, pendingWeights: Record<string, number>) =>
  tier.endpoints.map((endpoint) => ({
    id: endpoint.id,
    unit: clampUnit(pendingWeights[endpoint.id] ?? endpoint.weight ?? 0),
  }))

const rebalanceTierWeights = (
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

const ensureServerUnits = (entries: WeightEntry[]): Record<string, number> => {
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

const encodeServerWeight = (unit: number) => Number(clampUnit(unit).toFixed(4))

function distributeEvenWeights(endpointIds: string[]): Record<string, number> {
  return evenWeightMap(endpointIds)
}

const parseNumber = (value?: string) => {
  if (value === undefined) return undefined
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number(trimmed)
  return Number.isNaN(parsed) ? undefined : parsed
}

function mapProviders(input: llmApi.Provider[] = []): ProviderCardData[] {
  return input.map((provider) => ({
    id: provider.id,
    name: provider.name,
    status: provider.status,
    backend: provider.browser_backend,
    fallback: provider.env_var || 'Not configured',
    envVar: provider.env_var,
    supportsSafety: provider.supports_safety_identifier,
    vertexProject: provider.vertex_project,
    vertexLocation: provider.vertex_location,
    enabled: provider.enabled,
    endpoints: provider.endpoints.map((endpoint) => ({
      id: endpoint.id,
      name: endpoint.model,
      enabled: endpoint.enabled,
      api_base: endpoint.api_base,
      browser_base_url: endpoint.browser_base_url,
      max_output_tokens: endpoint.max_output_tokens ?? null,
      temperature: endpoint.temperature_override ?? null,
      supports_vision: endpoint.supports_vision,
      supports_tool_choice: endpoint.supports_tool_choice,
      use_parallel_tool_calls: endpoint.use_parallel_tool_calls,
      type: endpoint.type,
    })),
  }))
}

const shouldResetName = (name?: string) => {
  if (!name) return true
  const trimmed = name.trim()
  if (!trimmed) return true
  return /^tier\s+\d+$/i.test(trimmed)
}

const applySequentialFallbackNames = (tiers: Tier[], keySelector: (tier: Tier) => string) => {
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

function mapPersistentData(ranges: llmApi.TokenRange[] = []): { ranges: TokenRange[]; tiers: Tier[] } {
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
        premium: tier.is_premium,
        endpoints: tier.endpoints.map((endpoint) => ({
          id: endpoint.id,
          endpointId: endpoint.endpoint_id,
          label: endpoint.label,
          weight: normalized[endpoint.id] ?? 0,
        })),
      })
    })
  })
  applySequentialFallbackNames(mappedTiers, (tier) => `${tier.rangeId}:${tier.premium ? 'premium' : 'standard'}`)
  return { ranges: mappedRanges, tiers: mappedTiers }
}

function mapBrowserTiers(policy: llmApi.BrowserPolicy | null): Tier[] {
  if (!policy) return []
  const tiers = policy.tiers.map((tier) => {
    const normalized = normalizeTierEndpointWeights(tier.endpoints)
    return {
      id: tier.id,
      name: (tier.description || '').trim(),
      order: tier.order,
      rangeId: 'browser',
      premium: tier.is_premium,
      endpoints: tier.endpoints.map((endpoint) => ({
        id: endpoint.id,
        endpointId: endpoint.endpoint_id,
        label: endpoint.label,
        weight: normalized[endpoint.id] ?? 0,
      })),
    }
  })
  applySequentialFallbackNames(tiers, (tier) => `${tier.rangeId}:${tier.premium ? 'premium' : 'standard'}`)
  return tiers
}

function mapEmbeddingTiers(tiers: llmApi.EmbeddingTier[] = []): Tier[] {
  const mapped = tiers.map((tier) => {
    const normalized = normalizeTierEndpointWeights(tier.endpoints)
    return {
      id: tier.id,
      name: (tier.description || '').trim(),
      order: tier.order,
      rangeId: 'embedding',
      premium: false,
      endpoints: tier.endpoints.map((endpoint) => ({
        id: endpoint.id,
        endpointId: endpoint.endpoint_id,
        label: endpoint.label,
        weight: normalized[endpoint.id] ?? 0,
      })),
    }
  })
  applySequentialFallbackNames(mapped, () => 'embedding')
  return mapped
}

function AddEndpointModal({
  tier,
  scope,
  choices,
  onAdd,
  onClose,
  busy,
}: {
  tier: Tier
  scope: TierScope
  choices: llmApi.EndpointChoices
  onAdd: (endpointId: string) => Promise<void> | void
  onClose: () => void
  busy?: boolean
}) {
  const endpoints = scope === 'browser'
    ? choices.browser_endpoints
    : scope === 'embedding'
      ? choices.embedding_endpoints
      : choices.persistent_endpoints
  const [selected, setSelected] = useState(endpoints[0]?.id || '')
  const [submitting, setSubmitting] = useState(false)
  const isSubmitting = Boolean(busy || submitting)

  const handleAdd = async () => {
    if (!selected) return
    setSubmitting(true)
    try {
      await onAdd(selected)
      onClose()
    } catch {
      // feedback already shown
    } finally {
      setSubmitting(false)
    }
  }
  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/60">
        <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">Add endpoint to {tier.name}</h3>
            <button onClick={onClose} className={button.icon}>
              <X className="size-5" />
            </button>
          </div>
          <div className="mt-4">
            {endpoints.length === 0 ? (
              <p className="text-sm text-slate-500">No endpoints available for this tier.</p>
            ) : (
              <>
                <label className="text-sm font-medium text-slate-700">Endpoint</label>
                <select
                  className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                  value={selected}
                  onChange={(event) => setSelected(event.target.value)}
                >
                  {endpoints.map((endpoint) => (
                    <option key={endpoint.id} value={endpoint.id}>
                      {endpoint.label || endpoint.model}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
          <div className="mt-6 flex justify-end gap-3">
            <button type="button" className={button.secondary} onClick={onClose} disabled={isSubmitting}>
              Cancel
            </button>
            <button
              type="button"
              className={button.primary}
              onClick={handleAdd}
              disabled={!selected || isSubmitting}
            >
              {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} Add endpoint
            </button>
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}

function ConfirmActionModal({
  title,
  message,
  confirmLabel,
  cancelLabel,
  intent,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string
  message: string
  confirmLabel: string
  cancelLabel: string
  intent: 'danger' | 'primary'
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const accentClasses =
    intent === 'danger'
      ? { iconBg: 'bg-rose-100 text-rose-600', button: 'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-700 focus:outline-none focus:ring-2 focus:ring-rose-500/40 disabled:opacity-50' }
      : { iconBg: 'bg-blue-100 text-blue-600', button: button.primary }
  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[210] flex items-center justify-center bg-slate-900/60">
        <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl space-y-4">
          <div className="flex items-start gap-3">
            <div className={`rounded-full p-2 ${accentClasses.iconBg}`}>
              <AlertCircle className="size-5" />
            </div>
            <div className="space-y-1">
              <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
              <p className="text-sm text-slate-600">{message}</p>
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" className={button.secondary} onClick={onCancel} disabled={busy}>
              {cancelLabel}
            </button>
            <button type="button" className={accentClasses.button} onClick={onConfirm} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              <span>{confirmLabel}</span>
            </button>
          </div>
        </div>
      </div>
    </ModalPortal>
  )
}

type ProviderCardHandlers = {
  onRotateKey: (provider: ProviderCardData) => Promise<void>
  onToggleEnabled: (provider: ProviderCardData, enabled: boolean) => Promise<void>
  onAddEndpoint: (provider: ProviderCardData, type: llmApi.ProviderEndpoint['type'], values: EndpointFormValues & { key: string }) => Promise<void>
  onSaveEndpoint: (endpoint: ProviderEndpointCard, values: EndpointFormValues) => Promise<void>
  onDeleteEndpoint: (endpoint: ProviderEndpointCard) => Promise<void>
  onClearKey: (provider: ProviderCardData) => Promise<void>
  onTestEndpoint: (endpoint: ProviderEndpointCard) => Promise<void>
}

function ProviderCard({ provider, handlers, isBusy, testStatuses }: { provider: ProviderCardData; handlers: ProviderCardHandlers; isBusy: (key: string) => boolean; testStatuses: Record<string, EndpointTestStatus | undefined> }) {
  const [activeTab, setActiveTab] = useState<'endpoints' | 'settings'>('endpoints')
  const [editingEndpointId, setEditingEndpointId] = useState<string | null>(null)
  const [addingType, setAddingType] = useState<llmApi.ProviderEndpoint['type'] | null>(null)
  const rotateBusy = isBusy(actionKey('provider', provider.id, 'rotate'))
  const clearBusy = isBusy(actionKey('provider', provider.id, 'clear'))
  const toggleBusy = isBusy(actionKey('provider', provider.id, 'toggle'))
  const creatingEndpoint = isBusy(actionKey('provider', provider.id, 'create-endpoint'))

  return (
    <article className="rounded-2xl border border-slate-200/80 bg-white">
      <div className="flex items-center justify-between p-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900/90">{provider.name}</h3>
          <p className="text-xs text-slate-500">{provider.endpoints.length} endpoints</p>
        </div>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${provider.enabled ? 'bg-emerald-50/80 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
          <ShieldCheck className="size-3.5" /> {provider.status}
        </span>
      </div>
      <div className="border-b border-slate-200/80 px-4">
        <nav className="-mb-px flex space-x-6" aria-label="Tabs">
          <button onClick={() => setActiveTab('endpoints')} className={`whitespace-nowrap border-b-2 py-2 px-1 text-sm font-medium ${activeTab === 'endpoints' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
            Endpoints
          </button>
          <button onClick={() => setActiveTab('settings')} className={`whitespace-nowrap border-b-2 py-2 px-1 text-sm font-medium ${activeTab === 'settings' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
            Settings
          </button>
        </nav>
      </div>
      <div className="p-4 space-y-4">
        {activeTab === 'endpoints' && (
          <>
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-600">Manage provider endpoints</p>
              <div className="flex gap-2">
                <button className={button.secondary} onClick={() => setAddingType('persistent')}>
                  <Plus className="size-4" /> Persistent
                </button>
                <button className={button.secondary} onClick={() => setAddingType('browser')}>
                  <Plus className="size-4" /> Browser
                </button>
                <button className={button.secondary} onClick={() => setAddingType('embedding')}>
                  <Plus className="size-4" /> Embedding
                </button>
              </div>
            </div>
            {provider.endpoints.length === 0 && <p className="text-sm text-slate-500">No endpoints linked.</p>}
            <div className="space-y-3">
              {provider.endpoints.map((endpoint) => {
                const isEditing = editingEndpointId === endpoint.id
                const deleteBusy = isBusy(actionKey('endpoint', endpoint.id, 'delete'))
                const endpointSaving = isBusy(actionKey('endpoint', endpoint.id, 'update'))
                const testBusy = isBusy(actionKey('endpoint', endpoint.id, 'test'))
                const status = testStatuses[endpoint.id]
                const tone = status?.state === 'success'
                  ? 'text-emerald-600'
                  : status?.state === 'error'
                    ? 'text-rose-600'
                    : 'text-slate-500'
                const isPendingStatus = status?.state === 'pending'
                const headline = status?.state === 'success'
                  ? 'Success:'
                  : status?.state === 'error'
                    ? 'Error:'
                    : 'Testing…'
                return (
                  <div key={endpoint.id} className="rounded-lg border border-slate-200 p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold text-slate-900/90">{endpoint.name}</p>
                        <p className="text-xs text-slate-500 uppercase">{endpoint.type}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button type="button" className={button.secondary} onClick={() => handlers.onTestEndpoint(endpoint).catch(() => {})} disabled={testBusy}>
                          {testBusy ? <Loader2 className="size-4 animate-spin" /> : 'Test'}
                        </button>
                        <button className={button.muted} onClick={() => setEditingEndpointId(isEditing ? null : endpoint.id)}>
                          {isEditing ? 'Close' : 'Edit'}
                        </button>
                        <button className={button.iconDanger} onClick={() => handlers.onDeleteEndpoint(endpoint).catch(() => {})} disabled={deleteBusy}>
                          {deleteBusy ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                        </button>
                      </div>
                    </div>
                    {isEditing && (
                      <EndpointEditor
                        endpoint={endpoint}
                        saving={endpointSaving}
                        onCancel={() => setEditingEndpointId(null)}
                        onSave={async (values) => {
                          try {
                            await handlers.onSaveEndpoint(endpoint, values)
                            setEditingEndpointId(null)
                          } catch {
                            // feedback already shown
                          }
                        }}
                      />
                    )}
                    {status && (
                      <div className={`mt-3 text-xs ${tone}`}>
                        <p className="font-medium">
                          {isPendingStatus
                            ? status.message
                            : (
                              <>
                                <span>{headline}</span>
                                {status.message ? ` ${status.message}` : ''}
                              </>
                            )}
                        </p>
                        {status.state === 'success' && (
                          <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-slate-500">
                            {status.latencyMs != null && <span>Latency: {status.latencyMs} ms</span>}
                            {status.totalTokens != null && <span>Total tokens: {status.totalTokens}</span>}
                            {status.promptTokens != null && <span>Prompt: {status.promptTokens}</span>}
                            {status.completionTokens != null && <span>Completion: {status.completionTokens}</span>}
                          </div>
                        )}
                        {status.preview ? (
                          <p className="mt-1 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
                            Preview: <span className="font-mono">{status.preview}</span>
                          </p>
                        ) : null}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
        {activeTab === 'settings' && (
          <div className="space-y-4 text-sm text-slate-600">
            <div>
              <p className="font-semibold text-slate-900/90">Environment fallback</p>
              <p className="text-xs text-slate-500 break-all">{provider.fallback}</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-slate-500 uppercase">Backend</p>
                <p className="font-medium text-slate-900/90">{provider.backend}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Safety identifiers</p>
                <p className="font-medium text-slate-900/90">{provider.supportsSafety ? 'Supported' : 'Disabled'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Vertex project</p>
                <p className="font-medium text-slate-900/90">{provider.vertexProject || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Vertex location</p>
                <p className="font-medium text-slate-900/90">{provider.vertexLocation || '—'}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className={button.primary} onClick={() => handlers.onRotateKey(provider).catch(() => {})} disabled={rotateBusy}>
                {rotateBusy ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />} Rotate key
              </button>
              <button className={button.secondary} onClick={() => handlers.onClearKey(provider).catch(() => {})} disabled={clearBusy}>
                {clearBusy ? <Loader2 className="size-4 animate-spin" /> : null} Clear key
              </button>
              <button className={button.muted} onClick={() => handlers.onToggleEnabled(provider, !provider.enabled).catch(() => {})} disabled={toggleBusy}>
                {toggleBusy ? 'Working…' : provider.enabled ? 'Disable provider' : 'Enable provider'}
              </button>
            </div>
          </div>
        )}
      </div>
      {addingType && (
        <AddProviderEndpointModal
          providerName={provider.name}
          type={addingType}
          busy={creatingEndpoint}
          onClose={() => setAddingType(null)}
          onSubmit={async (values) => {
            try {
              await handlers.onAddEndpoint(provider, addingType!, values)
              setAddingType(null)
            } catch {
              // feedback already shown
            }
          }}
        />
      )}
    </article>
  )
}

type EndpointEditorProps = {
  endpoint: ProviderEndpointCard
  saving?: boolean
  onSave: (values: EndpointFormValues) => Promise<void> | void
  onCancel: () => void
}

function EndpointEditor({ endpoint, onSave, onCancel, saving }: EndpointEditorProps) {
  const [model, setModel] = useState(endpoint.name)
  const [temperature, setTemperature] = useState(endpoint.temperature?.toString() ?? '')
  const [apiBase, setApiBase] = useState(endpoint.api_base || endpoint.browser_base_url || '')
  const [maxTokens, setMaxTokens] = useState(endpoint.max_output_tokens?.toString() ?? '')
  const [supportsVision, setSupportsVision] = useState(Boolean(endpoint.supports_vision))
  const [supportsToolChoice, setSupportsToolChoice] = useState(Boolean(endpoint.supports_tool_choice))
  const [parallelTools, setParallelTools] = useState(Boolean(endpoint.use_parallel_tool_calls))

  const handleSave = () => {
    const values: EndpointFormValues = {
      model,
      temperature,
      api_base: apiBase,
      browser_base_url: apiBase,
      max_output_tokens: maxTokens,
      supportsToolChoice: supportsToolChoice,
      useParallelToolCalls: parallelTools,
      supportsVision: supportsVision,
    }
    onSave(values)
  }

  const isBrowser = endpoint.type === 'browser'
  const isEmbedding = endpoint.type === 'embedding'

  return (
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-slate-500">Model identifier</label>
          <input value={model} onChange={(event) => setModel(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
        </div>
        {!isBrowser && (
          <div>
            <label className="text-xs text-slate-500">Temperature override</label>
            <input type="number" value={temperature} onChange={(event) => setTemperature(event.target.value)} placeholder="auto" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
        )}
        <div>
          <label className="text-xs text-slate-500">API base URL</label>
          <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="https://api.example.com/v1" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
        </div>
        {isBrowser && (
          <div>
            <label className="text-xs text-slate-500">Max output tokens</label>
            <input type="number" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} placeholder="Default" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-4 text-sm">
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={supportsVision} onChange={(event) => setSupportsVision(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
          Vision
        </label>
        {!isEmbedding && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={supportsToolChoice} onChange={(event) => setSupportsToolChoice(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Tool choice
          </label>
        )}
        {!isEmbedding && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={parallelTools} onChange={(event) => setParallelTools(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Parallel calls
          </label>
        )}
      </div>
      <div className="flex justify-end gap-2">
        <button className={button.secondary} onClick={onCancel} disabled={saving}>Cancel</button>
        <button className={button.primary} onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null} Save changes
        </button>
      </div>
    </div>
  )
}

type AddProviderEndpointModalProps = {
  providerName: string
  type: llmApi.ProviderEndpoint['type']
  busy?: boolean
  onSubmit: (values: EndpointFormValues & { key: string }) => Promise<void> | void
  onClose: () => void
}

function AddProviderEndpointModal({ providerName, type, onSubmit, onClose, busy }: AddProviderEndpointModalProps) {
  const [key, setKey] = useState('')
  const [model, setModel] = useState('')
  const [apiBase, setApiBase] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const [supportsVision, setSupportsVision] = useState(false)
  const [supportsTools, setSupportsTools] = useState(true)
  const [parallelTools, setParallelTools] = useState(true)
  const [temperature, setTemperature] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const isSubmitting = busy || submitting

  const title = {
    persistent: 'Add persistent endpoint',
    browser: 'Add browser endpoint',
    embedding: 'Add embedding endpoint',
  }[type]

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      await onSubmit({
        key,
        model,
        api_base: apiBase,
        browser_base_url: apiBase,
        max_output_tokens: maxTokens,
        supportsVision,
        supportsToolChoice: supportsTools,
        useParallelToolCalls: parallelTools,
        temperature,
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/60">
        <div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-2xl">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">{title}</h3>
            <button onClick={onClose} className={button.icon}>
              <X className="size-5" />
            </button>
        </div>
        <p className="text-sm text-slate-500 mt-1">{providerName}</p>
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-500">Endpoint key</label>
              <input value={key} onChange={(event) => setKey(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
            </div>
            <div>
              <label className="text-xs text-slate-500">Model identifier</label>
              <input value={model} onChange={(event) => setModel(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
            </div>
            {(type === 'persistent' || type === 'embedding') && (
              <div>
                <label className="text-xs text-slate-500">Temperature override</label>
                <input type="number" value={temperature} onChange={(event) => setTemperature(event.target.value)} placeholder="auto" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
              </div>
            )}
            {type === 'browser' && (
              <div>
                <label className="text-xs text-slate-500">Max output tokens</label>
                <input type="number" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} placeholder="Default" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
              </div>
            )}
            <div className="md:col-span-2">
              <label className="text-xs text-slate-500">API base URL</label>
              <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="https://api.example.com/v1" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
            </div>
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={supportsVision} onChange={(event) => setSupportsVision(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
              Vision
            </label>
            {type !== 'embedding' && (
              <>
                <label className="inline-flex items-center gap-2">
                  <input type="checkbox" checked={supportsTools} onChange={(event) => setSupportsTools(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
                  Tool choice
                </label>
                <label className="inline-flex items-center gap-2">
                  <input type="checkbox" checked={parallelTools} onChange={(event) => setParallelTools(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
                  Parallel calls
                </label>
              </>
            )}
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button className={button.secondary} onClick={onClose} disabled={isSubmitting}>Cancel</button>
          <button className={button.primary} onClick={handleSubmit} disabled={!key || !model || isSubmitting}>
            {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} Add endpoint
          </button>
        </div>
      </div>
      </div>
    </ModalPortal>
  )
}

function ActivityDock({
  notices,
  activeLabels,
  onDismiss,
}: {
  notices: ActivityNotice[]
  activeLabels: string[]
  onDismiss: (id: string) => void
}) {
  if (notices.length === 0 && activeLabels.length === 0) return null
  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-30 flex w-full max-w-sm flex-col gap-3">
      {activeLabels.length > 0 && (
        <div className="pointer-events-auto rounded-2xl border border-blue-100 bg-white/95 p-4 text-sm text-blue-800 shadow-2xl shadow-blue-100/80 backdrop-blur transition" aria-live="polite">
          <div className="flex items-start gap-3">
            <Loader2 className="size-5 animate-spin text-blue-500" aria-hidden />
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-blue-500">Working on</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {activeLabels.map((label) => (
                  <span key={label} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
      {notices.map((notice) => (
        <div
          key={notice.id}
          className={`pointer-events-auto rounded-2xl border px-4 py-3 text-sm shadow-2xl transition ${notice.intent === 'success' ? 'border-emerald-100 bg-white/95 text-emerald-900 shadow-emerald-100/70' : 'border-rose-200 bg-white text-rose-900 shadow-rose-100/70'}`}
          role="status"
          aria-live="polite"
        >
          <div className="flex items-start gap-3">
            {notice.intent === 'success' ? <ShieldCheck className="mt-0.5 size-4 text-emerald-500" /> : <AlertCircle className="mt-0.5 size-4 text-rose-500" />}
            <div className="flex-1 space-y-0.5">
              {notice.context ? <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{notice.context}</p> : null}
              <p>{notice.message}</p>
            </div>
            <button className={button.icon} onClick={() => onDismiss(notice.id)} aria-label="Dismiss notification">
              <X className="size-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

function TierCard({
  tier,
  pendingWeights,
  scope,
  canMoveUp,
  canMoveDown,
  isDirty,
  isSaving,
  onMove,
  onRemove,
  onAddEndpoint,
  onStageEndpointWeight,
  onCommitEndpointWeights,
  onRemoveEndpoint,
  isActionBusy,
}: {
  tier: Tier
  pendingWeights: Record<string, number>
  scope: TierScope
  canMoveUp: boolean
  canMoveDown: boolean
  isDirty: boolean
  isSaving: boolean
  onMove: (direction: 'up' | 'down') => void
  onRemove: (tier: Tier) => void
  onAddEndpoint: () => void
  onStageEndpointWeight: (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => void
  onCommitEndpointWeights: (tier: Tier, scope: TierScope) => void
  onRemoveEndpoint: (tier: Tier, endpoint: TierEndpoint) => void
  isActionBusy: (key: string) => boolean
}) {
  const headerIcon = tier.premium ? <ShieldCheck className="size-4 text-emerald-700" /> : <Layers className="size-4 text-slate-500" />
  const canAdjustWeights = tier.endpoints.length > 1
  const disabledHint = canAdjustWeights ? '' : 'At least two endpoints are required to rebalance weights.'
  const handleCommit = () => {
    if (!canAdjustWeights) return
    onCommitEndpointWeights(tier, scope)
  }
  const rangeMoveBusy = scope === 'persistent' ? isActionBusy(actionKey('persistent-range', tier.rangeId, 'move')) : false
  const moveBusy = isActionBusy(actionKey(scope, tier.id, 'move'))
  const moveUpBusy = isActionBusy(actionKey(scope, tier.id, 'move', 'up'))
  const moveDownBusy = isActionBusy(actionKey(scope, tier.id, 'move', 'down'))
  const removeBusy = isActionBusy(actionKey(scope, tier.id, 'remove'))
  const addBusy = isActionBusy(actionKey(scope, tier.id, 'attach-endpoint'))
  const removingEndpoint = tier.endpoints.some((endpoint) => isActionBusy(actionKey('tier-endpoint', endpoint.id, 'remove')))
  const upDisabled = moveBusy || rangeMoveBusy || !canMoveUp
  const downDisabled = moveBusy || rangeMoveBusy || !canMoveDown

  const inlineStatus = (() => {
    if (isSaving) {
      return { icon: <Loader2 className="size-3 animate-spin" aria-hidden />, text: 'Saving…', className: 'text-blue-500' }
    }
    if (isDirty) {
      return { icon: <Clock3 className="size-3 animate-pulse" aria-hidden />, text: 'Pending…', className: 'text-amber-500' }
    }
    if (addBusy) {
      return { icon: <Loader2 className="size-3 animate-spin" aria-hidden />, text: 'Adding endpoint…', className: 'text-blue-500' }
    }
    if (removingEndpoint) {
      return { icon: <Loader2 className="size-3 animate-spin" aria-hidden />, text: 'Removing endpoint…', className: 'text-rose-500' }
    }
    return null
  })()
  return (
    <div className={`rounded-xl border ${tier.premium ? 'border-emerald-200' : 'border-slate-200'} bg-white`}>
      <div className="flex items-center justify-between p-4 text-xs uppercase tracking-wide text-slate-500">
        <span className="flex items-center gap-2">{headerIcon} {tier.name}</span>
        <div className="flex items-center gap-1 text-xs">
          <button className={button.icon} type="button" onClick={() => onMove('up')} disabled={upDisabled}>
            {moveUpBusy ? <Loader2 className="size-4 animate-spin" /> : <ChevronUp className={`size-4 ${upDisabled ? 'text-slate-300' : ''}`} />}
          </button>
          <button className={button.icon} type="button" onClick={() => onMove('down')} disabled={downDisabled}>
            {moveDownBusy ? <Loader2 className="size-4 animate-spin" /> : <ChevronDown className={`size-4 ${downDisabled ? 'text-slate-300' : ''}`} />}
          </button>
          <button className={button.iconDanger} type="button" onClick={() => onRemove(tier)} disabled={removeBusy}>
            {removeBusy ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
          </button>
        </div>
      </div>
      <div className="space-y-3 px-4 pb-4">
        <div className="flex items-center justify-between text-[13px] text-slate-500">
          <span>Weighted endpoints</span>
          {inlineStatus ? (
            <span className={`flex items-center gap-1 text-xs ${inlineStatus.className}`} aria-live="polite">
              {inlineStatus.icon} {inlineStatus.text}
            </span>
          ) : null}
        </div>
        <div className="space-y-3">
          {tier.endpoints.map((endpoint) => {
            const unitWeight = pendingWeights[endpoint.id] ?? endpoint.weight
            const displayWeight = roundToDisplayUnit(unitWeight)
            return (
              <div key={endpoint.id} className="grid grid-cols-12 items-center gap-3 text-sm font-medium text-slate-900/90">
                <span className="col-span-6 flex items-center gap-2 truncate" title={endpoint.label}><PlugZap className="size-4 flex-shrink-0 text-slate-400" /> {endpoint.label}</span>
                <div className="col-span-6 flex items-center gap-2">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={displayWeight}
                    onChange={(event) => {
                      if (!canAdjustWeights) return
                      const decimal = parseUnitInput(event.target.valueAsNumber)
                      onStageEndpointWeight(tier, endpoint.id, decimal, scope)
                    }}
                    disabled={!canAdjustWeights}
                    onMouseUp={handleCommit}
                    onTouchEnd={handleCommit}
                    onPointerUp={handleCommit}
                    className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                  />
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={displayWeight.toFixed(2)}
                    onChange={(event) => {
                      if (!canAdjustWeights) return
                      const decimal = parseUnitInput(event.target.valueAsNumber)
                      onStageEndpointWeight(tier, endpoint.id, decimal, scope)
                    }}
                    disabled={!canAdjustWeights}
                    onBlur={handleCommit}
                    inputMode="decimal"
                    className="block w-24 rounded-lg border-slate-300 text-right shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                  />
                  <button onClick={() => onRemoveEndpoint(tier, endpoint)} className={button.iconDanger} aria-label="Remove endpoint">
                    <Trash className="size-4" />
                  </button>
                </div>
              </div>
            )
          })}
          {!canAdjustWeights && tier.endpoints.length > 0 && (
            <p className="text-xs text-slate-400 text-right">{disabledHint}</p>
          )}
        </div>
        <div className="pt-2">
          <button type="button" className={button.muted} onClick={onAddEndpoint} disabled={addBusy}>
            {addBusy ? <Loader2 className="size-3 animate-spin" /> : <PlusCircle className="size-3" />} Add endpoint
          </button>
        </div>
      </div>
    </div>
  )
}

function RangeSection({
  range,
  tiers,
  onUpdate,
  onRemove,
  onAddTier,
  onMoveTier,
  onRemoveTier,
  onAddEndpoint,
  onStageEndpointWeight,
  onCommitEndpointWeights,
  onRemoveEndpoint,
  pendingWeights,
  savingTierIds,
  dirtyTierIds,
  isActionBusy,
}: {
  range: TokenRange
  tiers: Tier[]
  onUpdate: (field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => Promise<void> | void
  onRemove: () => void
  onAddTier: (isPremium: boolean) => void
  onMoveTier: (tierId: string, direction: 'up' | 'down') => void
  onRemoveTier: (tier: Tier) => void
  onAddEndpoint: (tier: Tier) => void
  onStageEndpointWeight: (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => void
  onCommitEndpointWeights: (tier: Tier, scope: TierScope) => void
  onRemoveEndpoint: (tier: Tier, endpoint: TierEndpoint) => void
  pendingWeights: Record<string, number>
  savingTierIds: Set<string>
  dirtyTierIds: Set<string>
  isActionBusy: (key: string) => boolean
}) {
  const standardTiers = tiers.filter((tier) => !tier.premium).sort((a, b) => a.order - b.order)
  const premiumTiers = tiers.filter((tier) => tier.premium).sort((a, b) => a.order - b.order)
  const [nameInput, setNameInput] = useState(range.name)
  const [minInput, setMinInput] = useState(range.min_tokens.toString())
  const [maxInput, setMaxInput] = useState(range.max_tokens?.toString() ?? '')

  useEffect(() => {
    setNameInput(range.name)
    setMinInput(range.min_tokens.toString())
    setMaxInput(range.max_tokens?.toString() ?? '')
  }, [range])

  const nameBusy = isActionBusy(actionKey('range', range.id, 'name'))
  const minBusy = isActionBusy(actionKey('range', range.id, 'min_tokens'))
  const maxBusy = isActionBusy(actionKey('range', range.id, 'max_tokens'))
  const removeBusy = isActionBusy(actionKey('range', range.id, 'remove'))

  const commitField = (field: 'name' | 'min_tokens' | 'max_tokens') => {
    if (field === 'name') {
      const trimmed = nameInput.trim()
      if (!trimmed || trimmed === range.name) {
        setNameInput(range.name)
        return
      }
      Promise.resolve(onUpdate('name', trimmed)).catch(() => setNameInput(range.name))
      return
    }
    if (field === 'min_tokens') {
      const parsed = Number(minInput)
      if (Number.isNaN(parsed)) {
        setMinInput(range.min_tokens.toString())
        return
      }
      if (parsed === range.min_tokens) return
      Promise.resolve(onUpdate('min_tokens', parsed)).catch(() => setMinInput(range.min_tokens.toString()))
      return
    }
    const parsed = maxInput === '' ? null : Number(maxInput)
    if (maxInput !== '' && Number.isNaN(parsed as number)) {
      setMaxInput(range.max_tokens?.toString() ?? '')
      return
    }
    if (parsed === range.max_tokens) return
    Promise.resolve(onUpdate('max_tokens', parsed)).catch(() => setMaxInput(range.max_tokens?.toString() ?? ''))
  }

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white">
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-12 items-center gap-3 text-sm">
          <div className="col-span-12 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Range Name</label>
            <input
              type="text"
              value={nameInput}
              disabled={nameBusy}
              onChange={(event) => setNameInput(event.target.value)}
              onBlur={() => commitField('name')}
              className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="col-span-6 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Min Tokens</label>
            <input
              type="number"
              value={minInput}
              disabled={minBusy}
              onChange={(event) => setMinInput(event.target.value)}
              onBlur={() => commitField('min_tokens')}
              className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="col-span-6 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Max Tokens</label>
            <input
              type="number"
              value={maxInput}
              disabled={maxBusy}
              placeholder="Infinity"
              onChange={(event) => setMaxInput(event.target.value)}
              onBlur={() => commitField('max_tokens')}
              className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="col-span-12 sm:col-span-3 text-right">
            <button type="button" className={button.danger} onClick={onRemove} disabled={removeBusy}>
              {removeBusy ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />} Remove Range
            </button>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
        <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-700">Standard tiers</h4>
            <button type="button" className={button.secondary} onClick={() => onAddTier(false)}>
              <PlusCircle className="size-4" /> Add
            </button>
          </div>
          {standardTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No standard tiers.</p>}
          {standardTiers.map((tier, index) => {
            const lastIndex = standardTiers.length - 1
            return (
              <TierCard
                key={tier.id}
                tier={tier}
                pendingWeights={pendingWeights}
                isDirty={dirtyTierIds.has(`persistent:${tier.id}`)}
                isSaving={savingTierIds.has(`persistent:${tier.id}`)}
                scope="persistent"
                canMoveUp={index > 0}
                canMoveDown={index < lastIndex}
                onMove={(direction) => onMoveTier(tier.id, direction)}
                onRemove={onRemoveTier}
                onAddEndpoint={() => onAddEndpoint(tier)}
                onStageEndpointWeight={(currentTier, endpointId, weight) => onStageEndpointWeight(currentTier, endpointId, weight, 'persistent')}
                onCommitEndpointWeights={(currentTier) => onCommitEndpointWeights(currentTier, 'persistent')}
                onRemoveEndpoint={onRemoveEndpoint}
                isActionBusy={isActionBusy}
              />
            )
          })}
        </div>
        <div className="bg-emerald-50/50 p-4 space-y-3 rounded-xl">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-emerald-800">Premium tiers</h4>
            <button type="button" className={button.secondary} onClick={() => onAddTier(true)}>
              <PlusCircle className="size-4" /> Add
            </button>
          </div>
          {premiumTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No premium tiers.</p>}
          {premiumTiers.map((tier, index) => {
            const lastIndex = premiumTiers.length - 1
            return (
              <TierCard
                key={tier.id}
                tier={tier}
                pendingWeights={pendingWeights}
                isDirty={dirtyTierIds.has(`persistent:${tier.id}`)}
                isSaving={savingTierIds.has(`persistent:${tier.id}`)}
                scope="persistent"
                canMoveUp={index > 0}
                canMoveDown={index < lastIndex}
                onMove={(direction) => onMoveTier(tier.id, direction)}
                onRemove={onRemoveTier}
                onAddEndpoint={() => onAddEndpoint(tier)}
                onStageEndpointWeight={(currentTier, endpointId, weight) => onStageEndpointWeight(currentTier, endpointId, weight, 'persistent')}
                onCommitEndpointWeights={(currentTier) => onCommitEndpointWeights(currentTier, 'persistent')}
                onRemoveEndpoint={onRemoveEndpoint}
                isActionBusy={isActionBusy}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function LlmConfigScreen() {
  const queryClient = useQueryClient()
  const { runWithFeedback, isBusy, activeLabels, notices, dismissNotice } = useAsyncFeedback()
  const [endpointModal, setEndpointModal] = useState<{ tier: Tier; scope: TierScope } | null>(null)
  const [pendingWeights, setPendingWeights] = useState<Record<string, number>>({})
  const [endpointTestStatuses, setEndpointTestStatuses] = useState<Record<string, EndpointTestStatus>>({})
  const resetEndpointTestStatuses = () => setEndpointTestStatuses({})
  const [savingTierIds, setSavingTierIds] = useState<Set<string>>(new Set())
  const [dirtyTierIds, setDirtyTierIds] = useState<Set<string>>(new Set())
  const stagedWeightsRef = useRef<Record<string, { scope: TierScope; updates: { id: string; weight: number }[] }>>({})

  const overviewQuery = useQuery({
    queryKey: ['llm-overview'],
    queryFn: ({ signal }) => llmApi.fetchLlmOverview(signal),
    refetchOnWindowFocus: false,
  })

  const stats = overviewQuery.data?.stats
  const providers = useMemo(() => mapProviders(overviewQuery.data?.providers), [overviewQuery.data?.providers])
  const persistentStructures = useMemo(() => mapPersistentData(overviewQuery.data?.persistent.ranges), [overviewQuery.data?.persistent.ranges])
  const browserTiers = useMemo(() => mapBrowserTiers(overviewQuery.data?.browser ?? null), [overviewQuery.data?.browser])
  const embeddingTiers = useMemo(() => mapEmbeddingTiers(overviewQuery.data?.embeddings.tiers), [overviewQuery.data?.embeddings.tiers])
  const browserStandardTiers = useMemo(() => browserTiers.filter((tier) => !tier.premium), [browserTiers])
  const browserPremiumTiers = useMemo(() => browserTiers.filter((tier) => tier.premium), [browserTiers])
  const endpointChoices = overviewQuery.data?.choices ?? { persistent_endpoints: [], browser_endpoints: [], embedding_endpoints: [] }

  useEffect(() => {
    setPendingWeights({})
    setDirtyTierIds(new Set())
  }, [overviewQuery.data])

  useEffect(() => {
    if (!providers.length) {
      setEndpointTestStatuses({})
      return
    }
    const valid = new Set(providers.flatMap((provider) => provider.endpoints.map((endpoint) => endpoint.id)))
    setEndpointTestStatuses((prev) => {
      const next: Record<string, EndpointTestStatus> = {}
      valid.forEach((id) => {
        if (prev[id]) next[id] = prev[id]
      })
      return next
    })
  }, [providers])

  const invalidateOverview = () => queryClient.invalidateQueries({ queryKey: ['llm-overview'] })

  const runMutation = async <T,>(action: () => Promise<T>, options?: MutationOptions) => {
    const { rethrow, ...feedbackOptions } = options ?? {}
    try {
      await runWithFeedback(async () => {
        const result = await action()
        await invalidateOverview()
        return result
      }, feedbackOptions)
    } catch (error) {
      if (rethrow) throw error
    }
  }

  const promptForKey = (message: string) => {
    const value = window.prompt(message)
    if (!value) return null
    return value.trim()
  }
  const [confirmDialog, setConfirmDialog] = useState<ActiveConfirmDialog | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const confirmDeferredRef = useRef<{ resolve: () => void; reject: (reason?: unknown) => void } | null>(null)
  const requestConfirmation = (options: ConfirmDialogConfig) => new Promise<void>((resolve, reject) => {
    const dialog: ActiveConfirmDialog = {
      title: options.title,
      message: options.message,
      confirmLabel: options.confirmLabel ?? 'Confirm',
      cancelLabel: options.cancelLabel ?? 'Cancel',
      intent: options.intent ?? 'danger',
      onConfirm: options.onConfirm,
    }
    setConfirmDialog(dialog)
    confirmDeferredRef.current = { resolve, reject }
  })
  const handleConfirmDialogConfirm = async () => {
    if (!confirmDialog) return
    setConfirmBusy(true)
    try {
      await confirmDialog.onConfirm()
      confirmDeferredRef.current?.resolve()
    } catch (error) {
      confirmDeferredRef.current?.reject(error)
    } finally {
      setConfirmBusy(false)
      setConfirmDialog(null)
      confirmDeferredRef.current = null
    }
  }
  const handleConfirmDialogCancel = () => {
    confirmDeferredRef.current?.resolve()
    confirmDeferredRef.current = null
    setConfirmDialog(null)
  }
  const confirmDestructiveAction = (options: ConfirmDialogConfig) =>
    requestConfirmation({
      ...options,
      confirmLabel: options.confirmLabel ?? 'Delete',
      cancelLabel: options.cancelLabel ?? 'Cancel',
      intent: options.intent ?? 'danger',
    })

  const handleProviderRotateKey = (provider: ProviderCardData) => {
    const next = promptForKey('Enter the new admin API key')
    if (!next) return Promise.resolve()
    return runMutation(() => llmApi.updateProvider(provider.id, { api_key: next }), {
      successMessage: 'API key updated',
      label: 'Rotating API key…',
      busyKey: actionKey('provider', provider.id, 'rotate'),
      context: provider.name,
      rethrow: true,
    })
  }

  const handleProviderClearKey = (provider: ProviderCardData) => {
    return runMutation(() => llmApi.updateProvider(provider.id, { clear_api_key: true }), {
      successMessage: 'Stored API key cleared',
      label: 'Clearing API key…',
      busyKey: actionKey('provider', provider.id, 'clear'),
      context: provider.name,
      rethrow: true,
    })
  }

  const handleProviderTestEndpoint = async (endpoint: ProviderEndpointCard) => {
    setEndpointTestStatuses((prev) => ({
      ...prev,
      [endpoint.id]: {
        state: 'pending',
        message: 'Testing…',
        updatedAt: Date.now(),
      },
    }))
    try {
      const result = await runWithFeedback(
        () => llmApi.testEndpoint({ endpoint_id: endpoint.id, kind: endpoint.type }),
        {
          label: 'Testing endpoint…',
          busyKey: actionKey('endpoint', endpoint.id, 'test'),
          context: endpoint.name,
        },
      )
      if (!result.ok) {
        throw new Error(result.message || 'Endpoint test failed')
      }
      setEndpointTestStatuses((prev) => ({
        ...prev,
        [endpoint.id]: {
          state: 'success',
          message: result.message || 'Endpoint responded successfully.',
          preview: result.preview?.trim() || '',
          latencyMs: result.latency_ms ?? null,
          totalTokens: result.total_tokens ?? null,
          promptTokens: result.prompt_tokens ?? null,
          completionTokens: result.completion_tokens ?? null,
          updatedAt: Date.now(),
        },
      }))
    } catch (error) {
      const message = error instanceof HttpError
        ? (typeof error.body === 'object' && error.body && 'message' in error.body ? String((error.body as { message?: unknown }).message || error.message) : error.message)
        : (error as Error).message
      setEndpointTestStatuses((prev) => ({
        ...prev,
        [endpoint.id]: {
          state: 'error',
          message,
          updatedAt: Date.now(),
        },
      }))
      throw error
    }
  }

  const handleProviderToggle = (provider: ProviderCardData, enabled: boolean) => {
    return runMutation(
      () => llmApi.updateProvider(provider.id, { enabled }),
      {
        successMessage: enabled ? 'Provider enabled' : 'Provider disabled',
        label: enabled ? 'Enabling provider…' : 'Disabling provider…',
        busyKey: actionKey('provider', provider.id, 'toggle'),
        context: provider.name,
      },
    )
  }

  const handleProviderAddEndpoint = (
    provider: ProviderCardData,
    type: llmApi.ProviderEndpoint['type'],
    values: EndpointFormValues & { key: string },
  ) => {
    const kind: 'persistent' | 'browser' | 'embedding' = type === 'browser' ? 'browser' : type === 'embedding' ? 'embedding' : 'persistent'
    const payload: Record<string, unknown> = {
      provider_id: provider.id,
      key: values.key,
    }
    if (type === 'browser') {
      payload.browser_model = values.model
      payload.model = values.model
      payload.browser_base_url = values.browser_base_url || values.api_base || ''
      const maxTokens = parseNumber(values.max_output_tokens)
      if (maxTokens !== undefined) payload.max_output_tokens = maxTokens
      payload.supports_vision = Boolean(values.supportsVision)
      payload.enabled = true
    } else if (type === 'embedding') {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      payload.enabled = true
    } else {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      const temp = parseNumber(values.temperature)
      payload.temperature_override = temp ?? null
      payload.supports_tool_choice = values.supportsToolChoice ?? true
      payload.use_parallel_tool_calls = values.useParallelToolCalls ?? true
      payload.supports_vision = values.supportsVision ?? false
      payload.enabled = true
    }
    return runMutation(() => llmApi.createEndpoint(kind, payload), {
      successMessage: 'Endpoint added',
      label: 'Creating endpoint…',
      busyKey: actionKey('provider', provider.id, 'create-endpoint'),
      context: provider.name,
      rethrow: true,
    }).then(() => {
      resetEndpointTestStatuses()
    })
  }

  const handleProviderSaveEndpoint = (endpoint: ProviderEndpointCard, values: EndpointFormValues) => {
    const kind: 'persistent' | 'browser' | 'embedding' = endpoint.type === 'browser' ? 'browser' : endpoint.type === 'embedding' ? 'embedding' : 'persistent'
    const payload: Record<string, unknown> = {}
    if (values.model) {
      payload.model = values.model
      if (kind === 'browser') payload.browser_model = values.model
      if (kind !== 'browser') payload.litellm_model = values.model
    }
    if (values.api_base) {
      payload.api_base = values.api_base
      if (kind === 'browser') payload.browser_base_url = values.api_base
    }
    if (values.browser_base_url) {
      payload.browser_base_url = values.browser_base_url
    }
    if (kind === 'browser' && values.max_output_tokens !== undefined) {
      const parsed = parseNumber(values.max_output_tokens)
      payload.max_output_tokens = parsed ?? null
    }
    if (kind !== 'browser' && values.temperature !== undefined) {
      const parsed = parseNumber(values.temperature)
      payload.temperature_override = parsed ?? null
    }
    if (values.supportsVision !== undefined) payload.supports_vision = values.supportsVision
    if (values.supportsToolChoice !== undefined) payload.supports_tool_choice = values.supportsToolChoice
    if (values.useParallelToolCalls !== undefined) payload.use_parallel_tool_calls = values.useParallelToolCalls
    return runMutation(() => llmApi.updateEndpoint(kind, endpoint.id, payload), {
      successMessage: 'Endpoint updated',
      label: 'Saving endpoint…',
      busyKey: actionKey('endpoint', endpoint.id, 'update'),
      context: endpoint.name,
      rethrow: true,
    }).then(() => {
      resetEndpointTestStatuses()
    })
  }

  const handleProviderDeleteEndpoint = (endpoint: ProviderEndpointCard) => {
    const kind: 'persistent' | 'browser' | 'embedding' = endpoint.type === 'browser' ? 'browser' : endpoint.type === 'embedding' ? 'embedding' : 'persistent'
    const displayName = endpoint.name || endpoint.api_base || endpoint.browser_base_url || endpoint.id
    return confirmDestructiveAction({
      title: `Delete endpoint "${displayName}"?`,
      message: 'This removes the endpoint from the provider and detaches it from any tiers.',
      confirmLabel: 'Delete endpoint',
      onConfirm: () => runMutation(() => llmApi.deleteEndpoint(kind, endpoint.id), {
        successMessage: 'Endpoint removed',
        label: 'Removing endpoint…',
        busyKey: actionKey('endpoint', endpoint.id, 'delete'),
        context: endpoint.name,
      }).then(() => {
        resetEndpointTestStatuses()
      }),
    })
  }

  const handleRangeUpdate = (rangeId: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    const payload: Record<string, string | number | null> = {}
    payload[field] = value
    return runMutation(() => llmApi.updateTokenRange(rangeId, payload), {
      label: 'Saving range…',
      busyKey: actionKey('range', rangeId, field),
      context: 'Token range',
      rethrow: true,
    })
  }

  const handleAddRange = () => {
    const sorted = [...persistentStructures.ranges].sort((a, b) => (a.max_tokens ?? Infinity) - (b.max_tokens ?? Infinity))
    const last = sorted.at(-1)
    const baseMin = last?.max_tokens ?? 0
    const name = `Range ${sorted.length + 1}`
    return runMutation(
      () => llmApi.createTokenRange({ name, min_tokens: baseMin, max_tokens: baseMin + 10000 }),
      {
        successMessage: 'Range added',
        label: 'Creating range…',
        busyKey: actionKey('range', 'create'),
        context: name,
      },
    )
  }

  const handleRangeRemove = (range: TokenRange) =>
    confirmDestructiveAction({
      title: `Delete range "${range.name}"?`,
      message: 'All tiers in this token range will also be deleted. Provider endpoints remain available for reuse.',
      confirmLabel: 'Delete range',
      onConfirm: () =>
        runMutation(() => llmApi.deleteTokenRange(range.id), {
          successMessage: 'Range removed',
          label: 'Removing range…',
          busyKey: actionKey('range', range.id, 'remove'),
          context: range.name,
        }),
    })

  const handleTierAdd = (rangeId: string, isPremium: boolean) =>
    runMutation(() => llmApi.createPersistentTier(rangeId, { is_premium: isPremium }), {
      successMessage: 'Tier added',
      label: 'Creating tier…',
      busyKey: actionKey('range', rangeId, isPremium ? 'add-premium-tier' : 'add-standard-tier'),
      context: 'Persistent tier',
    })
  const handleTierMove = (rangeId: string, tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updatePersistentTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving tier up…' : 'Moving tier down…',
      busyKey: actionKey('persistent', tierId, 'move', direction),
      busyKeys: [actionKey('persistent', tierId, 'move'), actionKey('persistent-range', rangeId, 'move')],
      context: 'Persistent tier',
    })
  const handleTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete tier "${tier.name}"?`,
      message: 'Endpoints will be detached from this persistent tier.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deletePersistentTier(tier.id), {
        successMessage: 'Tier removed',
        label: 'Removing tier…',
        busyKey: actionKey('persistent', tier.id, 'remove'),
        context: tier.name,
      }),
    })

  const stageTierEndpointWeight = (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => {
    const updates = rebalanceTierWeights(tier, tierEndpointId, weight, pendingWeights)
    if (!updates.length) return
    setPendingWeights((prev) => {
      const next = { ...prev }
      updates.forEach((entry) => {
        next[entry.id] = entry.weight
      })
      return next
    })
    const key = `${scope}:${tier.id}`
    stagedWeightsRef.current[key] = { scope, updates }
    setDirtyTierIds((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
  }

  const commitTierEndpointWeights = (tier: Tier, scope: TierScope) => {
    const key = `${scope}:${tier.id}`
    const staged = stagedWeightsRef.current[key]
    if (!staged) return
    delete stagedWeightsRef.current[key]
    setSavingTierIds((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
    const mutation = () => {
      const normalized: Record<string, number> = ensureServerUnits(
        staged.updates.map((entry) => ({ id: entry.id, unit: entry.weight })),
      )
      const ops = staged.updates.map((entry) => {
        const payload = { weight: encodeServerWeight(normalized[entry.id] ?? entry.weight) }
        if (scope === 'browser') {
          return llmApi.updateBrowserTierEndpoint(entry.id, payload)
        }
        if (scope === 'embedding') {
          return llmApi.updateEmbeddingTierEndpoint(entry.id, payload)
        }
        return llmApi.updatePersistentTierEndpoint(entry.id, payload)
      })
      return Promise.all(ops)
    }
    return runMutation(mutation, {
      label: 'Saving weights…',
      busyKey: actionKey('tier', tier.id, 'weights'),
      context: `${tier.name} weights`,
    }).finally(() => {
      setSavingTierIds((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
      setDirtyTierIds((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    })
  }

  const handleTierEndpointRemove = (tier: Tier, endpoint: TierEndpoint, scope: TierScope) =>
    confirmDestructiveAction({
      title: `Remove "${endpoint.label}" from ${tier.name}?`,
      message: 'This tier will lose access to the endpoint until it is added again.',
      confirmLabel: 'Remove endpoint',
      onConfirm: () => {
        if (scope === 'browser') {
          return runMutation(() => llmApi.deleteBrowserTierEndpoint(endpoint.id), {
            successMessage: 'Endpoint removed',
            label: 'Removing endpoint…',
            busyKey: actionKey('tier-endpoint', endpoint.id, 'remove'),
            context: tier.name,
          })
        }
        if (scope === 'embedding') {
          return runMutation(() => llmApi.deleteEmbeddingTierEndpoint(endpoint.id), {
            successMessage: 'Endpoint removed',
            label: 'Removing endpoint…',
            busyKey: actionKey('tier-endpoint', endpoint.id, 'remove'),
            context: tier.name,
          })
        }
        return runMutation(() => llmApi.deletePersistentTierEndpoint(endpoint.id), {
          successMessage: 'Endpoint removed',
          label: 'Removing endpoint…',
          busyKey: actionKey('tier-endpoint', endpoint.id, 'remove'),
          context: tier.name,
        })
      },
    })

  const handleBrowserTierAdd = (isPremium: boolean) =>
    runMutation(() => llmApi.createBrowserTier({ is_premium: isPremium }), {
      successMessage: 'Browser tier added',
      label: 'Creating browser tier…',
      busyKey: actionKey('browser', isPremium ? 'premium-add' : 'standard-add'),
      context: 'Browser tiers',
    })
  const handleBrowserTierMove = (tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updateBrowserTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving browser tier up…' : 'Moving browser tier down…',
      busyKey: actionKey('browser', tierId, 'move', direction),
      busyKeys: [actionKey('browser', tierId, 'move')],
      context: 'Browser tiers',
    })
  const handleBrowserTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete browser tier "${tier.name}"?`,
      message: 'Endpoints assigned to this tier will stop serving browser workloads.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteBrowserTier(tier.id), {
        successMessage: 'Browser tier removed',
        label: 'Removing browser tier…',
        busyKey: actionKey('browser', tier.id, 'remove'),
        context: tier.name,
      }),
    })

  const handleEmbeddingTierAdd = () => runMutation(() => llmApi.createEmbeddingTier({}), {
    successMessage: 'Embedding tier added',
    label: 'Creating embedding tier…',
    busyKey: actionKey('embedding', 'add'),
    context: 'Embedding tiers',
  })
  const handleEmbeddingTierMove = (tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updateEmbeddingTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving embedding tier up…' : 'Moving embedding tier down…',
      busyKey: actionKey('embedding', tierId, 'move', direction),
      busyKeys: [actionKey('embedding', tierId, 'move')],
      context: 'Embedding tiers',
    })
  const handleEmbeddingTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete embedding tier "${tier.name}"?`,
      message: 'Any weighting rules tied to this tier will be lost.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteEmbeddingTier(tier.id), {
        successMessage: 'Embedding tier removed',
        label: 'Removing embedding tier…',
        busyKey: actionKey('embedding', tier.id, 'remove'),
        context: tier.name,
      }),
    })

  const handleTierEndpointAdd = (tier: Tier, scope: TierScope) => setEndpointModal({ tier, scope })

  const submitTierEndpoint = async (endpointId: string) => {
    if (!endpointModal) return
    const { tier, scope } = endpointModal

    let stagedWeights: Record<string, number> | null = null
    const mutation = async () => {
      const initialUnit = tier.endpoints.length === 0 ? 1 : MIN_SERVER_UNIT
      const requestPayload = { endpoint_id: endpointId, weight: encodeServerWeight(initialUnit) }
      let response: { tier_endpoint_id?: string } = {}
      if (scope === 'browser') {
        response = await llmApi.addBrowserTierEndpoint(tier.id, requestPayload) as { tier_endpoint_id?: string }
      } else if (scope === 'embedding') {
        response = await llmApi.addEmbeddingTierEndpoint(tier.id, requestPayload) as { tier_endpoint_id?: string }
      } else {
        response = await llmApi.addPersistentTierEndpoint(tier.id, requestPayload) as { tier_endpoint_id?: string }
      }
      const newTierEndpointId = response?.tier_endpoint_id
      if (!newTierEndpointId) {
        return
      }

      const evenWeights = distributeEvenWeights([...tier.endpoints.map((endpoint) => endpoint.id), newTierEndpointId])
      stagedWeights = evenWeights
      setPendingWeights((prev) => {
        const next = { ...prev }
        Object.entries(evenWeights).forEach(([tierEndpointId, weight]) => {
          next[tierEndpointId] = weight
        })
        return next
      })
      const normalized: Record<string, number> = ensureServerUnits(
        Object.entries(evenWeights).map(([id, unit]) => ({ id, unit })),
      )
      const updates = Object.entries(evenWeights).map(([tierEndpointId, weight]) => {
        const payload = { weight: encodeServerWeight(normalized[tierEndpointId] ?? weight) }
        if (scope === 'browser') {
          return llmApi.updateBrowserTierEndpoint(tierEndpointId, payload)
        }
        if (scope === 'embedding') {
          return llmApi.updateEmbeddingTierEndpoint(tierEndpointId, payload)
        }
        return llmApi.updatePersistentTierEndpoint(tierEndpointId, payload)
      })

      await Promise.all(updates)
    }

    const busyKey = actionKey(scope, tier.id, 'attach-endpoint')
    try {
      await runMutation(mutation, {
        successMessage: 'Endpoint added',
        label: 'Adding endpoint…',
        busyKey,
        context: tier.name,
        rethrow: true,
      })
    } catch (error) {
      setPendingWeights((prev) => {
        const next = { ...prev }
        if (stagedWeights) {
          Object.keys(stagedWeights).forEach((key) => {
            delete next[key]
          })
        }
        return next
      })
      throw error
    }
  }

  const statsCards = [
    { label: 'Active providers', value: stats ? String(stats.active_providers) : '—', hint: 'Enabled vendors', icon: <PlugZap className="size-5" /> },
    { label: 'Persistent endpoints', value: stats ? String(stats.persistent_endpoints) : '—', hint: 'LLMs available for agents', icon: <Atom className="size-5" /> },
    { label: 'Browser models', value: stats ? String(stats.browser_endpoints) : '—', hint: 'Available to browser-use', icon: <Globe className="size-5" /> },
    { label: 'Premium tiers', value: stats ? String(stats.premium_persistent_tiers) : '—', hint: 'High-trust failover', icon: <Shield className="size-5" /> },
  ]

  return (
    <>
      <ActivityDock notices={notices} activeLabels={activeLabels} onDismiss={dismissNotice} />
      <div className="space-y-8">
        <div className="gobii-card-base space-y-2 px-6 py-6">
          <h1 className="text-2xl font-semibold text-slate-900/90">LLM configuration</h1>
          <p className="text-sm text-slate-600">Review providers, endpoints, and token tiers powering orchestrator, browser-use, and embedding flows.</p>
        </div>
        {overviewQuery.isError && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700 flex items-center gap-2">
            <AlertCircle className="size-4" />
            Unable to load configuration. Please refresh.
          </div>
        )}
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {statsCards.map((card) => (
            <StatCard key={card.label} label={card.label} value={card.value} hint={card.hint} icon={card.icon} />
          ))}
        </div>
        <SectionCard
          title="Provider inventory"
          description="Toggle providers on/off, rotate keys, and review exposed endpoints."
        >
          <div className="grid gap-4 md:grid-cols-1 lg:grid-cols-2">
            {providers.map((provider) => (
              <ProviderCard
                key={provider.id}
                provider={provider}
                isBusy={isBusy}
                testStatuses={endpointTestStatuses}
                handlers={{
                  onRotateKey: handleProviderRotateKey,
                  onToggleEnabled: handleProviderToggle,
                  onAddEndpoint: handleProviderAddEndpoint,
                  onSaveEndpoint: handleProviderSaveEndpoint,
                  onDeleteEndpoint: handleProviderDeleteEndpoint,
                  onClearKey: handleProviderClearKey,
                  onTestEndpoint: handleProviderTestEndpoint,
                }}
              />
            ))}
            {providers.length === 0 && (
              <div className="col-span-2">
                <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-slate-500">
                  {overviewQuery.isPending ? (
                    <div className="flex items-center justify-center gap-2">
                      <LoaderCircle className="size-5 animate-spin" /> Loading providers...
                    </div>
                  ) : (
                    'No providers found.'
                  )}
                </div>
              </div>
            )}
          </div>
        </SectionCard>
        <SectionCard
          title="Token-based failover tiers"
          description="Manage token ranges, tier ordering, and weighted endpoints."
          actions={
            <button type="button" className={button.primary} onClick={handleAddRange}>
              <PlusCircle className="size-4" /> Add range
            </button>
          }
        >
          <div className="space-y-6">
            {persistentStructures.ranges.map((range) => (
              <RangeSection
                key={range.id}
                range={range}
                tiers={persistentStructures.tiers.filter((tier) => tier.rangeId === range.id)}
                onAddTier={(isPremium) => handleTierAdd(range.id, isPremium)}
                onUpdate={(field, value) => handleRangeUpdate(range.id, field, value)}
                onRemove={() => handleRangeRemove(range)}
                onMoveTier={(tierId, direction) => handleTierMove(range.id, tierId, direction)}
                onRemoveTier={handleTierRemove}
                onAddEndpoint={(tier) => handleTierEndpointAdd(tier, 'persistent')}
                onStageEndpointWeight={stageTierEndpointWeight}
                onCommitEndpointWeights={commitTierEndpointWeights}
                onRemoveEndpoint={(tier, endpoint) => handleTierEndpointRemove(tier, endpoint, 'persistent')}
                pendingWeights={pendingWeights}
                savingTierIds={savingTierIds}
                dirtyTierIds={dirtyTierIds}
                isActionBusy={isBusy}
              />
            ))}
            {persistentStructures.ranges.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-slate-500">
                {overviewQuery.isPending ? (
                  <div className="flex items-center justify-center gap-2">
                    <LoaderCircle className="size-5 animate-spin" /> Loading ranges...
                  </div>
                ) : (
                  'No token ranges configured yet.'
                )}
              </div>
            )}
          </div>
        </SectionCard>
        <SectionCard
          title="Browser-use models"
          description="Dedicated tiers for browser automations."
        >
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-slate-700">Standard tiers</h4>
                <button type="button" className={button.secondary} onClick={() => handleBrowserTierAdd(false)}>
                  <PlusCircle className="size-4" /> Add
                </button>
              </div>
              {browserStandardTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No standard browser tiers.</p>}
              {browserStandardTiers.map((tier, index) => {
                const lastIndex = browserStandardTiers.length - 1
                return (
                <TierCard
                  key={tier.id}
                  tier={tier}
                  pendingWeights={pendingWeights}
                  scope="browser"
                  canMoveUp={index > 0}
                  canMoveDown={index < lastIndex}
                  isDirty={dirtyTierIds.has(`browser:${tier.id}`)}
                  isSaving={savingTierIds.has(`browser:${tier.id}`)}
                  onMove={(direction) => handleBrowserTierMove(tier.id, direction)}
                  onRemove={handleBrowserTierRemove}
                  onAddEndpoint={() => handleTierEndpointAdd(tier, 'browser')}
                  onStageEndpointWeight={(currentTier, tierEndpointId, weight) => stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'browser')}
                  onCommitEndpointWeights={(currentTier) => commitTierEndpointWeights(currentTier, 'browser')}
                  onRemoveEndpoint={(currentTier, endpoint) => handleTierEndpointRemove(currentTier, endpoint, 'browser')}
                  isActionBusy={isBusy}
                />
                )
              })}
            </div>
            <div className="bg-emerald-50/50 p-4 space-y-3 rounded-xl">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-emerald-800">Premium tiers</h4>
                <button type="button" className={button.secondary} onClick={() => handleBrowserTierAdd(true)}>
                  <PlusCircle className="size-4" /> Add
                </button>
              </div>
              {browserPremiumTiers.length === 0 && <p className="text-center text-xs text-emerald-500 py-4">No premium browser tiers.</p>}
              {browserPremiumTiers.map((tier, index) => {
                const lastIndex = browserPremiumTiers.length - 1
                return (
                <TierCard
                  key={tier.id}
                  tier={tier}
                  pendingWeights={pendingWeights}
                  scope="browser"
                  canMoveUp={index > 0}
                  canMoveDown={index < lastIndex}
                  isDirty={dirtyTierIds.has(`browser:${tier.id}`)}
                  isSaving={savingTierIds.has(`browser:${tier.id}`)}
                  onMove={(direction) => handleBrowserTierMove(tier.id, direction)}
                  onRemove={handleBrowserTierRemove}
                  onAddEndpoint={() => handleTierEndpointAdd(tier, 'browser')}
                  onStageEndpointWeight={(currentTier, tierEndpointId, weight) => stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'browser')}
                  onCommitEndpointWeights={(currentTier) => commitTierEndpointWeights(currentTier, 'browser')}
                  onRemoveEndpoint={(currentTier, endpoint) => handleTierEndpointRemove(currentTier, endpoint, 'browser')}
                  isActionBusy={isBusy}
                />
                )
              })}
            </div>
          </div>
        </SectionCard>
        <SectionCard
          title="Other model consumers"
          description="Surface-level overview of summarization, embeddings, and tooling hints."
        >
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="rounded-xl border border-slate-200/80 bg-white p-4">
                <div className="flex items-start gap-3">
                  <BookText className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-slate-900/90">Summaries</h4>
                    <p className="text-sm text-slate-600">Uses the primary model from the smallest token range, temperature forced to 0.</p>
                  </div>
                </div>
              </div>
              <div className="rounded-xl border border-slate-200/80 bg-white p-4">
                <div className="flex items-start gap-3">
                  <Search className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-slate-900/90">Search tools</h4>
                    <p className="text-sm text-slate-600">Decisions are delegated to the main agent tiers.</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
              <div className="flex items-center justify-between">
                <div className="flex items-start gap-3">
                  <PlugZap className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-slate-900/90">Embedding tiers</h4>
                    <p className="text-sm text-slate-600">Fallback order for generating embeddings.</p>
                  </div>
                </div>
                <button type="button" className={button.secondary} onClick={handleEmbeddingTierAdd}>
                  <PlusCircle className="size-4" /> Add tier
                </button>
              </div>
              {embeddingTiers.map((tier, index) => {
                const lastIndex = embeddingTiers.length - 1
                return (
                <TierCard
                  key={tier.id}
                  tier={tier}
                  pendingWeights={pendingWeights}
                  scope="embedding"
                  canMoveUp={index > 0}
                  canMoveDown={index < lastIndex}
                  isDirty={dirtyTierIds.has(`embedding:${tier.id}`)}
                  isSaving={savingTierIds.has(`embedding:${tier.id}`)}
                  onMove={(direction) => handleEmbeddingTierMove(tier.id, direction)}
                  onRemove={handleEmbeddingTierRemove}
                  onAddEndpoint={() => handleTierEndpointAdd(tier, 'embedding')}
                  onStageEndpointWeight={(currentTier, tierEndpointId, weight) => stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'embedding')}
                  onCommitEndpointWeights={(currentTier) => commitTierEndpointWeights(currentTier, 'embedding')}
                  onRemoveEndpoint={(currentTier, endpoint) => handleTierEndpointRemove(currentTier, endpoint, 'embedding')}
                  isActionBusy={isBusy}
                />
                )
              })}
              {embeddingTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No embedding tiers configured.</p>}
            </div>
          </div>
        </SectionCard>
        {endpointModal && (
          <AddEndpointModal
            tier={endpointModal.tier}
            scope={endpointModal.scope}
            choices={endpointChoices}
            busy={isBusy(actionKey(endpointModal.scope, endpointModal.tier.id, 'attach-endpoint'))}
            onAdd={(endpointId) => submitTierEndpoint(endpointId)}
            onClose={() => setEndpointModal(null)}
          />
        )}
        {confirmDialog && (
          <ConfirmActionModal
            title={confirmDialog.title}
            message={confirmDialog.message}
            confirmLabel={confirmDialog.confirmLabel}
            cancelLabel={confirmDialog.cancelLabel}
            intent={confirmDialog.intent}
            busy={confirmBusy}
            onConfirm={handleConfirmDialogConfirm}
            onCancel={handleConfirmDialogCancel}
          />
        )}
      </div>
    </>
  )
}
