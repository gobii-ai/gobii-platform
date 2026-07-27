import type { ToolEntryDisplay } from './types'

const MAX_PREVIEW_TEXT_LENGTH = 160
/** Roughly three lines at the size the summary renders, matching the live thinking stream. */
const MAX_THINKING_SUMMARY_LENGTH = 260

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

/**
 * The reasoning as a short summary rather than a clipped opening line.
 *
 * While a thought is streaming it is shown over three lines; the moment it finished it collapsed
 * to the first line of a single-line caption, so the timeline lost the thinking exactly when it
 * became complete. This keeps enough of it to be worth reading, and the full text stays one click
 * away in the detail view.
 */
export function deriveThinkingSummary(entry: ToolEntryDisplay): string | null {
  if (entry.toolName !== 'thinking') {
    return null
  }
  const reasoning = typeof entry.result === 'string' ? entry.result : ''
  const normalized = normalizeInlineText(stripBasicMarkdown(reasoning))
  if (!normalized) {
    return null
  }
  if (normalized.length <= MAX_THINKING_SUMMARY_LENGTH) {
    return normalized
  }
  // Prefer to end on a sentence so the summary reads as a thought, not a truncation.
  const window = normalized.slice(0, MAX_THINKING_SUMMARY_LENGTH)
  const lastStop = Math.max(window.lastIndexOf('. '), window.lastIndexOf('? '), window.lastIndexOf('! '))
  if (lastStop > MAX_THINKING_SUMMARY_LENGTH * 0.5) {
    return window.slice(0, lastStop + 1)
  }
  return `${window.trimEnd()}…`
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
