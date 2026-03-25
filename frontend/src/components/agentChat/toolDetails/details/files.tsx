import type { ToolDetailProps } from '../../tooling/types'
import { parseResultObject } from '../../../../util/objectUtils'
import { CodeBlock, KeyValueList, Section } from '../shared'
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
  const filePath =
    (isNonEmptyString(params.file_path) ? params.file_path : null) ||
    (isNonEmptyString(params.path) ? params.path : null)
  const content =
    (isNonEmptyString(params.content) ? params.content : null) ||
    (isNonEmptyString(params.csv_text) ? params.csv_text : null) ||
    (isNonEmptyString(params.html) ? params.html : null)

  const extension = (() => {
    if (!filePath) return null
    const match = filePath.match(/\.([a-z0-9]+)$/i)
    return match ? match[1].toLowerCase() : null
  })()

  const language = (() => {
    if (extension === 'json') return 'json'
    if (extension === 'html' || extension === 'htm') return 'html'
    if (extension === 'md' || extension === 'markdown') return 'markdown'
    if (extension === 'xml') return 'xml'
    if (extension === 'yaml' || extension === 'yml') return 'yaml'
    if (extension === 'csv') return 'text'
    if (isNonEmptyString(params.mime_type) && params.mime_type.includes('json')) return 'json'
    if (isNonEmptyString(params.mime_type) && params.mime_type.includes('html')) return 'html'
    return 'text'
  })()

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {status?.toLowerCase() === 'error' && message ? (
        <Section title="Error">
          <p className="text-slate-700">{message}</p>
        </Section>
      ) : content ? (
        <Section title="Contents">
          <CodeBlock code={content} language={language} />
        </Section>
      ) : filePath ? (
        <KeyValueList items={[{ label: 'Path', value: filePath }]} />
      ) : null}
    </div>
  )
}
