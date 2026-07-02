import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { Bell, BellOff, Check, Search, Settings, Star, X } from 'lucide-react'

import type { AgentRosterEntry, AgentRosterSortMode } from '../../types/agentRoster'
import { AgentChatAvatar, AgentChatPill, joinClassNames } from './uiPrimitives'

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
  isMuted?: boolean
  onSelect: (agent: AgentRosterEntry) => void
  onConfigure?: (agent: AgentRosterEntry) => void
  onToggleFavorite?: (agentId: string) => void
  onToggleMute?: (agentId: string) => void
  variant: 'drawer' | 'sidebar'
  collapsed?: boolean
  showFavoriteToggle?: boolean
}

type ContextMenuPosition = {
  x: number
  y: number
}

const CONTEXT_MENU_WIDTH = 208
const CONTEXT_MENU_HEIGHT = 104
const CONTEXT_MENU_MARGIN = 8

function clampContextMenuPosition(x: number, y: number): ContextMenuPosition {
  if (typeof window === 'undefined') {
    return { x, y }
  }
  return {
    x: Math.max(CONTEXT_MENU_MARGIN, Math.min(x, window.innerWidth - CONTEXT_MENU_WIDTH - CONTEXT_MENU_MARGIN)),
    y: Math.max(CONTEXT_MENU_MARGIN, Math.min(y, window.innerHeight - CONTEXT_MENU_HEIGHT - CONTEXT_MENU_MARGIN)),
  }
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
  isMuted = false,
  onSelect,
  onConfigure,
  onToggleFavorite,
  onToggleMute,
  variant,
  collapsed,
  showFavoriteToggle = true,
}: AgentListItemProps) {
  const rowRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const [menuPosition, setMenuPosition] = useState<ContextMenuPosition | null>(null)
  const showMeta = variant === 'drawer' || !collapsed
  const miniDescription = (agent.miniDescription || '').trim()
  const pendingRequestCount = Math.max(0, agent.pendingActionRequestCount ?? 0)
  const hasPendingRequests = pendingRequestCount > 0
  const longDescription = (agent.shortDescription || '').trim()
  const hoverDescription = longDescription && longDescription !== miniDescription ? longDescription : undefined
  const showFavoriteButton = Boolean(onToggleFavorite) && (variant === 'drawer' || !collapsed) && showFavoriteToggle
  const isWorking = Boolean(agent.processingActive)
  const hasUnread = Boolean(agent.hasUnreadAgentMessage) && !isMuted
  const showCollapsedUnreadBadge = variant === 'sidebar' && Boolean(collapsed) && hasUnread
  const showUnreadSlot = variant === 'drawer' || !collapsed
  const collapsedTitle = isWorking ? `${agent.name || 'Agent'} • Working` : agent.name || 'Agent'
  const contextMenuOpen = Boolean(menuPosition)

  const closeContextMenu = useCallback(() => setMenuPosition(null), [])

  const openContextMenu = useCallback((x: number, y: number) => {
    setMenuPosition(clampContextMenuPosition(x, y))
  }, [])

  const handleToggleFavorite = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
    onToggleFavorite?.(agent.id)
  }

  const handleFavoriteKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    onToggleFavorite?.(agent.id)
  }

  const handleContextMenu = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    openContextMenu(event.clientX, event.clientY)
  }

  const handleRowKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) {
      return
    }
    event.preventDefault()
    const rect = rowRef.current?.getBoundingClientRect()
    openContextMenu(
      rect ? rect.left + Math.min(rect.width, CONTEXT_MENU_WIDTH) / 2 : CONTEXT_MENU_MARGIN,
      rect ? rect.top + rect.height / 2 : CONTEXT_MENU_MARGIN,
    )
  }

  const handleToggleMute = () => {
    closeContextMenu()
    onToggleMute?.(agent.id)
  }

  const handleConfigure = () => {
    closeContextMenu()
    onConfigure?.(agent)
  }

  useEffect(() => {
    if (!contextMenuOpen || typeof document === 'undefined') {
      return
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Node && (menuRef.current?.contains(target) || rowRef.current?.contains(target))) {
        return
      }
      closeContextMenu()
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeContextMenu()
      }
    }

    document.addEventListener('pointerdown', handlePointerDown, true)
    document.addEventListener('keydown', handleKeyDown, true)
    window.addEventListener('resize', closeContextMenu)
    window.addEventListener('scroll', closeContextMenu, true)
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true)
      document.removeEventListener('keydown', handleKeyDown, true)
      window.removeEventListener('resize', closeContextMenu)
      window.removeEventListener('scroll', closeContextMenu, true)
    }
  }, [closeContextMenu, contextMenuOpen])

  return (
    <>
      <button
        ref={rowRef}
        type="button"
        className={joinClassNames('agent-roster-item', collapsed && variant === 'sidebar' && 'agent-roster-item--collapsed')}
        data-agent-roster-item-id={agent.id}
        data-variant={variant}
        data-active={isActive ? 'true' : 'false'}
        data-switching={isSwitching ? 'true' : 'false'}
        data-enabled={agent.isActive ? 'true' : 'false'}
        data-working={isWorking ? 'true' : 'false'}
        data-unread={hasUnread ? 'true' : 'false'}
        data-muted={isMuted ? 'true' : 'false'}
        data-collapsed={collapsed && variant === 'sidebar' ? 'true' : 'false'}
        onClick={() => onSelect(agent)}
        onContextMenu={handleContextMenu}
        onKeyDown={handleRowKeyDown}
        title={variant === 'sidebar' && collapsed ? (hasUnread ? `${collapsedTitle} • Unread` : collapsedTitle) : undefined}
        role="listitem"
        aria-current={isActive ? 'page' : undefined}
        aria-haspopup="menu"
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
            <AgentChatAvatar
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
              <AgentChatPill className="agent-roster-pending-pill" tone="info">
                {pendingRequestCount} {pendingRequestCount === 1 ? 'request' : 'requests'}
              </AgentChatPill>
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
      {menuPosition && typeof document !== 'undefined' ? createPortal(
        <div
          ref={menuRef}
          className="agent-roster-context-menu sidebar-settings__menu"
          role="menu"
          aria-label={`${agent.name || 'Agent'} actions`}
          style={{
            '--agent-roster-context-menu-x': `${menuPosition.x}px`,
            '--agent-roster-context-menu-y': `${menuPosition.y}px`,
          } as CSSProperties}
        >
          <button
            type="button"
            className="agent-roster-context-menu__item sidebar-settings__link"
            role="menuitem"
            onClick={handleToggleMute}
            disabled={!onToggleMute}
          >
            {isMuted ? (
              <Bell className="sidebar-settings__link-icon" aria-hidden="true" />
            ) : (
              <BellOff className="sidebar-settings__link-icon" aria-hidden="true" />
            )}
            <span>{isMuted ? 'Unmute' : 'Mute'}</span>
          </button>
          <button
            type="button"
            className="agent-roster-context-menu__item sidebar-settings__link"
            role="menuitem"
            onClick={handleConfigure}
            disabled={!onConfigure}
          >
            <Settings className="sidebar-settings__link-icon" aria-hidden="true" />
            <span>Settings</span>
          </button>
        </div>,
        document.body,
      ) : null}
    </>
  )
}
