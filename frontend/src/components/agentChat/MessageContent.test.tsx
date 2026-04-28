import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MessageContent } from './MessageContent'

describe('MessageContent', () => {
  it('forwards plain link clicks to the link handler', () => {
    const onLinkClick = vi.fn(() => true)

    render(
      <MessageContent
        bodyText="[Open settings](/console/agents/agent-123/)"
        onLinkClick={onLinkClick}
      />,
    )

    fireEvent.click(screen.getByRole('link', { name: 'Open settings' }))

    expect(onLinkClick).toHaveBeenCalledWith('/console/agents/agent-123/')
  })

  it('ignores modified link clicks', () => {
    const onLinkClick = vi.fn(() => true)

    render(
      <MessageContent
        bodyText="[Open settings](/console/agents/agent-123/)"
        onLinkClick={onLinkClick}
      />,
    )

    fireEvent.click(screen.getByRole('link', { name: 'Open settings' }), { metaKey: true })

    expect(onLinkClick).not.toHaveBeenCalled()
  })
})
