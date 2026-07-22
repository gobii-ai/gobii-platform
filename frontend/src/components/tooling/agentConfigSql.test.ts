import { describe, expect, it } from 'vitest'

import { parseAgentConfigCharterChange } from './agentConfigSql'

describe('parseAgentConfigCharterChange', () => {
  it('parses patch_text clauses containing escaped quotes, commas, and parentheses', () => {
    const parsed = parseAgentConfigCharterChange([
      "UPDATE __agent_config SET charter=patch_text(charter, 'Old, where (quoted) ''clause''', 'New) value, ''quoted''') WHERE id=1",
    ])

    expect(parsed).toEqual({
      previousText: "Old, where (quoted) 'clause'",
      replacementText: "New) value, 'quoted'",
    })
  })

  it('uses a later patch as the safe fallback instead of an earlier literal value', () => {
    const parsed = parseAgentConfigCharterChange([
      "UPDATE __agent_config SET charter='Initial assignment' WHERE id=1",
      "UPDATE __agent_config SET charter=patch_text(charter, 'Initial', 'Revised') WHERE id=1",
    ])

    expect(parsed?.replacementText).toBe('Revised')
  })

  it('does not treat a charter predicate as a charter assignment', () => {
    const parsed = parseAgentConfigCharterChange([
      "UPDATE __agent_config SET schedule='0 9 * * *' WHERE charter='Unchanged assignment'",
    ])

    expect(parsed).toBeNull()
  })

  it('does not reconstruct literal charter assignments', () => {
    const parsed = parseAgentConfigCharterChange([
      "UPDATE __agent_config SET charter='' WHERE id=1",
    ])

    expect(parsed).toBeNull()
  })
})
