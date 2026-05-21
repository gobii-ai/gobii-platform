import type { ReactNode } from 'react'

type EmbeddedAgentShellPanelProps = {
  children: ReactNode
}

export function EmbeddedAgentShellPanel({ children }: EmbeddedAgentShellPanelProps) {
  return (
    <div className="chat-sidebar-settings-theme">
      {children}
    </div>
  )
}
