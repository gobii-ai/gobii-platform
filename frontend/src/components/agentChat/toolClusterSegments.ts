import type { ToolEntryDisplay } from './tooling/types'

export type ToolClusterRenderSegment =
  | { kind: 'preview', key: string, entries: ToolEntryDisplay[], isTrailing: boolean }
  | { kind: 'separate', key: string, entry: ToolEntryDisplay }

export function buildToolClusterRenderSegments(entries: ToolEntryDisplay[]): ToolClusterRenderSegment[] {
  const segments: ToolClusterRenderSegment[] = []
  let previewEntries: ToolEntryDisplay[] = []
  const flushPreview = () => {
    if (!previewEntries.length) return
    segments.push({
      kind: 'preview',
      key: `preview:${previewEntries[0].id}`,
      entries: previewEntries,
      isTrailing: false,
    })
    previewEntries = []
  }

  for (const entry of entries) {
    if (entry.separateFromPreview) {
      flushPreview()
      segments.push({ kind: 'separate', key: `separate:${entry.id}`, entry })
    } else {
      previewEntries.push(entry)
    }
  }
  flushPreview()

  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index]
    if (segment.kind === 'preview') {
      segment.isTrailing = true
      break
    }
  }
  return segments
}
