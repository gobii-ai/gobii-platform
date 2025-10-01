import DOMPurify from 'dompurify'

export function sanitizeHtml(value: string): string {
  if (!value) return ''
  if (typeof window === 'undefined') {
    return value
  }
  return DOMPurify.sanitize(value, { USE_PROFILES: { html: true } })
}

export function looksLikeHtml(value: string | null | undefined): boolean {
  if (!value) return false
  return /<([a-z][\w-]*)(?:\s[^>]*)?>/i.test(value.trim())
}
