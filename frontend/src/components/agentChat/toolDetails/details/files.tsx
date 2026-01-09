import type { ToolDetailProps } from '../../tooling/types'
import { parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'
import { isNonEmptyString, stringify } from '../utils'

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

export function FileExportDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const result = parseResultObject(entry.result)
  const status = isNonEmptyString(result?.status) ? result?.status : null
  const message = isNonEmptyString(result?.message) ? result?.message : null
  const filename =
    (isNonEmptyString(result?.filename) ? result?.filename : null) ||
    (isNonEmptyString(params.filename) ? params.filename : null)
  const path = isNonEmptyString(result?.path) ? result?.path : null
  const nodeId = isNonEmptyString(result?.node_id) ? result?.node_id : null
  const statusLabel = status ? status.toUpperCase() : null

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          statusLabel ? { label: 'Status', value: statusLabel } : null,
          filename ? { label: 'File', value: filename } : null,
          path ? { label: 'Path', value: path } : null,
          nodeId ? { label: 'Node', value: nodeId } : null,
        ]}
      />
      {message ? (
        <Section title={status?.toLowerCase() === 'error' ? 'Error' : 'Message'}>
          <p className="text-slate-700">{message}</p>
        </Section>
      ) : null}
    </div>
  )
}
