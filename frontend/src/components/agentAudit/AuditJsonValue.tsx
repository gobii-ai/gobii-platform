import ReactJsonView from '@microlink/react-json-view'
import { useMemo } from 'react'

import { createNormalizeContext, normalizeStructuredValue, tryParseJson } from '../agentChat/toolDetails/normalize'
import { CHAT_JSON_VIEW_THEME } from '../agentChat/toolDetails/shared'
import { isRecord } from '../../util/objectUtils'
import { renderHtmlOrText } from './eventPrimitives'

type AuditJsonValueProps = {
  value: unknown
}

function normalizeJsonLikeValue(value: unknown): unknown {
  if (typeof value === 'string') {
    const parsed = tryParseJson(value)
    if (parsed !== null) {
      return normalizeStructuredValue(parsed, createNormalizeContext(6))
    }
    return value
  }

  if (value === null || value === undefined) {
    return value
  }

  return normalizeStructuredValue(value, createNormalizeContext(6))
}

function canUseJsonViewer(value: unknown): value is Record<string, unknown> | unknown[] {
  return Array.isArray(value) || isRecord(value)
}

export function AuditJsonValue({ value }: AuditJsonValueProps) {
  const normalized = useMemo(() => normalizeJsonLikeValue(value), [value])

  if (normalized === null || normalized === undefined) {
    return null
  }

  if (canUseJsonViewer(normalized)) {
    return (
      <div className="json-view-panel max-h-80 overflow-auto rounded-2xl border border-sky-200/80 bg-[linear-gradient(180deg,rgba(239,246,255,0.96),rgba(236,254,255,0.82))] px-3 py-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
        <ReactJsonView
          src={normalized}
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

  if (typeof normalized === 'string') {
    return renderHtmlOrText(normalized, {
      htmlClassName: 'prose prose-sm max-w-none rounded-xl bg-white px-3 py-2 text-slate-800 shadow-inner shadow-slate-200/60',
      textClassName: 'whitespace-pre-wrap break-words rounded-xl bg-indigo-50 px-3 py-2 text-[12px] text-slate-800',
    })
  }

  return (
    <div className="rounded-xl bg-indigo-50 px-3 py-2 font-mono text-[12px] text-slate-800">
      {String(normalized)}
    </div>
  )
}
