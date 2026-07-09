import { ExternalLink, Globe, Database, Code } from 'lucide-react'

import type { ToolDetailProps } from '../../tooling/types'
import { parseToolSearchResult } from '../../tooling/searchUtils'
import { toFriendlyToolName } from '../../../tooling/toolMetadata'
import { KeyValueList, Section } from '../shared'
import { isNonEmptyString } from '../utils'

function looksLikeJson(value: string): boolean {
  const trimmed = value.trim()
  return (trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))
}

function toSentenceCase(value: string | null): string | null {
  if (!value) return null
  if (!value.length) return null
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function formatList(items: string[]): string {
  if (items.length === 0) return ''
  if (items.length === 1) return items[0]
  if (items.length === 2) return `${items[0]} and ${items[1]}`
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`
}

function splitMessage(value: string | null): string[] {
  if (!value) return []
  return value
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

function determineCalloutVariant(status: string | null, toolCount: number | null): 'success' | 'info' | 'error' {
  if (!status) {
    return toolCount === 0 ? 'info' : 'success'
  }
  const normalized = status.toLowerCase()
  if (normalized.includes('error') || normalized.includes('fail')) {
    return 'error'
  }
  if (toolCount === 0) {
    return 'info'
  }
  return 'success'
}

const queryInlineClassName = 'font-normal text-[#334155]'
const calloutBaseClassName = 'flex items-center gap-[0.85rem] rounded-2xl border px-[1.1rem] py-4'
const calloutVariantClassNames = {
  success: 'border-[rgba(34,197,94,0.3)] bg-[rgba(220,252,231,0.85)]',
  info: 'border-[rgba(59,130,246,0.28)] bg-[rgba(219,234,254,0.85)]',
  error: 'border-[rgba(239,68,68,0.4)] bg-[rgba(254,226,226,0.9)]',
}
const calloutIconBaseClassName = 'grid h-[2.1rem] w-[2.1rem] shrink-0 place-items-center rounded-full'
const calloutIconVariantClassNames = {
  success: 'bg-[rgba(22,163,74,0.18)] text-[#047857]',
  info: 'bg-[rgba(59,130,246,0.18)] text-[#1d4ed8]',
  error: 'bg-[rgba(239,68,68,0.15)] text-[#b91c1c]',
}
const calloutContentClassName = 'flex min-w-0 flex-col gap-1'
const calloutBodyClassName = 'grid gap-[0.3rem] leading-[1.45] text-[#334155]'
const calloutListClassName = 'mt-[0.2rem] flex flex-col gap-[0.4rem]'
const calloutListGroupClassName = 'flex flex-wrap gap-[0.35rem] text-[0.78rem] text-[#1f2937]'
const calloutListLabelClassName = 'font-semibold text-[#1f2937]'
const calloutListItemsClassName = 'text-[#334155]'
const suggestionListClassName = 'm-0 grid list-none gap-[0.9rem] p-0'
const suggestionCardClassName = 'rounded-[0.85rem] border border-[rgba(203,213,225,0.5)] bg-[rgba(255,255,255,0.9)] px-4 py-[0.9rem]'
const suggestionHeaderClassName = 'mb-[0.35rem] flex items-center justify-between gap-2'
const suggestionNameClassName = 'font-semibold text-[#0f172a]'
const suggestionSourceClassName = 'rounded-full bg-[rgba(59,130,246,0.12)] px-[0.45rem] py-[0.1rem] text-[0.65rem] font-semibold uppercase tracking-[0.02em] text-[#1e3a8a]'
const suggestionDescriptionClassName = 'text-[0.78rem] leading-[1.45] text-[#475569]'
const suggestionNoteClassName = 'mt-[0.35rem] text-[0.72rem] text-[#64748b]'
const summaryListClassName = 'm-0 flex list-none flex-col gap-[0.35rem] p-0'
const summaryListItemClassName = "relative pl-[1.1rem] text-[0.78rem] text-[#475569] before:absolute before:left-0 before:top-[0.45rem] before:h-[0.35rem] before:w-[0.35rem] before:rounded-full before:bg-[rgba(99,102,241,0.4)] before:content-['']"

export function SearchToolDetail({ entry }: ToolDetailProps) {
  const params =
    entry.parameters && typeof entry.parameters === 'object'
      ? (entry.parameters as Record<string, unknown>)
      : {}
  const queryValue = isNonEmptyString(params.query) ? (params.query as string).trim() : null
  const query = queryValue && queryValue.length ? queryValue : null
  const site = isNonEmptyString(params.site) ? (params.site as string).trim() : null
  const language = isNonEmptyString(params.language) ? (params.language as string).trim() : null
  const topResults = Array.isArray(params.results) ? (params.results as Array<Record<string, unknown>>) : null

  const outcome = parseToolSearchResult(entry.result)
  const statusLabel = toSentenceCase(outcome.status?.toLowerCase() ?? null)
  const calloutVariant = determineCalloutVariant(outcome.status, outcome.toolCount)

  const infoItems = [
    query ? { label: 'Query', value: <span className={queryInlineClassName}>“{query}”</span> } : null,
    site ? { label: 'Site', value: site } : null,
    language ? { label: 'Language', value: language } : null,
    statusLabel ? { label: 'Status', value: statusLabel } : null,
    outcome.toolCount !== null
      ? { label: 'Matches', value: outcome.toolCount === 0 ? 'None' : String(outcome.toolCount) }
      : null,
  ]

  const messageLines = splitMessage(outcome.message)
  const fallbackSummary =
    !messageLines.length && isNonEmptyString(entry.summary) ? splitMessage(entry.summary) : []
  const combinedMessage = messageLines.length ? messageLines : fallbackSummary

  const derivedMessage: string[] = []
  if (!combinedMessage.length) {
    if (calloutVariant === 'error') {
      derivedMessage.push('The tool search ran into a problem. Please try again in a moment.')
    } else if (outcome.toolCount === 0) {
      derivedMessage.push('No tools matched this search yet. Try a different phrase or broaden your query.')
    } else if (outcome.enabledTools.length) {
      derivedMessage.push(`Enabled ${formatList(outcome.enabledTools)} for this agent.`)
    } else if (outcome.toolCount && outcome.toolCount > 0) {
      derivedMessage.push('Found tools that fit this request.')
    }
  }

  const suppressedGroupTitles = new Set<string>()

  const calloutLists: Array<{ label: string; items: string[] }> = []
  let calloutLines = combinedMessage.length ? [...combinedMessage] : [...derivedMessage]

  if (combinedMessage.length) {
    calloutLines = calloutLines.filter((line) => {
      const trimmed = line.trim()
      if (outcome.enabledTools.length && /^enabled:/i.test(trimmed)) {
        calloutLists.push({ label: 'Enabled', items: outcome.enabledTools })
        suppressedGroupTitles.add('Now enabled')
        return false
      }
      if (outcome.alreadyEnabledTools.length && /^already enabled:/i.test(trimmed)) {
        calloutLists.push({ label: 'Already enabled', items: outcome.alreadyEnabledTools })
        suppressedGroupTitles.add('Already enabled')
        return false
      }
      return true
    })
  }

  const summaryGroups = [
    { title: 'Now enabled', items: outcome.enabledTools },
    { title: 'Already enabled', items: outcome.alreadyEnabledTools },
    { title: 'Not available', items: outcome.invalidTools },
    { title: 'Replaced to stay within limits', items: outcome.evictedTools },
  ].filter((group) => group.items.length && !suppressedGroupTitles.has(group.title))

  const toolSuggestions = outcome.tools

  const resultString = typeof entry.result === 'string' ? entry.result.trim() : null
  const resultText = resultString && !looksLikeJson(resultString) ? resultString : null

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {calloutLines.length || calloutLists.length ? (
        <div className={`${calloutBaseClassName} ${calloutVariantClassNames[calloutVariant]}`} data-variant={calloutVariant}>
          <span className={`${calloutIconBaseClassName} ${calloutIconVariantClassNames[calloutVariant]}`} aria-hidden="true">
            {calloutVariant === 'error' ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            ) : calloutVariant === 'success' ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9 9 0 100-18 9 9 0 000 18z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8h.01" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9 9 0 100-18 9 9 0 000 18z" />
              </svg>
            )}
          </span>
          <div className={calloutContentClassName}>
            {calloutLines.length ? (
              <div className={calloutBodyClassName}>
                {calloutLines.map((line, idx) => (
                  <p key={idx} className="m-0">{line}</p>
                ))}
              </div>
            ) : null}
            {calloutLists.length ? (
              <div className={calloutListClassName}>
                {calloutLists.map((group) => (
                  <div key={group.label} className={calloutListGroupClassName}>
                    <span className={calloutListLabelClassName}>{group.label}</span>
                    <span className={calloutListItemsClassName}>{group.items.map(toFriendlyToolName).join(', ')}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {toolSuggestions.length ? (
        <Section title="Suggested tools">
          <ul className={suggestionListClassName}>
            {toolSuggestions.map((tool, idx) => (
              <li key={`${tool.name}-${idx}`} className={suggestionCardClassName}>
                <div className={suggestionHeaderClassName}>
                  <span className={suggestionNameClassName}>{toFriendlyToolName(tool.name)}</span>
                  {tool.source ? <span className={suggestionSourceClassName}>{tool.source}</span> : null}
                </div>
                {tool.description ? <p className={suggestionDescriptionClassName}>{tool.description}</p> : null}
                {tool.note ? <p className={suggestionNoteClassName}>{tool.note}</p> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {outcome.externalResources.length ? (
        <Section title="Public resources">
          <ul className="external-resources-list">
            {outcome.externalResources.map((resource, idx) => {
              const isApi = resource.url.includes('/api') || resource.name.toLowerCase().includes('api')
              const isData = resource.name.toLowerCase().includes('data') || resource.name.toLowerCase().includes('dataset')
              const ResourceIcon = isApi ? Code : isData ? Database : Globe
              return (
                <li key={`${resource.name}-${idx}`} className="external-resource-card">
                  <div className="external-resource-icon">
                    <ResourceIcon className="h-4 w-4" />
                  </div>
                  <div className="external-resource-content">
                    <div className="external-resource-header">
                      <span className="external-resource-name">{resource.name}</span>
                      <a
                        href={resource.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="external-resource-link"
                        title="Open in new tab"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </div>
                    {resource.description ? (
                      <p className="external-resource-description">{resource.description}</p>
                    ) : null}
                    <a
                      href={resource.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="external-resource-url"
                    >
                      {resource.url.replace(/^https?:\/\/(www\.)?/, '').slice(0, 60)}
                      {resource.url.length > 68 ? '…' : ''}
                    </a>
                  </div>
                </li>
              )
            })}
          </ul>
        </Section>
      ) : null}

      {summaryGroups.map((group) => (
        <Section key={group.title} title={group.title}>
          <ul className={summaryListClassName}>
            {group.items.map((item, idx) => (
              <li key={`${group.title}-${item}-${idx}`} className={summaryListItemClassName}>{toFriendlyToolName(item)}</li>
            ))}
          </ul>
        </Section>
      ))}

      {topResults && topResults.length ? (
        <Section title="Top results">
          <ol className="space-y-2">
            {topResults.slice(0, 5).map((result, idx) => {
              const title = (result.title as string) || `Result ${idx + 1}`
              const url = result.url as string | undefined
              const snippet = result.snippet as string | undefined
              return (
                <li key={idx} className="tool-search-result-card">
                  <p className="tool-search-result-title">{title}</p>
                  {url ? (
                    <a href={url} target="_blank" rel="noopener noreferrer" className="tool-search-result-link">
                      {url}
                    </a>
                  ) : null}
                  {snippet ? <p className="tool-search-result-snippet">{snippet}</p> : null}
                </li>
              )
            })}
          </ol>
        </Section>
      ) : null}

      {resultText ? (
        <Section title="Summary">
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{resultText}</div>
        </Section>
      ) : null}

      {!calloutLines.length && !toolSuggestions.length && !summaryGroups.length && (!topResults || !topResults.length) && !resultText ? (
        <p className="text-sm text-slate-500">No additional details were provided for this search.</p>
      ) : null}
    </div>
  )
}
