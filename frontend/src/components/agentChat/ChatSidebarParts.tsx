import type { CSSProperties, KeyboardEvent, MouseEvent } from 'react'
import { Check, Search, Star, X } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import type { AgentRosterEntry, AgentRosterSortMode } from '../../types/agentRoster'

type SearchVariant = 'drawer' | 'sidebar'
type SortVariant = SearchVariant

const SEARCH_VARIANTS: Record<
  SearchVariant,
  {
    containerClass: string
    iconClass: string
    inputClass: string
    clearClass: string
    placeholder: string
  }
> = {
  drawer: {
    containerClass: 'agent-drawer-search',
    iconClass: 'agent-drawer-search-icon',
    inputClass: 'agent-drawer-search-input',
    clearClass: 'agent-drawer-search-clear',
    placeholder: 'Search agents...',
  },
  sidebar: {
    containerClass: 'chat-sidebar-search',
    iconClass: 'chat-sidebar-search-icon',
    inputClass: 'chat-sidebar-search-input',
    clearClass: 'chat-sidebar-search-clear',
    placeholder: 'Search...',
  },
}

type AgentSearchInputProps = {
  variant: SearchVariant
  value: string
  onChange: (value: string) => void
  onClear: () => void
}

export function AgentSearchInput({ variant, value, onChange, onClear }: AgentSearchInputProps) {
  const styles = SEARCH_VARIANTS[variant]
  return (
    <div className={styles.containerClass}>
      <Search className={styles.iconClass} aria-hidden="true" />
      <input
        type="text"
        className={styles.inputClass}
        placeholder={styles.placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
      />
      {value ? (
        <button
          type="button"
          className={styles.clearClass}
          onClick={onClear}
          aria-label="Clear search"
        >
          <X className={variant === 'drawer' ? 'h-4 w-4' : 'h-3.5 w-3.5'} />
        </button>
      ) : null}
    </div>
  )
}

type AgentSortToggleProps = {
  variant: SortVariant
  value: AgentRosterSortMode
  onChange: (value: AgentRosterSortMode) => void
}

export function AgentSortToggle({ variant, value, onChange }: AgentSortToggleProps) {
  const containerClass = variant === 'drawer' ? 'agent-drawer-sort-toggle' : 'chat-sidebar-sort-toggle'
  const buttonClass = variant === 'drawer' ? 'agent-drawer-sort-toggle-button' : 'chat-sidebar-sort-toggle-button'
  return (
    <div className={containerClass} role="group" aria-label="Sort agents">
      <button
        type="button"
        className={buttonClass}
        data-active={value === 'recent' ? 'true' : 'false'}
        onClick={() => onChange('recent')}
      >
        Most recent
      </button>
      <button
        type="button"
        className={buttonClass}
        data-active={value === 'alphabetical' ? 'true' : 'false'}
        onClick={() => onChange('alphabetical')}
      >
        A-Z
      </button>
    </div>
  )
}

type AgentEmptyStateProps = {
  variant: 'drawer' | 'sidebar'
  hasAgents: boolean
  loading: boolean
  errorMessage?: string | null
  filteredCount: number
  searchQuery: string
}

export function AgentEmptyState({
  variant,
  hasAgents,
  loading,
  errorMessage,
  filteredCount,
  searchQuery,
}: AgentEmptyStateProps) {
  let message: string | null = null

  if (!hasAgents && loading) {
    message = 'Loading agents...'
  } else if (!hasAgents && !loading && errorMessage) {
    message = errorMessage
  } else if (!hasAgents && !loading && !errorMessage) {
    message = 'No agents yet.'
  } else if (hasAgents && filteredCount === 0 && searchQuery) {
    message = variant === 'drawer' ? `No agents match "${searchQuery}"` : 'No matches'
  }

  if (!message) return null
  const className = variant === 'drawer' ? 'agent-drawer-empty' : 'chat-sidebar-agent-empty'
  return <div className={className}>{message}</div>
}

type AgentListSectionHeaderProps = {
  variant: 'drawer' | 'sidebar'
  label: string
  count: number
}

export function AgentListSectionHeader({ variant, label, count }: AgentListSectionHeaderProps) {
  const className = variant === 'drawer' ? 'agent-drawer-section-header' : 'chat-sidebar-subsection-header'
  return (
    <div className={className}>
      <span>{label}</span>
      <span>{count}</span>
    </div>
  )
}

type AgentListItemProps = {
  agent: AgentRosterEntry
  isActive: boolean
  isSwitching: boolean
  isFavorite?: boolean
  onSelect: (agent: AgentRosterEntry) => void
  onToggleFavorite?: (agentId: string) => void
  variant: 'drawer' | 'sidebar'
  collapsed?: boolean
  showFavoriteToggle?: boolean
  accentColor?: string | null
}

const ITEM_STYLES = {
  drawer: {
    buttonClass: 'agent-drawer-item',
    avatarWrapClass: 'agent-drawer-item-avatar-wrap',
    avatarClass: 'agent-drawer-item-avatar',
    imageClass: 'agent-drawer-item-avatar-image',
    textClass: 'agent-drawer-item-avatar-text',
    metaClass: 'agent-drawer-item-meta',
    nameClass: 'agent-drawer-item-name',
    descClass: 'agent-drawer-item-desc',
    stateClass: 'agent-drawer-item-state',
  },
  sidebar: {
    buttonClass: 'chat-sidebar-agent',
    avatarWrapClass: 'chat-sidebar-agent-avatar-wrap',
    avatarClass: 'chat-sidebar-agent-avatar',
    imageClass: 'chat-sidebar-agent-avatar-image',
    textClass: 'chat-sidebar-agent-avatar-text',
    metaClass: 'chat-sidebar-agent-meta',
    nameClass: 'chat-sidebar-agent-name',
    descClass: 'chat-sidebar-agent-desc',
    stateClass: 'chat-sidebar-agent-state',
  },
}

export function AgentWorkingIndicator({ label = true }: { label?: boolean }) {
  return (
    <span className="agent-list-working" aria-label="Working">
      <span className="agent-list-working__dots" aria-hidden="true">
        <span className="agent-list-working__dot" />
        <span className="agent-list-working__dot" />
        <span className="agent-list-working__dot" />
      </span>
      {label ? <span className="agent-list-working__label">Working</span> : null}
    </span>
  )
}

export function AgentListItem({
  agent,
  isActive,
  isSwitching,
  isFavorite = false,
  onSelect,
  onToggleFavorite,
  variant,
  collapsed,
  showFavoriteToggle = true,
  accentColor,
}: AgentListItemProps) {
  const styles = ITEM_STYLES[variant]
  const accentStyle = accentColor
    ? ({ '--agent-accent': accentColor } as CSSProperties)
    : undefined
  const showMeta = variant === 'drawer' || !collapsed
  const miniDescription = (agent.miniDescription || '').trim()
  const longDescription = (agent.shortDescription || '').trim()
  const hoverDescription = longDescription && longDescription !== miniDescription ? longDescription : undefined
  const showFavoriteButton = Boolean(onToggleFavorite) && (variant === 'drawer' || !collapsed) && showFavoriteToggle
  const isWorking = Boolean(agent.processingActive)
  const hasUnread = Boolean(agent.hasUnreadAgentMessage)
  const collapsedTitle = isWorking ? `${agent.name || 'Agent'} • Working` : agent.name || 'Agent'

  const handleToggleFavorite = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
    onToggleFavorite?.(agent.id)
  }

  const handleFavoriteKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    onToggleFavorite?.(agent.id)
  }

  return (
    <button
      type="button"
      className={styles.buttonClass}
      data-active={isActive ? 'true' : 'false'}
      data-switching={isSwitching ? 'true' : 'false'}
      data-enabled={agent.isActive ? 'true' : 'false'}
      data-working={isWorking ? 'true' : 'false'}
      data-unread={hasUnread ? 'true' : 'false'}
      onClick={() => onSelect(agent)}
      title={variant === 'sidebar' && collapsed ? (hasUnread ? `${collapsedTitle} • Unread` : collapsedTitle) : undefined}
      style={accentStyle}
      role="listitem"
      aria-current={isActive ? 'page' : undefined}
    >
      <span className={variant === 'drawer' ? 'agent-drawer-item-leading' : 'chat-sidebar-agent-leading'}>
        <span className={variant === 'drawer' ? 'agent-drawer-item-unread-slot' : 'chat-sidebar-agent-unread-slot'}>
          {hasUnread ? (
            <span
              className={variant === 'drawer' ? 'agent-drawer-item-unread-dot' : 'chat-sidebar-agent-unread-dot'}
              aria-label="Unread message"
              title="Unread message"
            />
          ) : null}
        </span>
        <span className={styles.avatarWrapClass}>
          <AgentAvatarBadge
            name={agent.name || 'Agent'}
            avatarUrl={agent.avatarUrl}
            className={styles.avatarClass}
            imageClassName={styles.imageClass}
            textClassName={styles.textClass}
          />
          {variant === 'sidebar' && collapsed && isWorking ? (
            <span className="chat-sidebar-agent-working-badge" aria-hidden="true">
              <AgentWorkingIndicator label={false} />
            </span>
          ) : null}
        </span>
      </span>
      {showMeta ? (
        <span className={styles.metaClass}>
          <span className={styles.nameClass}>{agent.name || 'Agent'}</span>
          {isWorking ? (
            <span className={styles.descClass}>
              <AgentWorkingIndicator />
            </span>
          ) : miniDescription ? (
            <span className={styles.descClass} title={hoverDescription}>
              {miniDescription}
            </span>
          ) : !agent.isActive ? (
            <span className={styles.stateClass}>Paused</span>
          ) : null}
        </span>
      ) : null}
      {showFavoriteButton || (variant === 'drawer' && isActive) ? (
        <span className={variant === 'drawer' ? 'agent-drawer-item-trailing' : 'chat-sidebar-agent-trailing'}>
          {variant === 'drawer' && isActive ? (
            <Check className="agent-drawer-item-check" aria-hidden="true" />
          ) : null}
          {showFavoriteButton ? (
            <span
              className={variant === 'drawer' ? 'agent-drawer-item-favorite' : 'chat-sidebar-agent-favorite'}
              data-active={isFavorite ? 'true' : 'false'}
              onClick={handleToggleFavorite}
              onKeyDown={handleFavoriteKeyDown}
              role="button"
              tabIndex={0}
              aria-label={isFavorite ? 'Remove favorite' : 'Add favorite'}
              title={isFavorite ? 'Remove favorite' : 'Add favorite'}
            >
              <Star className={variant === 'drawer' ? 'h-4 w-4' : 'h-3.5 w-3.5'} />
            </span>
          ) : null}
        </span>
      ) : null}
    </button>
  )
}
