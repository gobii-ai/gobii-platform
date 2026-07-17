import { describe, expect, it } from 'vitest'

import { parseAgentConfigUpdates } from './agentConfigSql'

describe('parseAgentConfigUpdates', () => {
  it('parses patch_text clauses containing escaped quotes, commas, and parentheses', () => {
    const parsed = parseAgentConfigUpdates([
      "UPDATE __agent_config SET charter=patch_text(charter, 'Old, where (quoted) ''clause''', 'New) value, ''quoted''') WHERE id=1",
    ])

    expect(parsed?.charterChange).toEqual({
      previousText: "Old, where (quoted) 'clause'",
      replacementText: "New) value, 'quoted'",
    })
  })

  it('uses a later patch as the safe fallback instead of an earlier literal value', () => {
    const parsed = parseAgentConfigUpdates([
      "UPDATE __agent_config SET charter='Initial assignment' WHERE id=1",
      "UPDATE __agent_config SET charter=patch_text(charter, 'Initial', 'Revised') WHERE id=1",
    ])

    expect(parsed?.charterValue).toBeNull()
    expect(parsed?.charterChange?.replacementText).toBe('Revised')
  })

  it('does not treat a charter predicate as a charter assignment', () => {
    const parsed = parseAgentConfigUpdates([
      "UPDATE __agent_config SET schedule='0 9 * * *' WHERE charter='Unchanged assignment'",
    ])

    expect(parsed).toMatchObject({ updatesCharter: false, updatesSchedule: true })
  })

  it('preserves an explicitly empty literal assignment', () => {
    const parsed = parseAgentConfigUpdates([
      "UPDATE __agent_config SET charter='' WHERE id=1",
    ])

    expect(parsed).toMatchObject({ updatesCharter: true, charterValue: '' })
  })
})
