import { MarkdownViewer } from '../../../common/MarkdownViewer'
import type { ToolDetailProps } from '../../tooling/types'
import { isPlainObject, parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'
import { toText } from '../brightDataUtils'

const INLINE_IMG_SRC_RE = /<img[^>]+src=['"]([^'"]+)['"]/i
const MARKDOWN_IMG_RE = /!\[[^\]]*]\(([^)]+)\)/

function extractInlineHtmlImageUrl(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const match = value.match(INLINE_IMG_SRC_RE)
  const candidate = match?.[1]?.trim()
  return candidate || null
}

function extractMarkdownImageUrl(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const match = value.match(MARKDOWN_IMG_RE)
  const candidate = match?.[1]?.trim()
  return candidate || null
}

function toCountLabel(value: number | null): string | null {
  if (value === null) {
    return null
  }
  return `${value} source image${value === 1 ? '' : 's'}`
}

export function ImageDetail({ entry }: ToolDetailProps) {
  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  const resultObject = parseResultObject(entry.result)
  const resultRecord = isPlainObject(resultObject) ? (resultObject as Record<string, unknown>) : null

  const prompt = toText(parameters?.prompt)
  const filePath = toText(parameters?.file_path) || toText(resultRecord?.file)
  const aspectRatio = toText(parameters?.aspect_ratio)
  const model = toText(resultRecord?.model)
  const endpointKey = toText(resultRecord?.endpoint_key)
  const sourceImageCount = typeof resultRecord?.source_image_count === 'number'
    ? resultRecord.source_image_count
    : Array.isArray(parameters?.source_images)
      ? parameters.source_images.length
      : null

  const imageUrl =
    entry.sourceEntry?.createImageUrl ??
    toText(resultRecord?.image_url) ??
    toText(resultRecord?.url) ??
    extractInlineHtmlImageUrl(resultRecord?.inline_html) ??
    extractMarkdownImageUrl(resultRecord?.inline)

  const infoItems = [
    filePath ? { label: 'File', value: filePath } : null,
    aspectRatio ? { label: 'Aspect ratio', value: aspectRatio } : null,
    sourceImageCount !== null ? { label: 'Input', value: toCountLabel(sourceImageCount) } : null,
    endpointKey ? { label: 'Endpoint', value: endpointKey } : null,
    model ? { label: 'Model', value: model } : null,
  ]
  const hasDetails = infoItems.some(Boolean)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {imageUrl ? (
        <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm">
          <img
            src={imageUrl}
            alt={prompt ? `Generated image for prompt: ${prompt}` : 'Generated image'}
            className="max-h-[28rem] w-full object-contain"
          />
        </div>
      ) : null}

      {prompt ? (
        <Section title="Prompt">
          <MarkdownViewer content={prompt} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}

      {!hasDetails && !imageUrl && !prompt ? <p className="text-slate-500">No image details returned.</p> : null}
    </div>
  )
}
