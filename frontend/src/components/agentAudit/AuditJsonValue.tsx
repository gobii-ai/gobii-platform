import ReactJsonView from '@microlink/react-json-view'
import { useMemo } from 'react'

import { createNormalizeContext, normalizeStructuredValue, tryParseJson } from '../agentChat/toolDetails/normalize'
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
      <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white/90 p-3">
        <ReactJsonView
          src={normalized}
          name={false}
          collapsed={1}
          displayDataTypes={false}
          displayObjectSize={false}
          enableClipboard={false}
          iconStyle="triangle"
          sortKeys
          style={{ backgroundColor: 'transparent', fontSize: '0.8125rem', lineHeight: 1.5 }}
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
