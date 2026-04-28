import { Mail, MessageSquare, Settings, Star, Stethoscope } from 'lucide-react'

import type { AgentRosterEntry } from '../../types/agentRoster'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { AgentEmptyState, AgentListSectionHeader } from './ChatSidebarParts'

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
  onToggleAgentFavorite?: (agentId: string) => void
  onCreateAgent?: () => void
  createAgentButtonDisabled?: boolean
  createAgentDisabledReason?: string | null
}

const SURFACE_STYLES = {
  sidebar: {
    scrollClass: 'chat-sidebar-gallery-scroll',
    createClass: 'chat-sidebar-gallery-create',
    sectionClass: 'chat-sidebar-gallery-section',
    gridClass: 'chat-sidebar-gallery-grid',
    cardClass: 'chat-sidebar-gallery-card',
    heroClass: 'chat-sidebar-gallery-card-hero',
    heroMetaClass: 'chat-sidebar-gallery-card-hero-meta',
    favoriteClass: 'chat-sidebar-gallery-card-favorite',
    topActionsClass: 'chat-sidebar-gallery-card-top-actions',
    iconActionClass: 'chat-sidebar-gallery-card-icon-action',
    bodyButtonClass: 'chat-sidebar-gallery-card-button',
    avatarClass: 'chat-sidebar-gallery-card-avatar',
    avatarImageClass: 'chat-sidebar-gallery-card-avatar-image',
    avatarTextClass: 'chat-sidebar-gallery-card-avatar-text',
    nameClass: 'chat-sidebar-gallery-card-name',
    miniClass: 'chat-sidebar-gallery-card-mini',
    tagsClass: 'chat-sidebar-gallery-card-tags',
    footerClass: 'chat-sidebar-gallery-card-footer',
    primaryActionClass: 'chat-sidebar-gallery-card-primary-action',
    channelRowClass: 'chat-sidebar-gallery-card-channel-row',
    channelActionClass: 'chat-sidebar-gallery-card-channel-action',
    channelEmailClass: 'chat-sidebar-gallery-card-channel-action chat-sidebar-gallery-card-channel-action--email',
    channelSmsClass: 'chat-sidebar-gallery-card-channel-action chat-sidebar-gallery-card-channel-action--sms',
    channelChatClass: 'chat-sidebar-gallery-card-channel-action chat-sidebar-gallery-card-channel-action--chat',
  },
  drawer: {
    scrollClass: 'agent-drawer-gallery-scroll',
    createClass: 'agent-drawer-gallery-create',
    sectionClass: 'agent-drawer-gallery-section',
    gridClass: 'agent-drawer-gallery-grid',
    cardClass: 'agent-drawer-gallery-card',
    heroClass: 'agent-drawer-gallery-card-hero',
    heroMetaClass: 'agent-drawer-gallery-card-hero-meta',
    favoriteClass: 'agent-drawer-gallery-card-favorite',
    topActionsClass: 'agent-drawer-gallery-card-top-actions',
    iconActionClass: 'agent-drawer-gallery-card-icon-action',
    bodyButtonClass: 'agent-drawer-gallery-card-button',
    avatarClass: 'agent-drawer-gallery-card-avatar',
    avatarImageClass: 'agent-drawer-gallery-card-avatar-image',
    avatarTextClass: 'agent-drawer-gallery-card-avatar-text',
    nameClass: 'agent-drawer-gallery-card-name',
    miniClass: 'agent-drawer-gallery-card-mini',
    tagsClass: 'agent-drawer-gallery-card-tags',
    footerClass: 'agent-drawer-gallery-card-footer',
    primaryActionClass: 'agent-drawer-gallery-card-primary-action',
    channelRowClass: 'agent-drawer-gallery-card-channel-row',
    channelActionClass: 'agent-drawer-gallery-card-channel-action',
    channelEmailClass: 'agent-drawer-gallery-card-channel-action agent-drawer-gallery-card-channel-action--email',
    channelSmsClass: 'agent-drawer-gallery-card-channel-action agent-drawer-gallery-card-channel-action--sms',
    channelChatClass: 'agent-drawer-gallery-card-channel-action agent-drawer-gallery-card-channel-action--chat',
  },
} as const

type GalleryCardProps = {
  agent: AgentRosterEntry
  variant: 'sidebar' | 'drawer'
  isActive: boolean
  isSwitching: boolean
  isFavorite: boolean
  onSelectAgent?: (agent: AgentRosterEntry) => void
  onToggleAgentFavorite?: (agentId: string) => void
}

function GalleryCard({
  agent,
  variant,
  isActive,
  isSwitching,
  isFavorite,
  onSelectAgent,
  onToggleAgentFavorite,
}: GalleryCardProps) {
  const styles = SURFACE_STYLES[variant]
  const isSignupPreviewAgent = !agent.isCollaborator && Boolean(agent.signupPreviewState) && agent.signupPreviewState !== 'none'
  const showSmsAction = Boolean(agent.sms) && !isSignupPreviewAgent
  const showEmailAction = Boolean(agent.email) && !isSignupPreviewAgent
  const showConfigureAction = Boolean(agent.canManageAgent && agent.detailUrl) && !isSignupPreviewAgent
  const showAuditAction = Boolean(agent.auditUrl) && !isSignupPreviewAgent
  const miniDescription = (agent.miniDescription || '').trim()
  const showChatAction = Boolean(onSelectAgent)

  return (
    <article
      className={styles.cardClass}
      data-active={isActive ? 'true' : 'false'}
      data-switching={isSwitching ? 'true' : 'false'}
      role="listitem"
    >
      <div className={styles.topActionsClass}>
        {showAuditAction && agent.auditUrl ? (
          <a
            className={styles.iconActionClass}
            href={agent.auditUrl}
            target="_blank"
            rel="noreferrer"
            title="Audit"
            aria-label="Audit"
          >
            <Stethoscope className="h-3.5 w-3.5" />
          </a>
        ) : null}
        <button
          type="button"
          className={styles.favoriteClass}
          data-active={isFavorite ? 'true' : 'false'}
          onClick={() => onToggleAgentFavorite?.(agent.id)}
          disabled={!onToggleAgentFavorite}
          aria-label={isFavorite ? 'Remove favorite' : 'Add favorite'}
          title={isFavorite ? 'Remove favorite' : 'Add favorite'}
        >
          <Star className="h-4 w-4" />
        </button>
      </div>

      <button
        type="button"
        className={styles.bodyButtonClass}
        onClick={() => onSelectAgent?.(agent)}
        aria-current={isActive ? 'page' : undefined}
      >
        <div className={styles.heroClass}>
          <span className="chat-sidebar-gallery-card-hero-glow" aria-hidden="true" />
          <AgentAvatarBadge
            name={agent.name || 'Agent'}
            avatarUrl={agent.avatarUrl}
            className={styles.avatarClass}
            imageClassName={styles.avatarImageClass}
            textClassName={styles.avatarTextClass}
            fallbackStyle={{ background: 'linear-gradient(135deg, #6d28d9, #1e1145)' }}
          />
          <div className={styles.heroMetaClass}>
            <span className={styles.nameClass}>{agent.name || 'Agent'}</span>
            {miniDescription ? (
              <span className={styles.miniClass}>{miniDescription}</span>
            ) : null}
          </div>
        </div>

        {agent.displayTags.length > 0 ? (
          <div className={styles.tagsClass}>
            {agent.displayTags.map((tag) => (
              <span key={tag} className="chat-sidebar-gallery-card-tag">
                {tag}
              </span>
            ))}
          </div>
        ) : null}
      </button>

      <div className={styles.footerClass}>
        {showConfigureAction && agent.detailUrl ? (
          <a className={styles.primaryActionClass} href={agent.detailUrl}>
            <Settings className="h-3.5 w-3.5" />
            <span>Configure</span>
          </a>
        ) : null}
        <div className={styles.channelRowClass}>
          {showEmailAction && agent.email ? (
            <a className={styles.channelEmailClass} href={`mailto:${agent.email}`} title={agent.email}>
              <Mail className="h-3.5 w-3.5" />
              <span>Email</span>
            </a>
          ) : null}
          {showSmsAction && agent.sms ? (
            <a className={styles.channelSmsClass} href={`sms:${agent.sms}`} title={agent.sms}>
              <MessageSquare className="h-3.5 w-3.5" />
              <span>SMS</span>
            </a>
          ) : null}
          {showChatAction ? (
          <button
            type="button"
            className={styles.channelChatClass}
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
  onToggleAgentFavorite,
  onCreateAgent,
  createAgentButtonDisabled = false,
  createAgentDisabledReason = null,
}: ChatSidebarGalleryProps) {
  const styles = SURFACE_STYLES[variant]
  const favoriteAgentIdSet = new Set(favoriteAgentIds)
  const favoriteAgents = agents.filter((agent) => favoriteAgentIdSet.has(agent.id))
  const allAgents = agents.filter((agent) => !favoriteAgentIdSet.has(agent.id))
  const showFavoritesSection = favoriteAgents.length > 0
  const showAllSection = allAgents.length > 0 || !showFavoritesSection

  return (
    <div className={styles.scrollClass}>
      {onCreateAgent ? (
        <button
          type="button"
          className={styles.createClass}
          onClick={onCreateAgent}
          disabled={createAgentButtonDisabled}
          aria-disabled={createAgentButtonDisabled ? 'true' : undefined}
          title={createAgentDisabledReason ?? undefined}
        >
          Create Agent
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
        <section className={styles.sectionClass}>
          <AgentListSectionHeader variant={variant} label="Favorites" count={favoriteAgents.length} />
          <div className={styles.gridClass} role="list">
            {favoriteAgents.map((agent) => (
              <GalleryCard
                key={agent.id}
                agent={agent}
                variant={variant}
                isActive={agent.id === activeAgentId}
                isSwitching={agent.id === switchingAgentId}
                isFavorite={true}
                onSelectAgent={onSelectAgent}
                onToggleAgentFavorite={onToggleAgentFavorite}
              />
            ))}
          </div>
        </section>
      ) : null}

      {showAllSection && allAgents.length > 0 ? (
        <section className={styles.sectionClass}>
          <AgentListSectionHeader
            variant={variant}
            label={showFavoritesSection ? 'All agents' : 'Agents'}
            count={allAgents.length}
          />
          <div className={styles.gridClass} role="list">
            {allAgents.map((agent) => (
              <GalleryCard
                key={agent.id}
                agent={agent}
                variant={variant}
                isActive={agent.id === activeAgentId}
                isSwitching={agent.id === switchingAgentId}
                isFavorite={false}
                onSelectAgent={onSelectAgent}
                onToggleAgentFavorite={onToggleAgentFavorite}
              />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}
