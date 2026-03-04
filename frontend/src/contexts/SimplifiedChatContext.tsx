import { createContext, useContext, type ReactNode } from 'react'

const SimplifiedChatContext = createContext(false)

export function SimplifiedChatProvider({ value, children }: { value: boolean; children: ReactNode }) {
  return <SimplifiedChatContext.Provider value={value}>{children}</SimplifiedChatContext.Provider>
}

export function useSimplifiedChat(): boolean {
  return useContext(SimplifiedChatContext)
}
