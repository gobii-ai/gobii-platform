import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { ArrowRight, Ban, Check, Copy, Mail, MessageSquare, Phone, Plus, Search, Settings, Stethoscope, Zap } from 'lucide-react'
import { AgentAvatarBadge } from '../components/common/AgentAvatarBadge'
import { normalizeHexColor } from '../util/color'

declare global {
  interface Window {
    analytics?: {
      track?: (event: string, properties?: Record<string, unknown>) => void
    }
  }
}

type AgentSummary = {
  id: string
  name: string
  avatarUrl: string | null
  listingDescription: string
  listingDescriptionSource: string | null
  miniDescription: string
  miniDescriptionSource: string | null
  displayTags: string[]
  isActive: boolean
  pendingTransfer: boolean
  primaryEmail: string | null
  primarySms: string | null
  detailUrl: string
  chatUrl: string
  auditUrl: string | null
  cardGradientStyle: string
  iconBackgroundHex: string
  iconBorderHex: string
  displayColorHex: string | null
  headerTextClass: string
  headerSubtextClass: string
  headerStatusClass: string
  headerBadgeClass: string
  headerIconClass: string
  headerLinkHoverClass: string
  dailyCreditRemaining: number | null
  dailyCreditLow: boolean
  last24hCreditBurn: number | null
}

type AgentListPayload = {
  agents: AgentSummary[]
  hasAgents: boolean
  spawnAgentUrl: string
  upgradeUrl: string | null
  canSpawnAgents: boolean
  showUpgradeCta: boolean
  createFirstAgentEvent: string | null
  agentsAvailable: number
  agentsUnlimited: boolean
  isStaff: boolean
}

export type PersistentAgentsScreenProps = {
  initialData: AgentListPayload
}

type NormalizedAgent = AgentSummary & {
  searchBlob: string
  gradientStyle: CSSProperties
}

function formatCreditBurn(value: number | null): string {
  if (value == null || value <= 0 || Number.isNaN(value)) {
    return '0 credits/day'
  }
  const fractionDigits = value < 1 ? 2 : value < 10 ? 1 : 0
  return `${value.toFixed(fractionDigits)} credits/day`
}

export function PersistentAgentsScreen({ initialData }: PersistentAgentsScreenProps) {
  const [query, setQuery] = useState('')

  const normalizedAgents = useMemo<NormalizedAgent[]>(() => {
    return initialData.agents.map((agent) => ({
      ...agent,
      displayTags: agent.displayTags ?? [],
      searchBlob: buildSearchBlob(agent),
      gradientStyle: styleStringToObject(agent.cardGradientStyle),
    }))
  }, [initialData.agents])

  const hasAgents = normalizedAgents.length > 0
  const filteredAgents = useMemo(() => {
    if (!query.trim()) {
      return normalizedAgents
    }
    const needle = query.trim().toLowerCase()
    return normalizedAgents.filter((agent) => agent.searchBlob.includes(needle))
  }, [normalizedAgents, query])

  const showEmptyState = !hasAgents

  return (
    <div className="space-y-6 pb-6">
      {showEmptyState ? (
        <AgentEmptyState spawnUrl={initialData.spawnAgentUrl} analyticsEvent={initialData.createFirstAgentEvent} />
      ) : (
        <>
          <AgentListHeader
            query={query}
            onSearchChange={setQuery}
            canSpawnAgents={initialData.canSpawnAgents}
            spawnUrl={initialData.spawnAgentUrl}
            showUpgradeCta={initialData.showUpgradeCta}
            upgradeUrl={initialData.upgradeUrl}
          />

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 2xl:grid-cols-3">
            {filteredAgents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
              />
            ))}
          </div>

          {hasAgents && filteredAgents.length === 0 && (
            <div
              id="agent-search-empty"
              className="text-center py-12 bg-white rounded-xl shadow-inner border border-dashed border-gray-200"
            >
              <p className="text-sm font-semibold text-gray-700">No agents match your search yet.</p>
              <p className="text-sm text-gray-500 mt-1">Try another keyword or clear the search box.</p>
            </div>
          )}
        </>
      )}
    </div>
  )
}

type AgentListHeaderProps = {
  query: string
  onSearchChange: (value: string) => void
  canSpawnAgents: boolean
  spawnUrl: string
  showUpgradeCta: boolean
  upgradeUrl: string | null
}

function AgentListHeader({ query, onSearchChange, canSpawnAgents, spawnUrl, showUpgradeCta, upgradeUrl }: AgentListHeaderProps) {
  return (
    <div className="bg-white/80 backdrop-blur-sm shadow-xl rounded-xl overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-200/70">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-800">Agents</h1>
            <p className="text-sm text-gray-500 mt-1">Manage your always-on AI agents.</p>
          </div>
          <div className="flex w-full flex-col gap-3 sm:flex-row sm:items-center lg:w-auto">
            <div className="relative flex-1 sm:w-64 sm:flex-none">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" aria-hidden="true" />
              <input
                id="agent-search-input"
                type="search"
                placeholder="Search Agents..."
                className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent shadow-sm transition"
                autoComplete="off"
                value={query}
                onChange={(event) => onSearchChange(event.currentTarget.value)}
              />
            </div>

            {canSpawnAgents && (
              <a
                href={spawnUrl}
                className="group relative inline-flex w-full items-center justify-center rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 font-semibold text-white shadow-lg transition-all duration-300 hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:w-auto"
              >
                <span className="mr-2 h-2.5 w-2.5 animate-pulse rounded-full bg-emerald-400" />
                <span className="relative">Spawn Agent</span>
              </a>
            )}

            {!canSpawnAgents && showUpgradeCta && upgradeUrl && (
              <div className="inline-flex w-full items-center rounded-lg border border-gray-200 bg-gradient-to-r from-gray-50 to-gray-100 p-4 shadow-sm sm:w-auto">
                <div className="flex items-center space-x-3">
                  <div className="flex-shrink-0">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-200">
                      <Ban className="h-5 w-5 text-gray-400" aria-hidden="true" />
                    </div>
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-700">No agents available</p>
                    <p className="mt-1 text-xs text-gray-500">Upgrade your plan to create more agents</p>
                  </div>
                  <div className="flex-shrink-0">
                    <a
                      href={upgradeUrl}
                      className="inline-flex items-center rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors duration-200 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
                    >
                      <Zap className="mr-1 h-3 w-3" aria-hidden="true" />
                      Upgrade
                    </a>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

type AgentCardProps = {
  agent: NormalizedAgent
}

function AgentCard({ agent }: AgentCardProps) {
  const creditsRemaining = agent.dailyCreditRemaining !== null ? agent.dailyCreditRemaining.toFixed(2) : null
  const creditsBurnLast24h = formatCreditBurn(agent.last24hCreditBurn)
  const smsValue = agent.primarySms
  const emailValue = agent.primaryEmail
  const chatValue = agent.chatUrl
  const hasTags = agent.displayTags.length > 0
  const hasChannels = Boolean(smsValue || emailValue || chatValue)
  const [copiedField, setCopiedField] = useState<null | 'sms' | 'email'>(null)
  const copyResetTimeout = useRef<number | null>(null)

  const handleCopy = async (value: string, field: 'sms' | 'email') => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) {
      return
    }
    try {
      await navigator.clipboard.writeText(value)
      setCopiedField(field)
      if (copyResetTimeout.current !== null) {
        window.clearTimeout(copyResetTimeout.current)
      }
      copyResetTimeout.current = window.setTimeout(() => {
        setCopiedField(null)
      }, 1600)
    } catch (err) {
      console.error('Copy failed', err)
    }
  }

  useEffect(() => {
    return () => {
      if (copyResetTimeout.current !== null) {
        window.clearTimeout(copyResetTimeout.current)
      }
    }
  }, [])
  const accentColor = normalizeHexColor(agent.displayColorHex || agent.iconBorderHex || agent.iconBackgroundHex)
  return (
    <div className="gobii-card-hoverable group relative flex h-full flex-col">
      <div className="relative flex h-44 flex-col items-center justify-center overflow-hidden" style={agent.gradientStyle}>
        <div
          className="absolute inset-0 opacity-20"
          style={{ background: 'radial-gradient(circle at 20% 20%, rgba(255, 255, 255, 0.35), transparent 55%)' }}
        />
        <div
          className="absolute inset-0 opacity-10"
          style={{ background: 'radial-gradient(circle at 80% 0%, rgba(255, 255, 255, 0.25), transparent 60%)' }}
        />

        <AgentAvatarBadge
          name={agent.name}
          avatarUrl={agent.avatarUrl}
          className="relative z-10 mb-3 flex size-14 items-center justify-center overflow-hidden rounded-full border backdrop-blur-sm"
          imageClassName="h-full w-full object-cover"
          textClassName={`flex h-full w-full items-center justify-center text-xl font-semibold uppercase ${agent.headerIconClass} text-white`}
          style={{ backgroundColor: agent.iconBackgroundHex, borderColor: agent.iconBorderHex }}
          fallbackStyle={{ background: `linear-gradient(135deg, ${accentColor}, #0f172a)` }}
        />

        <h3 className={`relative z-10 px-4 text-center text-lg font-semibold ${agent.headerTextClass}`}>
          <a
            href={agent.detailUrl}
            className="transition duration-200 hover:drop-shadow-[0_0_10px_rgba(255,255,255,0.45)] focus-visible:underline focus-visible:outline-none"
          >
            {agent.name}
          </a>
        </h3>

        <div className={`relative z-10 mt-2 flex flex-wrap items-center gap-2 ${agent.headerStatusClass}`}>
          <span className={`size-2 rounded-full ${agent.isActive ? 'bg-green-300' : 'bg-gray-300'}`} />
          <span className="text-xs font-medium uppercase tracking-wide">{agent.isActive ? 'Active' : 'Paused'}</span>
          <span className="inline-flex items-center rounded-full border border-white/60 bg-white/90 px-3 py-1 text-xs font-semibold text-slate-700 shadow-sm backdrop-blur">
            {creditsBurnLast24h}
          </span>
        </div>

        {agent.pendingTransfer && (
          <div className={`relative z-10 mt-2 inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold ${agent.headerBadgeClass}`}>
            <span className="sr-only">Transfer pending</span>
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Transfer Pending</span>
          </div>
        )}

        <a
          href={agent.detailUrl}
          className="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full border border-white/70 bg-white/90 px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm backdrop-blur transition hover:bg-white"
        >
          <Settings className="h-4 w-4" aria-hidden="true" />
          Configure
        </a>

        {agent.auditUrl ? (
          <a
            href={agent.auditUrl}
            className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-full border border-amber-200/80 bg-amber-50/90 px-3 py-1.5 text-xs font-semibold text-amber-800 shadow-sm backdrop-blur transition hover:bg-amber-100 hover:border-amber-200"
            title="View agent audit"
          >
            <Stethoscope className="h-4 w-4" aria-hidden="true" />
            Audit
          </a>
        ) : null}
      </div>

      <div className="flex flex-1 flex-col p-2.5 md:p-3">
        {agent.dailyCreditLow && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800">
            {agent.name} is almost out of daily task credits
            {creditsRemaining !== null && ` (${creditsRemaining} left)`}. Increase the daily limit on the agent detail page if you want them to keep working today.
          </div>
        )}

        <div className={hasTags ? '' : 'flex-1'}>
          {agent.miniDescription && agent.miniDescriptionSource !== 'placeholder' ? (
            <p className="text-sm font-semibold text-gray-600">{agent.miniDescription}</p>
          ) : agent.listingDescriptionSource === 'placeholder' ? (
            <p className="text-sm italic text-gray-400">{agent.listingDescription}</p>
          ) : (
            <p
              className="text-sm text-gray-600"
              style={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
            >
              {agent.listingDescription}
            </p>
          )}
        </div>

        {hasTags && (
          <div className="mt-4 flex flex-1 flex-wrap content-start gap-2">
            {agent.displayTags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center rounded-full border border-indigo-100 bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        {hasChannels && (
          <div className="mt-4 pt-4">
            <div className="flex flex-wrap items-stretch gap-2">
              {smsValue && (
                <div className="inline-flex min-w-[7.5rem] flex-1 items-stretch overflow-hidden rounded-lg border border-emerald-600/80">
                  <a
                    href={`sms:${smsValue}`}
                    className="inline-flex flex-1 items-center justify-center gap-x-2 bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-emerald-700"
                  >
                    <Phone className="h-4 w-4" aria-hidden="true" />
                    SMS
                  </a>
                  <button
                    type="button"
                    onClick={() => handleCopy(smsValue, 'sms')}
                    data-copied={copiedField === 'sms'}
                    title={copiedField === 'sms' ? 'Copied!' : 'Copy SMS number'}
                    aria-label="Copy SMS number"
                    className="group relative inline-flex w-8 flex-none shrink-0 items-center justify-center border-l border-emerald-500/70 bg-emerald-600 px-2.5 py-2 text-white/80 transition hover:bg-emerald-700 hover:text-white data-[copied=true]:bg-emerald-700 data-[copied=true]:text-white"
                  >
                    {copiedField === 'sms' ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
                    <span className="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 rounded-md bg-slate-900 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-white opacity-0 transition group-data-[copied=true]:opacity-100">
                      Copied
                    </span>
                  </button>
                </div>
              )}
              {emailValue && (
                <div className="inline-flex min-w-[7.5rem] flex-1 items-stretch overflow-hidden rounded-lg border border-sky-600/80">
                  <a
                    href={`mailto:${emailValue}`}
                    className="inline-flex flex-1 items-center justify-center gap-x-2 bg-sky-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-sky-700"
                  >
                    <Mail className="h-4 w-4" aria-hidden="true" />
                    Email
                  </a>
                  <button
                    type="button"
                    onClick={() => handleCopy(emailValue, 'email')}
                    data-copied={copiedField === 'email'}
                    title={copiedField === 'email' ? 'Copied!' : 'Copy email address'}
                    aria-label="Copy email address"
                    className="group relative inline-flex w-8 flex-none shrink-0 items-center justify-center border-l border-sky-500/70 bg-sky-600 px-2.5 py-2 text-white/80 transition hover:bg-sky-700 hover:text-white data-[copied=true]:bg-sky-700 data-[copied=true]:text-white"
                  >
                    {copiedField === 'email' ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
                    <span className="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 rounded-md bg-slate-900 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-white opacity-0 transition group-data-[copied=true]:opacity-100">
                      Copied
                    </span>
                  </button>
                </div>
              )}
              {chatValue && (
                <a
                  href={chatValue}
                  data-immersive-link
                  className="inline-flex min-w-[6.5rem] flex-1 items-center justify-center gap-x-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700"
                >
                  <MessageSquare className="h-4 w-4" aria-hidden="true" />
                  Chat
                </a>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

type AgentEmptyStateProps = {
  spawnUrl: string
  analyticsEvent: string | null
}

function AgentEmptyState({ spawnUrl, analyticsEvent }: AgentEmptyStateProps) {
  const handleClick = () => {
    if (analyticsEvent && typeof window !== 'undefined') {
      window.analytics?.track?.(analyticsEvent)
    }
  }

  return (
    <div className="bg-white/80 backdrop-blur-sm shadow-xl rounded-xl overflow-hidden">
      <div className="mx-auto flex min-h-96 w-full max-w-md flex-col items-center justify-center px-6 py-16">
        <div className="mb-8 flex size-20 items-center justify-center rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-xl">
          <Zap className="size-8" aria-hidden="true" />
        </div>
        <h2 className="mb-3 text-center text-xl font-semibold text-gray-800">No always-on agents yet</h2>
        <p className="mb-6 text-center text-sm text-gray-600 leading-relaxed">
          Create your first AI agent that works 24/7. Agents can automate tasks, monitor changes, send notifications, and much more while you focus on what matters.
        </p>
        <div className="flex flex-col gap-3">
          <a
            href={spawnUrl}
            onClick={handleClick}
            className="group inline-flex items-center justify-center gap-x-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 font-semibold text-white shadow-lg transition-all duration-300 hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
            Create Your First Agent
          </a>
        </div>
      </div>
    </div>
  )
}

function buildSearchBlob(agent: AgentSummary): string {
  const tags = agent.displayTags?.join(' ') ?? ''
  return [agent.name ?? '', agent.listingDescription ?? '', tags].join(' ').toLowerCase()
}

function styleStringToObject(styleString: string): CSSProperties {
  if (!styleString) {
    return {}
  }

  return styleString
    .split(';')
    .map((rule) => rule.trim())
    .filter(Boolean)
    .reduce<CSSProperties | Record<string, string>>((acc, rule) => {
      const [property, value] = rule.split(':')
      if (!property || !value) {
        return acc
      }
      const camelProperty = property.trim().replace(/-([a-z])/g, (_, char) => char.toUpperCase())
      acc[camelProperty as keyof CSSProperties] = value.trim()
      return acc
    }, {})
}
