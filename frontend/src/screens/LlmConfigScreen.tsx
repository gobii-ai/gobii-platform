import {
  AlertCircle,
  Atom,
  Globe,
  PlugZap,
  Shield,
  X,
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
  Pencil,
  Beaker,
  LoaderCircle,
  Check,
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
      { id: 'ep1', label: 'openai/gpt-5', weight: 50 },
      { id: 'ep2', label: 'anthropic/claude-sonnet-4', weight: 30 },
      { id: 'ep-new', label: 'new-endpoint/some-model', weight: 20 },
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

const initialBrowserTiers: Tier[] = [
    {
        id: 'browser-std-1',
        name: 'Tier 1',
        rangeId: 'browser',
        order: 1,
        premium: false,
        endpoints: [
            { id: 'bep1', label: 'openrouter/z-ai/glm-4.5', weight: 100 },
        ],
    },
    {
        id: 'browser-std-2',
        name: 'Tier 2',
        rangeId: 'browser',
        order: 2,
        premium: false,
        endpoints: [
            { id: 'bep2', label: 'openai/gpt-4o', weight: 100 },
        ],
    }
]

const placeholderProviders = [
  {
    id: 'prov1',
    name: 'OpenRouter',
    status: 'Healthy',
    fallback: 'OPENROUTER_API_KEY',
    backend: 'OpenAI-compatible',
    usage: 'Primary for long-context + premium workloads.',
    endpoints: [
        { id: 'prov1-ep1', name: 'openrouter/z-ai/glm-4.6:exacto' },
        { id: 'prov1-ep2', name: 'openrouter/google/gemini-pro' },
    ]
  },
  {
    id: 'prov2',
    name: 'Anthropic',
    status: 'Healthy',
    fallback: 'ANTHROPIC_API_KEY',
    backend: 'Native API',
    usage: 'Used when Claude reasoning is preferred.',
    endpoints: [
        { id: 'prov2-ep1', name: 'anthropic/claude-sonnet-4' },
    ]
  },
  {
    id: 'prov3',
    name: 'OpenAI',
    status: 'Healthy',
    fallback: 'OPENAI_API_KEY',
    backend: 'Native API',
    usage: 'Default for summaries and quick iterations.',
    endpoints: [
        { id: 'prov3-ep1', name: 'openai/gpt-5' },
        { id: 'prov3-ep2', name: 'openai/gpt-4o' },
    ]
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

function TierCard({ tier, onMove, onRemove, onAddEndpoint, onUpdateEndpointWeight, onRemoveEndpoint }: { tier: Tier, onMove: (direction: 'up' | 'down') => void, onRemove: () => void, onAddEndpoint: () => void, onUpdateEndpointWeight: (endpointId: string, weight: number) => void, onRemoveEndpoint: (endpointId: string) => void }) {
  const borderColor = tier.premium ? 'border-emerald-200' : 'border-slate-200'
  const textColor = tier.premium ? 'text-emerald-700' : 'text-slate-500'
  const headerIcon = tier.premium ? <ShieldCheck className={`size-4 ${textColor}`} /> : <Layers className={`size-4 ${textColor}`} />

  return (
    <div className={`rounded-xl border ${borderColor} bg-white`}>
      <div className={`flex items-center justify-between p-4 text-xs uppercase tracking-wide ${textColor}`}>
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
          {tier.endpoints
            .sort((a, b) => a.label.localeCompare(b.label))
            .map((endpoint) => (
            <div key={endpoint.id} className="grid grid-cols-12 items-center gap-3 text-sm font-medium text-slate-900/90">
              <span className="col-span-6 flex items-center gap-2 truncate"><PlugZap className="size-4 flex-shrink-0 text-slate-400" /> {endpoint.label}</span>
              <div className="col-span-6 flex items-center gap-2">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={endpoint.weight}
                  onChange={(e) => onUpdateEndpointWeight(endpoint.id, parseInt(e.target.value, 10))}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                />
                <input
                  type="number"
                  value={endpoint.weight}
                  onChange={(e) => onUpdateEndpointWeight(endpoint.id, parseInt(e.target.value, 10) || 0)}
                  className="block w-20 rounded-lg border-slate-300 text-right shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                />
                <button onClick={() => onRemoveEndpoint(endpoint.id)} className={button.iconDanger}><X className="size-4" /></button>
              </div>
            </div>
          ))}
        </div>
        {tier.endpoints.length === 0 && <div className="text-center text-xs text-slate-400 py-2">No endpoints added.</div>}
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
  ...tierActions
}: {
  range: TokenRange,
  tiers: Tier[],
  onUpdate: (id: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => void,
  onRemove: (id: string) => void,
  onAddTier: (isPremium: boolean, rangeId: string) => void,
  [key: string]: any // for other tier actions
}) {
  const standardTiers = tiers.filter(t => t.rangeId === range.id && !t.premium).sort((a, b) => a.order - b.order);
  const premiumTiers = tiers.filter(t => t.rangeId === range.id && t.premium).sort((a, b) => a.order - b.order);

  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white">
        <div className="p-4 space-y-3">
            <div className="grid grid-cols-12 items-center gap-3 text-sm">
                <div className="col-span-12 sm:col-span-3 relative">
                    <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Range Name</label>
                    <input type="text" value={range.name} onChange={e => onUpdate(range.id, 'name', e.target.value)} className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
                </div>
                <div className="col-span-6 sm:col-span-3 relative">
                    <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Min Tokens</label>
                    <input type="number" value={range.min_tokens} onChange={e => onUpdate(range.id, 'min_tokens', parseInt(e.target.value, 10))} className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
                </div>
                <div className="col-span-6 sm:col-span-3 relative">
                    <label className="absolute -top-2 left-2 text-xs text-slate-400 bg-white px-1">Max Tokens</label>
                    <input type="number" value={range.max_tokens ?? ''} placeholder="Infinity" onChange={e => onUpdate(range.id, 'max_tokens', e.target.value === '' ? null : parseInt(e.target.value, 10))} className="block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
                </div>
                <div className="col-span-12 sm:col-span-3 text-right">
                    <button type="button" className={button.danger} onClick={() => onRemove(range.id)}><Trash2 className="size-4" /> Remove Range</button>
                </div>
            </div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
            <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
                <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-slate-700">Standard Tiers</h4>
                    <button type="button" className={button.secondary} onClick={() => onAddTier(false, range.id)}>
                        <Plus className="size-4" /> Add
                    </button>
                </div>
                {standardTiers.length > 0 ? standardTiers.map((tier, index) => (
                    <TierCard
                        key={tier.id}
                        tier={tier}
                        onMove={(direction) => tierActions.moveTier(tier.id, direction)}
                        onRemove={() => tierActions.removeTier(tier.id)}
                        onAddEndpoint={() => tierActions.setAddingEndpointTier(tier)}
                        onUpdateEndpointWeight={(endpointId: string, weight: number) => tierActions.updateEndpointWeight(tier.id, endpointId, weight)}
                        onRemoveEndpoint={(endpointId: string) => tierActions.removeEndpoint(tier.id, endpointId)}
                    />
                )) : <p className="text-center text-xs text-slate-400 py-4">No standard tiers for this range.</p>}
            </div>
            <div className="bg-emerald-50/50 p-4 space-y-3 rounded-xl">
                <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-emerald-800">Premium Tiers</h4>
                    <button type="button" className={button.secondary} onClick={() => onAddTier(true, range.id)}>
                        <Plus className="size-4" /> Add
                    </button>
                </div>
                {premiumTiers.length > 0 ? premiumTiers.map((tier, index) => (
                    <TierCard
                        key={tier.id}
                        tier={tier}
                        onMove={(direction) => tierActions.moveTier(tier.id, direction)}
                        onRemove={() => tierActions.removeTier(tier.id)}
                        onAddEndpoint={() => tierActions.setAddingEndpointTier(tier)}
                        onUpdateEndpointWeight={(endpointId: string, weight: number) => tierActions.updateEndpointWeight(tier.id, endpointId, weight)}
                        onRemoveEndpoint={(endpointId: string) => tierActions.removeEndpoint(tier.id, endpointId)}
                    />
                )) : <p className="text-center text-xs text-slate-400 py-4">No premium tiers for this range.</p>}
            </div>
        </div>
    </div>
  )
}

function ProviderCard({ provider }: { provider: any }) {
    const [activeTab, setActiveTab] = useState('endpoints');
    const [testStatus, setTestStatus] = useState<{[key: string]: 'idle' | 'testing' | 'success' | 'failed'}>({});

    const handleTest = (endpointId: string) => {
        setTestStatus(prev => ({ ...prev, [endpointId]: 'testing' }));
        setTimeout(() => {
            const success = Math.random() > 0.3; // Simulate success/failure
            setTestStatus(prev => ({ ...prev, [endpointId]: success ? 'success' : 'failed' }));
            setTimeout(() => setTestStatus(prev => ({ ...prev, [endpointId]: 'idle' })), 2000);
        }, 1500);
    }

    const TestButton = ({ endpointId }: { endpointId: string }) => {
        const status = testStatus[endpointId] || 'idle';
        if (status === 'testing') {
            return <button className={button.muted} disabled><LoaderCircle className="size-4 animate-spin" /> Testing...</button>
        }
        if (status === 'success') {
            return <button className={`${button.muted} bg-emerald-50 text-emerald-700`} disabled><Check className="size-4" /> Success</button>
        }
        if (status === 'failed') {
            return <button className={`${button.muted} bg-rose-50 text-rose-700`} disabled><X className="size-4" /> Failed</button>
        }
        return <button className={button.muted} onClick={() => handleTest(endpointId)}><Beaker className="size-4" /> Test</button>
    }

    return (
        <article className="rounded-2xl border border-slate-200/80 bg-white">
            <div className="flex items-start justify-between p-4">
                <div className="flex items-center gap-3">
                    <div className="flex-shrink-0 rounded-full bg-slate-100 p-2">
                        <BrainCircuit className="size-5 text-slate-600" />
                    </div>
                    <div>
                        <h3 className="text-base font-semibold text-slate-900/90">{provider.name}</h3>
                        <p className="text-xs text-slate-500">{provider.endpoints.length} endpoints</p>
                    </div>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50/80 px-3 py-1 text-xs font-medium text-emerald-700">
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

            <div className="p-4">
                {activeTab === 'endpoints' && (
                    <div className="space-y-2">
                        <div className="flex items-center justify-end">
                            <button className={button.muted}><Plus className="size-3" /> Add endpoint</button>
                        </div>
                        <div className="space-y-2 rounded-lg bg-slate-50 p-2">
                            {provider.endpoints.map((endpoint: any) => (
                                <div key={endpoint.id} className="flex items-center justify-between rounded-md bg-white p-2">
                                    <span className="text-sm font-mono text-slate-700">{endpoint.name}</span>
                                    <div className="flex items-center gap-1">
                                        <TestButton endpointId={endpoint.id} />
                                        <button className={button.icon}><Pencil className="size-4" /></button>
                                        <button className={button.iconDanger}><Trash2 className="size-4" /></button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
                {activeTab === 'settings' && (
                    <div className="space-y-4">
                        <dl className="space-y-3 text-sm text-slate-600">
                            <div>
                                <dt className="text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2"><KeyRound className="size-4" /> Env fallback</dt>
                                <dd className="font-medium text-slate-900/90 pl-6">{provider.fallback}</dd>
                            </div>
                            <div>
                                <dt className="text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2"><Server className="size-4" /> Browser backend</dt>
                                <dd className="font-medium text-slate-900/90 pl-6">{provider.backend}</dd>
                            </div>
                        </dl>
                        <div className="border-t border-slate-200/80 my-4" />
                        <div className="flex flex-wrap gap-2 text-sm">
                            <button className={button.muted} type="button">
                              <KeyRound className="size-3.5" /> Rotate key
                            </button>
                            <button className={button.danger} type="button">
                              <ToggleRight className="size-3.5" /> Disable
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </article>
    )
}

export function LlmConfigScreen() {
  const [tiers, setTiers] = useState<Tier[]>(initialTiers)
  const [browserTiers, setBrowserTiers] = useState<Tier[]>(initialBrowserTiers)
  const [ranges, setRanges] = useState<TokenRange[]>(initialRanges)
  const [providers, setProviders] = useState(placeholderProviders)
  const [addingEndpointTier, setAddingEndpointTier] = useState<Tier | null>(null)

  const addTier = (isPremium: boolean, rangeId: string) => {
    const targetStateSetter = rangeId === 'browser' ? setBrowserTiers : setTiers;
    targetStateSetter(currentTiers => {
        const relevantTiers = currentTiers.filter(t => t.premium === isPremium && t.rangeId === rangeId);
        const newOrder = relevantTiers.length > 0 ? Math.max(...relevantTiers.map(t => t.order)) + 1 : 1
        const newTier: Tier = {
          id: `new-tier-${Date.now()}`,
          name: `Tier ${newOrder}`,
          rangeId: rangeId,
          order: newOrder,
          premium: isPremium,
          endpoints: [],
        }
        return [...currentTiers, newTier];
    });
  }

  const removeTier = (tierId: string) => {
    setTiers(tiers.filter(t => t.id !== tierId));
    setBrowserTiers(browserTiers.filter(t => t.id !== tierId));
  }

  const moveTier = (tierId: string, direction: 'up' | 'down') => {
    const tierExistsInOrchestrator = tiers.some(t => t.id === tierId);
    const targetStateSetter = tierExistsInOrchestrator ? setTiers : setBrowserTiers;

    targetStateSetter(currentTiers => {
        const tierToMove = currentTiers.find(t => t.id === tierId);
        if (!tierToMove) return currentTiers;

        const siblings = currentTiers.filter(t => t.premium === tierToMove.premium && t.rangeId === tierToMove.rangeId).sort((a, b) => a.order - b.order);
        const currentIndex = siblings.findIndex(t => t.id === tierId);

        if (direction === 'up' && currentIndex > 0) {
          const otherTier = siblings[currentIndex - 1];
          [tierToMove.order, otherTier.order] = [otherTier.order, tierToMove.order];
        } else if (direction === 'down' && currentIndex < siblings.length - 1) {
          const otherTier = siblings[currentIndex + 1];
          [tierToMove.order, otherTier.order] = [otherTier.order, tierToMove.order];
        }
        return [...currentTiers];
    });
  };

  const addEndpoint = (tierId: string, endpointLabel: string) => {
    if (!endpointLabel) return;
    const tierExistsInOrchestrator = tiers.some(t => t.id === tierId);
    const targetStateSetter = tierExistsInOrchestrator ? setTiers : setBrowserTiers;

    targetStateSetter(currentTiers => {
      return currentTiers.map(t => {
        if (t.id === tierId) {
          const newEndpoints = [...t.endpoints, { id: `ep-${Date.now()}`, label: endpointLabel, weight: 0 }];
          const numEndpoints = newEndpoints.length;
          if (numEndpoints > 0) {
              const evenWeight = Math.floor(100 / numEndpoints);
              const remainder = 100 % numEndpoints;
              const finalEndpoints = newEndpoints.map((ep, index) => ({
                ...ep,
                weight: index < remainder ? evenWeight + 1 : evenWeight,
              }));
              return { ...t, endpoints: finalEndpoints };
          }
          return { ...t, endpoints: newEndpoints };
        }
        return t;
      });
    });
  }

  const updateEndpointWeight = (tierId: string, endpointId: string, newWeight: number) => {
    const tierExistsInOrchestrator = tiers.some(t => t.id === tierId);
    const targetStateSetter = tierExistsInOrchestrator ? setTiers : setBrowserTiers;

    targetStateSetter(currentTiers => currentTiers.map(tier => {
      if (tier.id !== tierId) return tier;

      newWeight = Math.max(0, Math.min(100, newWeight));
      const activeEndpoint = tier.endpoints.find(e => e.id === endpointId);
      if (!activeEndpoint) return tier;

      const otherEndpoints = tier.endpoints.filter(e => e.id !== endpointId);
      const remainder = 100 - newWeight;
      
      let finalEndpoints: Endpoint[] = [];

      if (otherEndpoints.length > 0) {
        const totalOtherWeight = otherEndpoints.reduce((sum, e) => sum + e.weight, 0);
        if (totalOtherWeight > 0) {
          otherEndpoints.forEach(ep => {
            ep.weight = (ep.weight / totalOtherWeight) * remainder;
          });
        } else {
          const equalShare = remainder / otherEndpoints.length;
          otherEndpoints.forEach(ep => {
            ep.weight = equalShare;
          });
        }
        finalEndpoints = [...otherEndpoints, { ...activeEndpoint, weight: newWeight }];
      } else {
        finalEndpoints = [{ ...activeEndpoint, weight: 100 }];
      }

      let roundedTotal = 0;
      finalEndpoints.forEach(ep => {
        ep.weight = Math.round(ep.weight);
        roundedTotal += ep.weight;
      });

      const roundingError = 100 - roundedTotal;
      if (roundingError !== 0 && finalEndpoints.length > 0) {
          const endpointToAdjust = finalEndpoints.find(e => e.id === endpointId) || finalEndpoints[0];
          endpointToAdjust.weight += roundingError;
      }

      return { ...tier, endpoints: finalEndpoints };
    }));
  };

  const removeEndpoint = (tierId: string, endpointId: string) => {
    const tierExistsInOrchestrator = tiers.some(t => t.id === tierId);
    const targetStateSetter = tierExistsInOrchestrator ? setTiers : setBrowserTiers;

    targetStateSetter(currentTiers => currentTiers.map(t => {
      if (t.id === tierId) {
        const newEndpoints = t.endpoints.filter(e => e.id !== endpointId);
        const totalWeight = newEndpoints.reduce((sum, ep) => sum + ep.weight, 0);
        if (totalWeight > 0 && newEndpoints.length > 0) {
            const scale = 100 / totalWeight;
            let redistributedTotal = 0;
            newEndpoints.forEach((ep, index) => {
                const newW = Math.round(ep.weight * scale);
                ep.weight = newW;
                redistributedTotal += newW;
            });
            const error = 100 - redistributedTotal;
            if (error !== 0 && newEndpoints.length > 0) newEndpoints[0].weight += error;
        } else if (newEndpoints.length > 0) {
            const evenWeight = Math.floor(100 / newEndpoints.length);
            const remainder = 100 % newEndpoints.length;
            newEndpoints.forEach((ep, index) => {
                ep.weight = index < remainder ? evenWeight + 1 : evenWeight;
            });
        }
        return { ...t, endpoints: newEndpoints };
      }
      return t;
    }));
  }

  const handleRangeUpdate = (id: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    setRanges(ranges.map(r => r.id === id ? { ...r, [field]: value } : r))
  }

  const handleAddRange = () => {
    const lastRange = ranges.sort((a, b) => (a.max_tokens ?? Infinity) - (b.max_tokens ?? Infinity)).slice(-1)[0];
    const newMinTokens = lastRange && lastRange.max_tokens !== null ? lastRange.max_tokens : 0;

    const newRange: TokenRange = {
        id: `range-${Date.now()}`,
        name: 'New Range',
        min_tokens: newMinTokens,
        max_tokens: newMinTokens + 10000,
    }
    setRanges([...ranges, newRange])
  }

  const handleRemoveRange = (id: string) => {
    setTiers(tiers.filter(t => t.rangeId !== id))
    setRanges(ranges.filter(r => r.id !== id))
  }

  return (
    <div className="space-y-8">
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
        <div className="grid gap-4 md:grid-cols-1 lg:grid-cols-2">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
            />
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Token-based failover tiers"
        description="Manage token ranges, tier ordering, and weighted endpoints."
        actions={
            <button type="button" className={button.primary} onClick={handleAddRange}>
              <Plus className="size-4" /> Add Range
            </button>
        }
      >
        <div className="space-y-6">
            {ranges.sort((a, b) => a.min_tokens - b.min_tokens).map(range => (
                <RangeSection
                    key={range.id}
                    range={range}
                    tiers={tiers}
                    onAddTier={addTier}
                    onUpdate={handleRangeUpdate}
                    onRemove={handleRemoveRange}
                    // Pass down other actions
                    moveTier={moveTier}
                    removeTier={removeTier}
                    setAddingEndpointTier={setAddingEndpointTier}
                    updateEndpointWeight={updateEndpointWeight}
                    removeEndpoint={removeEndpoint}
                />
            ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Browser-use models"
        description="The browser agent can share the orchestrator model or run a dedicated stack."
      >
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
                <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-slate-700">Standard Tiers</h4>
                    <button type="button" className={button.secondary} onClick={() => addTier(false, 'browser')}>
                        <Plus className="size-4" /> Add
                    </button>
                </div>
                {browserTiers.filter(t => !t.premium).map((tier, index) => (
                    <TierCard
                        key={tier.id}
                        tier={tier}
                        onMove={(direction) => moveTier(tier.id, direction)}
                        onRemove={() => removeTier(tier.id)}
                        onAddEndpoint={() => setAddingEndpointTier(tier)}
                        onUpdateEndpointWeight={(endpointId: string, weight: number) => updateEndpointWeight(tier.id, endpointId, weight)}
                        onRemoveEndpoint={(endpointId: string) => removeEndpoint(tier.id, endpointId)}
                    />
                ))}
            </div>
            <div className="bg-emerald-50/50 p-4 space-y-3 rounded-xl">
                <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-emerald-800">Premium Tiers</h4>
                    <button type="button" className={button.secondary} onClick={() => addTier(true, 'browser')}>
                        <Plus className="size-4" /> Add
                    </button>
                </div>
                 {browserTiers.filter(t => t.premium).length === 0 && <p className="text-center text-xs text-slate-400 py-4">No premium tiers for this range.</p>}
                 {browserTiers.filter(t => t.premium).map((tier, index) => (
                    <TierCard
                        key={tier.id}
                        tier={tier}
                        onMove={(direction) => moveTier(tier.id, direction)}
                        onRemove={() => removeTier(tier.id)}
                        onAddEndpoint={() => setAddingEndpointTier(tier)}
                        onUpdateEndpointWeight={(endpointId: string, weight: number) => updateEndpointWeight(tier.id, endpointId, weight)}
                        onRemoveEndpoint={(endpointId: string) => removeEndpoint(tier.id, endpointId)}
                    />
                ))}
            </div>
        </div>
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