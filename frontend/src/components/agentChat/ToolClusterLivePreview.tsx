import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Search } from 'lucide-react'

import { useAgentChatStore } from '../../stores/agentChatStore'
import { formatRelativeTimestamp } from '../../util/time'
import { ToolIconSlot } from './ToolIconSlot'
import { deriveSemanticPreview } from './tooling/clusterPreviewText'
import { parseToolSearchResult } from './tooling/searchUtils'
import type { ToolClusterTransform, ToolEntryDisplay } from './tooling/types'

type ToolClusterLivePreviewProps = {
  cluster: ToolClusterTransform
  isLatestEvent: boolean
  onOpenTimeline: () => void
  onSelectEntry: (entry: ToolEntryDisplay) => void
}

type PreviewEntry = {
  entry: ToolEntryDisplay
  activity: ActivityDescriptor
  visual: EntryVisual
  relativeTime: string | null
}

type ActivityKind = 'linkedin' | 'search' | 'snapshot' | 'thinking' | 'kanban' | 'tool'
type PreviewState = 'active' | 'complete'

type ActivityDescriptor = {
  kind: ActivityKind
  label: string
  detail: string | null
}

type EntryVisual = {
  badge: string | null
  snippet: string | null
  linkedInProfile: LinkedInProfileVisual | null
  searchItems: SearchPreviewItem[]
  searchTotal: number | null
}

type LinkedInProfileVisual = {
  displayName: string
  subtitle: string | null
  statusText: string | null
  avatarUrl: string | null
  initials: string
}

type SearchPreviewItem = {
  title: string
  url: string
  host: string
}

const MAX_DETAIL_LENGTH = 88
const MAX_PREVIEW_ENTRIES = 3
const MAX_SEARCH_PREVIEW_ITEMS = 8
const TOOL_SEARCH_TOOL_NAMES = new Set(['search_tools', 'search_web', 'web_search', 'search'])

function clampText(value: string, maxLength: number = MAX_DETAIL_LENGTH): string {
  const normalized = value.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) {
    return normalized
  }
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`
}

function parseLinkedInTarget(value: string | null): string | null {
  if (!value) {
    return null
  }

  const normalized = value.trim()
  const withProtocol = normalized.startsWith('http') ? normalized : `https://${normalized}`
  try {
    const url = new URL(withProtocol)
    if (!url.hostname.includes('linkedin.com')) {
      return clampText(normalized)
    }
    const parts = url.pathname.split('/').filter(Boolean)
    if (parts.length < 2) {
      return 'LinkedIn page'
    }
    const [section, slug] = parts
    const cleanSlug = slug.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim()
    if (!cleanSlug) {
      return section === 'company' ? 'Company page' : 'Profile page'
    }
    return clampText(cleanSlug.replace(/\b\w/g, (char) => char.toUpperCase()), 64)
  } catch {
    return clampText(normalized)
  }
}

function parseSearchQuery(value: string | null): string | null {
  if (!value) {
    return null
  }
  const cleaned = value.split('•')[0]?.trim() ?? value.trim()
  const quoteMatch = cleaned.match(/[“"]([^”"]+)[”"]/)
  if (quoteMatch?.[1]) {
    return clampText(quoteMatch[1], 64)
  }
  return clampText(cleaned, 64)
}

function extractStreamingThought(value: string): string {
  const lines = value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (!lines.length) {
    return clampText(value, 110)
  }
  return clampText(lines[lines.length - 1], 110)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== 'string') {
    return value
  }
  const trimmed = value.trim()
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) {
    return value
  }
  try {
    return JSON.parse(trimmed)
  } catch {
    return value
  }
}

function parseHostFromText(value: string | null | undefined): string | null {
  if (!value) {
    return null
  }
  const normalized = value.trim()
  const withProtocol = normalized.startsWith('http') ? normalized : `https://${normalized}`
  try {
    const url = new URL(withProtocol)
    const host = url.hostname.replace(/^www\./i, '')
    return host || null
  } catch {
    return null
  }
}

function normalizeSearchCandidateUrl(value: string | null): string | null {
  if (!value) {
    return null
  }
  const normalized = value.trim()
  if (!normalized) {
    return null
  }
  const withProtocol = /^https?:\/\//i.test(normalized) ? normalized : `https://${normalized}`
  try {
    const url = new URL(withProtocol)
    const host = url.hostname.toLowerCase()

    if (host.includes('google.') && url.pathname === '/url') {
      const candidate = url.searchParams.get('q') || url.searchParams.get('url')
      if (candidate && /^https?:\/\//i.test(candidate)) {
        return candidate
      }
    }

    if (host.includes('google.') || host.includes('googleusercontent.com')) {
      return null
    }

    return url.toString()
  } catch {
    return null
  }
}

function pickText(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length ? trimmed : null
}

function normalizeSearchPreviewItem(rawTitle: string | null, rawUrl: string | null): SearchPreviewItem | null {
  const url = normalizeSearchCandidateUrl(rawUrl)
  if (!url) {
    return null
  }
  const host = parseHostFromText(url)
  if (!host) {
    return null
  }
  const cleanedTitle = rawTitle ? sanitizeMarkdownTitle(rawTitle) : ''
  const effectiveTitle = cleanedTitle && !isGenericSearchTitle(cleanedTitle) ? cleanedTitle : host
  const title = clampText(effectiveTitle, 86)
  return { title, url, host }
}

const GENERIC_SEARCH_TITLES = new Set([
  'read more',
  'more',
  'more items',
  'visit',
  'visit site',
  'learn more',
  'details',
  'open',
])

const SEARCH_TITLE_LINE_SKIP = [
  'people also ask',
  'discussions and forums',
  'filters and topics',
  'accessibility feedback',
  'feedback',
  'ai mode',
]

function sanitizeMarkdownTitle(value: string): string {
  return value
    .replace(/^#+\s*/, '')
    .replace(/[_*`~]+/g, '')
    .replace(/\\\[/g, '[')
    .replace(/\\\]/g, ']')
    .replace(/\s+/g, ' ')
    .trim()
}

function isGenericSearchTitle(value: string | null): boolean {
  if (!value) {
    return true
  }
  const normalized = sanitizeMarkdownTitle(value)
    .toLowerCase()
    .replace(/[.…]+$/g, '')
    .trim()
  return GENERIC_SEARCH_TITLES.has(normalized)
}

function isPlausibleSearchTitleLine(line: string): boolean {
  const normalized = sanitizeMarkdownTitle(line)
  if (normalized.length < 4) {
    return false
  }
  if (!/[a-zA-Z]/.test(normalized)) {
    return false
  }
  if (/^https?:\/\//i.test(normalized)) {
    return false
  }
  if (normalized.startsWith('![') || normalized.startsWith('[') || normalized === ']') {
    return false
  }
  const lowered = normalized.toLowerCase()
  if (SEARCH_TITLE_LINE_SKIP.some((token) => lowered.includes(token))) {
    return false
  }
  if (isGenericSearchTitle(normalized)) {
    return false
  }
  return true
}

function extractForwardSearchTitle(source: string, fromIndex: number): string | null {
  const window = source.slice(fromIndex, Math.min(source.length, fromIndex + 440))
  const headingMatch = window.match(/###\s+([^\n]+)/)
  if (headingMatch?.[1]) {
    const heading = sanitizeMarkdownTitle(headingMatch[1])
    if (isPlausibleSearchTitleLine(heading)) {
      return heading
    }
  }

  const lines = window
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  for (const line of lines) {
    if (isPlausibleSearchTitleLine(line)) {
      return sanitizeMarkdownTitle(line)
    }
  }
  return null
}

function extractBackwardSearchTitle(source: string, toIndex: number): string | null {
  const window = source.slice(Math.max(0, toIndex - 260), toIndex)
  const lines = window
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .reverse()
  for (const line of lines) {
    if (isPlausibleSearchTitleLine(line)) {
      return sanitizeMarkdownTitle(line)
    }
  }
  return null
}

function extractMarkdownSearchItems(value: string): SearchPreviewItem[] {
  const items: SearchPreviewItem[] = []
  const markdownLinkPattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g

  let match: RegExpExecArray | null
  while ((match = markdownLinkPattern.exec(value)) !== null) {
    let title = pickText(match[1])
    const url = pickText(match[2])
    if (isGenericSearchTitle(title)) {
      title =
        extractForwardSearchTitle(value, match.index + match[0].length) ??
        extractBackwardSearchTitle(value, match.index) ??
        title
    }
    const normalized = normalizeSearchPreviewItem(title, url)
    if (!normalized) {
      continue
    }
    items.push(normalized)
    if (items.length >= MAX_SEARCH_PREVIEW_ITEMS * 4) {
      break
    }
  }

  return items
}

function dedupeSearchItems(items: SearchPreviewItem[]): SearchPreviewItem[] {
  const seen = new Set<string>()
  const unique: SearchPreviewItem[] = []
  for (const item of items) {
    const key = item.url
    if (seen.has(key)) {
      continue
    }
    seen.add(key)
    unique.push(item)
  }
  return unique
}

function buildFaviconUrl(host: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64`
}

function pickFromRecord(record: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = pickText(record[key])
    if (value) {
      return value
    }
  }
  return null
}

function normalizeUrlLike(value: string | null): string | null {
  if (!value) {
    return null
  }
  if (value.startsWith('//')) {
    return `https:${value}`
  }
  return value
}

function isLikelyProfileRecord(record: Record<string, unknown>): boolean {
  return Boolean(
    pickFromRecord(record, [
      'name',
      'first_name',
      'last_name',
      'headline',
      'title',
      'occupation',
      'current_company_name',
      'profile_url',
      'url',
      'city',
      'country_code',
    ]),
  )
}

function pickLinkedInProfileRecord(value: unknown): Record<string, unknown> | null {
  const parsed = parseMaybeJson(value)
  const candidates: unknown[] = []

  if (Array.isArray(parsed)) {
    candidates.push(...parsed)
  } else if (isRecord(parsed)) {
    candidates.push(parsed)
    if (Array.isArray(parsed.result)) {
      candidates.push(...parsed.result)
    } else if (isRecord(parsed.result)) {
      candidates.push(parsed.result)
      if (Array.isArray(parsed.result.result)) {
        candidates.push(...parsed.result.result)
      }
      if (isRecord(parsed.result.data)) {
        candidates.push(parsed.result.data)
      }
    }
    if (Array.isArray(parsed.data)) {
      candidates.push(...parsed.data)
    } else if (isRecord(parsed.data)) {
      candidates.push(parsed.data)
    }
  }

  const firstProfile = candidates.find((item) => isRecord(item) && isLikelyProfileRecord(item))
  return (firstProfile as Record<string, unknown> | undefined) ?? null
}

function pickLinkedInStatusText(value: unknown): string | null {
  const parsed = parseMaybeJson(value)
  if (!isRecord(parsed)) {
    return null
  }

  const possibleContainers: Record<string, unknown>[] = [parsed]
  if (isRecord(parsed.result)) {
    possibleContainers.push(parsed.result)
  }
  if (isRecord(parsed.data)) {
    possibleContainers.push(parsed.data)
  }

  for (const container of possibleContainers) {
    const status = pickText(container.status)?.toLowerCase() ?? ''
    if (status === 'starting' || status === 'pending' || status === 'running' || status === 'queued') {
      return 'Syncing profile data…'
    }
  }
  return null
}

function deriveInitials(value: string | null): string {
  if (!value) {
    return 'LI'
  }
  const parts = value
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .slice(0, 2)
  if (!parts.length) {
    return 'LI'
  }
  return parts.map((part) => part.charAt(0).toUpperCase()).join('')
}

function deriveLinkedInProfileVisual(entry: ToolEntryDisplay, activity: ActivityDescriptor): LinkedInProfileVisual {
  const profileRecord = pickLinkedInProfileRecord(entry.result)
  const fallbackTarget = parseLinkedInTarget(entry.caption ?? entry.summary ?? null)

  const fullName = profileRecord
    ? pickFromRecord(profileRecord, ['name', 'full_name']) ??
      [pickText(profileRecord.first_name), pickText(profileRecord.last_name)].filter(Boolean).join(' ').trim()
    : null
  const displayName = clampText(fullName || fallbackTarget || 'LinkedIn profile', 52)

  const currentCompany = profileRecord && isRecord(profileRecord.current_company) ? profileRecord.current_company : null
  const companyName =
    (currentCompany ? pickFromRecord(currentCompany, ['name']) : null) ??
    (profileRecord ? pickFromRecord(profileRecord, ['current_company_name', 'company_name', 'company']) : null)
  const headline = profileRecord ? pickFromRecord(profileRecord, ['headline', 'title', 'occupation']) : null
  const city = profileRecord ? pickFromRecord(profileRecord, ['city']) : null
  const countryCode = profileRecord ? pickFromRecord(profileRecord, ['country_code']) : null
  const location = [city, countryCode].filter(Boolean).join(', ') || null
  const subtitle = clampText([headline, companyName, location].filter(Boolean).join(' • ') || activity.detail || '', 86) || null

  const statusText = pickLinkedInStatusText(entry.result)
  const avatarSource =
    profileRecord
      ? pickFromRecord(profileRecord, [
          'profile_picture',
          'profile_picture_url',
          'profile_photo',
          'profile_photo_url',
          'photo_url',
          'avatar_url',
          'display_picture_url',
          'picture',
        ])
      : null
  const avatarUrl = normalizeUrlLike(avatarSource)

  return {
    displayName,
    subtitle,
    statusText,
    avatarUrl,
    initials: deriveInitials(displayName),
  }
}

function pickResultArray(value: unknown): unknown[] | null {
  if (Array.isArray(value)) {
    return value
  }
  if (!isRecord(value)) {
    return null
  }
  const candidates = [
    value.results,
    value.items,
    value.data,
    value.organic_results,
    value.search_results,
    value.organic,
  ]
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      return candidate
    }
  }
  return null
}

function pickResultCount(value: unknown): number | null {
  const parsed = parseMaybeJson(value)
  const resultArray = pickResultArray(parsed)
  if (resultArray) {
    return resultArray.length
  }
  if (!isRecord(parsed)) {
    return null
  }
  const fields = ['count', 'total', 'total_results', 'result_count', 'tool_count']
  for (const field of fields) {
    const candidate = parsed[field]
    if (typeof candidate === 'number' && Number.isFinite(candidate)) {
      return candidate
    }
    if (typeof candidate === 'string') {
      const parsedNumber = Number(candidate.replace(/[, ]+/g, ''))
      if (Number.isFinite(parsedNumber)) {
        return parsedNumber
      }
    }
  }
  return null
}

function pickSearchSnippet(value: unknown): string | null {
  const parsed = parseMaybeJson(value)
  const resultArray = pickResultArray(parsed)
  if (!resultArray?.length) {
    return null
  }
  const first = resultArray[0]
  if (!isRecord(first)) {
    return clampText(String(first), 84)
  }
  const rawTitle = first.title ?? first.name ?? first.headline
  const title = typeof rawTitle === 'string' ? rawTitle.trim() : ''
  const rawUrl = first.url ?? first.link ?? first.domain ?? null
  const host = typeof rawUrl === 'string' ? parseHostFromText(rawUrl) : null

  if (title && host) {
    return clampText(`${title} • ${host}`, 96)
  }
  if (title) {
    return clampText(title, 96)
  }
  if (host) {
    return clampText(host, 96)
  }
  return null
}

function extractSearchPreviewItems(value: unknown): { items: SearchPreviewItem[]; total: number } {
  const parsed = parseMaybeJson(value)
  const candidates: SearchPreviewItem[] = []

  if (typeof parsed === 'string') {
    candidates.push(...extractMarkdownSearchItems(parsed))
  }

  if (isRecord(parsed) && typeof parsed.result === 'string') {
    candidates.push(...extractMarkdownSearchItems(parsed.result))
  }

  const resultArray = pickResultArray(parsed) ?? (isRecord(parsed) ? pickResultArray(parsed.result) : null)
  if (resultArray?.length) {
    for (const candidate of resultArray) {
      if (!isRecord(candidate)) {
        continue
      }
      const title = pickText(candidate.title) || pickText(candidate.name) || pickText(candidate.headline) || pickText(candidate.t)
      const url = pickText(candidate.url) || pickText(candidate.link) || pickText(candidate.domain) || pickText(candidate.u)
      const normalized = normalizeSearchPreviewItem(title, url)
      if (normalized) {
        candidates.push(normalized)
      }
    }
  }

  const deduped = dedupeSearchItems(candidates)
  return {
    items: deduped.slice(0, MAX_SEARCH_PREVIEW_ITEMS),
    total: deduped.length,
  }
}

function deriveEntryVisual(entry: ToolEntryDisplay, activity: ActivityDescriptor): EntryVisual {
  const toolName = (entry.toolName ?? '').toLowerCase()

  if (TOOL_SEARCH_TOOL_NAMES.has(toolName)) {
    const outcome = parseToolSearchResult(entry.result)
    const toolCount = outcome.toolCount
    const badge = toolCount !== null
      ? `${toolCount} tool${toolCount === 1 ? '' : 's'}`
      : outcome.enabledTools.length
        ? `${outcome.enabledTools.length} enabled`
        : null
    const enabledPreview = outcome.enabledTools.slice(0, 3).join(', ')
    const snippet = enabledPreview ? clampText(`Enabled: ${enabledPreview}`, 96) : null
    return { badge, snippet, linkedInProfile: null, searchItems: [], searchTotal: null }
  }

  if (activity.kind === 'search') {
    const searchPreview = extractSearchPreviewItems(entry.result)
    const count = pickResultCount(entry.result)
    const effectiveTotal = count !== null ? Math.max(count, searchPreview.total) : searchPreview.total || null
    const badge = effectiveTotal !== null ? `${effectiveTotal} result${effectiveTotal === 1 ? '' : 's'}` : null
    return {
      badge,
      snippet: pickSearchSnippet(entry.result),
      linkedInProfile: null,
      searchItems: searchPreview.items,
      searchTotal: effectiveTotal,
    }
  }

  if (activity.kind === 'snapshot') {
    const host = parseHostFromText(entry.caption ?? entry.summary ?? null)
    return {
      badge: null,
      snippet: host ? clampText(`Source: ${host}`, 96) : null,
      linkedInProfile: null,
      searchItems: [],
      searchTotal: null,
    }
  }

  if (activity.kind === 'linkedin') {
    const linkedInProfile = deriveLinkedInProfileVisual(entry, activity)
    return {
      badge: linkedInProfile.statusText ? 'Syncing' : 'Profile',
      snippet: null,
      linkedInProfile,
      searchItems: [],
      searchTotal: null,
    }
  }

  const itemCount = pickResultCount(entry.result)
  return {
    badge: itemCount !== null ? `${itemCount} item${itemCount === 1 ? '' : 's'}` : null,
    snippet: null,
    linkedInProfile: null,
    searchItems: [],
    searchTotal: null,
  }
}

function classifyActivity(entry: ToolEntryDisplay): ActivityKind {
  const toolName = (entry.toolName || '').toLowerCase()
  const label = entry.label.toLowerCase()
  if (toolName === 'thinking') return 'thinking'
  if (toolName === 'kanban') return 'kanban'
  if (toolName.includes('linkedin') || label.includes('linkedin')) return 'linkedin'
  if (toolName.includes('search') || label.includes('search')) return 'search'
  if (
    toolName.includes('scrape_as_markdown') ||
    toolName.includes('scrape_as_html') ||
    label.includes('web snapshot')
  ) {
    return 'snapshot'
  }
  return 'tool'
}

function deriveLinkedInLabel(toolName: string): string {
  if (toolName.includes('company')) return 'Browsing company page'
  if (toolName.includes('people_search')) return 'Searching people'
  if (toolName.includes('job')) return 'Scanning job listings'
  if (toolName.includes('posts')) return 'Scanning posts'
  return 'Browsing profile'
}

function deriveActivityDescriptor(entry: ToolEntryDisplay): ActivityDescriptor {
  const semantic = deriveSemanticPreview(entry)
  const kind = classifyActivity(entry)
  const toolName = (entry.toolName || '').toLowerCase()

  if (kind === 'linkedin') {
    const target = parseLinkedInTarget(semantic ?? entry.caption ?? entry.summary ?? null)
    const label = deriveLinkedInLabel(toolName)
    return {
      kind,
      label,
      detail: target,
    }
  }

  if (kind === 'search') {
    const query = parseSearchQuery(semantic ?? entry.caption ?? entry.summary ?? null)
    const isToolSearch = TOOL_SEARCH_TOOL_NAMES.has(toolName) || entry.label.toLowerCase() === 'tool search'
    const label = isToolSearch ? 'Searching tools' : 'Searching web'
    return {
      kind,
      label,
      detail: query ? `“${query}”` : null,
    }
  }

  if (kind === 'snapshot') {
    const target = clampText(semantic ?? entry.caption ?? entry.summary ?? 'Web page')
    return {
      kind,
      label: 'Browsing the web',
      detail: target,
    }
  }

  if (kind === 'thinking') {
    const thought = clampText(semantic ?? 'Planning next steps')
    return {
      kind,
      label: 'Planning next step',
      detail: thought,
    }
  }

  if (kind === 'kanban') {
    const detail = clampText(semantic ?? entry.caption ?? 'Kanban board updated')
    return {
      kind,
      label: 'Updating kanban',
      detail,
    }
  }

  const detail = semantic ? clampText(semantic) : null
  return {
    kind,
    label: entry.label,
    detail,
  }
}

function derivePreviewState(activeEntry: ToolEntryDisplay | null, hasActiveProcessing: boolean): PreviewState {
  if (!activeEntry) {
    return hasActiveProcessing ? 'active' : 'complete'
  }
  if (activeEntry.status === 'pending' || activeEntry.toolName === 'thinking' || hasActiveProcessing) {
    return 'active'
  }
  return 'complete'
}

export function ToolClusterLivePreview({
  cluster,
  isLatestEvent,
  onOpenTimeline,
  onSelectEntry,
}: ToolClusterLivePreviewProps) {
  const reduceMotion = useReducedMotion()
  const processingActive = useAgentChatStore((state) => state.processingActive)
  const streaming = useAgentChatStore((state) => state.streaming)
  const [newEntryIds, setNewEntryIds] = useState<string[]>([])
  const previousEntryIdsRef = useRef<string[]>([])
  const newEntryTimeoutRef = useRef<number | null>(null)
  const previewableEntries = useMemo(
    () => cluster.entries.filter((entry) => !entry.separateFromPreview),
    [cluster.entries],
  )

  const previewEntries = useMemo<PreviewEntry[]>(
    () =>
      previewableEntries
        .slice(-MAX_PREVIEW_ENTRIES)
        .map((entry) => {
          const activity = deriveActivityDescriptor(entry)
          return {
            entry,
            activity,
            visual: deriveEntryVisual(entry, activity),
            relativeTime: formatRelativeTimestamp(entry.timestamp),
          }
        }),
    [previewableEntries],
  )

  const pendingCount = useMemo(
    () => previewableEntries.filter((entry) => entry.status === 'pending' || entry.toolName === 'thinking').length,
    [previewableEntries],
  )
  const streamingReasoning = (streaming?.reasoning ?? '').trim()
  const showStreamingReasoning = isLatestEvent && streamingReasoning.length > 0
  const streamingThought = useMemo(
    () => (showStreamingReasoning ? extractStreamingThought(streamingReasoning) : null),
    [showStreamingReasoning, streamingReasoning],
  )
  const hasActiveStreamingReasoning = Boolean(
    showStreamingReasoning && streaming?.source === 'stream' && !streaming?.done,
  )
  const hasActiveProcessing = (processingActive && isLatestEvent) || hasActiveStreamingReasoning
  const activePreviewEntry = useMemo<PreviewEntry | null>(() => {
    const pendingEntry = [...previewEntries].reverse().find((item) => item.entry.status === 'pending')
    if (pendingEntry) {
      return pendingEntry
    }
    if (!hasActiveStreamingReasoning) {
      return null
    }
    return [...previewEntries].reverse().find((item) => item.entry.toolName === 'thinking') ?? null
  }, [hasActiveStreamingReasoning, previewEntries])
  const previewState = derivePreviewState(activePreviewEntry?.entry ?? null, hasActiveProcessing)
  const activeEntryId = activePreviewEntry?.entry.id ?? null
  const newEntryIdSet = useMemo(() => new Set(newEntryIds), [newEntryIds])

  useEffect(() => {
    const currentEntryIds = previewableEntries.map((entry) => entry.id)
    const previousEntryIds = previousEntryIdsRef.current
    const addedEntryIds = currentEntryIds.filter((id) => !previousEntryIds.includes(id))
    if (addedEntryIds.length > 0 || (pendingCount > 0 && hasActiveProcessing)) {
      setNewEntryIds(addedEntryIds.slice(-MAX_PREVIEW_ENTRIES))
    }

    previousEntryIdsRef.current = currentEntryIds
  }, [hasActiveProcessing, pendingCount, previewableEntries])

  useEffect(() => {
    if (newEntryIds.length === 0) {
      return
    }
    if (newEntryTimeoutRef.current !== null) {
      window.clearTimeout(newEntryTimeoutRef.current)
    }
    newEntryTimeoutRef.current = window.setTimeout(() => {
      setNewEntryIds([])
      newEntryTimeoutRef.current = null
    }, 900)
    return () => {
      if (newEntryTimeoutRef.current !== null) {
        window.clearTimeout(newEntryTimeoutRef.current)
        newEntryTimeoutRef.current = null
      }
    }
  }, [newEntryIds])
  const hiddenEntryCount = Math.max(previewableEntries.length - previewEntries.length, 0)

  if (!previewEntries.length) {
    return null
  }

  return (
    <motion.div
      className="tool-cluster-live-preview"
      data-state={previewState}
      layout={!reduceMotion}
      transition={reduceMotion ? undefined : { type: 'spring', stiffness: 430, damping: 34, mass: 0.55 }}
    >
      <AnimatePresence initial={false}>
        {hiddenEntryCount > 0 ? (
          <motion.button
            key={`hidden-actions-${hiddenEntryCount}`}
            type="button"
            className="tool-cluster-live-preview__more-link"
            onClick={onOpenTimeline}
            initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: -6 }}
            animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 1 } : { opacity: 0, y: -4 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
          >
            <span className="tool-cluster-live-preview__more-link-line" aria-hidden="true" />
            <span className="tool-cluster-live-preview__more-link-label">
              {hiddenEntryCount} more actions
            </span>
            <span className="tool-cluster-live-preview__more-link-line" aria-hidden="true" />
          </motion.button>
        ) : null}
      </AnimatePresence>
      <div className="tool-cluster-live-preview__feed" aria-label="Recent tool activity">
        <AnimatePresence initial={false}>
          {previewEntries.map((item, index) => {
            const { entry, visual } = item
            const isActive = entry.id === activeEntryId
            const isHighlighted = isActive && previewState === 'active'
            const isNew = newEntryIdSet.has(entry.id)
            const showSearchSweep = !reduceMotion && isHighlighted && item.activity.kind === 'search'
            const detailText =
              item.activity.kind === 'thinking' && isHighlighted && streamingThought ? streamingThought : item.activity.detail
            const linkedInProfile = item.activity.kind === 'linkedin' ? visual.linkedInProfile : null
            const searchItems = item.activity.kind === 'search' ? visual.searchItems : []
            const searchTotal = item.activity.kind === 'search' ? visual.searchTotal : null
            const searchTruncated = Boolean(searchTotal !== null && searchTotal > searchItems.length)
            return (
              <motion.div
                key={entry.id}
                layout={!reduceMotion}
                className="tool-cluster-live-preview__entry"
                data-active={isHighlighted ? 'true' : 'false'}
                data-kind={item.activity.kind}
                data-new={isNew ? 'true' : 'false'}
                data-profile-card={linkedInProfile ? 'true' : 'false'}
                role="button"
                tabIndex={0}
                initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 5, scale: 0.995 }}
                animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
                exit={reduceMotion ? { opacity: 1 } : { opacity: 0, y: -4, scale: 0.995 }}
                transition={{
                  duration: reduceMotion ? 0.12 : 0.22,
                  ease: 'easeOut',
                  delay: reduceMotion ? 0 : index * 0.032,
                }}
                whileHover={reduceMotion ? undefined : { x: 1.5 }}
                whileTap={reduceMotion ? undefined : { scale: 0.998 }}
                onClick={() => onSelectEntry(entry)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelectEntry(entry)
                  }
                }}
              >
                {showSearchSweep ? (
                  <motion.span
                    className="tool-cluster-live-preview__search-sweep"
                    initial={{ x: '-120%', opacity: 0 }}
                    animate={{ x: ['-120%', '125%'], opacity: [0, 0.82, 0] }}
                    transition={{ duration: 0.9, ease: 'easeInOut', repeat: Infinity, repeatDelay: 0.12 }}
                    aria-hidden="true"
                  />
                ) : null}
                {linkedInProfile ? (
                  <motion.span
                    className="tool-cluster-live-preview__profile-avatar"
                    animate={reduceMotion || !isHighlighted ? undefined : { scale: [1, 1.06, 1] }}
                    transition={reduceMotion || !isHighlighted ? undefined : { duration: 0.96, repeat: Infinity, ease: 'easeInOut' }}
                  >
                    {linkedInProfile.avatarUrl ? (
                      <img
                        src={linkedInProfile.avatarUrl}
                        alt={linkedInProfile.displayName}
                        loading="lazy"
                        className="tool-cluster-live-preview__profile-avatar-image"
                      />
                    ) : (
                      <span className="tool-cluster-live-preview__profile-avatar-fallback">{linkedInProfile.initials}</span>
                    )}
                    {isHighlighted ? (
                      <motion.span
                        className="tool-cluster-live-preview__profile-live-dot"
                        animate={reduceMotion ? undefined : { scale: [1, 1.18, 1], opacity: [0.55, 1, 0.55] }}
                        transition={reduceMotion ? undefined : { duration: 1, repeat: Infinity, ease: 'easeInOut' }}
                        aria-hidden="true"
                      />
                    ) : null}
                  </motion.span>
                ) : (
                  <motion.span
                    className={`tool-cluster-live-preview__entry-icon ${entry.iconBgClass} ${entry.iconColorClass}`}
                    animate={
                      reduceMotion || !isHighlighted
                        ? undefined
                        : item.activity.kind === 'search'
                          ? { rotate: [0, -5, 5, 0] }
                          : { scale: [1, 1.05, 1] }
                    }
                    transition={
                      reduceMotion || !isHighlighted
                        ? undefined
                        : item.activity.kind === 'search'
                          ? { duration: 0.58, repeat: Infinity, ease: 'easeInOut' }
                          : { duration: 1.05, repeat: Infinity, ease: 'easeInOut' }
                    }
                  >
                    <ToolIconSlot entry={entry} />
                  </motion.span>
                )}
                <span className="tool-cluster-live-preview__entry-main">
                  <span className="tool-cluster-live-preview__entry-label-row">
                    <span className="tool-cluster-live-preview__entry-label">
                      {linkedInProfile ? linkedInProfile.displayName : item.activity.label}
                    </span>
                    {linkedInProfile ? (
                      <span className="tool-cluster-live-preview__entry-badge tool-cluster-live-preview__entry-badge--action">
                        {item.activity.label}
                      </span>
                    ) : null}
                    {visual.badge ? <span className="tool-cluster-live-preview__entry-badge">{visual.badge}</span> : null}
                    {item.activity.kind === 'search' ? (
                      <Search className="tool-cluster-live-preview__entry-search-icon" aria-hidden="true" />
                    ) : null}
                  </span>
                  <AnimatePresence initial={false} mode="wait">
                    {linkedInProfile?.subtitle ? (
                      <motion.span
                        key={`${entry.id}-profile-subtitle-${linkedInProfile.subtitle}`}
                        className="tool-cluster-live-preview__entry-caption"
                        initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 2 }}
                        animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
                        exit={reduceMotion ? { opacity: 1 } : { opacity: 0, y: -2 }}
                        transition={{ duration: 0.16, ease: 'easeOut' }}
                      >
                        {linkedInProfile.subtitle}
                      </motion.span>
                    ) : detailText ? (
                      <motion.span
                        key={`${entry.id}-detail-${detailText}`}
                        className="tool-cluster-live-preview__entry-caption"
                        initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 2 }}
                        animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
                        exit={reduceMotion ? { opacity: 1 } : { opacity: 0, y: -2 }}
                        transition={{ duration: 0.16, ease: 'easeOut' }}
                      >
                        {detailText}
                      </motion.span>
                    ) : null}
                  </AnimatePresence>
                  {visual.snippet && visual.snippet !== detailText && searchItems.length === 0 ? (
                    <span className="tool-cluster-live-preview__entry-context">{visual.snippet}</span>
                  ) : null}
                  {searchItems.length ? (
                    <motion.ul
                      className="tool-cluster-live-preview__search-results"
                      initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 2 }}
                      animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
                      transition={{ duration: 0.2, ease: 'easeOut', delay: reduceMotion ? 0 : 0.04 }}
                    >
                      {searchTruncated ? (
                        <li className="tool-cluster-live-preview__search-results-meta">
                          Showing {searchItems.length} of {searchTotal} results
                        </li>
                      ) : null}
                      {searchItems.map((searchItem, searchIndex) => (
                        <motion.li
                          key={`${entry.id}-search-item-${searchItem.url}`}
                          className="tool-cluster-live-preview__search-result-row"
                          initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 2 }}
                          animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
                          transition={{ duration: 0.16, ease: 'easeOut', delay: reduceMotion ? 0 : 0.05 + searchIndex * 0.03 }}
                        >
                          <a
                            href={searchItem.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="tool-cluster-live-preview__search-result-link"
                            onPointerDown={(event) => event.stopPropagation()}
                            onMouseDown={(event) => event.stopPropagation()}
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => event.stopPropagation()}
                          >
                            <span className="tool-cluster-live-preview__search-result-favicon-wrap">
                              <img
                                src={buildFaviconUrl(searchItem.host)}
                                alt=""
                                loading="lazy"
                                referrerPolicy="no-referrer"
                                className="tool-cluster-live-preview__search-result-favicon"
                              />
                            </span>
                            <span className="tool-cluster-live-preview__search-result-title">{searchItem.title}</span>
                            <span className="tool-cluster-live-preview__search-result-host">{searchItem.host}</span>
                          </a>
                        </motion.li>
                      ))}
                    </motion.ul>
                  ) : null}
                </span>
                {item.relativeTime ? (
                  <time className="tool-cluster-live-preview__entry-time" dateTime={entry.timestamp ?? undefined}>
                    {item.relativeTime}
                  </time>
                ) : null}
              </motion.div>
            )
          })}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}
