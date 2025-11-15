import {
  AlertCircle,
  Atom,
  Globe,
  PlugZap,
  Shield,
  X,
  ArrowRight,
  ArrowLeft,
  Plus,
  Trash2,
  SlidersHorizontal,
  ChevronUp,
  ChevronDown,
  KeyRound,
  Server,
  ToggleRight,
  Settings,
  Palette,
  FileCog,
  Bot,
  BrainCircuit,
  Layers,
  ShieldCheck,
  ShieldOff,
} from 'lucide-react'
import { SectionCard } from '../components/llmConfig/SectionCard'
import { StatCard } from '../components/llmConfig/StatCard'
import React, { useState } from 'react'

type Endpoint = {
  id: string
  label: string
  weight: number
}

type TokenRange = {
  id: string;
  name: string;
  min_tokens: number;
  max_tokens: number | null; // null for infinity
};

type Tier = {
  id: string
  rangeId: string;
  name: string
  order: number
  premium: boolean
  endpoints: Endpoint[]
}

const initialRanges: TokenRange[] = [
    { id: 'small', name: 'Small', min_tokens: 0, max_tokens: 7500 },
    { id: 'medium', name: 'Medium', min_tokens: 7500, max_tokens: 20000 },
    { id: 'large', name: 'Large', min_tokens: 20000, max_tokens: null },
]

const initialTiers: Tier[] = [
  {
    id: 'small-tier-1',
    name: 'Tier 1',
    rangeId: 'small',
    order: 1,
    premium: false,
    endpoints: [
      { id: 'ep1', label: 'openai/gpt-5', weight: 70 },
      { id: 'ep2', label: 'anthropic/claude-sonnet-4', weight: 30 },
    ],
  },
  {
    id: 'small-tier-2',
    name: 'Tier 2',
    rangeId: 'small',
    order: 2,
    premium: false,
    endpoints: [{ id: 'ep3', label: 'vertex_ai/gemini-2.5-pro', weight: 100 }],
  },
  {
    id: 'medium-premium-1',
    name: 'Premium Tier 1',
    rangeId: 'medium',
    order: 1,
    premium: true,
    endpoints: [
      { id: 'ep4', label: 'openrouter/z-ai/glm-4.6:exacto', weight: 60 },
      { id: 'ep5', label: 'openai/gpt-5', weight: 40 },
    ],
  },
]

const placeholderProviders = [
  {
    name: 'OpenRouter',
    endpoints: 3,
    status: 'Healthy',
    fallback: 'OPENROUTER_API_KEY',
    backend: 'OpenAI-compatible',
    usage: 'Primary for long-context + premium workloads.',
  },
  {
    name: 'Anthropic',
    endpoints: 3,
    status: 'Healthy',
    fallback: 'ANTHROPIC_API_KEY',
    backend: 'Native API',
    usage: 'Used when Claude reasoning is preferred.',
  },
  {
    name: 'OpenAI',
    endpoints: 3,
    status: 'Healthy',
    fallback: 'OPENAI_API_KEY',
    backend: 'Native API',
    usage: 'Default for summaries and quick iterations.',
  },
]

const workloadSummaries = [
  {
    name: 'Summaries',
    model: 'openai/gpt-4o-mini',
    detail: 'Temperature forced to 0 for deterministic compression.',
    icon: <FileCog className="size-5 text-blue-500" />
  },
  {
    name: 'Search tools',
    model: 'openrouter/z-ai/glm-4.6:exacto',
    detail: 'Uses the same failover list; tool calling enabled.',
    icon: <Bot className="size-5 text-blue-500" />
  },
  {
    name: 'Embeddings',
    model: 'text-embedding-3-large',
    detail: 'Rotates monthly for cached comparisons.',
    icon: <Palette className="size-5 text-blue-500" />
  },
]

const button = {
  primary:
    'inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500/40 disabled:opacity-50 disabled:cursor-not-allowed',
  secondary:
    'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-200/60 disabled:opacity-50 disabled:cursor-not-allowed',
  muted:
    'inline-flex items-center justify-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-200/60 disabled:opacity-50 disabled:cursor-not-allowed',
  danger:
    'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-sm font-medium text-rose-600 transition hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-200/60 disabled:opacity-50 disabled:cursor-not-allowed',
  link: 'text-blue-600 hover:underline disabled:opacity-50 disabled:cursor-not-allowed',
  dangerLink: 'text-rose-600 hover:underline disabled:opacity-50 disabled:cursor-not-allowed',
  icon: 'p-2 text-slate-500 hover:bg-slate-100 rounded-full transition',
  iconDanger: 'p-2 text-slate-500 hover:bg-rose-50 hover:text-rose-600 rounded-full transition'
}

function AddEndpointModal({ tier, onClose, onAdd }: { tier: Tier; onClose: () => void; onAdd: (tierId: string, endpointLabel: string) => void }) {
  const [label, setLabel] = useState('')
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Add Endpoint to {tier.name}</h3>
          <button onClick={onClose} className={button.icon}>
            <X className="size-5" />
          </button>
        </div>
        <div className="mt-4">
          <label htmlFor="endpoint-label" className="text-sm font-medium text-slate-700">Endpoint Label</label>
          <input
            id="endpoint-label"
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="e.g., openai/gpt-4o"
          />
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" className={button.secondary} onClick={onClose}>Cancel</button>
          <button type="button" className={button.primary} onClick={() => { onAdd(tier.id, label); onClose(); }}><Plus className="size-4" /> Add Endpoint</button>
        </div>
      </div>
    </div>
  )
}

function EditWeightsModal({ tier, onClose, onSave }: { tier: Tier; onClose: () => void; onSave: (tierId: string, endpoints: Endpoint[]) => void }) {
  const [endpoints, setEndpoints] = useState(tier.endpoints)
  const totalWeight = endpoints.reduce((sum, ep) => sum + ep.weight, 0)

  const handleWeightChange = (endpointId: string, newWeight: string) => {
    const weight = parseInt(newWeight, 10)
    setEndpoints(currentEndpoints =>
      currentEndpoints.map(ep =>
        ep.id === endpointId ? { ...ep, weight: isNaN(weight) ? 0 : weight } : ep
      )
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Edit Weights for {tier.name}</h3>
          <button onClick={onClose} className={button.icon}>
            <X className="size-5" />
          </button>
        </div>
        <div className="mt-4 space-y-3">
          {endpoints.map(endpoint => (
            <div key={endpoint.id} className="flex items-center justify-between">
              <label htmlFor={`weight-${endpoint.id}`} className="text-sm font-medium text-slate-800">{endpoint.label}</label>
              <div className="flex items-center gap-2">
                <input
                  id={`weight-${endpoint.id}`}
                  type="number"
                  value={endpoint.weight}
                  onChange={(e) => handleWeightChange(endpoint.id, e.target.value)}
                  className="block w-24 rounded-lg border-slate-300 text-right shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                />
                <span className="text-sm text-slate-500">%</span>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between">
            <p className={`text-sm ${totalWeight !== 100 ? 'text-red-600' : 'text-slate-500'}`}>
                Total: {totalWeight}%
            </p>
            {totalWeight !== 100 && <p className="text-xs text-red-500">Total weight must be 100%</p>}
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" className={button.secondary} onClick={onClose}>Cancel</button>
          <button type="button" className={button.primary} onClick={() => { onSave(tier.id, endpoints); onClose(); }} disabled={totalWeight !== 100}>Save Weights</button>
        </div>
      </div>
    </div>
  )
}


function TierCard({ tier, rangeName, onMove, onRemove, onAddEndpoint, onEditWeights, isFirst, isLast }: { tier: Tier, rangeName: string, onMove: (direction: 'up' | 'down') => void, onRemove: () => void, onAddEndpoint: () => void, onEditWeights: () => void, isFirst: boolean, isLast: boolean }) {
  const borderColor = tier.premium ? 'border-emerald-200' : 'border-slate-100/80'
  const textColor = tier.premium ? 'text-emerald-700' : 'text-slate-500'
  const headerIcon = tier.premium ? <ShieldCheck className={`size-4 ${textColor}`} /> : <Layers className={`size-4 ${textColor}`} />

  return (
    <div className={`rounded-xl border ${borderColor} bg-white px-4 py-4`}>
      <div className={`flex items-center justify-between text-xs uppercase tracking-wide ${textColor}`}>
        <span className="flex items-center gap-2">{headerIcon} {rangeName} range • {tier.name}</span>
        <div className="flex items-center gap-1 text-xs">
          <button className={button.icon} type="button" onClick={() => onMove('up')} disabled={isFirst}>
            <ChevronUp className="size-4" />
          </button>
          <button className={button.icon} type="button" onClick={() => onMove('down')} disabled={isLast}>
            <ChevronDown className="size-4" />
          </button>
          <button className={button.iconDanger} type="button" onClick={onRemove}>
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between text-[13px] text-slate-500">
        <span>Weighted endpoints</span>
        <span>Tier order {tier.order}</span>
      </div>
      <ul className="mt-1 space-y-1">
        {tier.endpoints.map((endpoint) => (
          <li key={`${tier.id}-${endpoint.label}`} className="flex items-center justify-between text-sm font-medium text-slate-900/90">
            <span className="flex items-center gap-2"><PlugZap className="size-4 text-slate-400" /> {endpoint.label}</span>
            <span className={`text-xs font-mono ${textColor}`}>{endpoint.weight}%</span>
          </li>
        ))}
         {tier.endpoints.length === 0 && <li className="text-center text-xs text-slate-400 py-2">No endpoints added.</li>}
      </ul>
      <div className="mt-3 flex gap-2 text-xs">
        <button type="button" className={button.muted} onClick={onAddEndpoint}>
          <Plus className="size-3" /> Add endpoint
        </button>
        <button type="button" className={button.muted} onClick={onEditWeights} disabled={tier.endpoints.length === 0}>
          <SlidersHorizontal className="size-3" /> Edit weights
        </button>
      </div>
    </div>
  )
}

function RangeManager({ ranges, onUpdate, onAdd, onRemove }: { ranges: TokenRange[], onUpdate: (id: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => void, onAdd: () => void, onRemove: (id: string) => void }) {
    return (
        <div className="space-y-4 rounded-2xl border border-slate-100/80 bg-white p-4 mb-6">
            <h3 className="text-sm font-semibold text-slate-900/90 flex items-center gap-2"><Settings className="size-4 text-slate-500" /> Token Ranges</h3>
            <div className="space-y-3">
                {ranges.map(range => (
                    <div key={range.id} className="grid grid-cols-12 items-center gap-3 text-sm">
                        <div className="col-span-3 relative">
                            <input type="text" value={range.name} onChange={e => onUpdate(range.id, 'name', e.target.value)} className="pl-8 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
                            <Layers className="size-4 text-slate-400 absolute left-2 top-1/2 -translate-y-1/2" />
                        </div>
                        <div className="col-span-3 relative">
                            <input type="number" value={range.min_tokens} onChange={e => onUpdate(range.id, 'min_tokens', parseInt(e.target.value, 10))} className="pl-8 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
                            <ArrowRight className="size-4 text-slate-400 absolute left-2 top-1/2 -translate-y-1/2" />
                        </div>
                        <div className="col-span-1 text-center text-slate-400">-</div>
                        <div className="col-span-3 relative">
                            <input type="number" value={range.max_tokens ?? ''} placeholder="Infinity" onChange={e => onUpdate(range.id, 'max_tokens', e.target.value === '' ? null : parseInt(e.target.value, 10))} className="pl-8 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
                            <ArrowLeft className="size-4 text-slate-400 absolute left-2 top-1/2 -translate-y-1/2" />
                        </div>
                        <div className="col-span-2 text-right">
                            <button type="button" className={button.iconDanger} onClick={() => onRemove(range.id)}><Trash2 className="size-4" /></button>
                        </div>
                    </div>
                ))}
            </div>
            <button type="button" className={button.secondary} onClick={onAdd}><Plus className="size-4" /> Add Range</button>
        </div>
    )
}

export function LlmConfigScreen() {
  const [tiers, setTiers] = useState<Tier[]>(initialTiers)
  const [ranges, setRanges] = useState<TokenRange[]>(initialRanges)
  const [editingWeightsTier, setEditingWeightsTier] = useState<Tier | null>(null)
  const [addingEndpointTier, setAddingEndpointTier] = useState<Tier | null>(null)

  const standardTiers = tiers.filter((tier) => !tier.premium).sort((a, b) => a.order - b.order)
  const premiumTiers = tiers.filter((tier) => tier.premium).sort((a, b) => a.order - b.order)

  const getRangeName = (rangeId: string) => ranges.find(r => r.id === rangeId)?.name ?? 'Unknown'

  const addTier = (isPremium: boolean) => {
    const relevantTiers = tiers.filter(t => t.premium === isPremium);
    const newOrder = relevantTiers.length > 0 ? Math.max(...relevantTiers.map(t => t.order)) + 1 : 1
    const newTier: Tier = {
      id: `new-tier-${Date.now()}`,
      name: `Tier ${newOrder}`,
      rangeId: 'small', // Default to small range
      order: newOrder,
      premium: isPremium,
      endpoints: [],
    }
    setTiers([...tiers, newTier])
  }

  const removeTier = (tierId: string) => {
    setTiers(tiers.filter(t => t.id !== tierId))
  }

  const moveTier = (tierId: string, direction: 'up' | 'down') => {
    const tierToMove = tiers.find(t => t.id === tierId);
    if (!tierToMove) return;

    const siblings = tiers.filter(t => t.premium === tierToMove.premium && t.rangeId === tierToMove.rangeId).sort((a, b) => a.order - b.order);
    const currentIndex = siblings.findIndex(t => t.id === tierId);

    if (direction === 'up' && currentIndex > 0) {
      const otherTier = siblings[currentIndex - 1];
      [tierToMove.order, otherTier.order] = [otherTier.order, tierToMove.order];
    } else if (direction === 'down' && currentIndex < siblings.length - 1) {
      const otherTier = siblings[currentIndex + 1];
      [tierToMove.order, otherTier.order] = [otherTier.order, tierToMove.order];
    }

    setTiers([...tiers]);
  };

  const addEndpoint = (tierId: string, endpointLabel: string) => {
    if (!endpointLabel) return;
    let tierToUpdate: Tier | undefined;
    const newTiers = tiers.map(t => {
        if (t.id === tierId) {
            tierToUpdate = {
                ...t,
                endpoints: [...t.endpoints, { id: `ep-${Date.now()}`, label: endpointLabel, weight: 0 }],
            };
            return tierToUpdate;
        }
        return t;
    });
    setTiers(newTiers);

    if (tierToUpdate) {
        setEditingWeightsTier(tierToUpdate);
    }
  }

  const saveWeights = (tierId: string, updatedEndpoints: Endpoint[]) => {
    setTiers(currentTiers =>
      currentTiers.map(t =>
        t.id === tierId ? { ...t, endpoints: updatedEndpoints } : t
      )
    )
  }

  const handleRangeUpdate = (id: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    setRanges(ranges.map(r => r.id === id ? { ...r, [field]: value } : r))
  }

  const handleAddRange = () => {
    const newRange: TokenRange = {
        id: `range-${Date.now()}`,
        name: 'New Range',
        min_tokens: 0,
        max_tokens: 0,
    }
    setRanges([...ranges, newRange])
  }

  const handleRemoveRange = (id: string) => {
    // Also remove tiers associated with this range
    setTiers(tiers.filter(t => t.rangeId !== id))
    setRanges(ranges.filter(r => r.id !== id))
  }

  return (
    <div className="space-y-8">
      {editingWeightsTier && <EditWeightsModal tier={editingWeightsTier} onClose={() => setEditingWeightsTier(null)} onSave={saveWeights} />}
      {addingEndpointTier && <AddEndpointModal tier={addingEndpointTier} onClose={() => setAddingEndpointTier(null)} onAdd={addEndpoint} />}

      <div className="gobii-card-base space-y-2 px-6 py-6">
        <h1 className="text-2xl font-semibold text-slate-900/90">LLM configuration</h1>
        <p className="text-sm text-slate-600">
          Review the providers, endpoints, and token tiers powering orchestrator, browser-use, and summarization flows.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Active providers" value="4" icon={<PlugZap className="size-5" />} hint="OpenAI, Anthropic, Google, OpenRouter" />
        <StatCard label="Persistent endpoints" value="11" icon={<Atom className="size-5" />} hint="Across all token ranges" />
        <StatCard label="Browser models" value="2 configured" icon={<Globe className="size-5" />} hint="Primary + fallback" />
        <StatCard label="Premium failover tiers" value="Enabled" icon={<Shield className="size-5" />} hint="Routing first loop traffic" />
      </div>

      <SectionCard
        title="Provider inventory"
        description="Toggle providers on/off, rotate keys, and understand which endpoints they expose."
        actions={
          <button
            type="button"
            className={button.primary}
          >
            <Plus className="size-4" /> Add provider
          </button>
        }
      >
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {placeholderProviders.map((provider) => (
            <article
              key={provider.name}
              className="space-y-4 rounded-2xl border border-slate-100/80 bg-white px-5 py-5"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="flex-shrink-0 rounded-full bg-slate-100 p-2">
                        <BrainCircuit className="size-5 text-slate-600" />
                    </div>
                    <div>
                        <h3 className="text-base font-semibold text-slate-900/90">{provider.name}</h3>
                        <p className="text-xs text-slate-500">{provider.endpoints} endpoints</p>
                    </div>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50/80 px-3 py-1 text-xs font-medium text-emerald-700">
                  <ShieldCheck className="size-3.5" /> {provider.status}
                </span>
              </div>
              <dl className="mt-4 space-y-3 text-sm text-slate-600">
                <div className="flex items-start gap-2">
                    <KeyRound className="size-4 text-slate-400 mt-0.5" />
                    <div>
                        <p className="text-xs uppercase tracking-wide text-slate-400">Env fallback</p>
                        <p className="font-medium text-slate-900/90">{provider.fallback}</p>
                    </div>
                </div>
                <div className="flex items-start gap-2">
                    <Server className="size-4 text-slate-400 mt-0.5" />
                    <div>
                        <p className="text-xs uppercase tracking-wide text-slate-400">Browser backend</p>
                        <p className="font-medium text-slate-900/90">{provider.backend}</p>
                    </div>
                </div>
              </dl>
              <p className="mt-3 text-xs text-slate-500">{provider.usage}</p>
              <div className="mt-4 flex flex-wrap gap-2 text-sm">
                <button className={button.muted} type="button">
                  <Settings className="size-3.5" /> Manage endpoints
                </button>
                <button className={button.muted} type="button">
                  <KeyRound className="size-3.5" /> Rotate key
                </button>
                <button className={button.danger} type="button">
                  <ToggleRight className="size-3.5" /> Disable
                </button>
              </div>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Token-based failover tiers"
        description="Manage token ranges, tier ordering, and weighted endpoints."
        actions={
            <button type="button" className={button.primary} onClick={() => addTier(false)}>
              <Plus className="size-4" /> Add Tier
            </button>
        }
      >
        <RangeManager ranges={ranges} onUpdate={handleRangeUpdate} onAdd={handleAddRange} onRemove={handleRemoveRange} />
        <div className="grid gap-6 lg:grid-cols-2">
          <article className="space-y-4 rounded-2xl border border-slate-200/80 bg-slate-50/80 p-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-slate-900/90">Standard failover tiers</h3>
                <p className="text-xs text-slate-500">Used for most traffic once premium routing is exhausted.</p>
              </div>
              <div className="flex-shrink-0">
                <button type="button" className={button.secondary} onClick={() => addTier(false)}>
                  <Plus className="size-4" /> Add tier
                </button>
              </div>
            </div>
            <div className="mt-4 space-y-4 text-sm text-slate-600">
              {ranges.map(range => {
                const rangeTiers = standardTiers.filter(t => t.rangeId === range.id);
                if (rangeTiers.length === 0) return null;
                return (
                  <div key={`standard-${range.id}`}>
                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">{range.name} Range</h4>
                    <div className="space-y-3">
                      {rangeTiers.map((tier, index) => (
                        <TierCard
                          key={tier.id}
                          tier={tier}
                          rangeName={getRangeName(tier.rangeId)}
                          onMove={(direction) => moveTier(tier.id, direction)}
                          onRemove={() => removeTier(tier.id)}
                          onAddEndpoint={() => setAddingEndpointTier(tier)}
                          onEditWeights={() => setEditingWeightsTier(tier)}
                          isFirst={index === 0}
                          isLast={index === rangeTiers.length - 1}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
               {standardTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No standard tiers configured.</p>}
            </div>
          </article>
          <article className="space-y-4 rounded-2xl border border-emerald-200/80 bg-emerald-50/50 p-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-slate-900/90">Premium failover tiers</h3>
                <p className="text-xs text-slate-500">Prepended for new agents or upgraded plans.</p>
              </div>
              <div className="flex-shrink-0">
                <button type="button" className={button.secondary} onClick={() => addTier(true)}>
                  <Plus className="size-4" /> Add premium tier
                </button>
              </div>
            </div>
            <div className="mt-4 space-y-4 text-sm text-slate-600">
              {ranges.map(range => {
                const rangeTiers = premiumTiers.filter(t => t.rangeId === range.id);
                if (rangeTiers.length === 0) return null;
                return (
                  <div key={`premium-${range.id}`}>
                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-800">{range.name} Range</h4>
                    <div className="space-y-3">
                      {rangeTiers.map((tier, index) => (
                        <TierCard
                          key={tier.id}
                          tier={tier}
                          rangeName={getRangeName(tier.rangeId)}
                          onMove={(direction) => moveTier(tier.id, direction)}
                          onRemove={() => removeTier(tier.id)}
                          onAddEndpoint={() => setAddingEndpointTier(tier)}
                          onEditWeights={() => setEditingWeightsTier(tier)}
                          isFirst={index === 0}
                          isLast={index === rangeTiers.length - 1}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
              {premiumTiers.length === 0 ? <p className="text-center text-xs text-slate-500 py-4">No premium tiers configured.</p> : null}
            </div>
          </article>
        </div>
      </SectionCard>

      <SectionCard
        title="Browser-use models"
        description="The browser agent can share the orchestrator model or run a dedicated stack."
        actions={
          <button
            type="button"
            className={button.secondary}
          >
            <Settings className="size-4" /> Configure browser routing
          </button>
        }
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-2xl border border-slate-100/80 bg-white px-5 py-4 text-sm text-slate-600">
            Dedicated browser endpoint
            {' '}
            <span className="font-medium text-slate-900/90">z-ai/glm-4.5 (OpenRouter)</span>
            {' '}
            stored in
            {' '}
            <code className="rounded bg-slate-100 px-1 py-0.5 text-xs text-slate-600">BrowserModelEndpoint</code>.
          </div>
          <div className="rounded-2xl border border-slate-100/80 bg-white px-5 py-4 text-sm text-slate-600">
            Policy fallback
            {' '}
            <p className="text-xs text-slate-500">Reuses the orchestrator failover tiers when disabled or unhealthy.</p>
          </div>
        </div>
        <ul className="grid gap-3 text-sm text-slate-600 md:grid-cols-2">
          <li className="rounded-2xl border border-slate-100/80 bg-white px-4 py-3">
            <p className="font-semibold text-slate-900/90">Primary tasks</p>
            <p className="text-xs text-slate-500">Form filling, long-running browsing, screenshot capture.</p>
          </li>
          <li className="rounded-2xl border border-slate-100/80 bg-white px-4 py-3">
            <p className="font-semibold text-slate-900/90">Monitoring hints</p>
            <p className="text-xs text-slate-500">Latency spikes or 4xx errors push traffic back to the orchestrator tiers.</p>
          </li>
        </ul>
      </SectionCard>

      <SectionCard
        title="Other model consumers"
        description="Surface-level overview of summarization, embeddings, and tooling hints."
      >
        <ul className="space-y-3 text-sm text-slate-600">
          {workloadSummaries.map((workload) => (
            <li key={workload.name} className="flex items-center gap-3 rounded-2xl border border-slate-100/80 bg-white px-4 py-3">
              {workload.icon}
              <div>
                <p className="font-semibold text-slate-900/90">{workload.name}</p>
                <p>{workload.model} – {workload.detail}</p>
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>
    </div>
  )
}