import { Mail, MessageSquare, Plus, Settings, Star } from 'lucide-react'

import type { AgentRosterEntry } from '../../types/agentRoster'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { AgentEmptyState, AgentListSectionHeader } from './ChatSidebarParts'
import { AgentChatIconButton, AgentChatPill, joinClassNames } from './uiPrimitives'

type ChatSidebarGalleryProps = {
  variant: 'sidebar' | 'drawer'
  agents: AgentRosterEntry[]
  favoriteAgentIds: string[]
  activeAgentId?: string | null
  switchingAgentId?: string | null
  hasAgents: boolean
  loading: boolean
  errorMessage?: string | null
  searchQuery: string
  onSelectAgent?: (agent: AgentRosterEntry) => void
  onConfigureAgent?: (agent: AgentRosterEntry) => void
  onToggleAgentFavorite?: (agentId: string) => void
  onCreateAgent?: () => void
  createAgentButtonDisabled?: boolean
  createAgentDisabledReason?: string | null
}

type GalleryCardProps = {
  agent: AgentRosterEntry
  variant: 'sidebar' | 'drawer'
  isActive: boolean
  isSwitching: boolean
  isFavorite: boolean
  onSelectAgent?: (agent: AgentRosterEntry) => void
  onConfigureAgent?: (agent: AgentRosterEntry) => void
  onToggleAgentFavorite?: (agentId: string) => void
}

function GalleryCard({
  agent,
  variant,
  isActive,
  isSwitching,
  isFavorite,
  onSelectAgent,
  onConfigureAgent,
  onToggleAgentFavorite,
}: GalleryCardProps) {
  const isSignupPreviewAgent = !agent.isCollaborator && Boolean(agent.signupPreviewState) && agent.signupPreviewState !== 'none'
  const showSmsAction = Boolean(agent.sms) && !isSignupPreviewAgent
  const showEmailAction = Boolean(agent.email) && !isSignupPreviewAgent
  const showConfigureAction = Boolean(agent.canManageAgent && (onConfigureAgent || agent.detailUrl)) && !isSignupPreviewAgent
  const miniDescription = (agent.miniDescription || '').trim()
  const pendingRequestCount = Math.max(0, agent.pendingActionRequestCount ?? 0)
  const showChatAction = Boolean(onSelectAgent)

  return (
    <article
      className="agent-gallery-card"
      data-variant={variant}
      data-active={isActive ? 'true' : 'false'}
      data-switching={isSwitching ? 'true' : 'false'}
      role="listitem"
    >
      <div className="agent-gallery-card__top-actions">
        <AgentChatIconButton
          size="sm"
          className="agent-gallery-card__favorite"
          data-active={isFavorite ? 'true' : 'false'}
          onClick={() => onToggleAgentFavorite?.(agent.id)}
          disabled={!onToggleAgentFavorite}
          aria-label={isFavorite ? 'Remove favorite' : 'Add favorite'}
          title={isFavorite ? 'Remove favorite' : 'Add favorite'}
        >
          <Star className="h-4 w-4" />
        </AgentChatIconButton>
      </div>

      <button
        type="button"
        className="agent-gallery-card__button"
        onClick={() => onSelectAgent?.(agent)}
        aria-current={isActive ? 'page' : undefined}
      >
        <div className="agent-gallery-card__hero">
          <span className="agent-gallery-card__hero-glow" aria-hidden="true" />
          <AgentAvatarBadge
            name={agent.name || 'Agent'}
            avatarUrl={agent.avatarUrl}
            className="agent-gallery-card__avatar"
            imageClassName="agent-gallery-card__avatar-image"
            textClassName="agent-gallery-card__avatar-text"
            fallbackStyle={{ background: 'linear-gradient(135deg, #6d28d9, #1e1145)' }}
          />
          <div className="agent-gallery-card__hero-meta">
            <span className="agent-gallery-card__name">{agent.name || 'Agent'}</span>
            {pendingRequestCount > 0 ? (
              <AgentChatPill className="agent-roster-pending-pill" tone="info">
                {pendingRequestCount} {pendingRequestCount === 1 ? 'request' : 'requests'}
              </AgentChatPill>
            ) : miniDescription ? (
              <span className="agent-gallery-card__mini">{miniDescription}</span>
            ) : null}
          </div>
        </div>

        {agent.displayTags.length > 0 ? (
          <div className="agent-gallery-card__tags">
            {agent.displayTags.map((tag) => (
              <AgentChatPill key={tag} className="agent-gallery-card__tag">
                {tag}
              </AgentChatPill>
            ))}
          </div>
        ) : null}
      </button>

      <div className="agent-gallery-card__footer">
        {showConfigureAction ? (
          onConfigureAgent ? (
            <button
              type="button"
              className="agent-gallery-card__primary-action"
              onClick={() => onConfigureAgent(agent)}
              disabled={isSwitching}
            >
              <Settings className="h-3.5 w-3.5" />
              <span>Configure</span>
            </button>
          ) : agent.detailUrl ? (
            <a className="agent-gallery-card__primary-action" href={agent.detailUrl}>
              <Settings className="h-3.5 w-3.5" />
              <span>Configure</span>
            </a>
          ) : null
        ) : null}
        <div className="agent-gallery-card__channel-row">
          {showEmailAction && agent.email ? (
            <a className="agent-gallery-card__channel-action" data-channel="email" href={`mailto:${agent.email}`} title={agent.email}>
              <Mail className="h-3.5 w-3.5" />
              <span>Email</span>
            </a>
          ) : null}
          {showSmsAction && agent.sms ? (
            <a className="agent-gallery-card__channel-action" data-channel="sms" href={`sms:${agent.sms}`} title={agent.sms}>
              <MessageSquare className="h-3.5 w-3.5" />
              <span>SMS</span>
            </a>
          ) : null}
          {showChatAction ? (
            <button
              type="button"
              className="agent-gallery-card__channel-action"
              data-channel="chat"
              onClick={() => onSelectAgent?.(agent)}
              disabled={isSwitching}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              <span>{isSwitching ? 'Loading…' : 'Chat'}</span>
            </button>
          ) : null}
        </div>
      </div>
    </article>
  )
}

export function ChatSidebarGallery({
  variant,
  agents,
  favoriteAgentIds,
  activeAgentId,
  switchingAgentId,
  hasAgents,
  loading,
  errorMessage,
  searchQuery,
  onSelectAgent,
  onConfigureAgent,
  onToggleAgentFavorite,
  onCreateAgent,
  createAgentButtonDisabled = false,
  createAgentDisabledReason = null,
}: ChatSidebarGalleryProps) {
  const favoriteAgentIdSet = new Set(favoriteAgentIds)
  const favoriteAgents = agents.filter((agent) => favoriteAgentIdSet.has(agent.id))
  const allAgents = agents.filter((agent) => !favoriteAgentIdSet.has(agent.id))
  const showFavoritesSection = favoriteAgents.length > 0
  const showAllSection = allAgents.length > 0 || !showFavoritesSection

  return (
    <div className="agent-gallery-scroll" data-variant={variant}>
      {onCreateAgent ? (
        <button
          type="button"
          className="agent-gallery-create"
          data-variant={variant}
          onClick={onCreateAgent}
          disabled={createAgentButtonDisabled}
          aria-disabled={createAgentButtonDisabled ? 'true' : undefined}
          title={createAgentDisabledReason ?? undefined}
        >
          <Plus className="h-4 w-4" />
          <span>New Agent</span>
        </button>
      ) : null}

      <AgentEmptyState
        variant={variant}
        hasAgents={hasAgents}
        loading={loading}
        errorMessage={errorMessage}
        filteredCount={agents.length}
        searchQuery={searchQuery}
      />

      {showFavoritesSection ? (
        <section className="agent-gallery-section" data-variant={variant}>
          <AgentListSectionHeader variant={variant} label="Favorites" count={favoriteAgents.length} />
          <div className={joinClassNames('agent-gallery-grid', variant === 'drawer' && 'agent-gallery-grid--drawer')} role="list">
            {favoriteAgents.map((agent) => (
              <GalleryCard
                key={agent.id}
                agent={agent}
                variant={variant}
                isActive={agent.id === activeAgentId}
                isSwitching={agent.id === switchingAgentId}
                isFavorite={true}
                onSelectAgent={onSelectAgent}
                onConfigureAgent={onConfigureAgent}
                onToggleAgentFavorite={onToggleAgentFavorite}
              />
            ))}
          </div>
        </section>
      ) : null}

      {showAllSection && allAgents.length > 0 ? (
        <section className="agent-gallery-section" data-variant={variant}>
          <AgentListSectionHeader
            variant={variant}
            label={showFavoritesSection ? 'All agents' : 'Agents'}
            count={allAgents.length}
          />
          <div className={joinClassNames('agent-gallery-grid', variant === 'drawer' && 'agent-gallery-grid--drawer')} role="list">
            {allAgents.map((agent) => (
              <GalleryCard
                key={agent.id}
                agent={agent}
                variant={variant}
                isActive={agent.id === activeAgentId}
                isSwitching={agent.id === switchingAgentId}
                isFavorite={false}
                onSelectAgent={onSelectAgent}
                onConfigureAgent={onConfigureAgent}
                onToggleAgentFavorite={onToggleAgentFavorite}
              />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}
