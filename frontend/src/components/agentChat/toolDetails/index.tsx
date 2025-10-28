import { Fragment } from 'react'
import type { ReactNode } from 'react'

import { MarkdownViewer } from '../../common/MarkdownViewer'
import { StructuredDataTable } from '../../common/StructuredDataTable'
import { looksLikeHtml, sanitizeHtml } from '../../../util/sanitize'
import { describeSchedule } from '../../../util/schedule'
import type { ScheduleDescription } from '../../../util/schedule'
import type { ToolDetailComponent, ToolDetailProps } from '../tooling/types'
import { parseToolSearchResult } from '../tooling/searchUtils'
import { isRecord, parseResultObject } from '../../../util/objectUtils'

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function stringify(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

type ContactDetail = {
  channel: string | null
  address: string | null
  name: string | null
  reason: string | null
  purpose: string | null
}

function normalizeContact(value: unknown): ContactDetail | null {
  if (!isRecord(value)) return null
  const channelValue = value['channel']
  const addressValue = value['address']
  const nameValue = value['name']
  const reasonValue = value['reason']
  const purposeValue = value['purpose']
  const channel = typeof channelValue === 'string' && channelValue.trim().length ? channelValue : null
  const address = typeof addressValue === 'string' && addressValue.trim().length ? addressValue : null
  const name = typeof nameValue === 'string' && nameValue.trim().length ? nameValue : null
  const reason = typeof reasonValue === 'string' && reasonValue.trim().length ? reasonValue : null
  const purpose = typeof purposeValue === 'string' && purposeValue.trim().length ? purposeValue : null
  return { channel, address, name, reason, purpose }
}

function formatChannelLabel(channel: string | null): string | null {
  if (!channel) return null
  switch (channel.toLowerCase()) {
    case 'email':
      return 'Email'
    case 'sms':
      return 'SMS text'
    default:
      return channel
  }
}

type CredentialDetail = {
  name: string | null
  key: string | null
  domainPattern: string | null
  description: string | null
}

function normalizeCredential(value: unknown): CredentialDetail | null {
  if (!isRecord(value)) return null
  const nameValue = value['name']
  const keyValue = value['key']
  const domainValue = value['domain_pattern']
  const descriptionValue = value['description']
  const name = typeof nameValue === 'string' && nameValue.trim().length ? nameValue : null
  const key = typeof keyValue === 'string' && keyValue.trim().length ? keyValue : null
  const domainPattern = typeof domainValue === 'string' && domainValue.trim().length ? domainValue : null
  const description = typeof descriptionValue === 'string' && descriptionValue.trim().length ? descriptionValue : null
  return { name, key, domainPattern, description }
}

function extractFirstUrl(text: string | null | undefined): string | null {
  if (!text) return null
  const match = text.match(/https?:\/\/[^\s)]+/i)
  if (!match) return null
  return match[0].replace(/[.,!?]+$/, '')
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="tool-chip-panel-title">{title}</p>
      <div className="tool-chip-panel-body">{children}</div>
    </div>
  )
}

function KeyValueList({ items }: { items: Array<{ label: string; value: ReactNode } | null> }) {
  const filtered = items.filter(Boolean) as Array<{ label: string; value: ReactNode }>
  if (!filtered.length) return null
  return (
    <dl className="grid gap-2 text-sm text-slate-600 sm:grid-cols-[auto_minmax(0,1fr)]">
      {filtered.map(({ label, value }) => (
        <Fragment key={label}>
          <dt className="font-semibold text-slate-700 sm:text-right">{label}</dt>
          <dd className="text-slate-600 sm:pl-4">{value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

function tryParseJson(content: string): unknown | null {
  const trimmed = content.trim()
  if (!trimmed) return null
  if (trimmed.length < 2) return null
  const firstChar = trimmed[0]
  if (!['{', '['].includes(firstChar)) {
    return null
  }
  const expectedClosing = firstChar === '{' ? '}' : ']'
  if (!trimmed.endsWith(expectedClosing)) {
    return null
  }
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

type NormalizeContext = {
  depth: number
  maxDepth: number
  seen: WeakSet<object>
}

function createNormalizeContext(maxDepth = 6): NormalizeContext {
  return {
    depth: 0,
    maxDepth,
    seen: new WeakSet<object>(),
  }
}

function normalizeStructuredValue(value: unknown, context: NormalizeContext): unknown {
  if (value === null || value === undefined) {
    return value
  }

  if (typeof value === 'string') {
    if (context.depth >= context.maxDepth) {
      return value
    }
    const parsed = tryParseJson(value)
    if (parsed !== null) {
      return normalizeStructuredValue(parsed, { ...context, depth: context.depth + 1 })
    }
    return value
  }

  if (Array.isArray(value)) {
    if (context.seen.has(value)) {
      return value
    }
    context.seen.add(value)
    if (context.depth >= context.maxDepth) {
      return value
    }
    const nextDepth = context.depth + 1
    let mutated = false
    const normalized = value.map((item) => {
      const normalizedItem = normalizeStructuredValue(item, { ...context, depth: nextDepth })
      if (normalizedItem !== item) {
        mutated = true
      }
      return normalizedItem
    })
    return mutated ? normalized : value
  }

  if (isRecord(value)) {
    if (context.seen.has(value)) {
      return value
    }
    context.seen.add(value)
    if (context.depth >= context.maxDepth) {
      return value
    }
    const nextDepth = context.depth + 1
    let mutated = false
    const entries = Object.entries(value)
    const normalizedEntries: Array<[string, unknown]> = entries.map(([key, child]) => {
      const normalizedChild = normalizeStructuredValue(child, { ...context, depth: nextDepth })
      if (normalizedChild !== child) {
        mutated = true
      }
      return [key, normalizedChild]
    })
    if (!mutated) {
      return value
    }
    const normalizedObject: Record<string, unknown> = {}
    for (const [key, child] of normalizedEntries) {
      normalizedObject[key] = child
    }
    return normalizedObject
  }

  return value
}

export function GenericToolDetail({ entry }: ToolDetailProps) {
  const parameters =
    entry.parameters && typeof entry.parameters === 'object' && !Array.isArray(entry.parameters)
      ? (entry.parameters as Record<string, unknown>)
      : null
  const showParameters = Boolean(parameters && Object.keys(parameters).length > 0)
  const stringResult = typeof entry.result === 'string' ? entry.result.trim() : null
  const htmlResult = stringResult && looksLikeHtml(stringResult) ? sanitizeHtml(stringResult) : null
  const objectResult =
    entry.result && typeof entry.result === 'object'
      ? (entry.result as Record<string, unknown> | unknown[])
      : null
  const normalizedParameters = parameters ? (normalizeStructuredValue(parameters, createNormalizeContext()) as Record<string, unknown>) : null
  const parsedJsonResult = stringResult ? tryParseJson(stringResult) : null
  const structuredResult = objectResult ?? parsedJsonResult
  const normalizedStructuredResult =
    structuredResult !== null && structuredResult !== undefined
      ? normalizeStructuredValue(structuredResult, createNormalizeContext())
      : null
  const hasStructuredResult = normalizedStructuredResult !== null && normalizedStructuredResult !== undefined
  const showStringResult = stringResult && !parsedJsonResult
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          entry.toolName ? { label: 'Tool', value: entry.toolName } : null,
          entry.summary ? { label: 'Summary', value: entry.summary } : null,
        ]}
      />
      {showParameters ? (
        <Section title="Parameters">
          <StructuredDataTable value={normalizedParameters ?? parameters} />
        </Section>
      ) : null}
      {showStringResult ? (
        <Section title="Result">
          {htmlResult ? (
            <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: htmlResult }} />
          ) : (
            <MarkdownViewer content={stringResult} className="prose prose-sm max-w-none" />
          )}
        </Section>
      ) : null}
      {hasStructuredResult ? (
        <Section title="Result">
          <StructuredDataTable value={normalizedStructuredResult ?? structuredResult} />
        </Section>
      ) : null}
    </div>
  )
}

export function UpdateCharterDetail({ entry }: ToolDetailProps) {
  const charter = entry.charterText || (entry.parameters?.new_charter as string | undefined) || (entry.parameters?.charter as string | undefined)
  const summary = isNonEmptyString(entry.summary) ? entry.summary : null
  const charterMarkdown = isNonEmptyString(charter) ? charter : null
  return (
    <div className="space-y-4 text-sm text-slate-600">
      <p className="text-slate-700">{summary ?? 'The agent assignment was updated.'}</p>
      {charterMarkdown ? (
        <Section title="Updated Charter">
          <MarkdownViewer content={charterMarkdown} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}
    </div>
  )
}

export function SqliteBatchDetail({ entry }: ToolDetailProps) {
  const statements = entry.sqlStatements || (Array.isArray(entry.parameters?.operations) ? (entry.parameters?.operations as string[]) : null)
  const result = entry.result
  return (
    <div className="space-y-3 text-sm text-slate-600">
      {statements && statements.length ? (
        <Section title={`SQL ${statements.length === 1 ? 'Statement' : 'Statements'}`}>
          <div className="space-y-2">
            {statements.map((statement, idx) => (
              <div key={idx} className="overflow-auto rounded-xl bg-slate-900/95 p-3 shadow-inner">
                <pre className="text-xs text-emerald-100">
                  <code className="language-sql">{statement}</code>
                </pre>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
      {result ? (
        <Section title="Result">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">{stringify(result)}</pre>
        </Section>
      ) : null}
    </div>
  )
}

export function EnableDatabaseDetail({ entry }: ToolDetailProps) {
  const resultObject = parseResultObject(entry.result)
  const statusValue = resultObject?.['status']
  const messageValue = resultObject?.['message']
  const managerValue = resultObject?.['tool_manager']
  const detailsValue = resultObject?.['details']

  const status = typeof statusValue === 'string' && statusValue.trim().length ? statusValue : null
  const message = typeof messageValue === 'string' && messageValue.trim().length ? messageValue : null
  const manager = isRecord(managerValue) ? managerValue : null
  const details = isRecord(detailsValue) ? detailsValue : null

  const toStringList = (value: unknown): string[] => {
    if (!Array.isArray(value)) return []
    return (value as unknown[])
      .map((item) => (typeof item === 'string' && item.trim().length > 0 ? item : null))
      .filter((item): item is string => Boolean(item))
  }

  const enabledList = toStringList(manager?.['enabled'])
  const alreadyEnabledList = toStringList(manager?.['already_enabled'])
  const evictedList = toStringList(manager?.['evicted'])
  const invalidList = toStringList(manager?.['invalid'])

  const renderedMessage = message ?? entry.summary ?? 'sqlite_batch availability updated.'

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <p className="text-slate-700">{renderedMessage}</p>
      <KeyValueList
        items={[
          status ? { label: 'Status', value: status.toUpperCase() } : null,
          enabledList.length ? { label: 'Enabled', value: enabledList.join(', ') } : null,
          alreadyEnabledList.length ? { label: 'Already enabled', value: alreadyEnabledList.join(', ') } : null,
          evictedList.length ? { label: 'Evicted', value: evictedList.join(', ') } : null,
          invalidList.length ? { label: 'Invalid', value: invalidList.join(', ') } : null,
        ]}
      />
      {details ? (
        <Section title="Details">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
            {stringify(details)}
          </pre>
        </Section>
      ) : null}
    </div>
  )
}

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
    query ? { label: 'Query', value: <span className="tool-search-query-inline">“{query}”</span> } : null,
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
  const resultText =
    resultString && !looksLikeJson(resultString)
      ? resultString
      : null

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {calloutLines.length || calloutLists.length ? (
        <div className={`tool-search-callout tool-search-callout--${calloutVariant}`}>
          <span className="tool-search-callout-icon" aria-hidden="true">
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
          <div className="tool-search-callout-content">
            {calloutLines.length ? (
              <div className="tool-search-callout-body">
                {calloutLines.map((line, idx) => (
                  <p key={idx}>{line}</p>
                ))}
              </div>
            ) : null}
            {calloutLists.length ? (
              <div className="tool-search-callout-list">
                {calloutLists.map((group) => (
                  <div key={group.label} className="tool-search-callout-list-group">
                    <span className="tool-search-callout-list-label">{group.label}</span>
                    <span className="tool-search-callout-list-items">{group.items.join(', ')}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {toolSuggestions.length ? (
        <Section title="Suggested tools">
          <ul className="tool-search-suggestion-list">
            {toolSuggestions.map((tool, idx) => (
              <li key={`${tool.name}-${idx}`} className="tool-search-suggestion-card">
                <div className="tool-search-suggestion-header">
                  <span className="tool-search-suggestion-name">{tool.name}</span>
                  {tool.source ? <span className="tool-search-suggestion-source">{tool.source}</span> : null}
                </div>
                {tool.description ? <p className="tool-search-suggestion-description">{tool.description}</p> : null}
                {tool.note ? <p className="tool-search-suggestion-note">{tool.note}</p> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {summaryGroups.map((group) => (
        <Section key={group.title} title={group.title}>
          <ul className="tool-search-list">
            {group.items.map((item, idx) => (
              <li key={`${group.title}-${item}-${idx}`}>{item}</li>
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

export function ApiRequestDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const method = (params.method as string) || 'GET'
  const url = (params.url as string) || (params.endpoint as string) || null
  const headers = params.headers
  const body = params.body ?? params.payload
  const response = entry.result
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          url ? { label: 'URL', value: url } : null,
          method ? { label: 'Method', value: method.toUpperCase() } : null,
        ]}
      />
      {headers && Object.keys(headers as Record<string, unknown>).length ? (
        <Section title="Headers">
          <pre className="max-h-48 overflow-auto rounded-xl bg-slate-900/95 p-3 text-xs text-slate-100 shadow-inner">{stringify(headers)}</pre>
        </Section>
      ) : null}
      {body ? (
        <Section title="Request Body">
          <pre className="max-h-48 overflow-auto rounded-xl bg-slate-900/95 p-3 text-xs text-slate-100 shadow-inner">{stringify(body)}</pre>
        </Section>
      ) : null}
      {response ? (
        <Section title="Response">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">{stringify(response)}</pre>
        </Section>
      ) : null}
    </div>
  )
}

export function FileReadDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const path = (params.path as string) || (params.file_path as string) || (params.filename as string) || null
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={[path ? { label: 'Path', value: path } : null]} />
      {entry.result ? (
        <Section title="Contents">
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-900/95 p-3 text-xs text-slate-100 shadow-inner">{stringify(entry.result)}</pre>
        </Section>
      ) : null}
    </div>
  )
}

export function FileWriteDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const path = (params.path as string) || (params.file_path as string) || (params.filename as string) || null
  const diff = params.diff || params.patch
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={[path ? { label: 'Path', value: path } : null]} />
      {diff ? (
        <Section title="Changes">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-900/95 p-3 text-xs text-emerald-100 shadow-inner">{stringify(diff)}</pre>
        </Section>
      ) : null}
      {entry.result ? (
        <Section title="Result">
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">{stringify(entry.result)}</pre>
        </Section>
      ) : null}
    </div>
  )
}

export function BrowserTaskDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  let prompt = (params.prompt as string) || null
  // Remove "Task:" prefix if present
  if (prompt?.toLowerCase().startsWith('task:')) {
    prompt = prompt.slice(5).trim()
  }
  const url = (params.url as string) || (params.start_url as string) || null

  // Parse result if it's a JSON string
  let resultData = entry.result
  if (typeof resultData === 'string') {
    try {
      resultData = JSON.parse(resultData)
    } catch {
      // Keep as string if not valid JSON
    }
  }

  const status = typeof resultData === 'object' && resultData !== null
    ? (resultData as Record<string, unknown>).status as string || null
    : null
  const taskId = typeof resultData === 'object' && resultData !== null
    ? (resultData as Record<string, unknown>).task_id as string || null
    : null

  const statusLabel = status === 'pending' ? 'Running in background'
    : status === 'completed' ? 'Completed'
    : status === 'failed' ? 'Failed'
    : status ? status.charAt(0).toUpperCase() + status.slice(1)
    : null

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {prompt ? (
        <Section title="Task">
          <MarkdownViewer content={prompt} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}
      <KeyValueList items={[
        statusLabel ? { label: 'Status', value: statusLabel } : null,
        url ? { label: 'Starting URL', value: url } : null,
      ]} />
      {taskId ? (
        <p className="text-xs text-slate-500">Task ID: {taskId}</p>
      ) : null}
    </div>
  )
}

export function RequestContactPermissionDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const contactsRaw = params['contacts']
  const contacts = Array.isArray(contactsRaw)
    ? (contactsRaw.map(normalizeContact).filter(Boolean) as ContactDetail[])
    : []

  const result = parseResultObject(entry.result)
  const statusValue = typeof result?.['status'] === 'string' ? (result['status'] as string) : null
  const messageValue = typeof result?.['message'] === 'string' ? (result['message'] as string) : null
  const createdCount = typeof result?.['created_count'] === 'number' ? (result['created_count'] as number) : null
  const alreadyAllowed = typeof result?.['already_allowed_count'] === 'number' ? (result['already_allowed_count'] as number) : null
  const alreadyPending = typeof result?.['already_pending_count'] === 'number' ? (result['already_pending_count'] as number) : null
  const approvalRaw = typeof result?.['approval_url'] === 'string' ? (result['approval_url'] as string) : null
  const approvalUrl = approvalRaw && /^https?:\/\//i.test(approvalRaw) ? approvalRaw : null
  const statusLabel = statusValue ? statusValue.toUpperCase() : null
  const messageText = isNonEmptyString(messageValue) ? messageValue : entry.summary || entry.caption || null

  const infoItems: Array<{ label: string; value: ReactNode } | null> = [
    statusLabel ? { label: 'Status', value: statusLabel } : null,
    createdCount !== null ? { label: 'Created requests', value: createdCount } : null,
    alreadyAllowed !== null ? { label: 'Already allowed', value: alreadyAllowed } : null,
    alreadyPending !== null ? { label: 'Already pending', value: alreadyPending } : null,
    approvalRaw
      ? {
          label: 'Approval link',
          value: approvalUrl ? (
            <a href={approvalUrl} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline">
              {approvalRaw}
            </a>
          ) : (
            approvalRaw
          ),
        }
      : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      {messageText ? <p className="whitespace-pre-line text-slate-700">{messageText}</p> : null}
      <KeyValueList items={infoItems} />
      {contacts.length ? (
        <Section title={`Contact request${contacts.length === 1 ? '' : 's'}`}>
          <ol className="space-y-3">
            {contacts.map((contact, index) => {
              const channelLabel = formatChannelLabel(contact.channel)
              const heading = contact.name || contact.address || `Contact ${index + 1}`
              const contactItems: Array<{ label: string; value: ReactNode } | null> = [
                channelLabel ? { label: 'Channel', value: channelLabel } : null,
                contact.address && contact.address !== heading ? { label: 'Address', value: contact.address } : null,
                contact.purpose ? { label: 'Purpose', value: contact.purpose } : null,
                contact.reason
                  ? {
                      label: 'Reason',
                      value: <span className="whitespace-pre-line">{contact.reason}</span>,
                    }
                  : null,
              ]
              return (
                <li key={`contact-${index}`} className="rounded-lg border border-slate-200/80 bg-white/90 p-3 shadow-sm">
                  <p className="font-semibold text-slate-800">{heading}</p>
                  <KeyValueList items={contactItems} />
                </li>
              )
            })}
          </ol>
        </Section>
      ) : null}
    </div>
  )
}

export function SecureCredentialsDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const credentialsRaw = params['credentials']
  const credentials = Array.isArray(credentialsRaw)
    ? (credentialsRaw.map(normalizeCredential).filter(Boolean) as CredentialDetail[])
    : []

  const result = parseResultObject(entry.result)
  const messageValue = typeof result?.['message'] === 'string' ? (result['message'] as string) : null
  const createdCount = typeof result?.['created_count'] === 'number' ? (result['created_count'] as number) : null
  const errorsRaw = Array.isArray(result?.['errors']) ? (result?.['errors'] as unknown[]) : []
  const errors = errorsRaw
    .map((error) => (typeof error === 'string' ? error : stringify(error)))
    .filter((value): value is string => Boolean(value && value.trim()))
  const messageText = isNonEmptyString(messageValue) ? messageValue : entry.summary || entry.caption || null
  const submissionUrl = extractFirstUrl(messageText)

  const infoItems: Array<{ label: string; value: ReactNode } | null> = [
    createdCount !== null ? { label: 'Created requests', value: createdCount } : null,
    submissionUrl
      ? {
          label: 'Submission link',
          value: (
            <a href={submissionUrl} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline">
              {submissionUrl}
            </a>
          ),
        }
      : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {errors.length ? (
        <Section title="Errors">
          <ul className="list-disc space-y-1 pl-5 text-sm text-rose-600">
            {errors.map((error, index) => (
              <li key={`error-${index}`}>{error}</li>
            ))}
          </ul>
        </Section>
      ) : null}
      {credentials.length ? (
        <Section title={`Credential${credentials.length === 1 ? '' : 's'} requested`}>
          <ol className="space-y-3">
            {credentials.map((credential, index) => {
              const credentialItems: Array<{ label: string; value: ReactNode } | null> = [
                credential.key ? { label: 'Key', value: credential.key } : null,
                credential.domainPattern ? { label: 'Domain', value: credential.domainPattern } : null,
                credential.description
                  ? {
                      label: 'Description',
                      value: <span className="whitespace-pre-line">{credential.description}</span>,
                    }
                  : null,
              ]
              return (
                <li key={`credential-${index}`} className="rounded-lg border border-slate-200/80 bg-white/90 p-3 shadow-sm">
                  <KeyValueList items={credentialItems} />
                </li>
              )
            })}
          </ol>
        </Section>
      ) : null}
    </div>
  )
}

export function AnalysisToolDetail({ entry }: ToolDetailProps) {
  const content = isNonEmptyString(entry.result) ? entry.result : entry.summary || entry.caption || null
  return (
    <div className="space-y-3 text-sm text-slate-600">
      {content ? (
        <div className="whitespace-pre-wrap text-sm text-slate-700">{content}</div>
      ) : (
        <p>No analysis output was captured.</p>
      )}
    </div>
  )
}

export function BrightDataSnapshotDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const urlValue = params['url'] || params['start_url']
  const targetUrl = typeof urlValue === 'string' ? urlValue : null
  const titleValue = params['title'] || params['page_title']
  const pageTitle = typeof titleValue === 'string' ? titleValue : entry.summary || null
  const markdownValue = params['markdown']
  const markdown = typeof markdownValue === 'string' && markdownValue.trim().length > 0 ? markdownValue : null
  const htmlValue = params['html']
  const htmlSnapshot = typeof htmlValue === 'string' && htmlValue.trim().length > 0 ? htmlValue : null
  const screenshotValue = params['screenshot_url'] || params['screenshot']
  const screenshotUrl = typeof screenshotValue === 'string' ? screenshotValue : null
  const contentFromResult =
    !markdown && !htmlSnapshot && typeof entry.result === 'string' ? entry.result : null
  const contentMarkdown = markdown || (contentFromResult && !looksLikeHtml(contentFromResult) ? contentFromResult : null)
  const contentHtml = htmlSnapshot || (contentFromResult && looksLikeHtml(contentFromResult) ? contentFromResult : null)
  const sanitizedHtml = contentHtml ? sanitizeHtml(contentHtml) : null

  const infoItems: Array<{ label: string; value: ReactNode }> = []
  if (pageTitle) {
    infoItems.push({ label: 'Page title', value: pageTitle })
  }
  if (targetUrl) {
    infoItems.push({
      label: 'URL',
      value: (
        <a href={targetUrl} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline">
          {targetUrl}
        </a>
      ),
    })
  }

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {infoItems.length ? <KeyValueList items={infoItems} /> : null}
      {screenshotUrl ? (
        <Section title="Screenshot">
          <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm">
            <img src={screenshotUrl} alt={pageTitle ? `Snapshot of ${pageTitle}` : 'Page snapshot'} className="w-full" />
          </div>
        </Section>
      ) : null}
      {contentMarkdown ? (
        <Section title="Snapshot">
          <MarkdownViewer content={contentMarkdown} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}
      {sanitizedHtml ? (
        <Section title={contentMarkdown ? 'Raw HTML' : 'Snapshot'}>
          <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: sanitizedHtml }} />
        </Section>
      ) : null}
    </div>
  )
}

export function UpdateScheduleDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const newScheduleValue = params['new_schedule']
  const newScheduleRaw = typeof newScheduleValue === 'string' ? newScheduleValue.trim() : null
  const scheduleValue = newScheduleRaw && newScheduleRaw.length > 0 ? newScheduleRaw : null
  const resultObject =
    entry.result && typeof entry.result === 'object'
      ? (entry.result as { status?: string; message?: string })
      : null
  const statusLabel = resultObject?.status ? resultObject.status.toUpperCase() : null
  const messageText =
    resultObject?.message || entry.summary || (scheduleValue ? 'Schedule updated successfully.' : 'Schedule disabled.')
  const scheduleDetails = describeSchedule(scheduleValue)
  const detailItems: Array<{ label: string; value: ReactNode }> = []
  if (statusLabel) {
    detailItems.push({ label: 'Status', value: statusLabel })
  }
  return (
    <div className="space-y-4 text-sm text-slate-600">
      <p className="text-slate-700">{messageText}</p>
      <KeyValueList items={detailItems} />
      {renderScheduleDetails(scheduleDetails)}
    </div>
  )
}

export const TOOL_DETAIL_COMPONENTS: Record<string, ToolDetailComponent> = {
  default: GenericToolDetail,
  updateCharter: UpdateCharterDetail,
  sqliteBatch: SqliteBatchDetail,
  enableDatabase: EnableDatabaseDetail,
  search: SearchToolDetail,
  apiRequest: ApiRequestDetail,
  fileRead: FileReadDetail,
  fileWrite: FileWriteDetail,
  browserTask: BrowserTaskDetail,
  contactPermission: RequestContactPermissionDetail,
  secureCredentials: SecureCredentialsDetail,
  analysis: AnalysisToolDetail,
  updateSchedule: UpdateScheduleDetail,
  brightDataSnapshot: BrightDataSnapshotDetail,
}

export function resolveDetailComponent(kind: string | null | undefined): ToolDetailComponent {
  if (!kind) return GenericToolDetail
  return TOOL_DETAIL_COMPONENTS[kind] ?? GenericToolDetail
}

function formatSummaryText(summary: string): string {
  return /[.!?]\s*$/.test(summary) ? summary : `${summary}.`
}

function renderScheduleDetails(schedule: ScheduleDescription): ReactNode {
  switch (schedule.kind) {
    case 'disabled':
      return (
        <Section title="Schedule">
          <p className="text-slate-700">No automated runs are scheduled.</p>
        </Section>
      )
    case 'preset':
      return (
        <Section title="Preset Interval">
          <div className="schedule-card">
            <span className="schedule-card-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10m-12 8h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </span>
            <div>
              <p className="schedule-card-label">{schedule.raw}</p>
              <p className="schedule-card-description">{schedule.description}</p>
            </div>
          </div>
        </Section>
      )
    case 'interval':
      return (
        <Section title="Repeats Every">
          <div className="schedule-interval">
            {schedule.parts.map((part, index) => (
              <span key={`${part.unit}-${index}`} className="schedule-pill">
                <span className="schedule-pill-value">{part.magnitude}</span>
                <span className="schedule-pill-unit">{part.label.replace(/^[0-9]+\s/, '')}</span>
              </span>
            ))}
          </div>
          <p className="schedule-note">{formatSummaryText(schedule.summary)}</p>
        </Section>
      )
    case 'cron':
      return (
        <Section title="Cron Fields">
          {schedule.summary ? <p className="schedule-note">{formatSummaryText(schedule.summary)}</p> : null}
          <dl className="schedule-cron-grid">
            {schedule.fields.map((field) => (
              <Fragment key={field.label}>
                <dt>{field.label}</dt>
                <dd>
                  <code>{field.value}</code>
                </dd>
              </Fragment>
            ))}
          </dl>
          {!schedule.summary ? (
            <p className="schedule-note">Standard cron expression with {schedule.fields.length} field(s).</p>
          ) : null}
        </Section>
      )
    case 'unknown':
      return (
        <Section title="Schedule">
          <p className="schedule-note">
            Unable to parse schedule format. Raw value: <code>{schedule.raw}</code>
          </p>
        </Section>
      )
    default:
      return null
  }
}
