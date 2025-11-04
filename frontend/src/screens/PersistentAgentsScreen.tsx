import {type ReactElement, useEffect, useMemo, useState} from 'react'
import { createPortal } from 'react-dom'
import type { CSSProperties } from 'react'
import { ArrowRight, Ban, Copy, Mail, MessageCircle, MessageSquare, Phone, Plus, Search, Settings, Zap } from 'lucide-react'

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
  cardGradientStyle: string
  iconBackgroundHex: string
  iconBorderHex: string
  headerTextClass: string
  headerSubtextClass: string
  headerStatusClass: string
  headerBadgeClass: string
  headerIconClass: string
  headerLinkHoverClass: string
  dailyCreditRemaining: number | null
  dailyCreditLow: boolean
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
}

export type PersistentAgentsScreenProps = {
  initialData: AgentListPayload
}

type TalkToAgentTarget = {
  name: string
  email: string | null
  phone: string | null
  chatUrl: string | null
}

type NormalizedAgent = AgentSummary & {
  searchBlob: string
  gradientStyle: CSSProperties
}

export function PersistentAgentsScreen({ initialData }: PersistentAgentsScreenProps) {
  const [query, setQuery] = useState('')
  const [modalAgent, setModalAgent] = useState<TalkToAgentTarget | null>(null)

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
                onTalkToAgent={() =>
                  setModalAgent({
                    name: agent.name,
                    email: agent.primaryEmail,
                    phone: agent.primarySms,
                    chatUrl: agent.chatUrl,
                  })
                }
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

      {modalAgent && (
        <TalkToAgentModal
          target={modalAgent}
          onClose={() => setModalAgent(null)}
        />
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
  onTalkToAgent: () => void
}

function AgentCard({ agent, onTalkToAgent }: AgentCardProps) {
  const creditsRemaining = agent.dailyCreditRemaining !== null ? agent.dailyCreditRemaining.toFixed(2) : null

  return (
    <div className="gobii-card-hoverable group flex h-full flex-col">
      <div className="relative flex h-52 flex-col items-center justify-center overflow-hidden" style={agent.gradientStyle}>
        <div
          className="absolute inset-0 opacity-20"
          style={{ background: 'radial-gradient(circle at 20% 20%, rgba(255, 255, 255, 0.35), transparent 55%)' }}
        />
        <div
          className="absolute inset-0 opacity-10"
          style={{ background: 'radial-gradient(circle at 80% 0%, rgba(255, 255, 255, 0.25), transparent 60%)' }}
        />

        <div
          className="relative z-10 mb-4 flex size-16 items-center justify-center rounded-full border backdrop-blur-sm"
          style={{ backgroundColor: agent.iconBackgroundHex, borderColor: agent.iconBorderHex }}
        >
          <Zap className={`h-8 w-8 ${agent.headerIconClass}`} aria-hidden="true" />
        </div>

        <h3 className={`relative z-10 px-4 text-center text-lg font-semibold ${agent.headerTextClass}`}>{agent.name}</h3>

        {agent.primaryEmail && (
          <div className={`relative z-10 mt-1 flex items-center gap-1.5 text-xs ${agent.headerSubtextClass}`}>
            <Mail className="h-4 w-4" aria-hidden="true" />
            <a href={`mailto:${agent.primaryEmail}`} className={`font-light transition-colors ${agent.headerLinkHoverClass}`}>
              {agent.primaryEmail}
            </a>
          </div>
        )}

        <div className={`relative z-10 mt-2 flex items-center gap-2 ${agent.headerStatusClass}`}>
          <span className={`size-2 rounded-full ${agent.isActive ? 'bg-green-300' : 'bg-gray-300'}`} />
          <span className="text-xs font-medium uppercase tracking-wide">{agent.isActive ? 'Active' : 'Paused'}</span>
        </div>

        {agent.pendingTransfer && (
          <div className={`relative z-10 mt-2 inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold ${agent.headerBadgeClass}`}>
            <span className="sr-only">Transfer pending</span>
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Transfer Pending</span>
          </div>
        )}
      </div>

      <div className="flex flex-1 flex-col p-4 md:p-5">
        {agent.dailyCreditLow && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800">
            {agent.name} is almost out of daily task credits
            {creditsRemaining !== null && ` (${creditsRemaining} left)`}. Increase the daily limit on the agent detail page if you want them to keep working today.
          </div>
        )}

        <div className="flex-1">
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

        {agent.displayTags.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
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

        <div className="mt-4 pt-4">
          <div className="flex gap-2">
            <a
              href={agent.detailUrl}
              className="inline-flex flex-1 items-center justify-center gap-x-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-800 shadow-sm hover:bg-gray-50"
            >
              <Settings className="h-4 w-4" aria-hidden="true" />
              Configure
            </a>
            <button
              type="button"
              onClick={onTalkToAgent}
              className="inline-flex flex-1 items-center justify-center gap-x-2 rounded-lg border border-transparent bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
            >
              <MessageCircle className="h-4 w-4" aria-hidden="true" />
              Talk to Agent
            </button>
          </div>
        </div>
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

type TalkToAgentModalProps = {
  target: TalkToAgentTarget
  onClose: () => void
}

function TalkToAgentModal({ target, onClose }: TalkToAgentModalProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = originalOverflow
    }
  }, [onClose])

  const channel = target.phone ? 'sms' : 'email'
  const canEmail = Boolean(target.email)
  const canText = Boolean(target.phone)

  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="talk-to-agent-title">
      <div className="fixed inset-0 bg-gray-500/75 backdrop-blur-sm" aria-hidden="true" onClick={onClose} />
      <div className="flex min-h-screen items-center justify-center px-4 py-10 text-center sm:block sm:p-0">
        <span className="hidden sm:inline-block sm:h-screen sm:align-middle" aria-hidden="true">
          &#8203;
        </span>
        <div className="inline-block w-full max-w-lg transform overflow-hidden rounded-lg bg-white text-left align-middle shadow-xl transition-all">
          <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
            <h3 id="talk-to-agent-title" className="text-lg font-medium text-gray-900">
              Talk to Your Agent
            </h3>
            <button type="button" onClick={onClose} className="text-gray-400 transition hover:text-gray-500" aria-label="Close dialog">
              <svg className="h-6 w-6" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" fill="none" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l12 12M6 18L18 6" />
              </svg>
            </button>
          </div>

          <div className="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
            <div className="sm:flex sm:items-start">
              <div className="mx-auto flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full bg-indigo-100 sm:mx-0 sm:h-10 sm:w-10">
                {channel === 'email' ? <Mail className="h-6 w-6 text-indigo-600" aria-hidden="true" /> : <Phone className="h-6 w-6 text-indigo-600" aria-hidden="true" />}
              </div>
              <div className="mt-3 flex-1 text-center sm:mt-0 sm:ml-4 sm:text-left">
                {channel === 'email' && (
                  <div>
                    <p className="mb-4 text-sm text-gray-600">
                      Your agent <strong>{target.name}</strong> is ready to assist you via email. Simply send an email to communicate directly.
                    </p>
                    <ContactCard
                      label="Agent Email"
                      value={target.email ?? ''}
                      href={`mailto:${target.email ?? ''}`}
                      icon={<Mail className="h-5 w-5" aria-hidden="true" />}
                    />
                    <p className="mt-4 text-xs text-gray-500">
                      💡 <strong>Tip:</strong> Your agent responds from that address. Each agent has its own unique email.
                    </p>
                  </div>
                )}

                {channel === 'sms' && (
                  <div>
                    <p className="mb-4 text-sm text-gray-600">
                      Your agent <strong>{target.name}</strong> is ready to chat over SMS. Send a text message to the number below to start the conversation.
                    </p>
                    <ContactCard
                      label="Agent Number"
                      value={target.phone ?? ''}
                      href={`sms:${target.phone ?? ''}`}
                      icon={<Phone className="h-5 w-5" aria-hidden="true" />}
                      valueClassName="phone-number-to-format"
                    />
                    <p className="mt-4 text-xs text-gray-500">
                      💡 <strong>Tip:</strong> Text like you would with a friend—your agent understands natural language.
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-3 bg-gray-50 px-4 py-3 sm:flex sm:flex-row-reverse sm:space-y-0 sm:px-6">
            {target.chatUrl && (
              <a
                href={target.chatUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-transparent bg-indigo-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 sm:ml-3 sm:w-auto sm:text-sm"
              >
                <MessageSquare className="h-4 w-4" aria-hidden="true" />
                Open Web Chat
              </a>
            )}
            {canEmail && (
              <a
                href={`mailto:${target.email ?? ''}`}
                className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-transparent bg-indigo-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 sm:ml-3 sm:w-auto sm:text-sm"
              >
                <Mail className="h-4 w-4" aria-hidden="true" />
                Send Email
              </a>
            )}
            {canText && (
              <a
                href={`sms:${target.phone ?? ''}`}
                className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-transparent bg-indigo-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 sm:ml-3 sm:w-auto sm:text-sm"
              >
                <Phone className="h-4 w-4" aria-hidden="true" />
                Send Text
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              className="inline-flex w-full justify-center rounded-md border border-gray-300 bg-white px-4 py-2 text-base font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 sm:w-auto sm:text-sm"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

type ContactCardProps = {
  label: string
  value: string
  href: string
  icon: ReactElement
  valueClassName?: string
}

function ContactCard({ label, value, href, icon, valueClassName }: ContactCardProps) {
  const handleCopy = () => {
    if (value) {
      navigator.clipboard.writeText(value).catch(() => {
        /* no-op */
      })
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="flex-shrink-0 text-gray-400">{icon}</div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</p>
            <a href={href} className={`text-sm font-medium text-indigo-600 transition hover:text-indigo-800 ${valueClassName ?? ''}`}>
              {value}
            </a>
          </div>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-md p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
          title="Copy value"
        >
          <Copy className="h-4 w-4" aria-hidden="true" />
        </button>
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
