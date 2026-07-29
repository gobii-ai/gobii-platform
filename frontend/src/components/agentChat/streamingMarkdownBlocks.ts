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
 * syntax (bug #510 follow-up: the tail must be *markdown*, not plain text). Rule set
 * follows Vercel's remend (the streamdown repair pipeline): close constructs whose
 * meaning is already known (fences, bold, italic, inline code, strikethrough — with
 * half-complete-closer and bare-marker guards), render incomplete links as their text
 * (never a fabricated URL), drop incomplete images and trailing half-typed HTML tags,
 * and neutralize a nascent setext underline so the previous paragraph doesn't flash as
 * a giant heading. Repairs apply only to the in-flight tail block; the final message
 * always renders the raw text.
 */
export function repairIncompleteMarkdown(block: string): string {
  const fenceRepaired = repairUnclosedFence(block)
  if (fenceRepaired !== block) {
    // Inside a code block: fence closure is the only safe repair.
    return fenceRepaired
  }

  let repaired = block

  // Trailing half-typed HTML tag ("text <sp") — hide it until the '>' arrives.
  repaired = repaired.replace(/<[a-zA-Z/][^>]*$/, '')

  // Incomplete image: remove entirely (a partial URL would fire a broken request).
  repaired = repaired.replace(/!\[[^\]]*(?:\]\([^)]*)?$/, '')
  // Incomplete link URL: keep the text flowing, drop the link markup ("text-only" mode).
  repaired = repaired.replace(/\[([^\]]*)\]\([^)]*$/, '$1')
  // Incomplete link text: strip just the opening bracket.
  repaired = repaired.replace(/(^|[^\\])\[([^\]]*)$/, '$1$2')

  // A lone "-"/"=" line under a paragraph is a setext underline mid-flight: the whole
  // previous paragraph would flash as an <h1>/<h2>. A zero-width space keeps it inert.
  const lines = repaired.split('\n')
  if (lines.length >= 2) {
    const last = lines[lines.length - 1]
    const prev = lines[lines.length - 2]
    if (/^\s{0,3}(-{1,2}|={1,2})\s*$/.test(last) && prev.trim() !== '') {
      repaired += '\u200B'
    }
  }

  // Scan outside fenced code and inline code spans, counting unbalanced markers.
  let inCode = false
  let lastLineEndedInCode = false
  let inFence = false
  let bold = 0
  let italicStar = 0
  let italicUnderscore = 0
  let underscoreDouble = 0
  let strike = 0
  for (const line of repaired.split('\n')) {
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
        if (line[i + 1] === '_') {
          underscoreDouble += 1
          i += 1
          continue
        }
        const prevCh = i > 0 ? line[i - 1] : ' '
        const nextCh = i + 1 < line.length ? line[i + 1] : ' '
        // Intra-word underscores (snake_case) are not emphasis.
        if (!/[A-Za-z0-9]/.test(prevCh) || !/[A-Za-z0-9]/.test(nextCh)) {
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

  // A closer is only appended when there is real content after the dangling opener —
  // a bare trailing "**" stays literal until text follows (remend guard). Half-complete
  // closers ("**text*", "~~text~", "__text_") get the one missing character, not a
  // fresh pair.
  const hasContentAfter = (marker: string): boolean => {
    const index = repaired.lastIndexOf(marker)
    if (index < 0) {
      return false
    }
    return !/^[\s_~*`]*$/.test(repaired.slice(index + marker.length))
  }

  if (bold % 2 === 1) {
    if (/\*\*(?:[^*]|\*(?!\*))+\*$/.test(repaired)) {
      repaired += '*'
    } else if (hasContentAfter('**')) {
      repaired += '**'
    }
  }
  if (italicStar % 2 === 1 && hasContentAfter('*')) {
    repaired += '*'
  }
  if (underscoreDouble % 2 === 1) {
    if (/__(?:[^_]|_(?!_))+_$/.test(repaired)) {
      repaired += '_'
    } else if (hasContentAfter('__')) {
      repaired += '__'
    }
  }
  if (italicUnderscore % 2 === 1 && hasContentAfter('_')) {
    repaired += '_'
  }
  if (strike % 2 === 1) {
    if (/~~(?:[^~]|~(?!~))+~$/.test(repaired)) {
      repaired += '~'
    } else if (hasContentAfter('~~')) {
      repaired += '~~'
    }
  }
  return repaired
}
