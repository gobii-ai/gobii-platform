import DOMPurify from 'dompurify'

export function sanitizeHtml(value: string): string {
  if (!value) return ''
  if (typeof window === 'undefined') {
    return value
  }
  // Explicitly add table and img tags to the allowed list alongside html profile
  return DOMPurify.sanitize(value, {
    USE_PROFILES: { html: true },
    ADD_TAGS: ['table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'img'],
    ADD_ATTR: ['colspan', 'rowspan', 'scope', 'headers', 'src', 'alt', 'width', 'height'],
  })
}

/**
 * Check if content appears to be HTML rather than markdown.
 *
 * Only returns true for content with block-level HTML tags (p, div, table, etc.),
 * NOT for inline elements like <br>, <a>, <img> which are commonly used within markdown.
 * This prevents markdown content with <br> tags from being incorrectly treated as HTML.
 */
export function looksLikeHtml(value: string | null | undefined): boolean {
  if (!value) return false
  // Block-level tags that indicate actual HTML content
  const blockTagPattern = /<(p|div|table|thead|tbody|tr|td|th|ul|ol|li|blockquote|pre|h[1-6]|section|article|header|footer|nav|main|form|dl|dt|dd|figure|figcaption|hr|address)(?:\s[^>]*)?>/i
  return blockTagPattern.test(value.trim())
}

// Quote characters that might wrap blockquote content redundantly
const OPENING_QUOTES = new Set(['"', '\u201c', '\u201e', '\u00ab', '\u2039', '\u2018', "'"])
const QUOTE_PAIRS: Record<string, string> = {
  '"': '"',
  '\u201c': '\u201d', // " → "
  '\u201e': '\u201d', // „ → "
  '\u00ab': '\u00bb', // « → »
  '\u2039': '\u203a', // ‹ → ›
  "'": "'",
  '\u2018': '\u2019', // ' → '
}

/**
 * Strip redundant quotation marks from markdown blockquotes.
 * When LLMs write `> "text..."`, the blockquote styling already indicates a quote,
 * so the literal quotes are redundant.
 */
export function stripBlockquoteQuotes(value: string): string {
  if (!value) return ''

  const lines = value.split('\n')
  const result: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Check if this is a blockquote line
    if (line.trimStart().startsWith('>')) {
      // Collect all consecutive blockquote lines
      const blockquoteLines: string[] = []
      while (i < lines.length && lines[i].trimStart().startsWith('>')) {
        blockquoteLines.push(lines[i])
        i++
      }

      // Process the blockquote block
      const processed = processBlockquoteBlock(blockquoteLines)
      result.push(...processed)
    } else {
      result.push(line)
      i++
    }
  }

  return result.join('\n')
}

function processBlockquoteBlock(lines: string[]): string[] {
  if (lines.length === 0) return lines

  // Parse each line into prefix and content
  const parsed = lines.map((line) => {
    const stripped = line.trimStart()
    const prefixSpaces = line.slice(0, line.length - stripped.length)
    if (stripped.startsWith('>')) {
      const afterMarker = stripped.slice(1)
      if (afterMarker.startsWith(' ')) {
        return { prefix: prefixSpaces + '> ', content: afterMarker.slice(1) }
      }
      return { prefix: prefixSpaces + '>', content: afterMarker }
    }
    return { prefix: '', content: line }
  })

  // Check if first line content starts with an opening quote
  const firstContent = parsed[0].content.trimStart()
  if (!firstContent) return lines

  const firstChar = firstContent[0]
  if (!OPENING_QUOTES.has(firstChar)) return lines

  // Find expected closing quote
  const expectedClose = QUOTE_PAIRS[firstChar] || firstChar

  // Check if last line ends with closing quote
  const lastContent = parsed[parsed.length - 1].content.trimEnd()
  if (!lastContent || lastContent[lastContent.length - 1] !== expectedClose) return lines

  // Strip the quotes
  if (parsed.length === 1) {
    let content = parsed[0].content.trim()
    if (content.length >= 2 && content[0] === firstChar && content[content.length - 1] === expectedClose) {
      content = content.slice(1, -1).trim()
    }
    return [parsed[0].prefix + content]
  }

  // Multi-line blockquote
  return parsed.map(({ prefix, content }, idx) => {
    let newContent = content
    if (idx === 0) {
      newContent = newContent.trimStart()
      if (newContent && newContent[0] === firstChar) {
        newContent = newContent.slice(1).trimStart()
      }
    }
    if (idx === parsed.length - 1) {
      newContent = newContent.trimEnd()
      if (newContent && newContent[newContent.length - 1] === expectedClose) {
        newContent = newContent.slice(0, -1).trimEnd()
      }
    }
    return prefix + newContent
  })
}
