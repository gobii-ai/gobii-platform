/**
 * Block splitting for streamed markdown (bug #510).
 *
 * Rendering the whole accumulated markdown on every commit re-parses everything the user
 * has already read. Split on blank lines (outside fenced code) instead: completed blocks
 * are stable strings, so a memoized renderer skips them entirely and only the growing
 * tail block ever re-parses. (The pattern and its measured ~2.3x win come from the AI SDK
 * memoization cookbook and LibreChat's streaming renderer.)
 */

const FENCE_RE = /^(\s*)(```|~~~)/

export function splitMarkdownBlocks(text: string): string[] {
  if (!text) {
    return []
  }
  const lines = text.split('\n')
  const blocks: string[] = []
  let current: string[] = []
  let inFence = false
  let fenceToken = ''
  for (const line of lines) {
    const fenceMatch = line.match(FENCE_RE)
    if (fenceMatch) {
      if (!inFence) {
        inFence = true
        fenceToken = fenceMatch[2]
      } else if (fenceMatch[2] === fenceToken) {
        inFence = false
      }
      current.push(line)
      continue
    }
    if (!inFence && line.trim() === '' && current.length > 0) {
      blocks.push(current.join('\n'))
      current = []
      continue
    }
    if (current.length === 0 && line.trim() === '') {
      continue
    }
    current.push(line)
  }
  if (current.length > 0) {
    blocks.push(current.join('\n'))
  }
  return blocks
}

/** Close an unclosed code fence so the parser never renders the rest of a message as
 *  code while the closing ``` is still in flight. Only ever applied to the tail block. */
export function repairUnclosedFence(block: string): string {
  const lines = block.split('\n')
  let inFence = false
  let fenceToken = ''
  for (const line of lines) {
    const fenceMatch = line.match(FENCE_RE)
    if (!fenceMatch) {
      continue
    }
    if (!inFence) {
      inFence = true
      fenceToken = fenceMatch[2]
    } else if (fenceMatch[2] === fenceToken) {
      inFence = false
    }
  }
  return inFence ? `${block}\n${fenceToken}` : block
}

/**
 * Make an in-flight block render as styled markdown rather than showing raw half-typed
 * syntax (bug #510 follow-up: the tail must be *markdown*, not plain text). Closes
 * constructs whose meaning is already known (fences, bold, italic, inline code,
 * strikethrough) and hides a trailing incomplete link/image (a fabricated URL would be
 * wrong; the characters reappear when the construct completes). Never touches content
 * inside code fences or inline code.
 */
export function repairIncompleteMarkdown(block: string): string {
  let repaired = repairUnclosedFence(block)
  const fenceClosed = repaired !== block
  if (fenceClosed) {
    // Inside a code block: fence closure is the only safe repair.
    return repaired
  }

  // Hide a trailing incomplete link or image: "[text](url-in-progress" or "[text-in-pr"
  repaired = repaired.replace(/!?\[[^\]]*(\]\([^)]*)?$/u, (match, _closer, offset, whole) => {
    // Keep escaped or footnote-looking brackets intact if the bracket is escaped.
    if (offset > 0 && whole[offset - 1] === '\\') {
      return match
    }
    return ''
  })

  // Scan outside code spans and count unbalanced inline markers.
  let inCode = false
  let lastLineEndedInCode = false
  let inFence = false
  let bold = 0
  let italicStar = 0
  let italicUnderscore = 0
  let strike = 0
  const lines = repaired.split('\n')
  for (const line of lines) {
    if (FENCE_RE.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence) {
      continue
    }
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i]
      if (ch === '\\') {
        i += 1
        continue
      }
      if (ch === '`') {
        inCode = !inCode
        continue
      }
      if (inCode) {
        continue
      }
      if (ch === '*') {
        if (line[i + 1] === '*') {
          bold += 1
          i += 1
        } else if (!(line.slice(0, i).trim() === '' && line[i + 1] === ' ')) {
          // A leading "* " is a list bullet, not emphasis.
          italicStar += 1
        }
      } else if (ch === '~' && line[i + 1] === '~') {
        strike += 1
        i += 1
      } else if (ch === '_') {
        const prev = i > 0 ? line[i - 1] : ' '
        const next = i + 1 < line.length ? line[i + 1] : ' '
        // Intra-word underscores (snake_case) are not emphasis.
        if (!/[A-Za-z0-9]/.test(prev) || !/[A-Za-z0-9]/.test(next)) {
          italicUnderscore += 1
        }
      }
    }
    lastLineEndedInCode = inCode
    inCode = false
  }

  if (lastLineEndedInCode) {
    repaired += '`'
  }
  if (bold % 2 === 1) {
    repaired += '**'
  }
  if (italicStar % 2 === 1) {
    repaired += '*'
  }
  if (italicUnderscore % 2 === 1) {
    repaired += '_'
  }
  if (strike % 2 === 1) {
    repaired += '~~'
  }
  return repaired
}
