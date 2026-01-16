import type { ReactNode } from 'react'

import { MarkdownViewer } from '../../../common/MarkdownViewer'
import { looksLikeHtml, sanitizeHtml } from '../../../../util/sanitize'
import type { ToolDetailProps } from '../../tooling/types'
import { extractBrightDataResultCount, extractBrightDataSearchQuery, extractBrightDataSerpItems } from '../../../tooling/brightdata'
import { KeyValueList, Section } from '../shared'

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

  const status =
    typeof resultData === 'object' && resultData !== null
      ? ((resultData as Record<string, unknown>).status as string) || null
      : null
  const taskId =
    typeof resultData === 'object' && resultData !== null
      ? ((resultData as Record<string, unknown>).task_id as string) || null
      : null

  const statusLabel =
    status === 'pending'
      ? 'Running in background'
      : status === 'completed'
        ? 'Completed'
        : status === 'failed'
          ? 'Failed'
          : status
            ? status.charAt(0).toUpperCase() + status.slice(1)
            : null

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {prompt ? (
        <Section title="Task">
          <MarkdownViewer content={prompt} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}
      <KeyValueList
        items={[
          statusLabel ? { label: 'Status', value: statusLabel } : null,
          url ? { label: 'Starting URL', value: url } : null,
        ]}
      />
      {taskId ? <p className="text-xs text-slate-500">Task ID: {taskId}</p> : null}
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
  const contentFromResult = !markdown && !htmlSnapshot && typeof entry.result === 'string' ? entry.result : null
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

export function BrightDataSearchDetail({ entry }: ToolDetailProps) {
  const parameters =
    entry.parameters && typeof entry.parameters === 'object' && !Array.isArray(entry.parameters)
      ? (entry.parameters as Record<string, unknown>)
      : null
  const query = extractBrightDataSearchQuery(parameters)
  const serpItems = extractBrightDataSerpItems(entry.result)
  const resultCount = extractBrightDataResultCount(entry.result) ?? (serpItems.length ? serpItems.length : null)
  const displayItems = serpItems.slice(0, 10)
  const hasMore = serpItems.length > displayItems.length
  const infoItems = [
    query ? { label: 'Query', value: <span className="tool-search-query-inline">“{query}”</span> } : null,
    resultCount !== null ? { label: 'Results', value: String(resultCount) } : null,
  ]
  const hasDetails = infoItems.some(Boolean)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {displayItems.length ? (
        <Section title="Results">
          <ol className="space-y-2">
            {displayItems.map((item, idx) => (
              <li key={`${item.url}-${idx}`} className="flex gap-2">
                <span className="mt-1 min-w-[1.5rem] text-right text-xs font-medium text-slate-400">
                  {item.position ?? idx + 1}.
                </span>
                <div className="space-y-0.5">
                  <div className="font-semibold text-slate-800">
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                      {item.title}
                    </a>
                  </div>
                  <p className="text-xs text-slate-500 break-all">{item.url}</p>
                </div>
              </li>
            ))}
          </ol>
          {hasMore ? <p className="text-xs text-slate-500">Showing first {displayItems.length} results.</p> : null}
        </Section>
      ) : null}
      {!hasDetails && !displayItems.length ? <p className="text-slate-500">No search details returned.</p> : null}
    </div>
  )
}
