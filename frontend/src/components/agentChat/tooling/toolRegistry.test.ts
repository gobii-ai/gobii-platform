import { describe, expect, it } from 'vitest'

import { transformToolCluster } from './toolRegistry'
import type { ToolClusterEvent } from '../../../types/agentChat'

function clusterForRequest(url: string, method = 'GET'): ToolClusterEvent {
  return {
    kind: 'steps',
    cursor: 'step:1',
    entryCount: 1,
    collapsible: false,
    collapseThreshold: 4,
    earliestTimestamp: '2026-01-01T00:00:00Z',
    latestTimestamp: '2026-01-01T00:00:00Z',
    entries: [
      {
        id: 'tool-call-1',
        cursor: 'step:1',
        timestamp: '2026-01-01T00:00:00Z',
        toolName: 'http_request',
        meta: {
          label: 'API request',
          iconPaths: [],
          iconBg: '',
          iconColor: '',
        },
        parameters: { method, url },
        result: '{}',
        status: 'complete',
      },
    ],
  }
}

describe('transformToolCluster Google API display', () => {
  it('labels Google Sheets values reads with the official Sheets icon', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads!A1:D5'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Read Google Sheets',
      caption: 'GET • Leads!A1:D5',
      iconSrc: '/static/images/integrations/pipedream/google_sheets.svg',
    })
  })

  it('labels Google Sheets appends', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads!A:D:append', 'POST'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Append to Google Sheets',
      caption: 'POST • Leads!A:D',
      iconSrc: '/static/images/integrations/pipedream/google_sheets.svg',
    })
  })

  it('tolerates malformed percent escapes in Google Sheets ranges', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/%25%20Complete!A:B%bad'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Read Google Sheets',
      caption: 'GET • %25%20Complete!A:B%bad',
      iconSrc: '/static/images/integrations/pipedream/google_sheets.svg',
    })
  })

  it('labels Google Drive file discovery with the official Drive icon', () => {
    const transformed = transformToolCluster(
      clusterForRequest(
        "https://www.googleapis.com/drive/v3/files?q=mimeType%20%3D%20'application%2Fvnd.google-apps.spreadsheet'",
      ),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Search Google Drive',
      caption: 'GET • file discovery',
      iconSrc: '/static/images/integrations/native/google_drive.svg',
    })
  })
})
