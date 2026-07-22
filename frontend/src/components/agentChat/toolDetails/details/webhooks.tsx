import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import type { ToolDetailProps } from '../../tooling/types'
import { EmptyToolResult, KeyValueList } from '../shared'

const text = (value: unknown) => typeof value === 'string' && value.trim() ? value.trim() : null
const date = (value: unknown) => {
  const raw = text(value)
  if (!raw) return 'Never'
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
}
const maskedUrl = (value: string) => {
  try {
    const parsed = new URL(value)
    return `${parsed.origin}${parsed.pathname}${parsed.search ? '?••••••••' : ''}`
  } catch { return 'Secret endpoint available' }
}

function Endpoint({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }
  return (
    <span className="flex min-w-0 items-center gap-2">
      <code className="min-w-0 flex-1 truncate text-xs text-indigo-900">{maskedUrl(value)}</code>
      <button type="button" onClick={() => void copy()} className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-100">
        {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
        {copied ? 'Copied' : 'Copy'}
      </button>
    </span>
  )
}

export function WebhookManagementDetail({ entry }: ToolDetailProps) {
  const result = parseResultObject(entry.result)
  if (entry.toolName === 'send_webhook_event') {
    const payload = isRecord(entry.parameters?.['payload']) ? entry.parameters?.['payload'] : null
    return <KeyValueList items={[
      { label: 'Webhook', value: text(result?.['webhook_name']) || text(entry.parameters?.['webhook_id']) || 'Unknown' },
      result?.['response_status'] !== undefined ? { label: 'Response', value: `HTTP ${String(result?.['response_status'])}` } : null,
      payload ? { label: 'Payload', value: `${Object.keys(payload).length} field${Object.keys(payload).length === 1 ? '' : 's'}` } : null,
    ]} />
  }

  const direction = entry.toolName.includes('inbound') ? 'inbound' : 'outbound'
  const action = text(entry.parameters?.['action']) || 'manage'
  const message = text(result?.['message'])
  if (text(result?.['status']) === 'error') return <p className="text-sm font-medium text-rose-700">{message || 'Webhook management failed.'}</p>

  const webhooks = Array.isArray(result?.['webhooks']) ? result['webhooks'].filter(isRecord) : []
  if (action === 'list') {
    if (!webhooks.length) return <EmptyToolResult>No {direction} webhooks configured.</EmptyToolResult>
    return <ul className="space-y-3">{webhooks.map((webhook, index) => (
      <li key={text(webhook['id']) || index} className="flex items-start justify-between gap-4">
        <span><strong className="block text-slate-800">{text(webhook['name']) || 'Unnamed webhook'}</strong><small>Last triggered: {date(webhook['last_triggered_at'])}</small></span>
        {typeof webhook['is_active'] === 'boolean' ? <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${webhook['is_active'] ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>{webhook['is_active'] ? 'Active' : 'Inactive'}</span>
          : webhook['last_response_status'] !== null && webhook['last_response_status'] !== undefined ? <span className="rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-semibold text-cyan-700">HTTP {String(webhook['last_response_status'])}</span> : null}
      </li>
    ))}</ul>
  }

  const webhook = result && (isRecord(result['webhook']) ? result['webhook'] : isRecord(result['deleted_webhook']) ? result['deleted_webhook'] : null)
  if (!webhook) return <EmptyToolResult>{message || 'No webhook details returned.'}</EmptyToolResult>
  const url = text(webhook['url'])
  return <KeyValueList items={[
    { label: 'Name', value: text(webhook['name']) || 'Unnamed webhook' },
    { label: 'Direction', value: direction === 'inbound' ? 'Inbound trigger' : 'Outbound destination' },
    typeof webhook['is_active'] === 'boolean' ? { label: 'State', value: webhook['is_active'] ? 'Active' : 'Inactive' } : null,
    webhook['last_response_status'] !== null && webhook['last_response_status'] !== undefined ? { label: 'Last response', value: `HTTP ${String(webhook['last_response_status'])}` } : null,
    { label: 'Last triggered', value: date(webhook['last_triggered_at']) },
    text(webhook['id']) ? { label: 'Webhook ID', value: <code className="break-all text-xs">{text(webhook['id'])}</code> } : null,
    url ? { label: direction === 'inbound' ? 'Secret endpoint' : 'Destination', value: <Endpoint value={url} /> } : null,
    message ? { label: 'Result', value: message } : null,
  ]} />
}
