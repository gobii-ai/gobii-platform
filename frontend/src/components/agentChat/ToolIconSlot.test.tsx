import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Network } from 'lucide-react'

import { ToolIconSlot } from './ToolIconSlot'
import type { ToolEntryDisplay } from './tooling/types'

function entry(overrides: Partial<ToolEntryDisplay>): ToolEntryDisplay {
  return {
    id: 'entry-1',
    clusterCursor: 'step:1',
    cursor: 'step:1',
    toolName: 'http_request',
    label: 'Read Google Sheets',
    caption: 'GET • Leads!A1:D5',
    timestamp: null,
    status: 'pending',
    icon: Network,
    iconSrc: '/static/images/integrations/pipedream/google_sheets.svg',
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-700',
    parameters: null,
    rawParameters: null,
    result: '{}',
    summary: null,
    charterText: null,
    detailComponent: () => <div />,
    meta: undefined,
    sourceEntry: undefined,
    separateFromPreview: false,
    ...overrides,
  }
}

describe('ToolIconSlot', () => {
  it('keeps branded icons visible while a tool call is pending', () => {
    const { container } = render(<ToolIconSlot entry={entry({})} />)

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/static/images/integrations/pipedream/google_sheets.svg',
    )
    expect(document.querySelector('.tool-chip-spinner--icon')).not.toBeInTheDocument()
  })
})
