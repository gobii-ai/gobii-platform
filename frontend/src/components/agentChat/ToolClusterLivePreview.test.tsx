import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ToolClusterLivePreview } from './ToolClusterLivePreview'
import { transformToolCluster } from './tooling/toolRegistry'
import type { ToolClusterEvent } from '../../types/agentChat'

vi.mock('../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { processingActive: boolean }) => unknown) => selector({ processingActive: true }),
}))

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
        status: 'pending',
      },
    ],
  }
}

function renderPreview(cluster: ToolClusterEvent) {
  return render(
    <ToolClusterLivePreview
      cluster={transformToolCluster(cluster)}
      isLatestEvent
      onOpenTimeline={vi.fn()}
      onSelectEntry={vi.fn()}
    />,
  )
}

describe('ToolClusterLivePreview Google API display', () => {
  it('does not render Google Drive file discovery as generic web search', () => {
    renderPreview(
      clusterForRequest(
        "https://www.googleapis.com/drive/v3/files?q=mimeType%20%3D%20'application%2Fvnd.google-apps.spreadsheet'",
      ),
    )

    expect(screen.getByText('Search Google Drive')).toBeInTheDocument()
    expect(screen.queryByText('Searching web')).not.toBeInTheDocument()
  })

  it('does not render Google Sheets API calls as browsing the API host', () => {
    renderPreview(
      clusterForRequest('https://sheets.googleapis.com/v4/spreadsheets/sheet-123/values/Leads!A1:D5'),
    )

    expect(screen.getByText('Read Google Sheets')).toBeInTheDocument()
    expect(screen.queryByText('Browsing')).not.toBeInTheDocument()
    expect(screen.queryByText('sheets.googleapis.com')).not.toBeInTheDocument()
  })
})
