import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import { keepPreviousData, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { InfiniteData } from '@tanstack/react-query'
import { AlertTriangle, BadgeCheck, Heart, Loader2, Search } from 'lucide-react'

import { fetchLibraryAgents, type LibraryAgent, type LibraryAgentsPayload, toggleLibraryAgentLike } from '../api/library'
import './library.css'

type LibraryScreenProps = {
  listUrl: string
  likeUrl: string
  canLike: boolean
  initialCategory?: string | null
  initialOfficialOnly?: boolean
  initialData?: LibraryAgentsPayload
}

const MOST_POPULAR_LABEL = 'All'
const MOST_POPULAR_KEY = '__most_popular__'
const PAGE_SIZE = 24
const PREVIEW_SLOTS = 4
const PREVIEW_INTERVAL_MS = 5400
const LIBRARY_HISTORY_STATE_KEY = '__gobiiLibraryFilters'

type LibraryHistoryFilters = {
  category: string | null
  categorySlug: string
  officialOnly: boolean
}

function buildLibraryUrl(categorySlug: string, officialOnly: boolean, pageNumber = 1): string {
  const pathname = categorySlug ? `/library/${encodeURIComponent(categorySlug)}/` : '/library/'
  const params = new URLSearchParams()
  if (officialOnly) {
    params.set('official', 'true')
  }
  if (pageNumber > 1) {
    params.set('page', String(pageNumber))
  }
  const query = params.toString()
  return query ? `${pathname}?${query}` : pathname
}

function readLibraryHistoryFilters(state: unknown): LibraryHistoryFilters | null {
  if (!state || typeof state !== 'object') {
    return null
  }
  const filters = (state as Record<string, unknown>)[LIBRARY_HISTORY_STATE_KEY]
  if (!filters || typeof filters !== 'object') {
    return null
  }
  const candidate = filters as Record<string, unknown>
  if (
    (candidate.category !== null && typeof candidate.category !== 'string') ||
    typeof candidate.categorySlug !== 'string' ||
    typeof candidate.officialOnly !== 'boolean'
  ) {
    return null
  }
  return {
    category: candidate.category as string | null,
    categorySlug: candidate.categorySlug,
    officialOnly: candidate.officialOnly,
  }
}

function shouldNavigateInPlace(event: ReactMouseEvent<HTMLAnchorElement>): boolean {
  return !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}

const STATIC_LOGOS = {
  greenhouse: '/static/images/integration-logos/greenhouse.svg',
  hubspot: '/static/images/integration-logos/hubspot.svg',
  sheets: '/static/images/integration-logos/sheets-glyph.svg',
  gmail: '/static/images/integration-logos/gmail.svg',
  fish: '/static/images/gobii-fish.svg',
} as const

const FACES = {
  mk: '/static/images/people/face-mk.jpg',
  rs: '/static/images/people/face-rs.jpg',
  al: '/static/images/people/face-al.jpg',
  jn: '/static/images/people/face-jn.jpg',
} as const

/* ---------------------------------------------------------------------------
 * Category identity
 *
 * Colours come from the validated categorical palette (each >= 3:1 against the
 * Luxe dark surface) and are assigned by a stable hash of the category NAME.
 * Filtering the grid therefore never repaints the survivors, and the same
 * category is the same colour on every page load. Colour is never the only
 * carrier of identity: the category label is always rendered next to the tile.
 * ------------------------------------------------------------------------- */
const CATEGORY_PALETTE = [
  '#9061f9',
  '#0d9f74',
  '#c2410c',
  '#2563eb',
  '#7c5cf0',
  '#0e7490',
  '#a1436b',
  '#b45309',
] as const

function categoryColor(category: string): string {
  const key = (category || '').trim().toLowerCase()
  if (!key) {
    return CATEGORY_PALETTE[0]
  }
  // djb2 — deterministic, order-independent
  let hash = 5381
  for (let index = 0; index < key.length; index += 1) {
    hash = ((hash << 5) + hash + key.charCodeAt(index)) >>> 0
  }
  return CATEGORY_PALETTE[hash % CATEGORY_PALETTE.length]
}

function monogram(name: string): string {
  const words = (name || '').trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) {
    return '••'
  }
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase()
  }
  return `${words[0][0]}${words[1][0]}`.toUpperCase()
}

/* ---------------------------------------------------------------------------
 * The DELIVERS strip
 *
 * Nothing here is keyed to an agent name. Both rows are extracted from the
 * template's OWN copy (tagline + description) that ships in the library
 * payload, so a community template describes itself in its author's words and
 * we never invent an output for somebody else's agent. When neither row
 * resolves the card renders its designed no-strip state instead.
 * ------------------------------------------------------------------------- */
const OUTPUT_PATTERNS: Array<[RegExp, string]> = [
  [/\bshortlists?\b/, 'shortlists'],
  [/\bcandidates?\b/, 'qualified candidates'],
  [/\bleads?\b/, 'leads'],
  [/\bprospects?\b/, 'prospects'],
  [/\bcontacts?\b/, 'contacts'],
  [/\bdigests?\b/, 'digests'],
  [/\breports?\b/, 'reports'],
  [/\bsummar(?:y|ies|ize[sd]?)\b/, 'summaries'],
  [/\brecaps?\b/, 'recaps'],
  [/\bbriefs?\b|\bbriefings?\b/, 'briefs'],
  [/\balerts?\b/, 'alerts'],
  [/\bprofiles?\b/, 'profiles'],
  [/\btrackers?\b/, 'trackers'],
  [/\bdashboards?\b/, 'dashboards'],
  [/\bcomparables?\b|\bcomps\b/, 'comps'],
  [/\bquotes?\b/, 'quotes'],
  [/\bdrafts?\b/, 'drafts'],
  [/\btickets?\b/, 'tickets'],
  [/\binvoices?\b/, 'invoices'],
  [/\btranscripts?\b/, 'transcripts'],
  [/\bforecasts?\b/, 'forecasts'],
  [/\brecommendations?\b/, 'recommendations'],
  [/\bsignals?\b/, 'signals'],
  [/\brisks?\b/, 'risk flags'],
  [/\bblockers?\b/, 'blockers'],
  [/\bmilestones?\b/, 'milestones'],
  [/\bresearch\b/, 'research notes'],
  [/\btrends?\b/, 'trend reports'],
]

const SURFACE_PATTERNS: Array<[RegExp, string]> = [
  [/\blinkedin\b/, 'LinkedIn'],
  [/\bgithub\b/, 'GitHub'],
  [/\bgreenhouse\b/, 'Greenhouse'],
  [/\bhubspot\b/, 'HubSpot'],
  [/\bsalesforce\b/, 'Salesforce'],
  [/\bapollo\b/, 'Apollo'],
  [/\bgoogle sheets?\b|\bspreadsheets?\b/, 'Sheets'],
  [/\bslack\b/, 'Slack'],
  [/\bdiscord\b/, 'Discord'],
  [/\bnotion\b/, 'Notion'],
  [/\bjira\b/, 'Jira'],
  [/\bzendesk\b/, 'Zendesk'],
  [/\bshopify\b/, 'Shopify'],
  [/\bstripe\b/, 'Stripe'],
  [/\bjob boards?\b/, 'job boards'],
  [/\bcrm\b/, 'CRM'],
  [/\bemail\b|\bgmail\b|\binbox\b/, 'email'],
  [/\bsms\b|\btext messages?\b/, 'SMS'],
  [/\bcalendars?\b/, 'calendar'],
  [/\bwebhooks?\b/, 'webhooks'],
]

type DeliveryRow = { kind: 'output' | 'surface'; text: string }

function matchVocabulary(copy: string, patterns: Array<[RegExp, string]>, max: number): string[] {
  const found: string[] = []
  for (const [pattern, label] of patterns) {
    if (found.length >= max) {
      break
    }
    if (pattern.test(copy) && !found.includes(label)) {
      found.push(label)
    }
  }
  return found
}

function sentenceCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value
}

function deriveDeliveryRows(agent: Pick<LibraryAgent, 'tagline' | 'description'>): DeliveryRow[] {
  const copy = `${agent.tagline || ''} ${agent.description || ''}`.toLowerCase()
  if (!copy.trim()) {
    return []
  }
  const rows: DeliveryRow[] = []
  const outputs = matchVocabulary(copy, OUTPUT_PATTERNS, 3)
  if (outputs.length > 0) {
    rows.push({ kind: 'output', text: sentenceCase(outputs.join(', ')) })
  }
  const surfaces = matchVocabulary(copy, SURFACE_PATTERNS, 3)
  if (surfaces.length > 0) {
    rows.push({ kind: 'surface', text: surfaces.join(' · ') })
  }
  return rows
}

function splitSentences(value: string, max: number): string[] {
  return (value || '')
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence.length > 2)
    .slice(0, max)
}

/* ---------------------------------------------------------------------------
 * Emulated app windows (.gkw-* chrome from templates/includes/_gk_showcase.html)
 *
 * The third-party windows carry illustrative sample records, so they are only
 * shown for Gobii's own official templates and the panel is always labelled
 * EXAMPLE. Community templates get the agent-report window, whose entire body
 * is the template author's own copy.
 * ------------------------------------------------------------------------- */
type WindowKind = 'greenhouse' | 'hubspot' | 'sheets' | 'report'

const WINDOW_RULES: Array<[RegExp, WindowKind]> = [
  [/people|recruit|talent|hiring|\bhr\b|human/, 'greenhouse'],
  [/revenue|sales|marketing|growth|customer|success|pipeline/, 'hubspot'],
  [/research|data|analy|financ|estate|market|operations|intel/, 'sheets'],
]

function windowKindFor(agent: LibraryAgent): WindowKind {
  if (!agent.isOfficial) {
    return 'report'
  }
  const key = `${agent.category || ''} ${agent.name || ''}`.toLowerCase()
  for (const [pattern, kind] of WINDOW_RULES) {
    if (pattern.test(key)) {
      return kind
    }
  }
  return 'report'
}

const rowDelay = (index: number) => ({ animationDelay: `${0.3 + index * 0.28}s` })

const SAMPLE_CANDIDATES = [
  { face: FACES.mk, name: 'Maya Kessler', detail: 'Go · Kubernetes · 7 yrs', tag: '94% match' },
  { face: FACES.rs, name: 'Rafael Santos', detail: 'distributed systems', tag: '91% match' },
  { face: FACES.al, name: 'Amy Liu', detail: 'platform lead', tag: '89% match' },
]

const SAMPLE_CONTACTS = [
  { name: 'Dana Whitfield', company: 'Northwind Robotics' },
  { name: 'Marcus Yoon', company: 'Acme Cloud' },
  { name: 'Elif Kaya', company: 'Blue Harbor Labs' },
]

const SAMPLE_SHEET_ROWS = [
  ['Northwind Robotics', 'series B', 'RevOps', '94'],
  ['Acme Cloud', 'series A', 'Platform', '91'],
  ['Blue Harbor Labs', 'seed', 'Security', '89'],
]

function GreenhouseWindow() {
  return (
    <div className="gkw-app">
      <div className="gkw-gh-nav">
        <img src={STATIC_LOGOS.greenhouse} alt="" height={18} />
        <span className="on">Candidates</span>
        <span className="gkw-hide">Jobs</span>
        <span className="gkw-hide">Reports</span>
      </div>
      <div className="gkw-gh-job">
        <b>Senior Backend Engineer</b>
        <span>Remote · US</span>
      </div>
      <div className="gkw-gh-stages">
        <span className="gkw-gh-stage on">Application Review · 23</span>
        <span className="gkw-gh-stage">Screen · 5</span>
        <span className="gkw-gh-stage gkw-hide">Offer · 1</span>
      </div>
      {SAMPLE_CANDIDATES.map((person, index) => (
        <div className="gkw-gh-row gkw-row" key={person.name} style={rowDelay(index)}>
          <span className="gkw-face">
            <img src={person.face} alt="" width={24} height={24} loading="lazy" />
          </span>
          <div>
            <div className="gkw-nm">{person.name}</div>
            <div className="gkw-dt">{person.detail}</div>
          </div>
          <span className="gkw-gh-tag">{person.tag}</span>
          <span className="gkw-gh-new">NEW</span>
        </div>
      ))}
    </div>
  )
}

function HubspotWindow() {
  return (
    <div className="gkw-app">
      <div className="gkw-hs-nav">
        <img src={STATIC_LOGOS.hubspot} alt="" height={16} />
        <span className="on">Contacts</span>
        <span className="gkw-hide">Companies</span>
        <span className="gkw-hide">Deals</span>
      </div>
      <div className="gkw-hs-sub">
        <b>Contacts</b>
        <span className="gkw-hs-btn">Create contact</span>
      </div>
      <div className="gkw-hs-th">
        <span />
        <span>NAME</span>
        <span>COMPANY</span>
        <span>STATUS</span>
      </div>
      {SAMPLE_CONTACTS.map((contact, index) => (
        <div className="gkw-hs-row gkw-row" key={contact.name} style={rowDelay(index)}>
          <span className="gkw-hs-cb" />
          <span className="lnk">{contact.name}</span>
          <span className="co">{contact.company}</span>
          <span className="gkw-hs-chip">New</span>
        </div>
      ))}
    </div>
  )
}

function SheetsWindow() {
  return (
    <div className="gkw-app">
      <div className="gkw-gs-title">
        <img src={STATIC_LOGOS.sheets} alt="" height={20} />
        <b>research-log</b>
        <span style={{ marginLeft: 'auto', color: '#188038', fontSize: 10, fontWeight: 700 }}>● 1 agent editing</span>
      </div>
      <div className="gkw-gs-menu">
        <span>File</span>
        <span>Edit</span>
        <span>View</span>
        <span>Insert</span>
        <span>Data</span>
      </div>
      <div className="gkw-gs-fx">
        <i>fx</i>
        <span className="cell">A4</span>
        <span>Blue Harbor Labs</span>
      </div>
      <div className="gkw-gs-grid">
        <div className="gkw-gs-h" />
        <div className="gkw-gs-h">A</div>
        <div className="gkw-gs-h">B</div>
        <div className="gkw-gs-h">C</div>
        <div className="gkw-gs-h">D</div>
        <div className="gkw-gs-r">1</div>
        <div className="gkw-gs-b">Account</div>
        <div className="gkw-gs-b">Stage</div>
        <div className="gkw-gs-b">Team</div>
        <div className="gkw-gs-b">Score</div>
        {SAMPLE_SHEET_ROWS.map((cells, rowIndex) => (
          <Cells key={cells[0]} cells={cells} rowIndex={rowIndex} />
        ))}
      </div>
    </div>
  )
}

function Cells({ cells, rowIndex }: { cells: string[]; rowIndex: number }) {
  return (
    <>
      <div className="gkw-gs-r">{rowIndex + 2}</div>
      {cells.map((cell, cellIndex) => (
        <div
          className="gkw-row gkw-gs-new"
          key={cell}
          style={{ animationDelay: `${0.3 + rowIndex * 0.28 + cellIndex * 0.06}s` }}
        >
          {cell}
        </div>
      ))}
    </>
  )
}

function ReportWindow({ agent }: { agent: LibraryAgent }) {
  const bullets = useMemo(() => {
    const derived = deriveDeliveryRows(agent).map((row) => row.text)
    if (derived.length > 0) {
      return derived
    }
    const sentences = splitSentences(agent.description || '', 3)
    if (sentences.length > 0) {
      return sentences
    }
    return agent.tagline ? [agent.tagline] : ['Findings, summarised and sent to you.']
  }, [agent])

  return (
    <div className="gkw-em">
      <div className="gkw-embar">
        <img src={STATIC_LOGOS.gmail} alt="" height={17} />
        <b>Inbox</b>
        <span className="sr">just now</span>
      </div>
      <div className="gkw-emsub">{agent.name} — your update</div>
      <div className="gkw-emmeta">
        <span className="gkw-emav">
          <img src={STATIC_LOGOS.fish} alt="" height={15} />
        </span>
        <span>
          <span className="gkw-emfrom">{agent.name} via Gobii</span>
          <span className="gkw-emto" style={{ display: 'block' }}>
            to you
          </span>
        </span>
      </div>
      <div className="gkw-embody">
        {agent.tagline || agent.description}
        <ul>
          {bullets.map((bullet, index) => (
            <li className="gkw-row" key={bullet} style={rowDelay(index)}>
              {bullet}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function AgentWindow({ agent }: { agent: LibraryAgent }) {
  switch (windowKindFor(agent)) {
    case 'greenhouse':
      return <GreenhouseWindow />
    case 'hubspot':
      return <HubspotWindow />
    case 'sheets':
      return <SheetsWindow />
    default:
      return <ReportWindow agent={agent} />
  }
}

/* -------------------------------------------------------------------- card */

type LibraryCardProps = {
  agent: LibraryAgent
  isSpotlit: boolean
  canLike: boolean
  isLikePending: boolean
  onLike: (agentId: string) => void
  onPreview: (agentId: string) => void
}

function LibraryCard({ agent, isSpotlit, canLike, isLikePending, onLike, onPreview }: LibraryCardProps) {
  const rows = useMemo(() => deriveDeliveryRows(agent), [agent])
  const accent = categoryColor(agent.category)
  const description = agent.description && agent.description !== agent.tagline ? agent.description : ''

  return (
    <article
      className={`gklib-card${isSpotlit ? ' is-spot' : ''}`}
      onMouseEnter={() => onPreview(agent.id)}
      onFocus={() => onPreview(agent.id)}
    >
      <a href={agent.templateUrl} aria-label={`View the ${agent.name}`} className="gklib-cardlink" />

      <div className="gklib-cardbody">
        <div className="gklib-top">
          <span className="gklib-mono" style={{ backgroundColor: accent }} aria-hidden="true">
            {monogram(agent.name)}
          </span>
          <span>
            <h2 className="gklib-nm">{agent.name}</h2>
            {agent.tagline ? <span className="gklib-tg">{agent.tagline}</span> : null}
          </span>
        </div>

        <div className="gklib-meta">
          <span className="gklib-catchip" style={{ backgroundColor: `${accent}2e` }} title={agent.category}>
            {agent.category}
          </span>
          {agent.isOfficial ? (
            <span className="gklib-official" title="Maintained by Gobii">
              <BadgeCheck className="size-3" aria-hidden="true" />
              Official
            </span>
          ) : agent.publicProfileHandle ? (
            <span className="gklib-handle">@{agent.publicProfileHandle}</span>
          ) : null}

          {canLike ? (
            <button
              type="button"
              disabled={isLikePending}
              onClick={() => onLike(agent.id)}
              className={`gklib-like${agent.isLiked ? ' is-liked' : ''}`}
              aria-label={agent.isLiked ? `Remove like from ${agent.name}` : `Like ${agent.name}`}
            >
              {isLikePending ? (
                <Loader2 className="size-3 gklib-spin" aria-hidden="true" />
              ) : (
                <Heart className="size-3" fill={agent.isLiked ? 'currentColor' : 'none'} aria-hidden="true" />
              )}
              <span>{agent.likeCount}</span>
            </button>
          ) : (
            <span className="gklib-like" title="Sign in to like templates.">
              <Heart className="size-3" aria-hidden="true" />
              <span>{agent.likeCount}</span>
              <span className="gklib-sr">likes</span>
            </span>
          )}
        </div>

        {rows.length > 0 ? (
          <div className="gklib-out">
            <div className="gklib-olab">DELIVERS</div>
            {rows.map((row) => (
              <div className="gklib-orow" key={row.kind}>
                <span className={`gklib-osq${row.kind === 'output' ? ' is-out' : ''}`} aria-hidden="true">
                  {row.kind === 'output' ? '▣' : '⇄'}
                </span>
                <span className="gklib-otext" title={row.text}>
                  {row.text}
                </span>
              </div>
            ))}
          </div>
        ) : description ? (
          <p className="gklib-desc">{description}</p>
        ) : null}
      </div>
    </article>
  )
}

/* --------------------------------------------------------------- spotlight */

type SpotlightProps = {
  agents: LibraryAgent[]
  activeAgent: LibraryAgent
  reducedMotion: boolean
  onSelect: (agentId: string) => void
  onPauseChange: (paused: boolean) => void
}

function Spotlight({ agents, activeAgent, reducedMotion, onSelect, onPauseChange }: SpotlightProps) {
  const panelRef = useRef<HTMLElement | null>(null)
  // No IntersectionObserver (very old browsers, jsdom) => render the finished state.
  const [inView, setInView] = useState(() => typeof IntersectionObserver === 'undefined')

  useEffect(() => {
    const element = panelRef.current
    if (!element || typeof IntersectionObserver === 'undefined') {
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true)
        }
      },
      { threshold: 0.15 },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const accent = categoryColor(activeAgent.category)
  const live = inView || reducedMotion

  return (
    <aside
      ref={panelRef}
      className="gklib-spot"
      aria-label="Agent preview"
      onMouseEnter={() => onPauseChange(true)}
      onMouseLeave={() => onPauseChange(false)}
      onFocusCapture={() => onPauseChange(true)}
      onBlurCapture={() => onPauseChange(false)}
    >
      <div className="gklib-spotinner">
        <p className="gklib-slab">
          <span className="gklib-livedot" aria-hidden="true" />
          SEE ONE WORKING
          <span className="gklib-exchip">EXAMPLE</span>
        </p>

        <div className="gklib-stop">
          <span className="gklib-mono" style={{ backgroundColor: accent }} aria-hidden="true">
            {monogram(activeAgent.name)}
          </span>
          <div>
            <p className="gklib-stoptitle">{activeAgent.name}</p>
            <p className="gklib-ssub">{activeAgent.tagline || activeAgent.description}</p>
          </div>
        </div>

        <div className={`gklib-swin${live ? ' gkw-live' : ''}`} key={activeAgent.id} aria-hidden="true">
          <AgentWindow agent={activeAgent} />
        </div>

        <a className="gklib-cta" href={activeAgent.templateUrl}>
          View {activeAgent.name}
        </a>

        {agents.length > 1 ? (
          <div className="gklib-dots">
            {agents.map((agent) => (
              <button
                key={agent.id}
                type="button"
                className={agent.id === activeAgent.id ? 'is-on' : ''}
                aria-label={`Preview ${agent.name}`}
                aria-current={agent.id === activeAgent.id}
                onClick={() => onSelect(agent.id)}
              />
            ))}
          </div>
        ) : null}
      </div>
    </aside>
  )
}

/* ------------------------------------------------------------------ helpers */

function updateLikeInCachedPayload(
  payload: InfiniteData<LibraryAgentsPayload, number> | undefined,
  update: {
    agentId: string
    likeCount: number
    isLiked: boolean
  },
): InfiniteData<LibraryAgentsPayload, number> | undefined {
  if (!payload) {
    return payload
  }
  const { agentId, likeCount, isLiked } = update

  let previousLikeCount: number | null = null
  for (const page of payload.pages) {
    const existingAgent = page.agents.find((agent) => agent.id === agentId)
    if (existingAgent) {
      previousLikeCount = existingAgent.likeCount
      break
    }
  }

  if (previousLikeCount === null) {
    return payload
  }

  const delta = likeCount - previousLikeCount
  return {
    ...payload,
    pages: payload.pages.map((page) => ({
      ...page,
      libraryTotalLikes: Math.max(0, page.libraryTotalLikes + delta),
      agents: page.agents.map((agent) => (agent.id === agentId ? { ...agent, likeCount, isLiked } : agent)),
    })),
  }
}

function computeReducedMotion(): boolean {
  if (typeof window === 'undefined') {
    return false
  }
  // The site's own perf guard (homepage script) plus the OS preference.
  const perf = (window as unknown as { GobiiHomePerf?: { shouldReduceHomepageMotion?: () => boolean } }).GobiiHomePerf
  const perfReduced = Boolean(perf?.shouldReduceHomepageMotion?.())
  const mediaReduced =
    typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return perfReduced || mediaReduced
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(computeReducedMotion)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return
    }
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReduced(computeReducedMotion())
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  return reduced
}

function SkeletonGrid() {
  return (
    <div className="gklib-grid" aria-hidden="true">
      {Array.from({ length: 6 }, (_, index) => (
        <div className="gklib-skel" key={index}>
          <div style={{ display: 'flex', gap: 11 }}>
            <div className="gklib-skelline tile" />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="gklib-skelline" style={{ width: '70%' }} />
              <div className="gklib-skelline" style={{ width: '95%' }} />
              <div className="gklib-skelline" style={{ width: '55%' }} />
            </div>
          </div>
          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="gklib-skelline" style={{ width: '45%' }} />
            <div className="gklib-skelline" style={{ width: '80%' }} />
          </div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ screen */

export function LibraryScreen({
  listUrl,
  likeUrl,
  canLike,
  initialCategory = null,
  initialOfficialOnly = false,
  initialData,
}: LibraryScreenProps) {
  const [selectedCategory, setSelectedCategory] = useState<string | null>(initialCategory || null)
  const [selectedCategorySlug, setSelectedCategorySlug] = useState(initialData?.selectedCategorySlug ?? '')
  const [officialOnly, setOfficialOnly] = useState(initialOfficialOnly || Boolean(initialData?.officialOnly))
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [activePreviewId, setActivePreviewId] = useState<string | null>(null)
  const [previewPaused, setPreviewPaused] = useState(false)
  const normalizedSearchQuery = debouncedSearchQuery.trim()
  const queryClient = useQueryClient()
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const initialHistoryFiltersRef = useRef<LibraryHistoryFilters>({
    category: initialCategory || null,
    categorySlug: initialData?.selectedCategorySlug ?? '',
    officialOnly: initialOfficialOnly || Boolean(initialData?.officialOnly),
  })
  const reducedMotion = useReducedMotion()
  const initialSelectedCategory = initialCategory || null
  const initialSelectedOfficialOnly = initialOfficialOnly || Boolean(initialData?.officialOnly)
  const shouldUseInitialData =
    selectedCategory === initialSelectedCategory &&
    officialOnly === initialSelectedOfficialOnly &&
    normalizedSearchQuery.length === 0
  const initialLibraryData = useMemo<InfiniteData<LibraryAgentsPayload, number> | undefined>(() => {
    if (!initialData || !shouldUseInitialData) {
      return undefined
    }
    return {
      pages: [initialData],
      pageParams: [initialData.offset],
    }
  }, [initialData, shouldUseInitialData])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedSearchQuery(searchQuery)
    }, 400)

    return () => {
      window.clearTimeout(timeoutId)
    }
  }, [searchQuery])

  useEffect(() => {
    const initialFilters = initialHistoryFiltersRef.current
    window.history.replaceState(
      {
        ...(window.history.state ?? {}),
        [LIBRARY_HISTORY_STATE_KEY]: initialFilters,
      },
      '',
      window.location.href,
    )

    const handlePopState = (event: PopStateEvent) => {
      const filters = readLibraryHistoryFilters(event.state)
      if (!filters) {
        return
      }
      setSelectedCategory(filters.category)
      setSelectedCategorySlug(filters.categorySlug)
      setOfficialOnly(filters.officialOnly)
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  // "/" focuses search, but never while the user is typing somewhere else.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) {
        return
      }
      const target = event.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable) {
        return
      }
      const input = searchInputRef.current
      if (!input) {
        return
      }
      event.preventDefault()
      input.focus()
      input.select()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  const libraryQueryKey = [
    'library-agents',
    listUrl,
    selectedCategory ?? MOST_POPULAR_KEY,
    normalizedSearchQuery,
    officialOnly ? 'official' : 'all',
  ] as const
  const libraryQuery = useInfiniteQuery<
    LibraryAgentsPayload,
    Error,
    InfiniteData<LibraryAgentsPayload, number>,
    typeof libraryQueryKey,
    number
  >({
    queryKey: libraryQueryKey,
    queryFn: ({ signal, pageParam }) =>
      fetchLibraryAgents(listUrl, {
        signal,
        offset: pageParam,
        limit: PAGE_SIZE,
        category: selectedCategory,
        query: normalizedSearchQuery || null,
        officialOnly,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => (lastPage.hasMore ? lastPage.offset + lastPage.limit : undefined),
    initialData: initialLibraryData,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const likeMutation = useMutation({
    mutationFn: (agentId: string) => toggleLibraryAgentLike(likeUrl, agentId),
    onSuccess: (result) => {
      queryClient.setQueriesData<InfiniteData<LibraryAgentsPayload, number>>(
        { queryKey: ['library-agents', listUrl] },
        (payload) =>
          updateLikeInCachedPayload(payload, {
            agentId: result.agentId,
            likeCount: result.likeCount,
            isLiked: result.isLiked,
          }),
      )
      void queryClient.invalidateQueries({ queryKey: ['library-agents', listUrl] })
    },
  })

  const pages = useMemo(() => libraryQuery.data?.pages ?? [], [libraryQuery.data?.pages])
  const firstPage = pages[0]
  const agents = useMemo(() => pages.flatMap((page) => page.agents), [pages])
  const topCategories = useMemo(() => firstPage?.topCategories ?? [], [firstPage?.topCategories])
  const totalAgents = firstPage?.totalAgents ?? 0
  const libraryTotalAgents = firstPage?.libraryTotalAgents ?? totalAgents
  const officialTotalAgents = firstPage?.officialTotalAgents ?? (officialOnly ? totalAgents : 0)
  const libraryTotalLikes = firstPage?.libraryTotalLikes ?? 0
  const officialTotalLikes = firstPage?.officialTotalLikes ?? 0
  const hasMore = Boolean(libraryQuery.hasNextPage)
  const firstPageNumber = firstPage ? Math.floor(firstPage.offset / firstPage.limit) + 1 : 1
  const previousPageNumber = firstPageNumber > 1 ? firstPageNumber - 1 : null
  const lastPage = pages[pages.length - 1]
  const nextPageNumber = lastPage ? Math.floor(lastPage.offset / lastPage.limit) + 2 : firstPageNumber + 1
  const displayedAgentCount = selectedCategory || normalizedSearchQuery || officialOnly ? totalAgents : libraryTotalAgents
  const displayedLikeCount = officialOnly ? officialTotalLikes : libraryTotalLikes
  const mostPopularCount = officialOnly ? officialTotalAgents : libraryTotalAgents

  const categoryFilters = useMemo(() => {
    if (!selectedCategory || topCategories.some((category) => category.name === selectedCategory)) {
      return topCategories
    }
    return [{ name: selectedCategory, slug: selectedCategorySlug, count: totalAgents }, ...topCategories]
  }, [selectedCategory, selectedCategorySlug, topCategories, totalAgents])

  const previewAgents = useMemo(() => agents.slice(0, PREVIEW_SLOTS), [agents])
  const activeAgent = useMemo(() => {
    if (activePreviewId) {
      const hovered = agents.find((agent) => agent.id === activePreviewId)
      if (hovered) {
        return hovered
      }
    }
    return previewAgents[0] ?? null
  }, [activePreviewId, agents, previewAgents])

  // Auto-cycle the preview. Stops entirely under reduced motion / the perf
  // guard, and while the pointer or keyboard focus is inside the panel.
  const previewIdsKey = previewAgents.map((agent) => agent.id).join('|')
  useEffect(() => {
    if (reducedMotion || previewPaused || previewAgents.length < 2) {
      return
    }
    const ids = previewIdsKey.split('|')
    const intervalId = window.setInterval(() => {
      setActivePreviewId((current) => {
        const index = current ? ids.indexOf(current) : -1
        return ids[(index + 1) % ids.length]
      })
    }, PREVIEW_INTERVAL_MS)
    return () => window.clearInterval(intervalId)
  }, [reducedMotion, previewPaused, previewIdsKey, previewAgents.length])

  const handlePreview = useCallback((agentId: string) => {
    setActivePreviewId(agentId)
  }, [])

  const handleLike = useCallback(
    (agentId: string) => {
      likeMutation.mutate(agentId)
    },
    [likeMutation],
  )

  const isMostPopularSelected = selectedCategory === null
  const isSearchActive = normalizedSearchQuery.length > 0
  const hasFilters = isSearchActive || officialOnly || Boolean(selectedCategory)
  const emptyHeading = isSearchActive
    ? `No ${officialOnly ? 'official templates' : 'shared templates'} match "${normalizedSearchQuery}".`
    : officialOnly && selectedCategory
      ? 'No official templates found in this category.'
      : officialOnly
        ? 'No official templates found right now.'
        : selectedCategory
          ? 'No shared templates found in this category.'
          : 'No shared templates found right now.'
  const emptyDescription = isSearchActive
    ? 'Try another keyword or clear search.'
    : officialOnly
      ? 'Clear the official filter to browse community templates too.'
      : selectedCategory
        ? 'Try another category.'
        : 'Check back soon for newly shared templates.'
  const pageHeading = selectedCategory
    ? `${officialOnly ? 'Official ' : ''}${selectedCategory} AI Employee Templates`
    : officialOnly
      ? 'Official Gobii AI Employee Templates'
      : 'AI Employee Template Library'
  const pageDescription = selectedCategory
    ? officialOnly
      ? `Browse official ${selectedCategory} AI employee templates maintained by Gobii.`
      : `Browse publicly shared ${selectedCategory} AI employee templates.`
    : officialOnly
      ? 'Browse AI employee templates maintained by Gobii for common workflows.'
      : 'Choose a role from Gobii and the community, then customize the AI employee for your workflow.'

  const navigateToFilters = useCallback((filters: LibraryHistoryFilters) => {
    const nextUrl = buildLibraryUrl(filters.categorySlug, filters.officialOnly)
    const currentUrl = `${window.location.pathname}${window.location.search}`
    if (nextUrl !== currentUrl) {
      window.history.pushState(
        {
          ...(window.history.state ?? {}),
          [LIBRARY_HISTORY_STATE_KEY]: filters,
        },
        '',
        nextUrl,
      )
    }
    setSelectedCategory(filters.category)
    setSelectedCategorySlug(filters.categorySlug)
    setOfficialOnly(filters.officialOnly)
  }, [])

  const handleFilterClick = useCallback(
    (event: ReactMouseEvent<HTMLAnchorElement>, filters: LibraryHistoryFilters) => {
      if (!shouldNavigateInPlace(event)) {
        return
      }
      event.preventDefault()
      navigateToFilters(filters)
    },
    [navigateToFilters],
  )

  const clearFilters = () => {
    navigateToFilters({ category: null, categorySlug: '', officialOnly: false })
    setSearchQuery('')
    setDebouncedSearchQuery('')
  }

  const isRefreshing = libraryQuery.isFetching && !libraryQuery.isFetchingNextPage && !libraryQuery.isPending

  const header = (
    <>
      <div className="gklib-head">
        <div className="gklib-headmain">
          <p className="gk-eyebrow">Discover</p>
          <h1 className="gklib-h1">{pageHeading}</h1>
          <p className="gklib-lead">{pageDescription}</p>
          {!canLike ? <p className="gklib-signin">Sign in to like templates.</p> : null}
        </div>

        <div className="gklib-headside">
          <label className="gklib-sr" htmlFor="gklib-search-input">
            Search agents
          </label>
          <div className="gklib-search">
            <Search className="size-4" aria-hidden="true" />
            <input
              id="gklib-search-input"
              ref={searchInputRef}
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.currentTarget.value)}
              placeholder="Search agents, workflows, tools…"
              autoComplete="off"
            />
            <span className="gklib-kbd" aria-hidden="true">
              /
            </span>
          </div>
          <div className="gklib-stats">
            <span className="gklib-stat">
              {displayedAgentCount} {officialOnly ? 'official templates' : 'shared templates'}
            </span>
            <span className="gklib-stat">
              <Heart className="size-3.5" aria-hidden="true" />
              {displayedLikeCount} likes
            </span>
            {isRefreshing ? (
              <span className="gklib-stat is-busy">
                <Loader2 className="size-3.5 gklib-spin" aria-hidden="true" />
                Updating…
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <nav className="gklib-pills" aria-label="Filter templates">
        <a
          href={buildLibraryUrl('', officialOnly)}
          onClick={(event) => handleFilterClick(event, { category: null, categorySlug: '', officialOnly })}
          className={`gklib-pill${isMostPopularSelected ? ' is-on' : ''}`}
          aria-current={isMostPopularSelected ? 'page' : undefined}
          rel={officialOnly ? 'nofollow' : undefined}
        >
          {MOST_POPULAR_LABEL}
          <span className="gklib-n">{mostPopularCount}</span>
        </a>
        <a
          href={buildLibraryUrl(selectedCategorySlug, !officialOnly)}
          onClick={(event) =>
            handleFilterClick(event, {
              category: selectedCategory,
              categorySlug: selectedCategorySlug,
              officialOnly: !officialOnly,
            })
          }
          className={`gklib-pill gklib-pill-official${officialOnly ? ' is-on' : ''}`}
          aria-current={officialOnly ? 'true' : undefined}
          rel="nofollow"
        >
          <BadgeCheck className="size-3.5" aria-hidden="true" />
          Official
          <span className="gklib-n">{officialTotalAgents}</span>
        </a>
        {categoryFilters.map((category) => {
          const isActive = selectedCategory === category.name
          return (
            <a
              key={category.name}
              href={buildLibraryUrl(category.slug, officialOnly)}
              onClick={(event) =>
                handleFilterClick(event, {
                  category: category.name,
                  categorySlug: category.slug,
                  officialOnly,
                })
              }
              className={`gklib-pill${isActive ? ' is-on' : ''}`}
              aria-current={isActive ? 'page' : undefined}
              rel={officialOnly ? 'nofollow' : undefined}
            >
              {category.name}
              <span className="gklib-n">{category.count}</span>
            </a>
          )
        })}
      </nav>
    </>
  )

  if (libraryQuery.isPending) {
    return (
      <div className="gklib">
        {header}
        <p className="gklib-sr" role="status">
          Loading AI employee templates...
        </p>
        <SkeletonGrid />
      </div>
    )
  }

  if (libraryQuery.isError) {
    const errorMessage =
      libraryQuery.error instanceof Error ? libraryQuery.error.message : 'Unable to load the library right now.'
    return (
      <div className="gklib">
        {header}
        <div className="gklib-notice gklib-notice-error" role="alert">
          <AlertTriangle className="size-5" aria-hidden="true" />
          <div>
            <p className="gklib-noticehead">Library unavailable</p>
            <p>{errorMessage}</p>
            <button type="button" className="gklib-quiet" onClick={() => void libraryQuery.refetch()}>
              Try again
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="gklib">
      {header}

      <p className="gklib-sr" role="status" aria-live="polite">
        {agents.length} of {displayedAgentCount} templates shown
      </p>

      {agents.length === 0 ? (
        <div className="gklib-notice">
          <p className="gklib-noticehead">{emptyHeading}</p>
          <p>{emptyDescription}</p>
          {hasFilters ? (
            <button type="button" className="gklib-quiet" onClick={clearFilters}>
              Clear filters
            </button>
          ) : null}
        </div>
      ) : (
        <div className="gklib-split">
          <div className="gklib-main">
            <div className="gklib-grid">
              {agents.map((agent) => (
                <LibraryCard
                  key={agent.id}
                  agent={agent}
                  isSpotlit={Boolean(activeAgent && activeAgent.id === agent.id)}
                  canLike={canLike}
                  isLikePending={likeMutation.isPending && likeMutation.variables === agent.id}
                  onLike={handleLike}
                  onPreview={handlePreview}
                />
              ))}
            </div>

            {previousPageNumber || hasMore ? (
              <nav className="gklib-more" aria-label="Library pages">
                {previousPageNumber ? (
                  <a
                    className="gklib-quiet"
                    href={buildLibraryUrl(selectedCategorySlug, officialOnly, previousPageNumber)}
                    rel={officialOnly ? 'nofollow' : undefined}
                  >
                    Previous page
                  </a>
                ) : null}
                {hasMore ? (
                  <a
                    href={buildLibraryUrl(selectedCategorySlug, officialOnly, nextPageNumber)}
                    className="gklib-quiet"
                    rel={officialOnly ? 'nofollow' : undefined}
                    aria-disabled={libraryQuery.isFetchingNextPage}
                    onClick={(event) => {
                      if (!shouldNavigateInPlace(event)) {
                        return
                      }
                      event.preventDefault()
                      if (!libraryQuery.isFetchingNextPage) {
                        void libraryQuery.fetchNextPage()
                      }
                    }}
                  >
                    {libraryQuery.isFetchingNextPage ? (
                      <Loader2 className="size-4 gklib-spin" aria-hidden="true" />
                    ) : null}
                    {libraryQuery.isFetchingNextPage ? 'Loading more...' : 'Load more'}
                  </a>
                ) : null}
              </nav>
            ) : null}
          </div>

          {activeAgent ? (
            <Spotlight
              agents={previewAgents}
              activeAgent={activeAgent}
              reducedMotion={reducedMotion}
              onSelect={handlePreview}
              onPauseChange={setPreviewPaused}
            />
          ) : null}
        </div>
      )}
    </div>
  )
}
