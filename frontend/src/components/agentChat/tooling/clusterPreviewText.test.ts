/**
 * A planning step's reasoning is shown over three lines while it streams, then collapsed to the
 * first clause of a single-line caption the moment it finished -- so the timeline lost the thought
 * exactly when it became complete, and the card read as a label with nothing under it. These pin
 * the summary that replaced it.
 */
import { describe, expect, it } from 'vitest'

import { deriveThinkingSummary } from './clusterPreviewText'
import type { ToolEntryDisplay } from './types'

function thinking(reasoning: unknown, toolName = 'thinking'): ToolEntryDisplay {
  return { id: 't1', toolName, result: reasoning } as unknown as ToolEntryDisplay
}

describe('deriveThinkingSummary', () => {
  it('keeps reasoning that runs past its first line', () => {
    // The whole defect: only the opening line survived, so multi-line thinking lost its substance.
    const summary = deriveThinkingSummary(thinking(
      'Let me process the current state.\nThe user asked for a pipeline summary.\nI will query the CRM table.',
    ))

    expect(summary).toContain('pipeline summary')
    expect(summary).toContain('CRM table')
  })

  it('collapses the line breaks so it reads as one thought', () => {
    const summary = deriveThinkingSummary(thinking('First line.\n\n   Second line.'))

    expect(summary).toBe('First line. Second line.')
  })

  it('ends on a sentence rather than mid-word when it has to cut', () => {
    const long = `${'This is a complete sentence about the pipeline. '.repeat(12)}trailing fragment that runs on`

    const summary = deriveThinkingSummary(thinking(long)) ?? ''

    expect(summary.endsWith('.')).toBe(true)
    expect(summary).not.toContain('…')
    expect(summary.length).toBeLessThanOrEqual(260)
  })

  it('falls back to an ellipsis when there is no sentence to end on', () => {
    const summary = deriveThinkingSummary(thinking('x'.repeat(400))) ?? ''

    expect(summary.endsWith('…')).toBe(true)
    expect(summary.length).toBeLessThanOrEqual(261)
  })

  it('strips bold markers so the summary reads as prose', () => {
    const summary = deriveThinkingSummary(thinking('**Plan:** query the table'))

    expect(summary).toBe('Plan: query the table')
  })

  it('says nothing for entries that are not thinking', () => {
    expect(deriveThinkingSummary(thinking('Some reasoning', 'sqlite_batch'))).toBeNull()
  })

  it('says nothing when the reasoning is empty or not text', () => {
    expect(deriveThinkingSummary(thinking('   '))).toBeNull()
    expect(deriveThinkingSummary(thinking(null))).toBeNull()
    expect(deriveThinkingSummary(thinking({ status: 'ok' }))).toBeNull()
  })
})
