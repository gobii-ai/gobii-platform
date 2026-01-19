import { MarkdownViewer } from '../../../common/MarkdownViewer'
import type { ToolDetailProps } from '../../tooling/types'
import { isPlainObject, parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'
import { toText } from '../brightDataUtils'

function extractDataArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value
  if (isPlainObject(value) && Array.isArray((value as Record<string, unknown>).data)) {
    return (value as Record<string, unknown>).data as unknown[]
  }
  return []
}

export function ChartDetail({ entry }: ToolDetailProps) {
  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  const resultObject = parseResultObject(entry.result)
  const resultRecord = isPlainObject(resultObject) ? (resultObject as Record<string, unknown>) : null

  const title =
    toText(parameters?.title) ||
    toText(resultRecord?.title) ||
    toText(entry.summary) ||
    toText(entry.caption)
  const chartType = toText(parameters?.type) || toText(resultRecord?.type)
  const description = toText(parameters?.description) || toText(resultRecord?.description)
  const chartId = toText(resultRecord?.chart_id) || toText(resultRecord?.id)
  const imageUrl =
    toText(resultRecord?.chart_url) ||
    toText(resultRecord?.image_url) ||
    toText(resultRecord?.url) ||
    toText(parameters?.image_url)

  const parameterData = extractDataArray(parameters?.data)
  const resultData = extractDataArray(resultRecord?.data)
  const dataArray = parameterData.length ? parameterData : resultData

  const infoItems = [
    title ? { label: 'Title', value: title } : null,
    chartType ? { label: 'Type', value: chartType } : null,
    dataArray.length ? { label: 'Data points', value: dataArray.length.toString() } : null,
    chartId ? { label: 'Chart ID', value: chartId } : null,
  ]

  const dataPreview = dataArray.slice(0, 3)
  const hasDetails = infoItems.some(Boolean)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {imageUrl ? (
        <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm">
          <img
            src={imageUrl}
            alt={title ? `Chart for ${title}` : 'Chart preview'}
            className="w-full"
          />
        </div>
      ) : null}

      {description ? (
        <Section title="Notes">
          <MarkdownViewer content={description} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}

      {dataPreview.length ? (
        <Section title="Data preview">
          <div className="overflow-hidden rounded-xl border border-slate-200/80">
            <pre className="max-h-64 overflow-auto bg-slate-900/95 p-3 text-xs leading-relaxed text-slate-100">
              {JSON.stringify(dataPreview, null, 2)}
            </pre>
          </div>
          {dataArray.length > dataPreview.length ? (
            <p className="text-xs text-slate-500">
              Showing {dataPreview.length} of {dataArray.length} rows.
            </p>
          ) : null}
        </Section>
      ) : null}

      {!hasDetails && !imageUrl && !dataPreview.length ? <p className="text-slate-500">No chart details returned.</p> : null}
    </div>
  )
}
