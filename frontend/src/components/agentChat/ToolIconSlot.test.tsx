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

  it('keeps the Apollo branded icon visible while a tool call is pending', () => {
    const { container } = render(<ToolIconSlot entry={entry({
      label: 'Search Apollo people',
      caption: 'POST • people search',
      iconSrc: '/static/images/integrations/native/apollo.svg',
      iconBgClass: 'bg-[#F8FF2C]',
      iconColorClass: 'text-slate-950',
    })} />)

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/static/images/integrations/native/apollo.svg',
    )
    expect(document.querySelector('.tool-chip-spinner--icon')).not.toBeInTheDocument()
  })

  it('keeps the HubSpot branded icon visible while a tool call is pending', () => {
    const { container } = render(<ToolIconSlot entry={entry({
      label: 'Search HubSpot contacts',
      caption: 'POST • contacts search',
      iconSrc: '/static/images/integrations/native/hubspot.svg',
      iconBgClass: 'bg-orange-100',
      iconColorClass: 'text-orange-700',
    })} />)

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/static/images/integrations/native/hubspot.svg',
    )
    expect(document.querySelector('.tool-chip-spinner--icon')).not.toBeInTheDocument()
  })

  it('keeps the Discord branded icon visible while a tool call is pending', () => {
    const { container } = render(<ToolIconSlot entry={entry({
      label: 'Send Discord message',
      caption: 'Shipping the report now.',
      iconSrc: '/static/images/integrations/native/discord.svg',
      iconBgClass: 'bg-indigo-100',
      iconColorClass: 'text-indigo-700',
    })} />)

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/static/images/integrations/native/discord.svg',
    )
    expect(document.querySelector('.tool-chip-spinner--icon')).not.toBeInTheDocument()
  })

  it('keeps the Telegram branded icon visible while a tool call is pending', () => {
    const { container } = render(<ToolIconSlot entry={entry({
      label: 'Send Telegram message',
      caption: 'I posted the update.',
      iconSrc: '/static/images/integrations/native/telegram.svg',
      iconBgClass: 'bg-sky-100',
      iconColorClass: 'text-sky-700',
    })} />)

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/static/images/integrations/native/telegram.svg',
    )
    expect(document.querySelector('.tool-chip-spinner--icon')).not.toBeInTheDocument()
  })
})
