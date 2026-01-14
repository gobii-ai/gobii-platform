import { useMemo, useState } from 'react'
import { MessageCircle, StepForward, Wrench } from 'lucide-react'

import type { AuditMessageEvent, AuditStepEvent, AuditToolCallEvent } from '../../types/agentAudit'
import { StructuredDataTable } from '../common/StructuredDataTable'
import { MessageContent } from '../agentChat/MessageContent'
import { normalizeStructuredValue } from '../agentChat/toolDetails'
import { EventHeader } from './EventHeader'
import { IconCircle, renderHtmlOrText } from './eventPrimitives'

export function ToolCallRow({
  tool,
  collapsed = false,
  onToggle,
}: {
  tool: AuditToolCallEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  const [expanded, setExpanded] = useState(true)
  const isControlled = collapsed !== undefined
  const isExpanded = isControlled ? !collapsed : expanded
  const toggle = () => {
    if (isControlled) {
      onToggle?.()
    } else {
      setExpanded((prev) => !prev)
    }
  }
  const resultPreview = useMemo(() => {
    if (!tool.result) return null
    const trimmed = tool.result.length > 160 ? `${tool.result.slice(0, 160)}…` : tool.result
    return trimmed
  }, [tool.result])

  const parsedParameters = useMemo(() => {
    if (tool.parameters === null || tool.parameters === undefined) return null
    try {
      return normalizeStructuredValue(tool.parameters, { depth: 0, maxDepth: 6, seen: new WeakSet<object>() })
    } catch {
      return tool.parameters
    }
  }, [tool.parameters])

  const parsedResult = useMemo(() => {
    if (tool.result === null || tool.result === undefined) return null
    let value: unknown = tool.result
    if (typeof value === 'string') {
      try {
        const maybeJson = JSON.parse(value)
        value = maybeJson
      } catch {
        // leave as string
      }
    }
    try {
      return normalizeStructuredValue(value, { depth: 0, maxDepth: 6, seen: new WeakSet<object>() })
    } catch {
      return value
    }
  }, [tool.result])

  const renderValue = (value: unknown) => {
    if (value === null || value === undefined) return null
    if (typeof value === 'string') {
      return renderHtmlOrText(value, {
        htmlClassName: 'prose prose-sm max-w-none rounded-md bg-white px-3 py-2 text-slate-800 shadow-inner shadow-slate-200/60',
        textClassName: 'rounded-md bg-indigo-50 px-2 py-1 text-[12px] text-slate-800',
      })
    }
    return <StructuredDataTable value={value} />
  }

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={Wrench} bgClass="bg-indigo-50" textClass="text-indigo-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">{tool.tool_name || 'Tool call'}</div>
              <div className="text-xs text-slate-600">{tool.timestamp ? new Date(tool.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={resultPreview ? <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700">Tool</span> : null}
        collapsed={!isExpanded}
        onToggle={toggle}
      />
      {!isExpanded && resultPreview ? (
        <div className="mt-2 text-sm text-slate-700">{resultPreview}</div>
      ) : null}
      {isExpanded && parsedParameters ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Parameters</div>
          {renderValue(parsedParameters)}
        </div>
      ) : null}
      {isExpanded && parsedResult ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Result</div>
          {renderValue(parsedResult)}
        </div>
      ) : null}
    </div>
  )
}

export function MessageRow({
  message,
  collapsed = false,
  onToggle,
}: {
  message: AuditMessageEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  const htmlBody = message.body_html && message.body_html.trim().length > 0 ? message.body_html : null
  const textBody = message.body_text || (htmlBody ? null : message.body_html)
  const hasBody = Boolean(htmlBody || (textBody && textBody.trim().length > 0))
  const attachments = message.attachments || []

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={MessageCircle} bgClass="bg-emerald-50" textClass="text-emerald-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">
                {message.is_outbound ? 'Agent → User' : 'User → Agent'}{' '}
                <span className="text-xs font-normal text-slate-500">({message.channel || 'web'})</span>
              </div>
              <div className="text-xs text-slate-600">{message.timestamp ? new Date(message.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={<span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700">Message</span>}
        collapsed={collapsed}
        onToggle={onToggle}
      />
      {!collapsed ? (
        <>
          {hasBody ? (
            <div className="mt-2 prose prose-sm max-w-none text-slate-800">
              <MessageContent bodyHtml={htmlBody} bodyText={textBody} showEmptyState={false} />
            </div>
          ) : null}
          {attachments.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {attachments.map((attachment) => {
                const href = attachment.download_url || attachment.url
                const label = attachment.filespace_path || attachment.filename
                return (
                  <a
                    key={attachment.id}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100"
                    title={attachment.filespace_path || attachment.filename}
                  >
                    <span className="max-w-[240px] truncate">{label}</span>
                    {attachment.file_size_label ? <span className="text-indigo-500">{attachment.file_size_label}</span> : null}
                  </a>
                )
              })}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

export function StepRow({
  step,
  collapsed = false,
  onToggle,
}: {
  step: AuditStepEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={StepForward} bgClass="bg-slate-100" textClass="text-slate-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">Step</div>
              <div className="text-xs text-slate-600">{step.timestamp ? new Date(step.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={
          step.is_system ? (
            <span className="rounded-full bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-700">
              {step.system_code || 'System'}
            </span>
          ) : (
            <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">Step</span>
          )
        }
        collapsed={collapsed}
        onToggle={onToggle}
      />
      {!collapsed ? (
        <>
          {step.description ? <div className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-800">{step.description}</div> : null}
          {step.is_system && step.system_notes ? (
            <div className="mt-2 rounded-md bg-slate-50 px-2 py-1 text-[12px] text-slate-700">{step.system_notes}</div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
