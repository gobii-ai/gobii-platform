import { Fragment, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import ReactJsonView from '@microlink/react-json-view'
import type { ThemeObject } from '@microlink/react-json-view'

import { MarkdownViewer } from '../../common/MarkdownViewer'

export const CHAT_JSON_VIEW_THEME: ThemeObject = {
  base00: 'transparent',
  base01: '#dbeafe',
  base02: '#bfdbfe',
  base03: '#64748b',
  base04: '#475569',
  base05: '#0f172a',
  base06: '#0b1220',
  base07: '#020617',
  base08: '#dc2626',
  base09: '#c2410c',
  base0A: '#b45309',
  base0B: '#0f766e',
  base0C: '#0891b2',
  base0D: '#1d4ed8',
  base0E: '#7c3aed',
  base0F: '#be185d',
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="tool-chip-panel-title">{title}</p>
      <div className="tool-chip-panel-body">{children}</div>
    </div>
  )
}

function buildCodeFence(content: string, language?: string) {
  const runs = Array.from(content.matchAll(/`+/g)).map((match) => match[0].length)
  const fence = '`'.repeat(Math.max(3, ...runs, 0) + 1)
  const languageSuffix = language ? language.trim() : ''
  return `${fence}${languageSuffix}\n${content}\n${fence}`
}

export function CodeBlock({
  code,
  language,
  className = 'prose prose-sm max-w-none',
}: {
  code: string
  language?: string
  className?: string
}) {
  return <MarkdownViewer content={buildCodeFence(code, language)} className={className} />
}

export function OutputBlock({
  content,
  className = '',
}: {
  content: string
  className?: string
}) {
  return (
    <pre
      className={`max-h-64 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-950/95 p-3 text-xs leading-5 text-slate-100 ${className}`.trim()}
    >
      {content}
    </pre>
  )
}

export function JsonBlock({ value }: { value: Record<string, unknown> | unknown[] }) {
  return (
    <div className="json-view-panel overflow-hidden rounded-2xl border border-sky-200/80 bg-[linear-gradient(180deg,rgba(239,246,255,0.96),rgba(236,254,255,0.82))] px-3 py-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
      <ReactJsonView
        src={value}
        name={false}
        collapsed={false}
        displayDataTypes={false}
        displayObjectSize={false}
        displayArrayKey={false}
        enableClipboard={false}
        iconStyle="triangle"
        indentWidth={2}
        collapseStringsAfterLength={false}
        groupArraysAfterLength={1000000}
        quotesOnKeys={false}
        sortKeys
        theme={CHAT_JSON_VIEW_THEME}
        style={{
          backgroundColor: 'transparent',
          fontSize: '0.8125rem',
          lineHeight: 1.45,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace',
          padding: 0,
        }}
      />
    </div>
  )
}

export function TruncatedMarkdown({ content, maxLines = 3 }: { content: string; maxLines?: number }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const lines = content.split('\n')
  const needsTruncation = lines.length > maxLines || content.length > 200

  if (!needsTruncation) {
    return <MarkdownViewer content={content} className="prose prose-sm max-w-none" />
  }

  const truncatedContent = isExpanded
    ? content
    : lines.slice(0, maxLines).join('\n').slice(0, 180) + (content.length > 180 ? '…' : '')

  return (
    <div className="space-y-2">
      <div className={isExpanded ? '' : 'line-clamp-3'}>
        <MarkdownViewer content={truncatedContent} className="prose prose-sm max-w-none" />
      </div>
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
      >
        {isExpanded ? (
          <>
            <ChevronUp className="h-3.5 w-3.5" />
            Show less
          </>
        ) : (
          <>
            <ChevronDown className="h-3.5 w-3.5" />
            Read full assignment
          </>
        )}
      </button>
    </div>
  )
}

export function KeyValueList({ items }: { items: Array<{ label: string; value: ReactNode } | null> }) {
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
