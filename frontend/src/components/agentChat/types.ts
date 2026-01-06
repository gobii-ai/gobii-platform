import type {
  AgentMessage,
  AgentTimelineSnapshot,
  TimelineEvent,
  MessageEvent,
  ToolClusterEvent,
  ToolCallEntry,
  ThinkingEvent,
  KanbanEvent,
  KanbanCardChange,
  KanbanBoardSnapshot,
} from '../../types/agentChat'

export type AgentTimelineProps = AgentTimelineSnapshot & {
  agentFirstName: string
}

export type {
  TimelineEvent,
  MessageEvent,
  ToolClusterEvent,
  ToolCallEntry,
  ThinkingEvent,
  KanbanEvent,
  KanbanCardChange,
  KanbanBoardSnapshot,
  AgentMessage,
}
