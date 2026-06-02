import { describe, expect, it } from 'vitest'
import { Search } from 'lucide-react'
import type { ReactElement } from 'react'

import { deriveActivityEntryPresentation } from './activityPresentation'
import type { ToolEntryDisplay } from './types'

function entry(overrides: Partial<ToolEntryDisplay>): ToolEntryDisplay {
  return {
    id: 'entry-1',
    clusterCursor: 'step:1',
    cursor: 'step:1',
    toolName: 'search_tools',
    label: 'Tool search',
    caption: 'Google Sheets enabled',
    timestamp: null,
    status: 'complete',
    icon: Search,
    iconSrc: null,
    iconBgClass: 'bg-blue-100',
    iconColorClass: 'text-blue-600',
    parameters: null,
    rawParameters: null,
    result: '{}',
    summary: null,
    charterText: null,
    detailComponent: () => null as unknown as ReactElement,
    meta: undefined,
    sourceEntry: undefined,
    separateFromPreview: false,
    ...overrides,
  }
}

describe('deriveActivityEntryPresentation', () => {
  it('uses the live search label for modal search rows', () => {
    expect(deriveActivityEntryPresentation(entry({}))).toMatchObject({
      label: 'Searching tools',
      caption: 'Google Sheets enabled',
    })
  })

  it('leaves Google Sheets API labels unchanged', () => {
    expect(deriveActivityEntryPresentation(entry({
      toolName: 'http_request',
      label: 'Read Google Sheets',
      caption: 'GET • Leads!A1:D5',
      iconSrc: '/static/images/integrations/pipedream/google_sheets.svg',
    }))).toMatchObject({
      label: 'Read Google Sheets',
      caption: 'GET • Leads!A1:D5',
    })
  })
})
