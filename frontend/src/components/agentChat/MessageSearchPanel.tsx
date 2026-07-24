import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { File, History, Image, Loader2, Paperclip, Search, Trash2, UserRoundSearch } from 'lucide-react'

import {
  fetchAgentMessageSearch,
  type AgentMessageSearchFilters,
  type MessageSearchExcerptSegment,
  type MessageAttachmentFilter,
} from '../../api/agentMessageSearch'
import type { ConsoleContext } from '../../api/context'
import type { AgentRosterEntry } from '../../types/agentRoster'
import { buildAgentSearchBlob } from '../../util/agentCards'
import { AgentSearchInput } from './ChatSidebarParts'
import { AgentChatAvatar } from './uiPrimitives'

type SearchHistoryEntry = AgentMessageSearchFilters & {
  agentName: string | null
  displayQuery?: string
}

type MessageSearchPanelProps = {
  agents: AgentRosterEntry[]
  context: ConsoleContext | null
  viewerKey: string | number | null
  agentsLoading?: boolean
  query: string
  onQueryChange: (query: string) => void
  submitted: AgentMessageSearchFilters | null
  onSubmittedChange: (filters: AgentMessageSearchFilters | null) => void
  onAgentSelect?: (agent: AgentRosterEntry) => void
  onResultSelect?: () => void
}

type ParsedSearch = AgentMessageSearchFilters & {
  selectedAgent: AgentRosterEntry | null
}

type ActiveShortcut = {
  kind: 'has' | 'in'
  fragment: string
  start: number
} | null

const HISTORY_LIMIT = 10
const AGENT_RESULT_LIMIT = 8
const SHORTCUT_LIST_ID = 'message-search-shortcut-list'
const ATTACHMENT_OPTIONS: {
  value: Exclude<MessageAttachmentFilter, 'any'>
  label: string
  token: string
  icon: typeof Paperclip
}[] = [
  { value: 'attachment', label: 'Any attachment', token: 'attachment', icon: Paperclip },
  { value: 'image', label: 'Image', token: 'image', icon: Image },
  { value: 'file', label: 'Other file', token: 'file', icon: File },
]

function historyStorageKey(viewerKey: string | number | null, context: ConsoleContext | null): string | null {
  if (!viewerKey || !context) return null
  return `gobii:message-search-history:v1:${viewerKey}:${context.type}:${context.id}`
}

function readHistory(key: string | null): SearchHistoryEntry[] {
  if (!key || typeof window === 'undefined') return []
  try {
    const raw = JSON.parse(window.localStorage.getItem(key) ?? '[]')
    if (!Array.isArray(raw)) return []
    return raw.filter((entry): entry is SearchHistoryEntry => (
      entry
      && typeof entry === 'object'
      && typeof entry.q === 'string'
      && (entry.agentId === null || typeof entry.agentId === 'string')
      && (entry.displayQuery === undefined || typeof entry.displayQuery === 'string')
      && (
        entry.attachment === 'any'
        || ATTACHMENT_OPTIONS.some((option) => option.value === entry.attachment)
      )
    )).slice(0, HISTORY_LIMIT)
  } catch {
    return []
  }
}

function writeHistory(key: string | null, history: SearchHistoryEntry[]): void {
  if (!key || typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, JSON.stringify(history.slice(0, HISTORY_LIMIT)))
  } catch {
    // Search remains usable when browser storage is disabled or full.
  }
}

function parseSearchQuery(value: string, agents: AgentRosterEntry[]): ParsedSearch {
  let searchableText = value
  let attachment: MessageAttachmentFilter = 'any'
  let selectedAgent: AgentRosterEntry | null = null

  searchableText = searchableText.replace(
    /\bhas:\s*(attachment|attachments|image|file)\b/gi,
    (_match, token: string) => {
      attachment = token.toLowerCase() === 'image'
        ? 'image'
        : token.toLowerCase() === 'file'
          ? 'file'
          : 'attachment'
      return ' '
    },
  )

  const quotedAgentPattern = /\bin:\s*(?:"([^"]+)"|'([^']+)')/i
  const quotedAgentMatch = quotedAgentPattern.exec(searchableText)
  if (quotedAgentMatch) {
    const requestedName = (quotedAgentMatch[1] || quotedAgentMatch[2] || '').trim().toLocaleLowerCase()
    selectedAgent = agents.find((agent) => agent.name.trim().toLocaleLowerCase() === requestedName) ?? null
    if (selectedAgent) {
      searchableText = searchableText.replace(quotedAgentMatch[0], ' ')
    }
  }

  if (!selectedAgent) {
    const operatorMatch = /\bin:\s*/i.exec(searchableText)
    if (operatorMatch) {
      const remainder = searchableText.slice(operatorMatch.index + operatorMatch[0].length)
      selectedAgent = [...agents]
        .sort((left, right) => right.name.length - left.name.length)
        .find((agent) => remainder.toLocaleLowerCase().startsWith(agent.name.trim().toLocaleLowerCase())) ?? null
      if (selectedAgent) {
        const end = operatorMatch.index + operatorMatch[0].length + selectedAgent.name.length
        searchableText = `${searchableText.slice(0, operatorMatch.index)} ${searchableText.slice(end)}`
      }
    }
  }

  searchableText = searchableText
    .replace(/\b(?:has|in):\s*$/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  return {
    q: searchableText,
    agentId: selectedAgent?.id ?? null,
    attachment,
    selectedAgent,
  }
}

function activeShortcutFor(query: string): ActiveShortcut {
  const matches = [...query.matchAll(/(^|\s)(has|in):\s*/gi)]
  const match = matches.at(-1)
  if (!match || match.index === undefined) return null
  const kind = match[2].toLowerCase() as 'has' | 'in'
  const fragment = query.slice(match.index + match[0].length)
  if (
    (kind === 'has' && /^(?:attachment|attachments|image|file)\s+/i.test(fragment))
    || (kind === 'in' && /^(?:"[^"]+"|'[^']+')\s+/.test(fragment))
  ) {
    return null
  }
  return {
    kind,
    fragment: fragment.toLocaleLowerCase(),
    start: match.index + match[1].length,
  }
}

function replaceActiveShortcut(query: string, shortcut: ActiveShortcut, replacement: string): string {
  if (!shortcut) return query
  return `${query.slice(0, shortcut.start)}${replacement}`
}

function appendOperator(query: string, operator: 'has' | 'in'): string {
  const prefix = query.trim()
  return `${prefix ? `${prefix} ` : ''}${operator}:`
}

function fallbackHistoryLabel(entry: SearchHistoryEntry): string {
  const parts: string[] = []
  if (entry.q.trim()) parts.push(entry.q.trim())
  if (entry.agentName) parts.push(`in:"${entry.agentName}"`)
  if (entry.attachment !== 'any') parts.push(`has:${entry.attachment}`)
  return parts.join(' ') || 'Search'
}

function resultUrl(agentId: string, messageId: string): string {
  const base = `/app/agents/${agentId}`
  if (typeof window === 'undefined') return `${base}?message=${messageId}`
  const params = new URLSearchParams(window.location.search)
  params.set('message', messageId)
  return `${base}?${params.toString()}${window.location.hash}`
}

function navigateToResult(event: MouseEvent<HTMLElement>, href: string): boolean {
  if (
    event.defaultPrevented
    || event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
    || typeof window === 'undefined'
  ) {
    return false
  }
  if (event.target instanceof Element && event.target.closest('a[href]')) {
    return false
  }
  event.preventDefault()
  window.history.pushState({ messageSearch: true }, '', href)
  window.dispatchEvent(new PopStateEvent('popstate'))
  return true
}

function revealVisibleMessage(messageId: string): void {
  if (typeof document === 'undefined') return
  window.requestAnimationFrame(() => {
    const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
      ? CSS.escape(messageId)
      : messageId.replace(/["\\]/g, '\\$&')
    const target = document.querySelector<HTMLElement>(`[data-message-id="${escaped}"]`)
    if (!target) return
    const reducedMotion = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    target.scrollIntoView({ block: 'start', behavior: reducedMotion ? 'auto' : 'smooth' })
    target.classList.remove('message-search-target')
    window.requestAnimationFrame(() => target.classList.add('message-search-target'))
    window.setTimeout(() => target.classList.remove('message-search-target'), 2200)
  })
}

function SearchExcerpt({ segments }: { segments: MessageSearchExcerptSegment[] }) {
  return (
    <p>
      {segments.map((segment, index) => (
        segment.highlighted
          ? <mark key={index}>{segment.text}</mark>
          : <span key={index}>{segment.text}</span>
      ))}
    </p>
  )
}

export function MessageSearchPanel({
  agents,
  context,
  viewerKey,
  agentsLoading = false,
  query,
  onQueryChange,
  submitted,
  onSubmittedChange,
  onAgentSelect,
  onResultSelect,
}: MessageSearchPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [highlightedShortcutIndex, setHighlightedShortcutIndex] = useState(0)
  const storageKey = historyStorageKey(viewerKey, context)
  const [history, setHistory] = useState<SearchHistoryEntry[]>(() => readHistory(storageKey))
  const availableAgentIds = useMemo(() => new Set(agents.map((agent) => agent.id)), [agents])
  const availableHistory = useMemo(
    () => history.filter((entry) => entry.agentId === null || availableAgentIds.has(entry.agentId)),
    [availableAgentIds, history],
  )
  const parsedSearch = useMemo(() => parseSearchQuery(query, agents), [agents, query])
  const activeShortcut = useMemo(() => activeShortcutFor(query), [query])
  const matchingAgents = useMemo(() => {
    if (parsedSearch.selectedAgent) return [parsedSearch.selectedAgent]
    const agentQuery = parsedSearch.q.trim().toLocaleLowerCase()
    if (!agentQuery) return []
    const terms = agentQuery.split(/\s+/).filter(Boolean)
    return agents
      .filter((agent) => {
        const searchBlob = buildAgentSearchBlob(agent)
        return terms.every((term) => searchBlob.includes(term))
      })
      .slice(0, AGENT_RESULT_LIMIT)
  }, [agents, parsedSearch.q, parsedSearch.selectedAgent])
  const shortcutAgents = useMemo(() => {
    if (activeShortcut?.kind !== 'in') return []
    return agents
      .filter((agent) => (
        !activeShortcut.fragment
        || buildAgentSearchBlob(agent).includes(activeShortcut.fragment)
      ))
      .slice(0, AGENT_RESULT_LIMIT)
  }, [activeShortcut, agents])
  const shortcutAttachments = useMemo(() => {
    if (activeShortcut?.kind !== 'has') return []
    return ATTACHMENT_OPTIONS.filter((option) => (
      !activeShortcut.fragment
      || option.token.startsWith(activeShortcut.fragment)
      || option.label.toLocaleLowerCase().includes(activeShortcut.fragment)
    ))
  }, [activeShortcut])
  const shortcutCount = activeShortcut?.kind === 'in'
    ? shortcutAgents.length
    : shortcutAttachments.length
  const resolvedShortcutIndex = shortcutCount > 0
    ? Math.min(highlightedShortcutIndex, shortcutCount - 1)
    : 0

  useEffect(() => {
    if (!agentsLoading) writeHistory(storageKey, availableHistory)
  }, [agentsLoading, availableHistory, storageKey])

  const searchQuery = useInfiniteQuery({
    queryKey: ['agent-message-search', context?.type, context?.id, submitted],
    queryFn: ({ pageParam, signal }) => fetchAgentMessageSearch(submitted!, {
      cursor: pageParam,
      signal,
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: Boolean(submitted),
    staleTime: 30_000,
  })
  const results = useMemo(
    () => searchQuery.data?.pages.flatMap((page) => page.results) ?? [],
    [searchQuery.data],
  )
  const canSearch = Boolean(parsedSearch.q || parsedSearch.agentId) || parsedSearch.attachment !== 'any'

  const runSearch = useCallback((displayQuery: string) => {
    const parsed = parseSearchQuery(displayQuery, agents)
    const filters: AgentMessageSearchFilters = {
      q: parsed.q,
      agentId: parsed.agentId,
      attachment: parsed.attachment,
    }
    if (!filters.q && !filters.agentId && filters.attachment === 'any') return
    onQueryChange(displayQuery)
    onSubmittedChange(filters)
    const entry: SearchHistoryEntry = {
      ...filters,
      displayQuery: displayQuery.trim(),
      agentName: parsed.selectedAgent?.name ?? null,
    }
    const nextHistory = [
      entry,
      ...availableHistory.filter((item) => (
        item.q !== entry.q
        || item.agentId !== entry.agentId
        || item.attachment !== entry.attachment
      )),
    ].slice(0, HISTORY_LIMIT)
    setHistory(nextHistory)
    writeHistory(storageKey, nextHistory)
  }, [agents, availableHistory, onQueryChange, onSubmittedChange, storageKey])

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    runSearch(query)
  }

  const handleQueryChange = (nextQuery: string) => {
    onQueryChange(nextQuery)
    onSubmittedChange(null)
    setHighlightedShortcutIndex(0)
  }

  const selectAttachmentShortcut = (token: string) => {
    const nextQuery = replaceActiveShortcut(query, activeShortcut, `has:${token} `)
    handleQueryChange(nextQuery)
    if (!parseSearchQuery(nextQuery, agents).q) runSearch(nextQuery)
    inputRef.current?.focus()
  }

  const selectAgentShortcut = (agent: AgentRosterEntry) => {
    const nextQuery = replaceActiveShortcut(query, activeShortcut, `in:"${agent.name}" `)
    handleQueryChange(nextQuery)
    if (!parseSearchQuery(nextQuery, agents).q) runSearch(nextQuery)
    inputRef.current?.focus()
  }

  const beginShortcut = (kind: 'has' | 'in') => {
    handleQueryChange(appendOperator(query, kind))
    window.requestAnimationFrame(() => inputRef.current?.focus())
  }

  const selectHighlightedShortcut = () => {
    if (activeShortcut?.kind === 'in') {
      const agent = shortcutAgents[resolvedShortcutIndex]
      if (agent) selectAgentShortcut(agent)
      return
    }
    const attachmentOption = shortcutAttachments[resolvedShortcutIndex]
    if (attachmentOption) selectAttachmentShortcut(attachmentOption.token)
  }

  const handleSearchInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!activeShortcut || shortcutCount === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlightedShortcutIndex((index) => (index + 1) % shortcutCount)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlightedShortcutIndex((index) => (index - 1 + shortcutCount) % shortcutCount)
      return
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault()
      selectHighlightedShortcut()
    }
  }

  const clearHistory = () => {
    setHistory([])
    writeHistory(storageKey, [])
  }

  const showInitialSuggestions = !query.trim() && !submitted
  const showShortcutSuggestions = Boolean(activeShortcut)

  return (
    <section className="message-search-panel" aria-label="Search agents and messages">
      <div className="message-search-panel__heading">
        <div>
          <h2>Search</h2>
          <p>Find an agent first, or search messages across this workspace.</p>
        </div>
      </div>

      <form className="message-search-panel__form" onSubmit={handleSubmit}>
        <AgentSearchInput
          ref={inputRef}
          variant="sidebar"
          value={query}
          onChange={handleQueryChange}
          onClear={() => {
            onQueryChange('')
            onSubmittedChange(null)
          }}
          placeholder="Search agents and messages…"
          autoFocus
          onKeyDown={handleSearchInputKeyDown}
          ariaControls={showShortcutSuggestions ? SHORTCUT_LIST_ID : undefined}
          ariaExpanded={showShortcutSuggestions}
          ariaActiveDescendant={
            showShortcutSuggestions && shortcutCount > 0
              ? `message-search-shortcut-${resolvedShortcutIndex}`
              : undefined
          }
        />
        <button type="submit" className="message-search-panel__submit" disabled={!canSearch || searchQuery.isFetching}>
          {searchQuery.isFetching && !searchQuery.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Search messages
        </button>
      </form>

      <div className="message-search-panel__results">
        {showInitialSuggestions ? (
          <div className="message-search-shortcuts">
            <div className="message-search-panel__section-title"><span>Filters</span></div>
            <button type="button" className="message-search-shortcut" onClick={() => beginShortcut('in')}>
              <UserRoundSearch className="h-4 w-4" />
              <span><strong>In a specific agent</strong><small>in: agent name</small></span>
            </button>
            <button type="button" className="message-search-shortcut" onClick={() => beginShortcut('has')}>
              <Paperclip className="h-4 w-4" />
              <span><strong>Includes an attachment</strong><small>has: image or file</small></span>
            </button>
          </div>
        ) : null}

        {showShortcutSuggestions ? (
          <div className="message-search-shortcuts" id={SHORTCUT_LIST_ID} role="listbox">
            <div className="message-search-panel__section-title">
              <span>{activeShortcut?.kind === 'in' ? 'Agents' : 'Message contains'}</span>
            </div>
            {activeShortcut?.kind === 'in' ? shortcutAgents.map((agent) => (
              <button
                type="button"
                className="message-search-shortcut"
                id={`message-search-shortcut-${shortcutAgents.indexOf(agent)}`}
                key={agent.id}
                role="option"
                aria-selected={shortcutAgents.indexOf(agent) === resolvedShortcutIndex}
                data-highlighted={shortcutAgents.indexOf(agent) === resolvedShortcutIndex ? 'true' : 'false'}
                onMouseEnter={() => setHighlightedShortcutIndex(shortcutAgents.indexOf(agent))}
                onClick={() => selectAgentShortcut(agent)}
              >
                <AgentChatAvatar
                  name={agent.name}
                  avatarUrl={agent.avatarUrl}
                  className="message-search-shortcut__avatar"
                  imageClassName="message-search-shortcut__avatar-image"
                  textClassName="message-search-shortcut__avatar-text"
                />
                <span><strong>{agent.name}</strong><small>{agent.miniDescription || agent.shortDescription || 'Agent'}</small></span>
              </button>
            )) : shortcutAttachments.map((option) => {
              const Icon = option.icon
              return (
                <button
                  type="button"
                  className="message-search-shortcut"
                  id={`message-search-shortcut-${shortcutAttachments.indexOf(option)}`}
                  key={option.value}
                  role="option"
                  aria-selected={shortcutAttachments.indexOf(option) === resolvedShortcutIndex}
                  data-highlighted={shortcutAttachments.indexOf(option) === resolvedShortcutIndex ? 'true' : 'false'}
                  onMouseEnter={() => setHighlightedShortcutIndex(shortcutAttachments.indexOf(option))}
                  onClick={() => selectAttachmentShortcut(option.token)}
                >
                  <Icon className="h-4 w-4" />
                  <span><strong>{option.label}</strong><small>has:{option.token}</small></span>
                </button>
              )
            })}
          </div>
        ) : null}

        {!showShortcutSuggestions && matchingAgents.length ? (
          <div className="message-search-agent-results">
            <div className="message-search-panel__section-title"><span>Agents</span></div>
            {matchingAgents.map((agent) => (
              <button
                type="button"
                className="message-search-agent-result"
                key={agent.id}
                onClick={() => onAgentSelect?.(agent)}
              >
                <AgentChatAvatar
                  name={agent.name}
                  avatarUrl={agent.avatarUrl}
                  className="message-search-agent-result__avatar"
                  imageClassName="message-search-agent-result__avatar-image"
                  textClassName="message-search-agent-result__avatar-text"
                />
                <span>
                  <strong>{agent.name}</strong>
                  <small>{agent.miniDescription || agent.shortDescription || 'Open conversation'}</small>
                </span>
              </button>
            ))}
          </div>
        ) : null}

        {!submitted && !showShortcutSuggestions ? (
          <div className="message-search-panel__history">
            <div className="message-search-panel__section-title">
              <span><History className="h-3.5 w-3.5" /> Recent searches</span>
              {availableHistory.length ? (
                <button type="button" onClick={clearHistory}><Trash2 className="h-3.5 w-3.5" /> Clear</button>
              ) : null}
            </div>
            {availableHistory.length ? availableHistory.map((entry, index) => (
              <button
                type="button"
                className="message-search-panel__history-item"
                key={`${entry.q}:${entry.agentId}:${entry.attachment}:${index}`}
                onClick={() => runSearch(entry.displayQuery || fallbackHistoryLabel(entry))}
              >
                <Search className="h-4 w-4" />
                <span>{entry.displayQuery || fallbackHistoryLabel(entry)}</span>
              </button>
            )) : !showInitialSuggestions ? (
              <p className="message-search-panel__empty">Press Enter to search messages.</p>
            ) : null}
          </div>
        ) : null}

        {submitted && !showShortcutSuggestions ? (
          <>
            <div className="message-search-panel__section-title">
              <span>Messages</span>
              <button type="button" onClick={() => onSubmittedChange(null)}>Search history</button>
            </div>
            {searchQuery.isLoading ? (
              <div className="message-search-panel__empty"><Loader2 className="h-5 w-5 animate-spin" /> Searching…</div>
            ) : searchQuery.isError ? (
              <p className="message-search-panel__empty">Message search is unavailable. Try again.</p>
            ) : results.length ? results.map((result) => (
              <article
                className="message-search-result settings-card-surface settings-card-surface--embedded"
                key={result.message_id}
                role="link"
                tabIndex={0}
                aria-label={`Open message from ${result.agent.name}`}
                onClick={(event) => {
                  if (navigateToResult(event, resultUrl(result.agent.id, result.message_id))) {
                    revealVisibleMessage(result.message_id)
                    onResultSelect?.()
                  }
                }}
                onKeyDown={(event: KeyboardEvent<HTMLElement>) => {
                  if (event.key !== 'Enter' || typeof window === 'undefined') return
                  event.preventDefault()
                  const href = resultUrl(result.agent.id, result.message_id)
                  window.history.pushState({ messageSearch: true }, '', href)
                  window.dispatchEvent(new PopStateEvent('popstate'))
                  revealVisibleMessage(result.message_id)
                  onResultSelect?.()
                }}
              >
                <AgentChatAvatar
                  name={result.agent.name}
                  avatarUrl={result.agent.avatar_url}
                  className="message-search-result__avatar"
                  imageClassName="message-search-result__avatar-image"
                  textClassName="message-search-result__avatar-text"
                />
                <div className="message-search-result__content">
                  <div className="message-search-result__meta">
                    <strong>{result.agent.name}</strong>
                    <time dateTime={result.timestamp}>{new Date(result.timestamp).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</time>
                    {result.attachment_count ? (
                      <span title={result.has_images ? 'Includes image' : 'Includes file'}>
                        {result.has_images ? <Image className="h-3.5 w-3.5" /> : result.attachment_count > 1 ? <Paperclip className="h-3.5 w-3.5" /> : <File className="h-3.5 w-3.5" />}
                        {result.attachment_count}
                      </span>
                    ) : null}
                  </div>
                  <div className="message-search-result__excerpt">
                    <SearchExcerpt segments={result.excerpt} />
                  </div>
                </div>
              </article>
            )) : <p className="message-search-panel__empty">No matching messages.</p>}
            {searchQuery.hasNextPage ? (
              <button
                type="button"
                className="message-search-panel__load-more"
                disabled={searchQuery.isFetchingNextPage}
                onClick={() => void searchQuery.fetchNextPage()}
              >
                {searchQuery.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Load more
              </button>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  )
}
