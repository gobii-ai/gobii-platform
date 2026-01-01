import DOMPurify from 'dompurify'

export function sanitizeHtml(value: string): string {
  if (!value) return ''
  if (typeof window === 'undefined') {
    return value
  }
  // Explicitly add table tags to the allowed list alongside html profile
  return DOMPurify.sanitize(value, {
    USE_PROFILES: { html: true },
    ADD_TAGS: ['table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col'],
    ADD_ATTR: ['colspan', 'rowspan', 'scope', 'headers'],
  })
}

export function looksLikeHtml(value: string | null | undefined): boolean {
  if (!value) return false
  return /<([a-z][\w-]*)(?:\s[^>]*)?>/i.test(value.trim())
}
