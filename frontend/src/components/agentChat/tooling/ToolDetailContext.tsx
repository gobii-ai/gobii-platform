import { createContext, useContext, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'

import type { ToolEntryDisplay } from './types'

type ToolDetailState = {
  openKey: string | null
  setOpenKey: Dispatch<SetStateAction<string | null>>
}

const ToolDetailContext = createContext<ToolDetailState | null>(null)

export function ToolDetailProvider({ children }: { children: ReactNode }) {
  const [openKey, setOpenKey] = useState<string | null>(null)

  const value = useMemo<ToolDetailState>(() => ({ openKey, setOpenKey }), [openKey, setOpenKey])

  return <ToolDetailContext.Provider value={value}>{children}</ToolDetailContext.Provider>
}

export function useToolDetailController(): ToolDetailState {
  const context = useContext(ToolDetailContext)
  if (!context) {
    throw new Error('useToolDetailController must be used within a ToolDetailProvider')
  }
  return context
}

export function entryKey(entry: Pick<ToolEntryDisplay, 'clusterCursor' | 'id'>): string {
  return `${entry.clusterCursor}::${entry.id}`
}
