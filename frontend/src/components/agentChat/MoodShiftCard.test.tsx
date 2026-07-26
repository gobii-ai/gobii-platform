/**
 * A mood change used to reach the timeline as "Database query, 1 statement" -- the agent's feeling
 * rendered as a row update, indistinguishable from a SELECT beside it. These pin what the card
 * must say, and in particular that it never claims a mood it does not know.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MoodShiftCard } from './MoodShiftCard'
import { chatActions } from '../../store/chatSlice'
import { createTestAppStore, StoreProvider } from '../../test/storeTestUtils'
import type { ToolEntryDisplay } from './tooling/types'

function renderCard(entry: Partial<ToolEntryDisplay>, agentName = 'Sweep Probe') {
  const store = createTestAppStore()
  store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))
  store.dispatch(chatActions.agentIdentityUpdated({ agentId: 'agent-1', agentName }))
  return render(
    <MoodShiftCard entry={{ id: 'e1', timestamp: '2026-07-26T12:00:00Z', ...entry } as ToolEntryDisplay} />,
    { wrapper: ({ children }) => <StoreProvider store={store}>{children}</StoreProvider> },
  )
}

describe('MoodShiftCard', () => {
  it('shows the feeling itself rather than the write that carried it', () => {
    const { container } = renderCard({ emotion: '\u{1F60A}' })

    expect(screen.getByText(/is feeling/i)).toBeInTheDocument()
    expect(container.querySelector('.mood-shift__glyph')?.textContent).toBe('\u{1F60A}')
    expect(container.textContent).not.toMatch(/database query|sqlite|__agent_config/i)
  })

  it('says how long the mood is meant to last when that is known', () => {
    renderCard({ emotion: '\u{1F60A}', emotionTimeoutSeconds: 3600 })

    expect(screen.getByText('for an hour')).toBeInTheDocument()
  })

  it('omits the duration when none was given', () => {
    const { container } = renderCard({ emotion: '\u{1F60A}' })

    expect(container.querySelector('.mood-shift__meta')).toBeNull()
  })

  it('reads a cleared mood as settling, with no face to show', () => {
    const { container } = renderCard({ emotion: null })

    expect(screen.getByText(/let their mood settle/i)).toBeInTheDocument()
    expect(container.querySelector('.mood-shift__glyph')).toBeNull()
    expect(container.querySelector('.mood-shift')?.getAttribute('data-cleared')).toBe('true')
  })

  it('gives different feelings visibly different halos', () => {
    // Emoji sit in dense contiguous blocks, so a naive modulo mapped neighbours to the same hue
    // and every mood glowed identically.
    const hueOf = (emoji: string) => {
      const { container } = renderCard({ emotion: emoji })
      return Number(container.querySelector<HTMLElement>('.mood-shift')?.style.getPropertyValue('--mood-hue'))
    }

    const happy = hueOf('\u{1F60A}')
    const unsure = hueOf('\u{1F615}')

    expect(Math.abs(happy - unsure)).toBeGreaterThan(20)
  })

  it('gives the same feeling the same halo every time', () => {
    const hueOf = () => {
      const { container } = renderCard({ emotion: '\u{1F525}' })
      return container.querySelector<HTMLElement>('.mood-shift')?.style.getPropertyValue('--mood-hue')
    }

    expect(hueOf()).toBe(hueOf())
  })
})
