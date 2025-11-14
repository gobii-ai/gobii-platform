import { AlertCircle, Atom, Globe, PlugZap, ServerCog, Shield, Workflow } from 'lucide-react'
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

const flowSteps = [
  {
    title: 'Provider credentials',
    detail: 'Keys + env fallbacks captured in LLMProvider records.',
    icon: <PlugZap className="size-5 text-blue-600" aria-hidden="true" />,
    hint: '4 active providers',
  },
  {
    title: 'Persistent endpoints',
    detail: 'Each endpoint defines a LiteLLM model string + capabilities.',
    icon: <ServerCog className="size-5 text-indigo-600" aria-hidden="true" />,
    hint: '11 total endpoints',
  },
  {
    title: 'Token tiers',
    detail: 'Ranges + tier weights decide the failover order per request.',
    icon: <Workflow className="size-5 text-slate-600" aria-hidden="true" />,
    hint: 'Premium tiers optional',
  },
]

const workloadSummaries = [
  {
    name: 'Summaries',
    model: 'openai/gpt-4o-mini',
    detail: 'Temperature forced to 0 for deterministic compression.',
  },
  {
    name: 'Search tools',
    model: 'openrouter/z-ai/glm-4.6:exacto',
    detail: 'Uses the same failover list; tool calling enabled.',
  },
  {
    name: 'Embeddings',
    model: 'text-embedding-3-large',
    detail: 'Rotates monthly for cached comparisons.',
  },
]

export function LlmConfigScreen() {
  const standardTiers = placeholderTiers.filter((tier) => !tier.premium)
  const premiumTiers = placeholderTiers.filter((tier) => tier.premium)

  return (
    <div className="space-y-8">
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
        <StatCard label="Premium tiers" value="Enabled" icon={<Shield className="size-5" />} hint="Routing first loop traffic" />
      </div>

      <SectionCard
        title="How traffic flows"
        description="Every request walks the same pipeline. This view is read-only today but mirrors the real routing logic."
        className="space-y-5"
      >
        <div className="grid gap-4 md:grid-cols-3">
          {flowSteps.map((step) => (
            <article
              key={step.title}
              className="rounded-2xl border border-slate-100/80 bg-slate-50/70 p-4 text-sm text-slate-600 shadow-inner"
            >
              <div className="flex items-center gap-3">
                {step.icon}
                <div>
                  <p className="text-sm font-semibold text-slate-900/90">{step.title}</p>
                  <p className="text-xs uppercase tracking-wide text-slate-500">{step.hint}</p>
                </div>
              </div>
              <p className="mt-3 leading-relaxed">{step.detail}</p>
            </article>
          ))}
        </div>
      </SectionCard>

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
          {placeholderProviders.map((provider) => (
            <article key={provider.name} className="rounded-2xl border border-slate-100/80 bg-white/80 p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-900/90">{provider.name}</h3>
                  <p className="text-xs text-slate-500">API key stored • {provider.endpoints} endpoints</p>
                </div>
                <span className="inline-flex items-center rounded-full bg-emerald-50/80 px-3 py-1 text-xs font-medium text-emerald-700">
                  {provider.status}
                </span>
              </div>
              <dl className="mt-4 space-y-2 text-sm text-slate-600">
                <div className="flex justify-between">
                  <dt>Env fallback</dt>
                  <dd className="font-medium text-slate-900/90">{provider.fallback}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Browser backend</dt>
                  <dd className="font-medium text-slate-900/90">{provider.backend}</dd>
                </div>
              </dl>
              <p className="mt-3 text-xs text-slate-500">{provider.usage}</p>
              <div className="mt-4 flex flex-wrap gap-2 text-sm">
                <button
                  className="rounded-2xl border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  type="button"
                >
                  Manage endpoints
                </button>
                <button
                  className="rounded-2xl border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  type="button"
                >
                  Rotate key
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
        <div className="grid gap-6 lg:grid-cols-2">
          <article className="rounded-2xl border border-slate-100/80 bg-white/80 p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-900/90">Standard tiers</h3>
            <p className="text-xs text-slate-500">Used for most traffic once premium routing is exhausted.</p>
            <div className="mt-4 space-y-3 text-sm text-slate-600">
              {standardTiers.map((tier) => (
                <div key={tier.id} className="rounded-xl border border-slate-100/80 bg-slate-50 px-3 py-3">
                  <div className="flex items-center justify-between text-xs uppercase tracking-wide text-slate-500">
                    <span>{tier.name}</span>
                    <span>Order #{tier.id.split('-').pop()}</span>
                  </div>
                  <ul className="mt-2 space-y-1">
                    {tier.endpoints.map((endpoint) => (
                      <li key={`${tier.id}-${endpoint.label}`} className="flex items-center justify-between font-medium text-slate-900/90">
                        <span>{endpoint.label}</span>
                        <span className="text-xs text-slate-500">{endpoint.weight}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </article>
          <article className="rounded-2xl border border-slate-100/80 bg-white/80 p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-900/90">Premium tiers</h3>
            <p className="text-xs text-slate-500">Prepended for new agents or upgraded plans before standard tiers.</p>
            <div className="mt-4 space-y-3 text-sm text-slate-600">
              {premiumTiers.map((tier) => (
                <div key={tier.id} className="rounded-xl border border-emerald-100 bg-emerald-50/70 px-3 py-3">
                  <div className="flex items-center justify-between text-xs uppercase tracking-wide text-emerald-700">
                    <span>{tier.name}</span>
                    <span>Order #{tier.id.split('-').pop()}</span>
                  </div>
                  <ul className="mt-2 space-y-1">
                    {tier.endpoints.map((endpoint) => (
                      <li key={`${tier.id}-${endpoint.label}`} className="flex items-center justify-between font-medium text-slate-900/90">
                        <span>{endpoint.label}</span>
                        <span className="text-xs text-slate-600">{endpoint.weight}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {premiumTiers.length === 0 ? <p className="text-xs text-slate-500">No premium tier configured.</p> : null}
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
          <ul className="grid gap-3 text-sm text-slate-600 md:grid-cols-2">
            <li className="rounded-2xl border border-slate-100/80 bg-white/80 p-4">
              <p className="font-semibold text-slate-900/90">Primary tasks</p>
              <p className="text-xs text-slate-500">Form filling, long-running browsing, screenshot capture.</p>
            </li>
            <li className="rounded-2xl border border-slate-100/80 bg-white/80 p-4">
              <p className="font-semibold text-slate-900/90">Fallback behavior</p>
              <p className="text-xs text-slate-500">Falls back to orchestrator config if dedicated endpoint disabled.</p>
            </li>
          </ul>
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
          {workloadSummaries.map((workload) => (
            <li key={workload.name} className="flex items-center gap-3 rounded-2xl border border-slate-100/80 bg-white/80 p-4">
              <AlertCircle className="size-5 text-blue-500" aria-hidden="true" />
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
