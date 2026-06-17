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

function clusterForToolCall(
  toolName: string,
  parameters: Record<string, unknown>,
  result = '{}',
): ToolClusterEvent {
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
        toolName,
        meta: {
          label: toolName,
          iconPaths: [],
          iconBg: '',
          iconColor: '',
        },
        parameters,
        result,
        status: 'complete',
      },
    ],
  }
}

describe('transformToolCluster Google API display', () => {
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

  it('labels Discord channel subscription actions with the official Discord icon', () => {
    const transformed = transformToolCluster(
      clusterForToolCall(
        'discord_channel_subscriptions',
        { action: 'discover_channels', guild_id: 'guild-1', query: 'support' },
        JSON.stringify({ status: 'success', channels: [{ id: 'channel-1' }, { id: 'channel-2' }] }),
      ),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Discover Discord channels',
      caption: 'support',
      summary: '2 channels',
      iconSrc: '/static/images/integrations/native/discord.svg',
    })
  })

  it('labels Discord outbound messages with the official Discord icon', () => {
    const transformed = transformToolCluster(
      clusterForToolCall(
        'send_discord_message',
        { channel_id: 'channel-1', message: 'Shipping the report now.' },
        JSON.stringify({ status: 'success', attachment_count: 1 }),
      ),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Send Discord message',
      caption: 'Shipping the report now.',
      summary: 'Channel channel-1 • 1 attachment',
      iconSrc: '/static/images/integrations/native/discord.svg',
    })
  })

  it('labels Telegram chat management with the official Telegram icon', () => {
    const transformed = transformToolCluster(
      clusterForToolCall(
        'telegram_chats',
        { action: 'status' },
        JSON.stringify({ status: 'success', bot_username: 'agent_bot', chats: [{ id: 'chat-1' }] }),
      ),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Check Telegram status',
      caption: '@agent_bot',
      summary: '1 chat',
      iconSrc: '/static/images/integrations/native/telegram.svg',
    })
  })

  it('labels Telegram outbound messages with the official Telegram icon', () => {
    const transformed = transformToolCluster(
      clusterForToolCall(
        'send_telegram_message',
        { chat_id: '-100123', message: 'I posted the update.' },
        JSON.stringify({ status: 'success', attachment_count: 0 }),
      ),
    )

    expect(transformed.entries[0]).toMatchObject({
      label: 'Send Telegram message',
      caption: 'I posted the update.',
      summary: 'Chat -100123',
      iconSrc: '/static/images/integrations/native/telegram.svg',
    })
  })
})
