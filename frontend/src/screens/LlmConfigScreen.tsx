import { AlertCircle, Atom, Globe, PlugZap, Shield } from 'lucide-react'
import { SectionCard } from '../components/llmConfig/SectionCard'
import { StatCard } from '../components/llmConfig/StatCard'

type PlaceholderTier = {
  id: string
  name: string
  premium?: boolean
  endpoints: Array<{ label: string; weight: string }>
}

const placeholderTiers: PlaceholderTier[] = [
  {
    id: 'small-standard',
    name: 'Small range – Tier 1',
    endpoints: [
      { label: 'openai/gpt-5', weight: '70%' },
      { label: 'anthropic/claude-sonnet-4', weight: '30%' },
    ],
  },
  {
    id: 'small-standard-2',
    name: 'Small range – Tier 2',
    endpoints: [
      { label: 'vertex_ai/gemini-2.5-pro', weight: '100%' },
    ],
  },
  {
    id: 'medium-premium',
    name: 'Medium range – Premium Tier',
    premium: true,
    endpoints: [
      { label: 'openrouter/z-ai/glm-4.6:exacto', weight: '60%' },
      { label: 'openai/gpt-5', weight: '40%' },
    ],
  },
]

export function LlmConfigScreen() {
  return (
    <div className="space-y-8">
      <div className="gobii-card-base space-y-2 px-6 py-6">
        <h1 className="text-2xl font-semibold text-slate-900/90">LLM configuration</h1>
        <p className="text-sm text-slate-600">
          Review the providers, endpoints, and token tiers powering orchestrator, browser-use, and summarization flows.
          This screen is a starting point—hooking it up to live APIs comes next.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Active providers" value="4" icon={<PlugZap className="size-5" />} hint="OpenAI, Anthropic, Google, OpenRouter" />
        <StatCard label="Persistent endpoints" value="11" icon={<Atom className="size-5" />} hint="Across all token ranges" />
        <StatCard label="Browser models" value="2 configured" icon={<Globe className="size-5" />} hint="Primary + fallback" />
        <StatCard label="Premium tiers" value="Enabled" icon={<Shield className="size-5" />} hint="Routing first loop traffic" />
      </div>

      <SectionCard
        title="Provider inventory"
        description="Toggle providers on/off, rotate keys, and understand which endpoints they expose."
        actions={
          <button
            type="button"
            className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Add provider
          </button>
        }
      >
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {['OpenRouter', 'Anthropic', 'OpenAI'].map((provider) => (
            <article key={provider} className="rounded-2xl border border-slate-100/80 bg-white/80 p-4 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-900/90">{provider}</h3>
                  <p className="text-xs text-slate-500">API key stored • 3 endpoints</p>
                </div>
                <span className="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                  Healthy
                </span>
              </div>
              <dl className="mt-4 space-y-2 text-sm text-slate-600">
                <div className="flex justify-between">
                  <dt>Env fallback</dt>
                  <dd className="font-medium text-slate-900/90">OPENROUTER_API_KEY</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Browser backend</dt>
                  <dd className="font-medium text-slate-900/90">OpenAI-compatible</dd>
                </div>
              </dl>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  className="rounded-2xl border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  type="button"
                >
                  Manage endpoints
                </button>
                <button
                  className="rounded-2xl border border-transparent px-3 py-1.5 text-sm font-medium text-rose-600 hover:bg-rose-50"
                  type="button"
                >
                  Disable
                </button>
              </div>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Token tiers & failover order"
        description="Map token ranges to weighted model tiers. Drag-and-drop and inline editing will come once APIs are wired."
      >
        <div className="space-y-4">
          {placeholderTiers.map((tier) => (
            <article key={tier.id} className="rounded-2xl border border-slate-100/80 bg-white/80 p-4 shadow-sm">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-xs uppercase tracking-wide text-slate-500">{tier.premium ? 'Premium tier' : 'Standard tier'}</p>
                  <h3 className="text-base font-semibold text-slate-900/90">{tier.name}</h3>
                </div>
                <span className="text-xs font-medium text-slate-500">Order #{tier.id.split('-').pop()}</span>
              </div>
              <ul className="mt-3 space-y-2 text-sm text-slate-600">
                {tier.endpoints.map((endpoint) => (
                  <li key={`${tier.id}-${endpoint.label}`} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2">
                    <span className="font-medium text-slate-900/90">{endpoint.label}</span>
                    <span className="text-xs font-semibold text-slate-500">{endpoint.weight}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="Browser-use models"
        description="The browser agent can share the orchestrator model or run a dedicated stack."
        actions={
          <button
            type="button"
            className="rounded-2xl border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Edit policy
          </button>
        }
      >
        <div className="space-y-3">
          <div className="rounded-2xl border border-slate-100/80 bg-slate-50/70 p-4 text-sm text-slate-600">
            Dedicated browser endpoint: <span className="font-medium text-slate-900/90">z-ai/glm-4.5 (OpenRouter)</span>
          </div>
          <div className="rounded-2xl border border-slate-100/80 bg-white/80 p-4">
            <p className="text-sm text-slate-500">Fallback logic mirrors the persistent tier configuration. Browser task snapshots will appear here once connected.</p>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Other model consumers"
        description="Surface-level overview of summarization, embeddings, and tooling hints."
        footer={
          <p>
            Need to change something immediately?{' '}
            <a href="/console/agents/" className="text-blue-600 underline-offset-4 hover:underline">
              Jump to the agent list
            </a>
            .
          </p>
        }
      >
        <ul className="space-y-3 text-sm text-slate-600">
          <li className="flex items-center gap-3 rounded-2xl border border-slate-100/80 bg-white/80 p-4">
            <AlertCircle className="size-5 text-amber-500" aria-hidden="true" />
            <div>
              <p className="font-semibold text-slate-900/90">Summaries</p>
              <p>Currently pinned to openai/gpt-4o-mini at temperature 0.</p>
            </div>
          </li>
          <li className="flex items-center gap-3 rounded-2xl border border-slate-100/80 bg-white/80 p-4">
            <AlertCircle className="size-5 text-blue-500" aria-hidden="true" />
            <div>
              <p className="font-semibold text-slate-900/90">Embeddings</p>
              <p>text-embedding-3-large (OpenAI) • rotating monthly.</p>
            </div>
          </li>
        </ul>
      </SectionCard>
    </div>
  )
}
