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
        },
        parameters: { method, url },
        result: '{}',
        status: 'complete',
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

function clusterForAgentConfig(options: {
  sql?: string
  status?: 'pending' | 'complete' | 'error'
  result?: unknown
  charterText?: string | null
  scheduleValue?: string | null
} = {}): ToolClusterEvent {
  return {
    kind: 'steps',
    cursor: 'step:config',
    entryCount: 1,
    collapsible: false,
    collapseThreshold: 4,
    earliestTimestamp: '2026-01-01T00:00:00Z',
    latestTimestamp: '2026-01-01T00:00:00Z',
    entries: [{
      id: 'tool-call-config',
      cursor: 'step:config',
      timestamp: '2026-01-01T00:00:00Z',
      toolName: 'sqlite_batch',
      meta: { label: 'Database query' },
      parameters: {
        sql: options.sql ?? "UPDATE __agent_config SET charter=patch_text(charter, 'Old text', 'New text') WHERE id=1",
      },
      result: options.result ?? '',
      status: options.status ?? 'complete',
      charterText: options.charterText,
      scheduleValue: options.scheduleValue,
    }],
  }
}

describe('transformToolCluster Google API display', () => {
  it('preserves raw developer tool names and payloads', () => {
    const cluster = clusterForRequest('https://example.com')
    cluster.collapseThreshold = Number.POSITIVE_INFINITY
    cluster.entries[0] = {
      ...cluster.entries[0],
      toolName: 'search_web',
      parameters: { q: 'raw query' },
      result: { items: [{ id: 1 }] },
      developerEvent: {
        kind: 'tool_call',
        id: 'tool-call-1',
        timestamp: '2026-01-01T00:00:00Z',
        completion_id: null,
        tool_name: 'search_web',
        parameters: { q: 'raw query' },
        result: { items: [{ id: 1 }] },
      },
    }

    const transformed = transformToolCluster(cluster)

    expect(transformed.collapsible).toBe(false)
    expect(transformed.entries[0]).toMatchObject({
      label: 'search_web',
      rawParameters: { q: 'raw query' },
      result: { items: [{ id: 1 }] },
    })
  })

  it('renders developer steps as expandable tool-cluster entries', () => {
    const cluster = clusterForRequest('https://example.com')
    cluster.collapseThreshold = Number.POSITIVE_INFINITY
    cluster.entries[0] = {
      ...cluster.entries[0],
      toolName: '__developer_step__',
      developerEvent: {
        kind: 'step',
        id: 'step-1',
        timestamp: '2026-07-07T12:21:33Z',
        description: 'Internal reasoning: send an email instead.',
        completion_id: 'completion-1',
        is_system: false,
        system_code: null,
        system_notes: null,
      },
    }

    const transformed = transformToolCluster(cluster)

    expect(transformed.collapsible).toBe(false)
    expect(transformed.entries[0]).toMatchObject({
      label: 'Step',
      caption: 'Internal reasoning: send an email instead.',
      toolName: '__developer_step__',
    })
  })

  it('labels apply_patch previews with the target file path', () => {
    const transformed = transformToolCluster(clusterForApplyPatch())

    expect(transformed.entries[0]).toMatchObject({
      label: 'Apply patch',
      caption: '/tools/greeter.py',
    })
  })

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

  it('labels Apollo people search with the official Apollo icon', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.apollo.io/api/v1/mixed_people/api_search', 'POST'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Search Apollo people',
      caption: 'POST • people search',
      iconSrc: '/static/images/integrations/native/apollo.svg',
    })
  })

  it('labels Apollo company search', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.apollo.io/api/v1/mixed_companies/search', 'POST'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Search Apollo companies',
      caption: 'POST • company search',
      iconSrc: '/static/images/integrations/native/apollo.svg',
    })
  })

  it('labels Apollo person enrichment', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.apollo.io/api/v1/people/match', 'POST'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Enrich Apollo person',
      caption: 'POST • person enrichment',
      iconSrc: '/static/images/integrations/native/apollo.svg',
    })
  })

  it('labels unknown Apollo endpoints as Apollo API requests', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.apollo.io/api/v1/custom/reporting', 'GET'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Apollo API request',
      caption: 'GET • custom/reporting',
      iconSrc: '/static/images/integrations/native/apollo.svg',
    })
  })

  it('labels HubSpot contact search with the official HubSpot icon', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.hubapi.com/crm/v3/objects/contacts/search', 'POST'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Search HubSpot contacts',
      caption: 'POST • contacts search',
      iconSrc: '/static/images/integrations/native/hubspot.svg',
    })
  })

  it('labels HubSpot company creation', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.hubapi.com/crm/v3/objects/companies', 'POST'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Create HubSpot company',
      caption: 'POST • companies',
      iconSrc: '/static/images/integrations/native/hubspot.svg',
    })
  })

  it('labels HubSpot deal updates', () => {
    const transformed = transformToolCluster(
      clusterForRequest('https://api.hubapi.com/crm/v3/objects/deals/deal_123', 'PATCH'),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Update HubSpot deal',
      caption: 'PATCH • deal_123',
      iconSrc: '/static/images/integrations/native/hubspot.svg',
    })
  })
})

describe('transformToolCluster agent config display', () => {
  it.each([
    ['pending update', clusterForAgentConfig({ status: 'pending' })],
    ['failed update', clusterForAgentConfig({ status: 'error', result: { status: 'error' } })],
    ['unconfirmed successful update', clusterForAgentConfig({ result: { status: 'ok' } })],
    ['patch preview', clusterForAgentConfig({
      sql: "SELECT patch_text(charter, 'Old text', 'New text') FROM __agent_config WHERE id=1",
      result: { status: 'ok', results: [] },
    })],
  ])('keeps a %s as a database query', (_name, cluster) => {
    const transformed = transformToolCluster(cluster)

    expect(transformed.entries).toHaveLength(1)
    expect(transformed.entries[0]).toMatchObject({ label: 'Database query' })
  })

  it('renders a confirmed charter update with its persisted snapshot', () => {
    const transformed = transformToolCluster(clusterForAgentConfig({
      charterText: 'Full persisted assignment',
      result: {
        status: 'ok',
        agent_config_update: {
          updated_fields: ['charter'],
          unchanged_fields: [],
          errors: {},
        },
      },
    }))

    expect(transformed.entries).toHaveLength(1)
    expect(transformed.entries[0]).toMatchObject({
      label: 'Assignment updated',
      charterText: 'Full persisted assignment',
      agentConfigCharterChange: {
        previousText: 'Old text',
        replacementText: 'New text',
      },
      agentConfigConfirmation: { charter: 'updated' },
    })
  })

  it('labels an error-free unchanged charter as already current', () => {
    const transformed = transformToolCluster(clusterForAgentConfig({
      charterText: 'Full persisted assignment',
      result: JSON.stringify({
        status: 'ok',
        agent_config_update: {
          updated_fields: [],
          unchanged_fields: ['charter'],
          errors: {},
        },
      }),
    }))

    expect(transformed.entries[0]).toMatchObject({
      label: 'Assignment already current',
      agentConfigConfirmation: { charter: 'unchanged' },
    })
    expect(transformed.entries[0].agentConfigCharterChange).toBeNull()
  })

  it('suppresses a failed charter field while displaying a confirmed schedule field', () => {
    const transformed = transformToolCluster(clusterForAgentConfig({
      sql: "UPDATE __agent_config SET charter='Rejected', schedule='0 9 * * *' WHERE id=1",
      scheduleValue: '0 9 * * *',
      result: {
        status: 'ok',
        agent_config_update: {
          updated_fields: ['charter', 'schedule'],
          unchanged_fields: [],
          errors: { charter: 'Rejected charter' },
        },
      },
    }))

    expect(transformed.entries).toHaveLength(1)
    expect(transformed.entries[0]).toMatchObject({
      label: 'Schedule updated',
      agentConfigConfirmation: { schedule: 'updated' },
      scheduleValue: '0 9 * * *',
      charterText: null,
    })
  })
})
