import type {
  AgentMessage,
  DeveloperMessageEvent,
  DeveloperToolCallEvent,
  TimelineEvent,
  ToolCallEntry,
  ToolClusterEvent,
} from '../../types/agentChat'

export function developerMessageToAgentMessage(event: DeveloperMessageEvent): AgentMessage {
  return {
    id: event.id,
    cursor: event.cursor,
    bodyHtml: event.body_html ?? undefined,
    bodyText: event.body_text ?? undefined,
    isOutbound: event.is_outbound,
    channel: event.channel ?? undefined,
    timestamp: event.timestamp,
    attachments: event.attachments.map((attachment) => ({
      id: attachment.id,
      filename: attachment.filename,
      url: attachment.url,
      downloadUrl: attachment.download_url,
      filespacePath: attachment.filespace_path,
      filespaceNodeId: attachment.filespace_node_id,
      fileSizeLabel: attachment.file_size_label,
    })),
    isPeer: Boolean(event.peer_agent || event.peer_link_id),
    peerAgent: event.peer_agent,
    peerLinkId: event.peer_link_id,
    selfAgentName: event.self_agent_name,
  }
}

function developerToolEntry(event: DeveloperToolCallEvent): ToolCallEntry {
  return {
    id: event.id,
    cursor: event.cursor,
    meta: { label: event.tool_name || 'Tool call' },
    toolName: event.tool_name || 'tool',
    timestamp: event.timestamp,
    parameters: event.parameters,
    result: event.result,
    status: 'complete',
    developerRaw: true,
    developerExecutionDurationMs: event.execution_duration_ms,
  }
}

export function groupDeveloperToolCalls(events: TimelineEvent[]): TimelineEvent[] {
  const grouped: TimelineEvent[] = []
  let activeCluster: ToolClusterEvent | null = null

  for (const event of events) {
    if (event.kind !== 'developer_tool_call') {
      activeCluster = null
      grouped.push(event)
      continue
    }

    if (!activeCluster) {
      activeCluster = {
        kind: 'steps',
        cursor: event.cursor,
        entryCount: 0,
        collapsible: false,
        collapseThreshold: Number.POSITIVE_INFINITY,
        earliestTimestamp: event.timestamp,
        latestTimestamp: event.timestamp,
        entries: [],
      }
      grouped.push(activeCluster)
    }

    activeCluster.entries.push(developerToolEntry(event))
    activeCluster.entryCount = activeCluster.entries.length
    activeCluster.latestTimestamp = event.timestamp
  }

  return grouped
}
