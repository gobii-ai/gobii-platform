import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MessageContent } from './MessageContent'

const longEmailHtml = '<div>' + '<p>Reddit digest paragraph with enough text to be a real line of content.</p>'.repeat(120) + '</div>'

describe('MessageContent long email collapse (bug #504)', () => {
  it('collapses long email HTML behind a "Show full email" control', () => {
    render(<MessageContent bodyHtml={longEmailHtml} />)
    const toggle = screen.getByRole('button', { name: /show full email/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: /show less/i })).toHaveAttribute('aria-expanded', 'true')
  })

  it('renders short email HTML without a collapse control', () => {
    render(<MessageContent bodyHtml="<p>Short reply about the meeting.</p>" />)
    expect(screen.queryByRole('button', { name: /show full email/i })).not.toBeInTheDocument()
  })
})
