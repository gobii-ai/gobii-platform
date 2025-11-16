import {
  AlertCircle,
  Atom,
  Globe,
  PlugZap,
  Shield,
  X,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  KeyRound,
  ShieldCheck,
  LoaderCircle,
  BookText,
  Search,
  Layers,
} from 'lucide-react'
import React, { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { SectionCard } from '../components/llmConfig/SectionCard'
import { StatCard } from '../components/llmConfig/StatCard'
import * as llmApi from '../api/llmConfig'

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
  endpoints: ProviderEndpointCard[]
}

type TierScope = 'persistent' | 'browser' | 'embedding'

function mapProviders(input: llmApi.Provider[] = []): ProviderCardData[] {
  return input.map((provider) => ({
    id: provider.id,
    name: provider.name,
    status: provider.status,
    backend: provider.browser_backend,
    fallback: provider.env_var || 'Not configured',
    enabled: provider.enabled,
    endpoints: provider.endpoints.map((endpoint) => ({
      id: endpoint.id,
      name: endpoint.model,
      enabled: endpoint.enabled,
      api_base: endpoint.api_base || endpoint.browser_base_url || '',
      temperature: endpoint.temperature_override ?? null,
      supports_vision: endpoint.supports_vision,
      supports_tool_choice: endpoint.supports_tool_choice,
      use_parallel_tool_calls: endpoint.use_parallel_tool_calls,
      type: endpoint.type,
    })),
  }))
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
      mappedTiers.push({
        id: tier.id,
        name: tier.description || `Tier ${tier.order}`,
        order: tier.order,
        rangeId: range.id,
        premium: tier.is_premium,
        endpoints: tier.endpoints.map((endpoint) => ({
          id: endpoint.id,
          endpointId: endpoint.endpoint_id,
          label: endpoint.label,
          weight: Math.round(endpoint.weight),
        })),
      })
    })
  })
  return { ranges: mappedRanges, tiers: mappedTiers }
}

function mapBrowserTiers(policy: llmApi.BrowserPolicy | null): Tier[] {
  if (!policy) return []
  return policy.tiers.map((tier) => ({
    id: tier.id,
    name: tier.description || `Tier ${tier.order}`,
    order: tier.order,
    rangeId: 'browser',
    premium: tier.is_premium,
    endpoints: tier.endpoints.map((endpoint) => ({
      id: endpoint.id,
      endpointId: endpoint.endpoint_id,
      label: endpoint.label,
      weight: Math.round(endpoint.weight),
    })),
  }))
}

function mapEmbeddingTiers(tiers: llmApi.EmbeddingTier[] = []): Tier[] {
  return tiers.map((tier) => ({
    id: tier.id,
    name: tier.description || `Tier ${tier.order}`,
    order: tier.order,
    rangeId: 'embedding',
    premium: false,
    endpoints: tier.endpoints.map((endpoint) => ({
      id: endpoint.id,
      endpointId: endpoint.endpoint_id,
      label: endpoint.label,
      weight: Math.round(endpoint.weight),
    })),
  }))
}

function AddEndpointModal({
  tier,
  scope,
  choices,
  onAdd,
  onClose,
}: {
  tier: Tier
  scope: TierScope
  choices: llmApi.EndpointChoices
  onAdd: (endpointId: string) => void
  onClose: () => void
}) {
  const endpoints = scope === 'browser'
    ? choices.browser_endpoints
    : scope === 'embedding'
      ? choices.embedding_endpoints
      : choices.persistent_endpoints
  const [selected, setSelected] = useState(endpoints[0]?.id || '')
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
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
          <button type="button" className={button.secondary} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={button.primary}
            onClick={() => {
              if (selected) onAdd(selected)
              onClose()
            }}
            disabled={!selected}
          >
            <Plus className="size-4" /> Add endpoint
          </button>
        </div>
      </div>
    </div>
  )
}

function TierCard({
  tier,
  scope,
  onMove,
  onRemove,
  onAddEndpoint,
  onUpdateEndpointWeight,
  onRemoveEndpoint,
}: {
  tier: Tier
  scope: TierScope
  onMove: (direction: 'up' | 'down') => void
  onRemove: () => void
  onAddEndpoint: () => void
  onUpdateEndpointWeight: (tierEndpointId: string, weight: number) => void
  onRemoveEndpoint: (tierEndpointId: string) => void
}) {
  const headerIcon = tier.premium ? <ShieldCheck className="size-4 text-emerald-700" /> : <Layers className="size-4 text-slate-500" />
  return (
    <div className={`rounded-xl border ${tier.premium ? 'border-emerald-200' : 'border-slate-200'} bg-white`}>
      <div className="flex items-center justify-between p-4 text-xs uppercase tracking-wide text-slate-500">
        <span className="flex items-center gap-2">{headerIcon} {tier.name}</span>
        <div className="flex items-center gap-1 text-xs">
          <button className={button.icon} type="button" onClick={() => onMove('up')}><ChevronUp className="size-4" /></button>
          <button className={button.icon} type="button" onClick={() => onMove('down')}><ChevronDown className="size-4" /></button>
          <button className={button.iconDanger} type="button" onClick={onRemove}><Trash2 className="size-4" /></button>
        </div>
      </div>
      <div className="space-y-3 px-4 pb-4">
        <div className="flex items-center justify-between text-[13px] text-slate-500">
          <span>Weighted endpoints</span>
        </div>
        <div className="space-y-3">
          {tier.endpoints.map((endpoint) => (
            <div key={endpoint.id} className="grid grid-cols-12 items-center gap-3 text-sm font-medium text-slate-900/90">
              <span className="col-span-6 flex items-center gap-2 truncate"><PlugZap className="size-4 flex-shrink-0 text-slate-400" /> {endpoint.label}</span>
              <div className="col-span-6 flex items-center gap-2">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={endpoint.weight}
                  onChange={(event) => onUpdateEndpointWeight(endpoint.id, parseInt(event.target.value, 10))}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                />
                <input
                  type="number"
                  value={endpoint.weight}
                  onChange={(event) => onUpdateEndpointWeight(endpoint.id, parseInt(event.target.value, 10) || 0)}
                  className="block w-20 rounded-lg border-slate-300 text-right shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                />
                <button onClick={() => onRemoveEndpoint(endpoint.id)} className={button.iconDanger}><X className="size-4" /></button>
              </div>
            </div>
          ))}
        </div>
        <div className="pt-2">
          <button type="button" className={button.muted} onClick={onAddEndpoint}>
            <Plus className="size-3" /> Add endpoint
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
  onUpdateEndpointWeight,
  onRemoveEndpoint,
}: {
  range: TokenRange
  tiers: Tier[]
  onUpdate: (field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => void
  onRemove: () => void
  onAddTier: (isPremium: boolean) => void
  onMoveTier: (tierId: string, direction: 'up' | 'down') => void
  onRemoveTier: (tierId: string) => void
  onAddEndpoint: (tier: Tier) => void
  onUpdateEndpointWeight: (tier: Tier, tierEndpointId: string, weight: number) => void
  onRemoveEndpoint: (tier: Tier, tierEndpointId: string) => void
}) {
  const standardTiers = tiers.filter((tier) => !tier.premium).sort((a, b) => a.order - b.order)
  const premiumTiers = tiers.filter((tier) => tier.premium).sort((a, b) => a.order - b.order)
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white">
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-12 items-center gap-3 text-sm">
          <div className="col-span-12 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Range Name</label>
            <input type="text" value={range.name} onChange={(event) => onUpdate('name', event.target.value)} className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
          <div className="col-span-6 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Min Tokens</label>
            <input type="number" value={range.min_tokens} onChange={(event) => onUpdate('min_tokens', parseInt(event.target.value, 10))} className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
          <div className="col-span-6 sm:col-span-3 relative">
            <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Max Tokens</label>
            <input type="number" value={range.max_tokens ?? ''} placeholder="Infinity" onChange={(event) => onUpdate('max_tokens', event.target.value === '' ? null : parseInt(event.target.value, 10))} className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
          <div className="col-span-12 sm:col-span-3 text-right">
            <button type="button" className={button.danger} onClick={onRemove}><Trash2 className="size-4" /> Remove Range</button>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
        <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-700">Standard tiers</h4>
            <button type="button" className={button.secondary} onClick={() => onAddTier(false)}>
              <Plus className="size-4" /> Add
            </button>
          </div>
          {standardTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No standard tiers.</p>}
          {standardTiers.map((tier) => (
            <TierCard
              key={tier.id}
              tier={tier}
              scope="persistent"
              onMove={(direction) => onMoveTier(tier.id, direction)}
              onRemove={() => onRemoveTier(tier.id)}
              onAddEndpoint={() => onAddEndpoint(tier)}
              onUpdateEndpointWeight={(tierEndpointId, weight) => onUpdateEndpointWeight(tier, tierEndpointId, weight)}
              onRemoveEndpoint={(tierEndpointId) => onRemoveEndpoint(tier, tierEndpointId)}
            />
          ))}
        </div>
        <div className="bg-emerald-50/50 p-4 space-y-3 rounded-xl">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-emerald-800">Premium tiers</h4>
            <button type="button" className={button.secondary} onClick={() => onAddTier(true)}>
              <Plus className="size-4" /> Add
            </button>
          </div>
          {premiumTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No premium tiers.</p>}
          {premiumTiers.map((tier) => (
            <TierCard
              key={tier.id}
              tier={tier}
              scope="persistent"
              onMove={(direction) => onMoveTier(tier.id, direction)}
              onRemove={() => onRemoveTier(tier.id)}
              onAddEndpoint={() => onAddEndpoint(tier)}
              onUpdateEndpointWeight={(tierEndpointId, weight) => onUpdateEndpointWeight(tier, tierEndpointId, weight)}
              onRemoveEndpoint={(tierEndpointId) => onRemoveEndpoint(tier, tierEndpointId)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

export function LlmConfigScreen() {
  const queryClient = useQueryClient()
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [endpointModal, setEndpointModal] = useState<{ tier: Tier; scope: TierScope } | null>(null)

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
  const endpointChoices = overviewQuery.data?.choices ?? { persistent_endpoints: [], browser_endpoints: [], embedding_endpoints: [] }

  const invalidateOverview = () => queryClient.invalidateQueries({ queryKey: ['llm-overview'] })

  const runMutation = async (action: () => Promise<unknown>, success?: string) => {
    try {
      await action()
      await invalidateOverview()
      if (success) {
        setBanner(success)
        setErrorBanner(null)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Request failed'
      setErrorBanner(message)
      setBanner(null)
    }
  }

  const handleRangeUpdate = (rangeId: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    const payload: Record<string, string | number | null> = {}
    payload[field] = value
    runMutation(() => llmApi.updateTokenRange(rangeId, payload))
  }

  const handleAddRange = () => {
    const sorted = [...persistentStructures.ranges].sort((a, b) => (a.max_tokens ?? Infinity) - (b.max_tokens ?? Infinity))
    const last = sorted.at(-1)
    const baseMin = last?.max_tokens ?? 0
    const name = `Range ${sorted.length + 1}`
    runMutation(() => llmApi.createTokenRange({ name, min_tokens: baseMin, max_tokens: baseMin + 10000 }), 'Range added')
  }

  const handleTierAdd = (rangeId: string, isPremium: boolean) => runMutation(() => llmApi.createPersistentTier(rangeId, { is_premium: isPremium }), 'Tier added')
  const handleTierMove = (tierId: string, direction: 'up' | 'down') => runMutation(() => llmApi.updatePersistentTier(tierId, { move: direction }))
  const handleTierRemove = (tierId: string) => runMutation(() => llmApi.deletePersistentTier(tierId), 'Tier removed')

  const handleTierEndpointWeight = (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => {
    if (scope === 'browser') {
      return runMutation(() => llmApi.updateBrowserTierEndpoint(tierEndpointId, { weight }))
    }
    if (scope === 'embedding') {
      return runMutation(() => llmApi.updateEmbeddingTierEndpoint(tierEndpointId, { weight }))
    }
    return runMutation(() => llmApi.updatePersistentTierEndpoint(tierEndpointId, { weight }))
  }

  const handleTierEndpointRemove = (tierEndpointId: string, scope: TierScope) => {
    if (scope === 'browser') {
      return runMutation(() => llmApi.deleteBrowserTierEndpoint(tierEndpointId), 'Endpoint removed')
    }
    if (scope === 'embedding') {
      return runMutation(() => llmApi.deleteEmbeddingTierEndpoint(tierEndpointId), 'Endpoint removed')
    }
    return runMutation(() => llmApi.deletePersistentTierEndpoint(tierEndpointId), 'Endpoint removed')
  }

  const handleBrowserTierAdd = (isPremium: boolean) => runMutation(() => llmApi.createBrowserTier({ is_premium: isPremium }), 'Browser tier added')
  const handleBrowserTierMove = (tierId: string, direction: 'up' | 'down') => runMutation(() => llmApi.updateBrowserTier(tierId, { move: direction }))
  const handleBrowserTierRemove = (tierId: string) => runMutation(() => llmApi.deleteBrowserTier(tierId), 'Browser tier removed')

  const handleEmbeddingTierAdd = () => runMutation(() => llmApi.createEmbeddingTier({}), 'Embedding tier added')
  const handleEmbeddingTierMove = (tierId: string, direction: 'up' | 'down') => runMutation(() => llmApi.updateEmbeddingTier(tierId, { move: direction }))
  const handleEmbeddingTierRemove = (tierId: string) => runMutation(() => llmApi.deleteEmbeddingTier(tierId), 'Embedding tier removed')

  const handleTierEndpointAdd = (tier: Tier, scope: TierScope) => setEndpointModal({ tier, scope })

  const submitTierEndpoint = (endpointId: string) => {
    if (!endpointModal) return
    const { tier, scope } = endpointModal
    if (scope === 'browser') {
      runMutation(() => llmApi.addBrowserTierEndpoint(tier.id, { endpoint_id: endpointId, weight: 100 }), 'Endpoint added')
    } else if (scope === 'embedding') {
      runMutation(() => llmApi.addEmbeddingTierEndpoint(tier.id, { endpoint_id: endpointId, weight: 100 }), 'Endpoint added')
    } else {
      runMutation(() => llmApi.addPersistentTierEndpoint(tier.id, { endpoint_id: endpointId, weight: 100 }), 'Endpoint added')
    }
  }

  const statsCards = [
    { label: 'Active providers', value: stats ? String(stats.active_providers) : '—', hint: 'Enabled vendors', icon: <PlugZap className="size-5" /> },
    { label: 'Persistent endpoints', value: stats ? String(stats.persistent_endpoints) : '—', hint: 'LLMs available for agents', icon: <Atom className="size-5" /> },
    { label: 'Browser models', value: stats ? String(stats.browser_endpoints) : '—', hint: 'Available to browser-use', icon: <Globe className="size-5" /> },
    { label: 'Premium tiers', value: stats ? String(stats.premium_persistent_tiers) : '—', hint: 'High-trust failover', icon: <Shield className="size-5" /> },
  ]

  return (
    <div className="space-y-8">
      {banner && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">{banner}</div>
      )}
      {errorBanner && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700 flex items-center gap-2">
          <AlertCircle className="size-4" />
          {errorBanner}
        </div>
      )}
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
            <article key={provider.id} className="rounded-2xl border border-slate-200/80 bg-white">
              <div className="flex items-center justify-between p-4">
                <div>
                  <h3 className="text-base font-semibold text-slate-900/90">{provider.name}</h3>
                  <p className="text-xs text-slate-500">{provider.endpoints.length} endpoints</p>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50/80 px-3 py-1 text-xs font-medium text-emerald-700">
                  <ShieldCheck className="size-3.5" /> {provider.status}
                </span>
              </div>
              <div className="border-t border-slate-200/80 p-4 space-y-3">
                <dl className="space-y-3 text-sm text-slate-600">
                  <div>
                    <dt className="text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2"><KeyRound className="size-4" /> Env fallback</dt>
                    <dd className="font-medium text-slate-900/90 pl-6">{provider.fallback}</dd>
                  </div>
                  <div>
                    <dt className="text-xs uppercase tracking-wide text-slate-400">Backend</dt>
                    <dd className="font-medium text-slate-900/90">{provider.backend}</dd>
                  </div>
                </dl>
                <div className="border-t border-slate-200/80 pt-4 space-y-2">
                  {provider.endpoints.length === 0 && <p className="text-sm text-slate-500">No endpoints linked.</p>}
                  {provider.endpoints.map((endpoint) => (
                    <div key={endpoint.id} className="rounded-lg border border-slate-200 px-3 py-2 text-sm flex items-center justify-between">
                      <div>
                        <p className="font-medium text-slate-900/90">{endpoint.name}</p>
                        <p className="text-xs text-slate-500">{endpoint.type}</p>
                      </div>
                      <span className={`text-xs font-semibold ${endpoint.enabled ? 'text-emerald-600' : 'text-slate-500'}`}>
                        {endpoint.enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </article>
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
            <Plus className="size-4" /> Add range
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
              onRemove={() => runMutation(() => llmApi.deleteTokenRange(range.id), 'Range removed')}
              onMoveTier={(tierId, direction) => handleTierMove(tierId, direction)}
              onRemoveTier={handleTierRemove}
              onAddEndpoint={(tier) => handleTierEndpointAdd(tier, 'persistent')}
              onUpdateEndpointWeight={(tier, tierEndpointId, weight) => handleTierEndpointWeight(tier, tierEndpointId, weight, 'persistent')}
              onRemoveEndpoint={(tier, tierEndpointId) => handleTierEndpointRemove(tierEndpointId, 'persistent')}
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
                <Plus className="size-4" /> Add
              </button>
            </div>
            {browserTiers.filter((tier) => !tier.premium).map((tier) => (
              <TierCard
                key={tier.id}
                tier={tier}
                scope="browser"
                onMove={(direction) => handleBrowserTierMove(tier.id, direction)}
                onRemove={() => handleBrowserTierRemove(tier.id)}
                onAddEndpoint={() => handleTierEndpointAdd(tier, 'browser')}
                onUpdateEndpointWeight={(tierEndpointId, weight) => handleTierEndpointWeight(tier, tierEndpointId, weight, 'browser')}
                onRemoveEndpoint={(tierEndpointId) => handleTierEndpointRemove(tierEndpointId, 'browser')}
              />
            ))}
          </div>
          <div className="bg-emerald-50/50 p-4 space-y-3 rounded-xl">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-semibold text-emerald-800">Premium tiers</h4>
              <button type="button" className={button.secondary} onClick={() => handleBrowserTierAdd(true)}>
                <Plus className="size-4" /> Add
              </button>
            </div>
            {browserTiers.filter((tier) => tier.premium).map((tier) => (
              <TierCard
                key={tier.id}
                tier={tier}
                scope="browser"
                onMove={(direction) => handleBrowserTierMove(tier.id, direction)}
                onRemove={() => handleBrowserTierRemove(tier.id)}
                onAddEndpoint={() => handleTierEndpointAdd(tier, 'browser')}
                onUpdateEndpointWeight={(tierEndpointId, weight) => handleTierEndpointWeight(tier, tierEndpointId, weight, 'browser')}
                onRemoveEndpoint={(tierEndpointId) => handleTierEndpointRemove(tierEndpointId, 'browser')}
              />
            ))}
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
                <Plus className="size-4" /> Add tier
              </button>
            </div>
            {embeddingTiers.map((tier) => (
              <TierCard
                key={tier.id}
                tier={tier}
                scope="embedding"
                onMove={(direction) => handleEmbeddingTierMove(tier.id, direction)}
                onRemove={() => handleEmbeddingTierRemove(tier.id)}
                onAddEndpoint={() => handleTierEndpointAdd(tier, 'embedding')}
                onUpdateEndpointWeight={(tierEndpointId, weight) => handleTierEndpointWeight(tier, tierEndpointId, weight, 'embedding')}
                onRemoveEndpoint={(tierEndpointId) => handleTierEndpointRemove(tierEndpointId, 'embedding')}
              />
            ))}
            {embeddingTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No embedding tiers configured.</p>}
          </div>
        </div>
      </SectionCard>
      {endpointModal && (
        <AddEndpointModal
          tier={endpointModal.tier}
          scope={endpointModal.scope}
          choices={endpointChoices}
          onAdd={(endpointId) => submitTierEndpoint(endpointId)}
          onClose={() => setEndpointModal(null)}
        />
      )}
    </div>
  )
}
