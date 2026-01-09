import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList, Section } from '../shared'
import { stringify } from '../utils'

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
