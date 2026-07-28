/**
 * #405: every planning card read "Let me understand the current situation:" because the preview
 * took the first line of reasoning — the lead-in — while the actual thought sat one line down.
 */
import { describe, expect, it } from 'vitest'

import { deriveThinkingPreview } from './clusterPreviewText'
import type { ToolEntryDisplay } from './types'

function thinkingEntry(reasoning: string): ToolEntryDisplay {
  return { id: 't1', toolName: 'thinking', result: reasoning } as unknown as ToolEntryDisplay
}

describe('deriveThinkingPreview', () => {
  it('skips a lead-in line and previews the actual thought', () => {
    const preview = deriveThinkingPreview(thinkingEntry(
      'Let me understand the current situation:\nThe user asked for a weekly digest and none has been scheduled yet.',
    ))
    expect(preview).toBe('The user asked for a weekly digest and none has been scheduled yet.')
  })

  it('skips colon-terminated openers regardless of wording', () => {
    const preview = deriveThinkingPreview(thinkingEntry(
      'Assessing the state of the pipeline:\nThree prospects are ready for outreach.',
    ))
    expect(preview).toBe('Three prospects are ready for outreach.')
  })

  it('falls back to the first line when everything looks like a lead-in', () => {
    const preview = deriveThinkingPreview(thinkingEntry('Okay, let me see'))
    expect(preview).toBe('Okay, let me see')
  })

  it('previews a single substantive line unchanged', () => {
    const preview = deriveThinkingPreview(thinkingEntry('The deploy finished and the probe is green.'))
    expect(preview).toBe('The deploy finished and the probe is green.')
  })
})
