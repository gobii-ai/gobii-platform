import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { StarterPromptSuggestions } from './StarterPromptSuggestions'

describe('StarterPromptSuggestions', () => {
  it('distinguishes temporary dismissal from turning off future suggestions', () => {
    const onDismiss = vi.fn()
    const onTurnOff = vi.fn()

    render(
      <StarterPromptSuggestions
        prompts={[{
          id: 'follow-up-1',
          text: 'Summarize the next steps',
          category: 'deliverables',
        }]}
        onDismiss={onDismiss}
        onTurnOff={onTurnOff}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Hide for now' }))

    expect(onDismiss).toHaveBeenCalledOnce()
    expect(onTurnOff).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Turn off suggestions' }))

    expect(onDismiss).toHaveBeenCalledOnce()
    expect(onTurnOff).toHaveBeenCalledOnce()
  })
})
