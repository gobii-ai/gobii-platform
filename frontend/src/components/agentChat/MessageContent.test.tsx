import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MessageContent } from './MessageContent'

describe('MessageContent', () => {
  it('wraps html tables in a table-specific scroll container', () => {
    render(
      <MessageContent
        bodyHtml={`
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Fit</th>
                <th>Category</th>
                <th>Suggested opener</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>BatchLeads</td>
                <td>4.2</td>
                <td>Real Estate</td>
                <td>Your team researches properties daily, so this should remain readable.</td>
              </tr>
            </tbody>
          </table>
        `}
      />,
    )

    const table = screen.getByRole('table')
    const tableScrollContainer = table.parentElement
    const htmlContainer = table.closest('.chat-html-content')

    expect(tableScrollContainer).toHaveClass('chat-html-table-scroll')
    expect(htmlContainer).not.toBeNull()
    expect(htmlContainer).toHaveClass('not-prose')
    expect(screen.getByRole('columnheader', { name: 'Suggested opener' })).toBeInTheDocument()
  })

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
