import type { ToolEntryDisplay } from './types'

const MAX_PREVIEW_TEXT_LENGTH = 160

function normalizeInlineText(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function stripBasicMarkdown(value: string): string {
  return value
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
}

function clampPreviewText(value: string): string {
  const normalized = normalizeInlineText(value)
  if (normalized.length <= MAX_PREVIEW_TEXT_LENGTH) {
    return normalized
  }
  return `${normalized.slice(0, MAX_PREVIEW_TEXT_LENGTH - 1).trimEnd()}…`
}

function firstMeaningfulLine(value: string): string | null {
  const lines = value.split(/\r?\n/)
  const firstLine = lines.find((line) => line.trim().length > 0)
  if (!firstLine) {
    return null
  }
  return firstLine
}

export function deriveEntryCaption(entry: ToolEntryDisplay): string | null {
  if (entry.caption && entry.caption !== entry.label) {
    return entry.caption
  }
  if (entry.summary && entry.summary !== entry.label) {
    return entry.summary
  }
  return null
}

export function deriveThinkingPreview(entry: ToolEntryDisplay): string | null {
  if (entry.toolName !== 'thinking') {
    return null
  }
  const reasoning = typeof entry.result === 'string' ? entry.result : ''
  if (!reasoning.trim()) {
    return null
  }
  const firstLine = firstMeaningfulLine(reasoning)
  if (!firstLine) {
    return null
  }
  return clampPreviewText(stripBasicMarkdown(firstLine))
}

export function deriveSemanticPreview(entry: ToolEntryDisplay): string | null {
  const thinkingPreview = deriveThinkingPreview(entry)
  if (thinkingPreview) {
    return thinkingPreview
  }

  const caption = deriveEntryCaption(entry)
  if (caption) {
    return clampPreviewText(caption)
  }

  if (typeof entry.result === 'string') {
    const line = firstMeaningfulLine(entry.result)
    if (line) {
      return clampPreviewText(stripBasicMarkdown(line))
    }
  }

  return null
}
