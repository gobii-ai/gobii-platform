import { Mail, MessageSquare, Settings, Star } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import type { AgentRosterEntry } from '../../types/agentRoster'
import { AgentEmotionIndicator } from '../common/AgentEmotionIndicator'
import { AgentCreateSplitButton, type TeamTemplateCreateMenu } from './AgentCreateSplitButton'
import { AgentEmptyState, AgentListSectionHeader } from './ChatSidebarParts'
import { AgentChatAvatar, AgentChatIconButton, AgentChatPill, joinClassNames } from './uiPrimitives'
import { VirtualizedRosterSurface, type VirtualRosterRow } from './VirtualizedRosterSurface'

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
  createAgentDisabled?: boolean
  createAgentButtonDisabled?: boolean
  createAgentDisabledReason?: string | null
  teamTemplateMenu?: TeamTemplateCreateMenu | null
  enabled?: boolean
  scrollToAgentId?: string | null
  onScrolledToAgent?: (agentId: string) => void
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
      data-agent-roster-item-id={agent.id}
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
          <AgentChatAvatar
            name={agent.name || 'Agent'}
            avatarUrl={agent.avatarUrl}
            className="agent-gallery-card__avatar"
            imageClassName="agent-gallery-card__avatar-image"
            textClassName="agent-gallery-card__avatar-text"
            loading="lazy"
            decoding="async"
          />
          <div className="agent-gallery-card__hero-meta">
            <span className="agent-name-emotion-row">
              <span className="agent-gallery-card__name">{agent.name || 'Agent'}</span>
              <AgentEmotionIndicator
                name={agent.name || 'Agent'}
                emotion={agent.emotion}
                emotionExpiresAt={agent.emotionExpiresAt}
                className="agent-gallery-card__emotion"
              />
            </span>
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
  createAgentDisabled = false,
  createAgentButtonDisabled = false,
  createAgentDisabledReason = null,
  teamTemplateMenu = null,
  enabled = true,
  scrollToAgentId,
  onScrolledToAgent,
}: ChatSidebarGalleryProps) {
  const [viewportWidth, setViewportWidth] = useState(0)
  const handleViewportWidthChange = useCallback((width: number) => {
    setViewportWidth((current) => current === width ? current : width)
  }, [])
  const columnCount = viewportWidth > 0
    ? variant === 'drawer' && viewportWidth < 560
      ? 1
      : Math.max(1, Math.floor((viewportWidth + 12) / (232 + 12)))
    : 1
  const rows = useMemo<VirtualRosterRow[]>(() => {
    const favoriteAgentIdSet = new Set(favoriteAgentIds)
    const favoriteAgents = agents.filter((agent) => favoriteAgentIdSet.has(agent.id))
    const allAgents = agents.filter((agent) => !favoriteAgentIdSet.has(agent.id))
    const showFavoritesSection = favoriteAgents.length > 0
    const showAllSection = allAgents.length > 0 || !showFavoritesSection
    const nextRows: VirtualRosterRow[] = []

    if (onCreateAgent) {
      nextRows.push({
        key: 'gallery:create',
        content: (
          <AgentCreateSplitButton
            variant="gallery"
            onCreateAgent={onCreateAgent}
            createAgentDisabled={createAgentDisabled}
            createAgentButtonDisabled={createAgentButtonDisabled}
            createAgentDisabledReason={createAgentDisabledReason}
            menu={teamTemplateMenu ?? null}
          />
        ),
      })
    }

    nextRows.push({
      key: 'gallery:empty',
      content: (
        <AgentEmptyState
          variant={variant}
          hasAgents={hasAgents}
          loading={loading}
          errorMessage={errorMessage}
          filteredCount={agents.length}
          searchQuery={searchQuery}
        />
      ),
    })

    const pushSection = (key: string, label: string, sectionAgents: AgentRosterEntry[], favorite: boolean) => {
      if (!sectionAgents.length) return
      nextRows.push({
        key: `gallery:${key}:header`,
        content: <AgentListSectionHeader variant={variant} label={label} count={sectionAgents.length} />,
      })
      for (let index = 0; index < sectionAgents.length; index += columnCount) {
        const rowAgents = sectionAgents.slice(index, index + columnCount)
        nextRows.push({
          key: `gallery:${key}:${rowAgents.map((agent) => agent.id).join(':')}`,
          agentIds: rowAgents.map((agent) => agent.id),
          content: (
            <div
              className={joinClassNames('agent-gallery-grid', variant === 'drawer' && 'agent-gallery-grid--drawer')}
              style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}
              role="list"
            >
              {rowAgents.map((agent) => (
                <GalleryCard
                  key={agent.id}
                  agent={agent}
                  variant={variant}
                  isActive={agent.id === activeAgentId}
                  isSwitching={agent.id === switchingAgentId}
                  isFavorite={favorite}
                  onSelectAgent={onSelectAgent}
                  onConfigureAgent={onConfigureAgent}
                  onToggleAgentFavorite={onToggleAgentFavorite}
                />
              ))}
            </div>
          ),
        })
      }
    }
    if (showFavoritesSection) {
      pushSection('favorites', 'Favorites', favoriteAgents, true)
    }
    if (showAllSection) {
      pushSection('all', showFavoritesSection ? 'All agents' : 'Agents', allAgents, false)
    }
    return nextRows
  }, [activeAgentId, agents, columnCount, createAgentButtonDisabled, createAgentDisabled, createAgentDisabledReason, errorMessage, favoriteAgentIds, hasAgents, loading, onConfigureAgent, onCreateAgent, onSelectAgent, onToggleAgentFavorite, searchQuery, switchingAgentId, teamTemplateMenu, variant])

  return (
    <VirtualizedRosterSurface
      rows={rows}
      className="agent-gallery-scroll"
      estimateSize={260}
      gap={14}
      overscan={3}
      variant={variant}
      enabled={enabled}
      scrollToAgentId={scrollToAgentId}
      onScrolledToAgent={onScrolledToAgent}
      onViewportWidthChange={handleViewportWidthChange}
    />
  )
}
