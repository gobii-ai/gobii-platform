import { describe, expect, it } from 'vitest'

import { parseAgentConfigCharterChange, parseCharterLiteralForDisplay } from './agentConfigSql'

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

// #247: the historical detail card needs the assignment text even when it was written as
// a direct literal (pre-snapshot rows) — display-only, never fed to the confirmation flow.
describe('parseCharterLiteralForDisplay', () => {
  it('recovers a direct literal assignment with escaped quotes', () => {
    const text = parseCharterLiteralForDisplay([
      "UPDATE __agent_config SET charter='Track ''priority'' leads daily' WHERE id=1",
    ])

    expect(text).toBe("Track 'priority' leads daily")
  })

  it('ignores charter predicates and patch_text calls', () => {
    expect(parseCharterLiteralForDisplay([
      "UPDATE __agent_config SET schedule='0 9 * * *' WHERE charter='Unchanged'",
    ])).toBeNull()
    expect(parseCharterLiteralForDisplay([
      "UPDATE __agent_config SET charter=patch_text(charter, 'a', 'b') WHERE id=1",
    ])).toBeNull()
  })

  it('uses the last literal when several statements write the charter', () => {
    expect(parseCharterLiteralForDisplay([
      "UPDATE __agent_config SET charter='First' WHERE id=1",
      "UPDATE __agent_config SET charter='Second' WHERE id=1",
    ])).toBe('Second')
  })
})
