import { ArrowLeft, ArrowRight, RotateCcw, Globe, ExternalLink } from 'lucide-react'

import { MarkdownViewer } from '../../../common/MarkdownViewer'
import { looksLikeHtml, pickHtmlCandidate, sanitizeHtml } from '../../../../util/sanitize'
import type { ToolDetailProps } from '../../tooling/types'
import { extractBrightDataResultCount, extractBrightDataSearchQuery, extractBrightDataSerpItems } from '../../../tooling/brightdata'
import { isPlainObject, parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'

function pickString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length ? value.trim() : null
}

function parseHostname(value: string): string | null {
  try {
    return new URL(value).hostname.toLowerCase()
  } catch {
    return null
  }
}

function buildFaviconUrl(hostname: string | null): string | null {
  if (!hostname) return null
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(hostname)}&sz=64`
}

type LinkItem = { title: string; url: string; position: number }
type SectionItem = { heading: string; snippet: string | null }

function normalizeLinkItem(value: unknown, index: number): LinkItem | null {
  if (!isPlainObject(value)) return null
  const url =
    pickString(value['u']) || pickString(value['url']) || pickString(value['link']) || pickString(value['href'])
  if (!url) return null
  const title =
    pickString(value['t']) ||
    pickString(value['title']) ||
    pickString(value['name']) ||
    pickString(value['label']) ||
    url
  const posRaw = value['p'] ?? value['position']
  const pos =
    typeof posRaw === 'number' && Number.isFinite(posRaw)
      ? posRaw
      : typeof posRaw === 'string'
        ? Number.parseInt(posRaw, 10)
        : index + 1
  return { title, url, position: Number.isFinite(pos) ? pos : index + 1 }
}

function normalizeSectionItem(value: unknown): SectionItem | null {
  if (!isPlainObject(value)) return null
  const heading =
    pickString(value['h']) || pickString(value['heading']) || pickString(value['title']) || pickString(value['name'])
  const snippet = pickString(value['c']) || pickString(value['content']) || pickString(value['excerpt'])
  if (!heading && !snippet) return null
  return { heading: heading ?? 'Section', snippet }
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
  const parsedResult = parseResultObject(entry.result)
  const nestedResultString = parsedResult && typeof parsedResult['result'] === 'string' ? parsedResult['result'] : null
  const rawResultString = typeof entry.result === 'string' ? entry.result : null

  const htmlCandidate = pickHtmlCandidate(pickString(params['html']), nestedResultString ?? rawResultString)
  const sanitizedHtml = htmlCandidate ? sanitizeHtml(htmlCandidate) : null

  const markdownCandidate =
    pickString(params['markdown']) ||
    (nestedResultString && !looksLikeHtml(nestedResultString) ? nestedResultString : null) ||
    (!parsedResult && rawResultString && !looksLikeHtml(rawResultString) ? rawResultString : null)
  const readerText = markdownCandidate || pickString(parsedResult?.excerpt) || null

  const screenshotUrl =
    pickString(params['screenshot_url']) || pickString(params['screenshot']) || pickString(parsedResult?.screenshot_url)

  const targetUrl =
    pickString(params['url']) ||
    pickString(params['start_url']) ||
    pickString(params['target_url']) ||
    pickString(parsedResult?.url)

  const pageTitle =
    pickString(params['title']) ||
    pickString(params['page_title']) ||
    pickString(parsedResult?.title) ||
    pickString(entry.summary) ||
    null

  const itemsRaw = Array.isArray(parsedResult?.items) ? (parsedResult?.items as unknown[]) : []
  const linkItems = itemsRaw.map(normalizeLinkItem).filter((item): item is LinkItem => Boolean(item))
  const sectionItems = itemsRaw.map(normalizeSectionItem).filter((item): item is SectionItem => Boolean(item))
  const meta = isPlainObject(parsedResult?._meta) ? (parsedResult?._meta as Record<string, unknown>) : null
  const compressionLabel = pickString(meta?.ratio)

  const urlLabel = targetUrl || pickString(parsedResult?.url) || 'Web snapshot'
  const hasOutline = sectionItems.length > 0 || linkItems.length > 0

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm">
        <div className="flex items-center gap-2 bg-slate-900/95 px-4 py-2 text-slate-100">
          <div className="flex items-center gap-1 rounded-lg bg-slate-800/80 px-2 py-1 shadow-inner shadow-slate-900/60">
            <button
              type="button"
              disabled
              className="rounded-md px-2 py-1 text-slate-500/80 ring-1 ring-slate-700/60"
              aria-label="Back"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled
              className="rounded-md px-2 py-1 text-slate-500/80 ring-1 ring-slate-700/60"
              aria-label="Forward"
            >
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled
              className="rounded-md px-2 py-1 text-slate-500/80 ring-1 ring-slate-700/60"
              aria-label="Refresh"
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg bg-slate-800/80 px-3 py-1.5 text-xs font-semibold leading-tight shadow-inner shadow-slate-900/60">
            <Globe className="h-4 w-4 text-slate-200" aria-hidden="true" />
            <span className="truncate">{urlLabel}</span>
          </div>
          {compressionLabel ? (
            <span className="rounded-full bg-indigo-500/20 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-indigo-50 ring-1 ring-indigo-400/50">
              {compressionLabel} trimmed
            </span>
          ) : null}
        </div>
        <div className="space-y-3 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              {pageTitle ? <p className="text-base font-semibold leading-snug text-slate-900">{pageTitle}</p> : null}
            </div>
          </div>
          {screenshotUrl ? (
            <div className="overflow-hidden rounded-xl border border-slate-200/70">
              <img
                src={screenshotUrl}
                alt={pageTitle ? `Snapshot of ${pageTitle}` : 'Page snapshot'}
                className="w-full"
              />
            </div>
          ) : null}
          {sanitizedHtml ? <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: sanitizedHtml }} /> : null}
          {!sanitizedHtml && readerText ? (
            <MarkdownViewer content={readerText} className="prose prose-sm max-w-none" />
          ) : null}
          {hasOutline ? (
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Page outline</p>
              {sectionItems.length
                ? sectionItems.map((item, idx) => (
                    <div key={`${item.heading}-${idx}`} className="rounded-xl border border-slate-200/80 px-3 py-2.5">
                      <p className="text-sm font-semibold text-slate-800">{item.heading}</p>
                      {item.snippet ? <p className="text-xs leading-relaxed text-slate-500">{item.snippet}</p> : null}
                    </div>
                  ))
                : null}
              {!sectionItems.length && linkItems.length ? (
                <ol className="space-y-2">
                  {linkItems.map((item) => (
                    <li key={`${item.url}-${item.position}`} className="rounded-xl border border-slate-200/80 px-3 py-2.5">
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 text-[11px] font-semibold text-slate-400">{item.position}.</span>
                        <div className="min-w-0 space-y-1">
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noreferrer"
                            className="line-clamp-2 text-sm font-semibold text-indigo-600 hover:text-indigo-700"
                          >
                            {item.title}
                          </a>
                          <p className="text-[11px] text-slate-500 break-all">{item.url}</p>
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : null}
            </div>
          ) : null}
          {!sanitizedHtml && !readerText && !hasOutline ? <p className="text-slate-500">No page content returned.</p> : null}
        </div>
      </div>
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
  const displayItems = serpItems
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
            {displayItems.map((item, idx) => {
              const host = parseHostname(item.url)
              const faviconUrl = buildFaviconUrl(host)
              return (
                <li key={`${item.url}-${idx}`}>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-start gap-2 rounded-md border border-slate-200 bg-white px-2 py-2 transition-colors hover:bg-slate-50"
                  >
                    <span className="mt-0.5 min-w-[1.5rem] text-right text-xs font-medium text-slate-400">
                      {item.position ?? idx + 1}.
                    </span>
                    <span className="mt-0.5 inline-grid h-5 w-5 shrink-0 place-items-center overflow-hidden rounded-full border border-slate-200 bg-white">
                      {faviconUrl ? (
                        <img src={faviconUrl} alt="" className="h-3.5 w-3.5 object-contain" loading="lazy" referrerPolicy="no-referrer" />
                      ) : (
                        <Globe className="h-3.5 w-3.5 text-slate-400" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1 space-y-0.5">
                      <span className="block line-clamp-2 text-sm font-semibold text-slate-800 group-hover:text-indigo-700">
                        {item.title}
                      </span>
                      <span className="block text-xs text-slate-500 break-all">{item.url}</span>
                    </span>
                    <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-indigo-600" />
                  </a>
                </li>
              )
            })}
          </ol>
        </Section>
      ) : null}
      {!hasDetails && !displayItems.length ? <p className="text-slate-500">No search details returned.</p> : null}
    </div>
  )
}
