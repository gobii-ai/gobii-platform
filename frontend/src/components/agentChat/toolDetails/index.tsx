import { Fragment } from 'react'
import type { ReactNode } from 'react'

import { MarkdownViewer } from '../../common/MarkdownViewer'
import { looksLikeHtml, sanitizeHtml } from '../../../util/sanitize'
import type { ToolDetailComponent, ToolDetailProps } from '../tooling/types'

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function stringify(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch (error) {
    return String(value)
  }
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
          <pre className="max-h-56 overflow-auto rounded-xl bg-slate-900/95 p-3 text-xs text-slate-100 shadow-inner">
            {stringify(parameters)}
          </pre>
        </Section>
      ) : null}
      {stringResult ? (
        <Section title="Result">
          {htmlResult ? (
            <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: htmlResult }} />
          ) : (
            <MarkdownViewer content={stringResult} className="prose prose-sm max-w-none" />
          )}
        </Section>
      ) : null}
      {!stringResult && objectResult ? (
        <Section title="Result">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
            {stringify(objectResult)}
          </pre>
        </Section>
      ) : null}
    </div>
  )
}

export function UpdateCharterDetail({ entry }: ToolDetailProps) {
  const charter = entry.charterText || (entry.parameters?.new_charter as string | undefined) || (entry.parameters?.charter as string | undefined)
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <p>The agent assignment was updated.</p>
      {isNonEmptyString(charter) ? (
        <Section title="New Charter">
          <div className="whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-slate-700 shadow-inner">{charter}</div>
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

export function SearchToolDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const query = (params.query as string) || ''
  const topResults = Array.isArray(params.results) ? (params.results as Array<Record<string, unknown>>) : null
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          isNonEmptyString(query) ? { label: 'Query', value: `“${query}”` } : null,
          params.site ? { label: 'Site', value: String(params.site) } : null,
          params.language ? { label: 'Language', value: String(params.language) } : null,
        ]}
      />
      {topResults && topResults.length ? (
        <Section title="Top Results">
          <ol className="space-y-2">
            {topResults.slice(0, 5).map((result, idx) => {
              const title = (result.title as string) || `Result ${idx + 1}`
              const url = result.url as string | undefined
              const snippet = result.snippet as string | undefined
              return (
                <li key={idx} className="rounded-lg border border-slate-200/70 bg-white/90 p-3">
                  <p className="font-semibold text-slate-800">{title}</p>
                  {url ? (
                    <p className="text-xs text-indigo-600" title={url}>
                      {url}
                    </p>
                  ) : null}
                  {snippet ? <p className="mt-1 text-xs text-slate-600">{snippet}</p> : null}
                </li>
              )
            })}
          </ol>
        </Section>
      ) : null}
      {entry.result && !topResults ? (
        <Section title="Summary">
          <div className="whitespace-pre-wrap text-sm text-slate-700">{stringify(entry.result)}</div>
        </Section>
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
  const url = (params.url as string) || (params.start_url as string) || null
  const status = params.status || entry.result
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={[url ? { label: 'URL', value: url } : null]} />
      {status ? (
        <Section title="Outcome">
          <div className="whitespace-pre-wrap text-sm text-slate-700">{stringify(status)}</div>
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
  const detailItems: Array<{ label: string; value: ReactNode }> = []
  if (statusLabel) {
    detailItems.push({ label: 'Status', value: statusLabel })
  }
  detailItems.push({
    label: 'Schedule',
    value: scheduleValue ? <code className="rounded bg-slate-100 px-1 py-0.5">{scheduleValue}</code> : 'Disabled',
  })
  return (
    <div className="space-y-3 text-sm text-slate-600">
      <p>{messageText}</p>
      <KeyValueList items={detailItems} />
    </div>
  )
}

export const TOOL_DETAIL_COMPONENTS: Record<string, ToolDetailComponent> = {
  default: GenericToolDetail,
  updateCharter: UpdateCharterDetail,
  sqliteBatch: SqliteBatchDetail,
  search: SearchToolDetail,
  apiRequest: ApiRequestDetail,
  fileRead: FileReadDetail,
  fileWrite: FileWriteDetail,
  browserTask: BrowserTaskDetail,
  analysis: AnalysisToolDetail,
  updateSchedule: UpdateScheduleDetail,
  brightDataSnapshot: BrightDataSnapshotDetail,
}

export function resolveDetailComponent(kind: string | null | undefined): ToolDetailComponent {
  if (!kind) return GenericToolDetail
  return TOOL_DETAIL_COMPONENTS[kind] ?? GenericToolDetail
}
