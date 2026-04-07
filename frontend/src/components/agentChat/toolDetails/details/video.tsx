import { MarkdownViewer } from '../../../common/MarkdownViewer'
import type { ToolDetailProps } from '../../tooling/types'
import { isPlainObject, parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'
import { toText } from '../brightDataUtils'

export function VideoDetail({ entry }: ToolDetailProps) {
  const parameters = isPlainObject(entry.parameters) ? (entry.parameters as Record<string, unknown>) : null
  const resultObject = parseResultObject(entry.result)
  const resultRecord = isPlainObject(resultObject) ? (resultObject as Record<string, unknown>) : null

  const prompt = toText(parameters?.prompt)
  const duration = toText(parameters?.duration) ?? toText(resultRecord?.duration)
  const size = toText(parameters?.size) ?? toText(resultRecord?.size)
  const sourceImage = toText(parameters?.source_image)
  const videoUrl =
    entry.sourceEntry?.createVideoUrl ??
    toText(resultRecord?.video_url) ??
    toText(resultRecord?.download_url) ??
    toText(resultRecord?.url)

  const infoItems = [
    duration ? { label: 'Duration', value: duration } : null,
    size ? { label: 'Size', value: size } : null,
    sourceImage ? { label: 'Source image', value: sourceImage } : null,
  ]

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {videoUrl ? (
        <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm">
          <video
            src={videoUrl}
            controls
            playsInline
            preload="metadata"
            className="max-h-[28rem] w-full bg-slate-950 object-contain"
          >
            Your browser does not support video playback.
          </video>
        </div>
      ) : null}

      {prompt ? (
        <Section title="Prompt">
          <MarkdownViewer content={prompt} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}

      {!videoUrl && !prompt ? <p className="text-slate-500">No video details returned.</p> : null}
    </div>
  )
}
