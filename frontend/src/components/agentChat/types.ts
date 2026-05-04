import type {
  AgentMessage,
  AgentTimelineSnapshot,
  TimelineEvent,
  MessageEvent,
  ToolClusterEvent,
  ToolCallEntry,
  ThinkingEvent,
  PlanEvent,
  PlanStepChange,
  PlanSnapshot,
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
  PlanEvent,
  PlanStepChange,
  PlanSnapshot,
  AgentMessage,
}
