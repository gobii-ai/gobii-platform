import type { CSSProperties } from 'react'
import { Check, Search, X } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import type { AgentRosterEntry } from '../../types/agentRoster'

type SearchVariant = 'drawer' | 'sidebar'

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

type AgentListItemProps = {
  agent: AgentRosterEntry
  isActive: boolean
  isSwitching: boolean
  onSelect: (agent: AgentRosterEntry) => void
  variant: 'drawer' | 'sidebar'
  collapsed?: boolean
  accentColor?: string | null
}

const ITEM_STYLES = {
  drawer: {
    buttonClass: 'agent-drawer-item',
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
    avatarClass: 'chat-sidebar-agent-avatar',
    imageClass: 'chat-sidebar-agent-avatar-image',
    textClass: 'chat-sidebar-agent-avatar-text',
    metaClass: 'chat-sidebar-agent-meta',
    nameClass: 'chat-sidebar-agent-name',
    descClass: 'chat-sidebar-agent-desc',
    stateClass: 'chat-sidebar-agent-state',
  },
}

export function AgentListItem({
  agent,
  isActive,
  isSwitching,
  onSelect,
  variant,
  collapsed,
  accentColor,
}: AgentListItemProps) {
  const styles = ITEM_STYLES[variant]
  const accentStyle = accentColor
    ? ({ '--agent-accent': accentColor } as CSSProperties)
    : undefined
  const showMeta = variant === 'drawer' || !collapsed

  return (
    <button
      type="button"
      className={styles.buttonClass}
      data-active={isActive ? 'true' : 'false'}
      data-switching={isSwitching ? 'true' : 'false'}
      data-enabled={agent.isActive ? 'true' : 'false'}
      onClick={() => onSelect(agent)}
      title={variant === 'sidebar' && collapsed ? agent.name || 'Agent' : undefined}
      style={accentStyle}
      role="listitem"
      aria-current={isActive ? 'page' : undefined}
    >
      <AgentAvatarBadge
        name={agent.name || 'Agent'}
        avatarUrl={agent.avatarUrl}
        className={styles.avatarClass}
        imageClassName={styles.imageClass}
        textClassName={styles.textClass}
      />
      {showMeta ? (
        <span className={styles.metaClass}>
          <span className={styles.nameClass}>{agent.name || 'Agent'}</span>
          {agent.miniDescription ? (
            <span className={styles.descClass}>{agent.miniDescription}</span>
          ) : !agent.isActive ? (
            <span className={styles.stateClass}>Paused</span>
          ) : null}
        </span>
      ) : null}
      {variant === 'drawer' && isActive ? (
        <Check className="agent-drawer-item-check" aria-hidden="true" />
      ) : null}
    </button>
  )
}
