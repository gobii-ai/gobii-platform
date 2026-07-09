import type { RootState } from './appStore'
import { createInitialSession, type AgentChatSession } from './chatSlice'

const EMPTY_CHAT_SESSION = createInitialSession()

export const selectActiveChatAgentId = (state: RootState): string | null => state.chat.activeAgentId
export const selectActiveChatSession = (state: RootState): AgentChatSession => {
  const agentId = state.chat.activeAgentId
  return agentId ? state.chat.sessionsByAgentId[agentId] ?? EMPTY_CHAT_SESSION : EMPTY_CHAT_SESSION
}

export const selectCreateAgentWorkflow = (state: RootState) => state.chat.createAgent
