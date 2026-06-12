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

function clusterForApplyPatch(): ToolClusterEvent {
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
        id: 'tool-call-apply-patch',
        cursor: 'step:1',
        timestamp: '2026-01-01T00:00:00Z',
        toolName: 'apply_patch',
        meta: {
          label: 'Apply patch',
          iconPaths: [],
          iconBg: '',
          iconColor: '',
        },
        parameters: {
          patch: [
            '*** Begin Patch',
            '*** Update File: /tools/greeter.py',
            '@@',
            "-    return {'message': 'hi'}",
            "+    return {'message': 'hello'}",
            '*** End Patch',
          ].join('\n'),
        },
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
  it('renders apply_patch as a live patch preview with the target file path', () => {
    renderPreview(clusterForApplyPatch())

    expect(screen.getByText('Apply patch')).toBeInTheDocument()
    expect(screen.getByText('/tools/greeter.py')).toBeInTheDocument()
  })

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

  it('does not render Apollo API calls as browsing the API host', () => {
    renderPreview(
      clusterForRequest('https://api.apollo.io/api/v1/mixed_people/api_search', 'POST'),
    )

    expect(screen.getByText('Search Apollo people')).toBeInTheDocument()
    expect(screen.queryByText('Browsing')).not.toBeInTheDocument()
    expect(screen.queryByText('api.apollo.io')).not.toBeInTheDocument()
  })
})
