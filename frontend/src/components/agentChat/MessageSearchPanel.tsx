import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type FormEvent, type KeyboardEvent, type SetStateAction } from 'react'
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
import { handleAppAnchorClick } from '../../util/appNavigation'
import { buildAgentSearchBlob } from '../../util/agentCards'
import { revealTimelineMessage } from '../../util/timelineNavigation'
import { AgentSearchInput } from './ChatSidebarParts'
import { AgentChatAvatar } from './uiPrimitives'

export type MessageSearchState = {
  open: boolean
  query: string
  submittedQuery: string | null
}

type MessageSearchPanelProps = {
  agents: AgentRosterEntry[]
  context: ConsoleContext | null
  viewerKey: string | number | null
  agentsLoading?: boolean
  state: MessageSearchState
  onStateChange: Dispatch<SetStateAction<MessageSearchState>>
  onAgentSelect?: (agent: AgentRosterEntry) => void
  onResultSelect?: () => void
}

type ParsedSearch = AgentMessageSearchFilters & {
  agentFilterPresent: boolean
  selectedAgent: AgentRosterEntry | null
}

type ActiveShortcut = {
  kind: 'has' | 'agent'
  fragment: string
  start: number
} | null

const HISTORY_LIMIT = 10
const AGENT_RESULT_LIMIT = 8
const SHORTCUT_LIST_ID = 'message-search-shortcut-list'
const ATTACHMENT_OPTIONS: {
  value: Exclude<MessageAttachmentFilter, 'any'>
  label: string
  icon: typeof Paperclip
}[] = [
  { value: 'attachment', label: 'Any attachment', icon: Paperclip },
  { value: 'image', label: 'Image', icon: Image },
  { value: 'file', label: 'Other file', icon: File },
]

function historyStorageKey(viewerKey: string | number | null, context: ConsoleContext | null): string | null {
  if (!viewerKey || !context) return null
  return `gobii:message-search-history:v2:${viewerKey}:${context.type}:${context.id}`
}

function readHistory(key: string | null): string[] {
  if (!key || typeof window === 'undefined') return []
  try {
    const raw = JSON.parse(window.localStorage.getItem(key) ?? '[]')
    if (!Array.isArray(raw)) return []
    return raw.filter((entry): entry is string => typeof entry === 'string').slice(0, HISTORY_LIMIT)
  } catch {
    return []
  }
}

function writeHistory(key: string | null, history: string[]): void {
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

  const operatorMatch = /\bagent:\s*/i.exec(searchableText)
  const agentFilterPresent = Boolean(operatorMatch)
  let selectedAgent: AgentRosterEntry | null = null
  if (operatorMatch) {
    const valueStart = operatorMatch.index + operatorMatch[0].length
    const remainder = searchableText.slice(valueStart)
    const quoted = /^(?:"([^"]+)"|'([^']+)')/.exec(remainder)
    const requestedName = quoted
      ? (quoted[1] || quoted[2] || '').trim().toLocaleLowerCase()
      : null
    selectedAgent = requestedName
      ? agents.find((agent) => agent.name.trim().toLocaleLowerCase() === requestedName) ?? null
      : [...agents]
          .sort((left, right) => right.name.length - left.name.length)
          .find((agent) => remainder.toLocaleLowerCase().startsWith(agent.name.trim().toLocaleLowerCase())) ?? null
    if (selectedAgent) {
      const consumedLength = quoted?.[0].length ?? selectedAgent.name.length
      searchableText = `${searchableText.slice(0, operatorMatch.index)} ${searchableText.slice(valueStart + consumedLength)}`
    }
  }

  searchableText = searchableText
    .replace(/\b(?:has|agent):\s*$/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  return {
    q: searchableText,
    agentId: selectedAgent?.id ?? null,
    agentFilterPresent,
    attachment,
    selectedAgent,
  }
}

function searchFilters(parsed: ParsedSearch): AgentMessageSearchFilters {
  return {
    q: parsed.q,
    agentId: parsed.agentId,
    attachment: parsed.attachment,
  }
}

function sameFilters(left: AgentMessageSearchFilters, right: AgentMessageSearchFilters): boolean {
  return left.q === right.q
    && left.agentId === right.agentId
    && left.attachment === right.attachment
}

function activeShortcutFor(query: string): ActiveShortcut {
  const matches = [...query.matchAll(/(^|\s)(has|agent):\s*/gi)]
  const match = matches.at(-1)
  if (!match || match.index === undefined) return null
  const kind = match[2].toLowerCase() as 'has' | 'agent'
  const fragment = query.slice(match.index + match[0].length)
  if (
    (kind === 'has' && /^(?:attachment|attachments|image|file)(?:\s+|$)/i.test(fragment))
    || (kind === 'agent' && /^(?:"[^"]+"|'[^']+')(?:\s+|$)/.test(fragment))
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

function appendOperator(query: string, operator: 'has' | 'agent'): string {
  const prefix = query.trim()
  return `${prefix ? `${prefix} ` : ''}${operator}:`
}

function resultUrl(agentId: string, messageId: string): string {
  const base = `/app/agents/${agentId}`
  if (typeof window === 'undefined') return `${base}?message=${messageId}`
  const params = new URLSearchParams(window.location.search)
  params.set('message', messageId)
  return `${base}?${params.toString()}${window.location.hash}`
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
  state,
  onStateChange,
  onAgentSelect,
  onResultSelect,
}: MessageSearchPanelProps) {
  const { query, submittedQuery } = state
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [highlightedShortcutIndex, setHighlightedShortcutIndex] = useState(0)
  const storageKey = historyStorageKey(viewerKey, context)
  const [history, setHistory] = useState<string[]>(() => readHistory(storageKey))
  const availableHistory = useMemo(
    () => history.filter((entry) => {
      const parsed = parseSearchQuery(entry, agents)
      return !parsed.agentFilterPresent || Boolean(parsed.selectedAgent)
    }),
    [agents, history],
  )
  const parsedSearch = useMemo(() => parseSearchQuery(query, agents), [agents, query])
  const submitted = useMemo(
    () => submittedQuery ? searchFilters(parseSearchQuery(submittedQuery, agents)) : null,
    [agents, submittedQuery],
  )
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
    if (activeShortcut?.kind !== 'agent') return []
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
      || option.value.startsWith(activeShortcut.fragment)
      || option.label.toLocaleLowerCase().includes(activeShortcut.fragment)
    ))
  }, [activeShortcut])
  const shortcutCount = activeShortcut?.kind === 'agent'
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
  const canSearch = (
    (!parsedSearch.agentFilterPresent || Boolean(parsedSearch.agentId))
    && (Boolean(parsedSearch.q || parsedSearch.agentId) || parsedSearch.attachment !== 'any')
  )

  const runSearch = useCallback((displayQuery: string) => {
    const parsed = parseSearchQuery(displayQuery, agents)
    if (parsed.agentFilterPresent && !parsed.agentId) return
    const filters = searchFilters(parsed)
    if (!filters.q && !filters.agentId && filters.attachment === 'any') return
    const entry = displayQuery.trim()
    onStateChange((current) => ({ ...current, query: displayQuery, submittedQuery: entry }))
    const nextHistory = [
      entry,
      ...availableHistory.filter((item) => (
        !sameFilters(searchFilters(parseSearchQuery(item, agents)), filters)
      )),
    ].slice(0, HISTORY_LIMIT)
    setHistory(nextHistory)
    writeHistory(storageKey, nextHistory)
  }, [agents, availableHistory, onStateChange, storageKey])

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    runSearch(query)
  }

  const handleQueryChange = (nextQuery: string) => {
    onStateChange((current) => ({ ...current, query: nextQuery, submittedQuery: null }))
    setHighlightedShortcutIndex(0)
  }

  const selectAttachmentShortcut = (token: string) => {
    const nextQuery = replaceActiveShortcut(query, activeShortcut, `has:${token} `)
    handleQueryChange(nextQuery)
    if (!parseSearchQuery(nextQuery, agents).q) runSearch(nextQuery)
    inputRef.current?.focus()
  }

  const selectAgentShortcut = (agent: AgentRosterEntry) => {
    const nextQuery = replaceActiveShortcut(query, activeShortcut, `agent:"${agent.name}" `)
    handleQueryChange(nextQuery)
    if (!parseSearchQuery(nextQuery, agents).q) runSearch(nextQuery)
    inputRef.current?.focus()
  }

  const beginShortcut = (kind: 'has' | 'agent') => {
    handleQueryChange(appendOperator(query, kind))
    window.requestAnimationFrame(() => inputRef.current?.focus())
  }

  const selectHighlightedShortcut = () => {
    if (activeShortcut?.kind === 'agent') {
      const agent = shortcutAgents[resolvedShortcutIndex]
      if (agent) selectAgentShortcut(agent)
      return
    }
    const attachmentOption = shortcutAttachments[resolvedShortcutIndex]
    if (attachmentOption) selectAttachmentShortcut(attachmentOption.value)
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
    <section className="message-search-panel flex min-h-0 flex-1 flex-col" aria-label="Search agents and messages">
      <div className="message-search-panel__heading">
        <div>
          <h2>Search</h2>
          <p>Find an agent first, or search messages across this workspace.</p>
        </div>
      </div>

      <form className="message-search-panel__form flex flex-col" onSubmit={handleSubmit}>
        <AgentSearchInput
          ref={inputRef}
          variant="sidebar"
          value={query}
          onChange={handleQueryChange}
          onClear={() => handleQueryChange('')}
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
        <button type="submit" className="message-search-panel__submit inline-flex items-center justify-center" disabled={!canSearch || searchQuery.isFetching}>
          {searchQuery.isFetching && !searchQuery.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Search messages
        </button>
      </form>

      <div className="message-search-panel__results flex min-h-0 flex-1 flex-col overflow-y-auto">
        {showInitialSuggestions ? (
          <div className="message-search-shortcuts flex flex-col">
            <div className="message-search-panel__section-title"><span>Filters</span></div>
            <button type="button" className="message-search-shortcut flex w-full items-center text-left" onClick={() => beginShortcut('agent')}>
              <UserRoundSearch className="h-4 w-4" />
              <span><strong>In a specific agent</strong><small>agent: agent name</small></span>
            </button>
            <button type="button" className="message-search-shortcut flex w-full items-center text-left" onClick={() => beginShortcut('has')}>
              <Paperclip className="h-4 w-4" />
              <span><strong>Includes an attachment</strong><small>has: image or file</small></span>
            </button>
          </div>
        ) : null}

        {showShortcutSuggestions ? (
          <div className="message-search-shortcuts flex flex-col" id={SHORTCUT_LIST_ID} role="listbox">
            <div className="message-search-panel__section-title">
              <span>{activeShortcut?.kind === 'agent' ? 'Agents' : 'Message contains'}</span>
            </div>
            {activeShortcut?.kind === 'agent' ? shortcutAgents.map((agent) => (
              <button
                type="button"
                className="message-search-shortcut flex w-full items-center text-left"
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
                  className="message-search-avatar"
                  imageClassName="message-search-avatar__image"
                  textClassName="message-search-avatar__text"
                />
                <span><strong>{agent.name}</strong><small>{agent.miniDescription || agent.shortDescription || 'Agent'}</small></span>
              </button>
            )) : shortcutAttachments.map((option) => {
              const Icon = option.icon
              return (
                <button
                  type="button"
                  className="message-search-shortcut flex w-full items-center text-left"
                  id={`message-search-shortcut-${shortcutAttachments.indexOf(option)}`}
                  key={option.value}
                  role="option"
                  aria-selected={shortcutAttachments.indexOf(option) === resolvedShortcutIndex}
                  data-highlighted={shortcutAttachments.indexOf(option) === resolvedShortcutIndex ? 'true' : 'false'}
                  onMouseEnter={() => setHighlightedShortcutIndex(shortcutAttachments.indexOf(option))}
                  onClick={() => selectAttachmentShortcut(option.value)}
                >
                  <Icon className="h-4 w-4" />
                  <span><strong>{option.label}</strong><small>has:{option.value}</small></span>
                </button>
              )
            })}
          </div>
        ) : null}

        {!showShortcutSuggestions && matchingAgents.length ? (
          <div className="message-search-agent-results flex flex-col">
            <div className="message-search-panel__section-title"><span>Agents</span></div>
            {matchingAgents.map((agent) => (
              <button
                type="button"
                className="message-search-agent-result settings-card-surface settings-card-surface--embedded flex w-full items-center text-left"
                key={agent.id}
                onClick={() => onAgentSelect?.(agent)}
              >
                <AgentChatAvatar
                  name={agent.name}
                  avatarUrl={agent.avatarUrl}
                  className="message-search-avatar"
                  imageClassName="message-search-avatar__image"
                  textClassName="message-search-avatar__text"
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
          <div className="message-search-panel__history flex flex-col">
            <div className="message-search-panel__section-title">
              <span><History className="h-3.5 w-3.5" /> Recent searches</span>
              {availableHistory.length ? (
                <button type="button" onClick={clearHistory}><Trash2 className="h-3.5 w-3.5" /> Clear</button>
              ) : null}
            </div>
            {availableHistory.length ? availableHistory.map((entry) => (
              <button
                type="button"
                className="message-search-panel__history-item flex w-full items-center text-left"
                key={entry}
                onClick={() => runSearch(entry)}
              >
                <Search className="h-4 w-4" />
                <span>{entry}</span>
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
              <button
                type="button"
                onClick={() => onStateChange((current) => ({ ...current, submittedQuery: null }))}
              >
                Search history
              </button>
            </div>
            {searchQuery.isLoading ? (
              <div className="message-search-panel__empty"><Loader2 className="h-5 w-5 animate-spin" /> Searching…</div>
            ) : searchQuery.isError ? (
              <p className="message-search-panel__empty">Message search is unavailable. Try again.</p>
            ) : results.length ? results.map((result) => {
              const href = resultUrl(result.agent.id, result.message_id)
              return (
                <a
                  className="message-search-result settings-card-surface settings-card-surface--embedded flex items-start"
                  key={result.message_id}
                  href={href}
                  aria-label={`Open message from ${result.agent.name}`}
                  onClick={(event) => {
                    if (handleAppAnchorClick(event, href)) {
                      window.requestAnimationFrame(() => {
                        revealTimelineMessage(result.message_id, { highlight: true })
                      })
                      onResultSelect?.()
                    }
                  }}
                >
                  <AgentChatAvatar
                    name={result.agent.name}
                    avatarUrl={result.agent.avatar_url}
                    className="message-search-avatar"
                    imageClassName="message-search-avatar__image"
                    textClassName="message-search-avatar__text"
                  />
                  <div className="message-search-result__content flex min-w-0 flex-1 flex-col">
                    <div className="message-search-result__meta flex min-w-0 items-center">
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
                </a>
              )
            }) : <p className="message-search-panel__empty">No matching messages.</p>}
            {searchQuery.hasNextPage ? (
              <button
                type="button"
                className="message-search-panel__load-more inline-flex w-full items-center justify-center"
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
