import { describe, expect, it } from 'vitest'

import { repairUnclosedFence, splitMarkdownBlocks } from './streamingMarkdownBlocks'

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
