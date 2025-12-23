import type { CSSProperties } from 'react'

type CSSVariableProperties = CSSProperties & Record<string, string | number>

export const DEFAULT_CHAT_COLOR_HEX = '#0074D4'

function clamp(value: number, min = 0, max = 255): number {
  return Math.min(Math.max(value, min), max)
}

function normalizeHex(input?: string | null): string {
  if (!input) {
    return DEFAULT_CHAT_COLOR_HEX
  }
  let hex = input.trim()
  if (!hex.startsWith('#')) {
    hex = `#${hex}`
  }
  if (hex.length === 4) {
    const [, r, g, b] = hex
    return `#${r}${r}${g}${g}${b}${b}`.toUpperCase()
  }
  if (hex.length !== 7) {
    return DEFAULT_CHAT_COLOR_HEX
  }
  return hex.toUpperCase()
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = normalizeHex(hex)
  const r = parseInt(normalized.slice(1, 3), 16)
  const g = parseInt(normalized.slice(3, 5), 16)
  const b = parseInt(normalized.slice(5, 7), 16)
  return [clamp(r), clamp(g), clamp(b)]
}

function rgbToHex(r: number, g: number, b: number): string {
  return `#${clamp(r).toString(16).padStart(2, '0')}${clamp(g).toString(16).padStart(2, '0')}${clamp(b).toString(16).padStart(2, '0')}`.toUpperCase()
}

function hexToRgbaString(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function adjustHex(hex: string, ratio: number): string {
  const [r, g, b] = hexToRgb(hex)
  if (ratio >= 0) {
    return rgbToHex(
      Math.round(r + (255 - r) * ratio),
      Math.round(g + (255 - g) * ratio),
      Math.round(b + (255 - b) * ratio),
    )
  }
  const factor = 1 - Math.abs(ratio)
  return rgbToHex(Math.round(r * factor), Math.round(g * factor), Math.round(b * factor))
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex)
  const transform = (channel: number) => {
    const c = channel / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  const rLin = transform(r)
  const gLin = transform(g)
  const bLin = transform(b)
  return 0.2126 * rLin + 0.7152 * gLin + 0.0722 * bLin
}

export function normalizeHexColor(input?: string | null): string {
  return normalizeHex(input)
}

export function buildUserChatPalette(baseHex?: string | null): { cssVars: CSSVariableProperties } {
  const base = normalizeHex(baseHex)
  const lighter = adjustHex(base, 0.35)
  const darker = adjustHex(base, -0.25)
  const border = adjustHex(base, -0.3)
  const textIsLight = relativeLuminance(base) <= 0.55

  const bubbleText = textIsLight ? '#F8FAFC' : '#0F172A'
  const authorColor = textIsLight ? 'rgba(255, 255, 255, 0.78)' : adjustHex(base, -0.55)
  const metaColor = textIsLight ? 'rgba(226, 232, 240, 0.9)' : adjustHex(base, -0.4)
  const attachmentBg = textIsLight ? 'rgba(255, 255, 255, 0.2)' : adjustHex(base, 0.65)
  const attachmentHoverBg = textIsLight ? 'rgba(255, 255, 255, 0.28)' : adjustHex(base, 0.5)
  const attachmentColor = textIsLight ? '#EDE9FE' : adjustHex(base, -0.65)
  const badgeBg = textIsLight ? 'rgba(255, 255, 255, 0.22)' : adjustHex(base, 0.55)
  const badgeBorder = textIsLight ? 'rgba(255, 255, 255, 0.38)' : adjustHex(base, 0.4)
  const badgeText = textIsLight ? 'rgba(255, 255, 255, 0.9)' : adjustHex(base, -0.65)

  const gradient = `linear-gradient(135deg, ${lighter} 0%, ${base} 55%, ${darker} 100%)`

  const cssVars: CSSVariableProperties = {
    '--user-bubble-bg': gradient,
    '--user-bubble-border': border,
    '--user-bubble-text': bubbleText,
    '--user-author-color': authorColor,
    '--user-content-color': bubbleText,
    '--user-meta-color': metaColor,
    '--user-attachment-bg': attachmentBg,
    '--user-attachment-hover-bg': attachmentHoverBg,
    '--user-attachment-color': attachmentColor,
    '--user-channel-badge-bg': badgeBg,
    '--user-channel-badge-border': badgeBorder,
    '--user-channel-badge-text': badgeText,
  }

  return { cssVars }
}

export function buildAgentComposerPalette(baseHex?: string | null): { cssVars: CSSVariableProperties } {
  const base = normalizeHex(baseHex)
  const lighter = adjustHex(base, 0.35)
  const darker = adjustHex(base, -0.25)
  const darkest = adjustHex(base, -0.45)
  const textIsLight = relativeLuminance(base) <= 0.55
  const textColor = textIsLight ? '#F8FAFC' : '#0F172A'

  const cssVars: CSSVariableProperties = {
    '--composer-accent': base,
    '--composer-accent-light': lighter,
    '--composer-accent-dark': darker,
    '--composer-accent-darker': darkest,
    '--composer-accent-text': textColor,
    '--composer-accent-border': hexToRgbaString(base, 0.24),
    '--composer-accent-focus': hexToRgbaString(base, 0.35),
  }

  return { cssVars }
}
