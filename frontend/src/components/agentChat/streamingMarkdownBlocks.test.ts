import { describe, expect, it } from 'vitest'

import { repairIncompleteMarkdown, repairUnclosedFence, splitMarkdownBlocks } from './streamingMarkdownBlocks'

describe('splitMarkdownBlocks', () => {
  it('splits on blank lines', () => {
    expect(splitMarkdownBlocks('para one\n\npara two\n\npara three')).toEqual([
      'para one',
      'para two',
      'para three',
    ])
  })

  it('keeps blank lines inside code fences in one block', () => {
    const text = 'intro\n\n```python\ndef f():\n\n    return 1\n```\n\noutro'
    expect(splitMarkdownBlocks(text)).toEqual([
      'intro',
      '```python\ndef f():\n\n    return 1\n```',
      'outro',
    ])
  })

  it('is append-stable: earlier blocks do not change as text grows', () => {
    const first = splitMarkdownBlocks('alpha\n\nbeta gro')
    const second = splitMarkdownBlocks('alpha\n\nbeta grown longer\n\ngamma')
    expect(second[0]).toBe(first[0])
    expect(second).toHaveLength(3)
  })

  it('handles empty and whitespace-only input', () => {
    expect(splitMarkdownBlocks('')).toEqual([])
    expect(splitMarkdownBlocks('\n\n')).toEqual([])
  })
})

describe('repairUnclosedFence', () => {
  it('closes an unclosed fence so the tail never renders as runaway code', () => {
    expect(repairUnclosedFence('```js\nconst a = 1')).toBe('```js\nconst a = 1\n```')
  })

  it('leaves balanced fences alone', () => {
    const block = '```js\nconst a = 1\n```'
    expect(repairUnclosedFence(block)).toBe(block)
  })

  it('matches the fence token style', () => {
    expect(repairUnclosedFence('~~~\ntext')).toBe('~~~\ntext\n~~~')
  })

  it('leaves plain text untouched', () => {
    expect(repairUnclosedFence('just words **bold')).toBe('just words **bold')
  })
})

// The in-flight tail block must render as styled markdown, never as raw half-typed
// syntax (#510 follow-up).
describe('repairIncompleteMarkdown', () => {
  it('closes unfinished bold so styling applies mid-word', () => {
    expect(repairIncompleteMarkdown('This is **very import')).toBe('This is **very import**')
  })

  it('closes unfinished italic and inline code', () => {
    expect(repairIncompleteMarkdown('some *empha')).toBe('some *empha*')
    expect(repairIncompleteMarkdown('run `npm insta')).toBe('run `npm insta`')
  })

  it('closes strikethrough', () => {
    expect(repairIncompleteMarkdown('was ~~wro')).toBe('was ~~wro~~')
  })

  it('renders an incomplete link as its text, never a fabricated URL', () => {
    expect(repairIncompleteMarkdown('see [the docs](https://exa')).toBe('see the docs')
    expect(repairIncompleteMarkdown('see [the do')).toBe('see the do')
  })

  it('drops an incomplete image entirely', () => {
    expect(repairIncompleteMarkdown('shot: ![diagram](https://exa')).toBe('shot: ')
    expect(repairIncompleteMarkdown('shot: ![diagr')).toBe('shot: ')
  })

  it('hides a trailing half-typed html tag', () => {
    expect(repairIncompleteMarkdown('line break <br')).toBe('line break ')
  })

  it('neutralizes a nascent setext underline so the paragraph does not flash as a heading', () => {
    expect(repairIncompleteMarkdown('Some paragraph\n-')).toBe('Some paragraph\n-\u200B')
    expect(repairIncompleteMarkdown('Some paragraph\n=')).toBe('Some paragraph\n=\u200B')
  })

  it('leaves a bare trailing opener literal until content follows', () => {
    expect(repairIncompleteMarkdown('ends with **')).toBe('ends with **')
  })

  it('completes a half-complete bold closer with one star, not a pair', () => {
    expect(repairIncompleteMarkdown('a **bold thing*')).toBe('a **bold thing**')
  })

  it('leaves complete links alone', () => {
    const text = 'see [docs](https://example.com) for more'
    expect(repairIncompleteMarkdown(text)).toBe(text)
  })

  it('closes an open code fence and repairs nothing inside it', () => {
    expect(repairIncompleteMarkdown('```py\nx = "**bold** is code')).toBe('```py\nx = "**bold** is code\n```')
  })

  it('does not treat list bullets as emphasis', () => {
    const text = '* first item\n* second ite'
    expect(repairIncompleteMarkdown(text)).toBe(text)
  })

  it('does not treat snake_case as italic', () => {
    const text = 'call send_chat_message now'
    expect(repairIncompleteMarkdown(text)).toBe(text)
  })

  it('leaves balanced text untouched', () => {
    const text = 'A **bold** and *italic* and `code` sentence.'
    expect(repairIncompleteMarkdown(text)).toBe(text)
  })
})
