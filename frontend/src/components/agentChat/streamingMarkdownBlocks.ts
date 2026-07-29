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
