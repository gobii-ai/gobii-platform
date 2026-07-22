import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { StarterPromptSuggestions } from './StarterPromptSuggestions'

describe('StarterPromptSuggestions', () => {
  it('offers an accessible way to dismiss the current suggestions', () => {
    const onDismiss = vi.fn()

    render(
      <StarterPromptSuggestions
        prompts={[{
          id: 'follow-up-1',
          text: 'Summarize the next steps',
          category: 'deliverables',
        }]}
        onDismiss={onDismiss}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss suggested follow-ups' }))

    expect(onDismiss).toHaveBeenCalledOnce()
  })
})
