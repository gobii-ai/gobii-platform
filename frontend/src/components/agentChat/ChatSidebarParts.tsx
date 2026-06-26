import type { CSSProperties, KeyboardEvent, MouseEvent } from 'react'
import { Check, Search, Star, X } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import type { AgentRosterEntry, AgentRosterSortMode } from '../../types/agentRoster'
import { joinClassNames } from './uiPrimitives'

type SearchVariant = 'drawer' | 'sidebar'
type SortVariant = SearchVariant

type AgentSearchInputProps = {
  variant: SearchVariant
  value: string
  onChange: (value: string) => void
  onClear: () => void
}

export function AgentSearchInput({ variant, value, onChange, onClear }: AgentSearchInputProps) {
  return (
    <div className="agent-roster-search" data-variant={variant}>
      <Search className="agent-roster-search__icon" aria-hidden="true" />
      <input
        type="text"
        className="agent-roster-search__input"
        placeholder={variant === 'drawer' ? 'Search agents...' : 'Search...'}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
      />
      {value ? (
        <button
          type="button"
          className="agent-roster-search__clear"
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
  return (
    <div className="agent-roster-sort" data-variant={variant} role="group" aria-label="Sort agents">
      <button
        type="button"
        className="agent-roster-sort__button"
        data-active={value === 'recent' ? 'true' : 'false'}
        onClick={() => onChange('recent')}
      >
        Most recent
      </button>
      <button
        type="button"
        className="agent-roster-sort__button"
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
  return (
    <div className="agent-roster-empty" data-variant={variant}>
      {message}
    </div>
  )
}

type AgentListSectionHeaderProps = {
  variant: 'drawer' | 'sidebar'
  label: string
  count: number
}

export function AgentListSectionHeader({ variant, label, count }: AgentListSectionHeaderProps) {
  return (
    <div className="agent-roster-section-header" data-variant={variant}>
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
  const accentStyle = accentColor
    ? ({ '--agent-accent': accentColor } as CSSProperties)
    : undefined
  const showMeta = variant === 'drawer' || !collapsed
  const miniDescription = (agent.miniDescription || '').trim()
  const pendingRequestCount = Math.max(0, agent.pendingActionRequestCount ?? 0)
  const hasPendingRequests = pendingRequestCount > 0
  const longDescription = (agent.shortDescription || '').trim()
  const hoverDescription = longDescription && longDescription !== miniDescription ? longDescription : undefined
  const showFavoriteButton = Boolean(onToggleFavorite) && (variant === 'drawer' || !collapsed) && showFavoriteToggle
  const isWorking = Boolean(agent.processingActive)
  const hasUnread = Boolean(agent.hasUnreadAgentMessage)
  const showCollapsedUnreadBadge = variant === 'sidebar' && Boolean(collapsed) && hasUnread
  const showUnreadSlot = variant === 'drawer' || !collapsed
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
      className={joinClassNames('agent-roster-item', collapsed && variant === 'sidebar' && 'agent-roster-item--collapsed')}
      data-variant={variant}
      data-active={isActive ? 'true' : 'false'}
      data-switching={isSwitching ? 'true' : 'false'}
      data-enabled={agent.isActive ? 'true' : 'false'}
      data-working={isWorking ? 'true' : 'false'}
      data-unread={hasUnread ? 'true' : 'false'}
      data-collapsed={collapsed && variant === 'sidebar' ? 'true' : 'false'}
      onClick={() => onSelect(agent)}
      title={variant === 'sidebar' && collapsed ? (hasUnread ? `${collapsedTitle} • Unread` : collapsedTitle) : undefined}
      style={accentStyle}
      role="listitem"
      aria-current={isActive ? 'page' : undefined}
    >
      <span className="agent-roster-item__leading">
        {showUnreadSlot ? (
          <span className="agent-roster-item__unread-slot">
            {hasUnread ? (
              <span
                className="agent-roster-item__unread-dot"
                aria-label="Unread message"
                title="Unread message"
              />
            ) : null}
          </span>
        ) : null}
        <span className="agent-roster-item__avatar-wrap">
          <AgentAvatarBadge
            name={agent.name || 'Agent'}
            avatarUrl={agent.avatarUrl}
            className="agent-roster-item__avatar"
            imageClassName="agent-roster-item__avatar-image"
            textClassName="agent-roster-item__avatar-text"
          />
          {showCollapsedUnreadBadge ? (
            <span
              className="agent-roster-item__unread-badge"
              aria-label="Unread message"
              title="Unread message"
            />
          ) : null}
          {variant === 'sidebar' && collapsed && isWorking ? (
            <span className="agent-roster-item__working-badge" aria-hidden="true">
              <AgentWorkingIndicator label={false} />
            </span>
          ) : null}
        </span>
      </span>
      {showMeta ? (
        <span className="agent-roster-item__meta">
          <span className="agent-roster-item__name">{agent.name || 'Agent'}</span>
          {hasPendingRequests ? (
            <span className="agent-roster-pending-pill">
              {pendingRequestCount} {pendingRequestCount === 1 ? 'request' : 'requests'}
            </span>
          ) : isWorking ? (
            <span className="agent-roster-item__desc">
              <AgentWorkingIndicator />
            </span>
          ) : miniDescription ? (
            <span className="agent-roster-item__desc" title={hoverDescription}>
              {miniDescription}
            </span>
          ) : !agent.isActive ? (
            <span className="agent-roster-item__state">Paused</span>
          ) : null}
        </span>
      ) : null}
      {showFavoriteButton || (variant === 'drawer' && isActive) ? (
        <span className="agent-roster-item__trailing">
          {variant === 'drawer' && isActive ? (
            <Check className="agent-roster-item__check" aria-hidden="true" />
          ) : null}
          {showFavoriteButton ? (
            <span
              className="agent-roster-item__favorite"
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
