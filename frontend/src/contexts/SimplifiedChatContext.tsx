import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

type SimplifiedChatContextValue = {
  enabled: boolean
  toggleAvailable: boolean
  setEnabled: (value: boolean) => void
}

const SimplifiedChatContext = createContext<SimplifiedChatContextValue>({
  enabled: false,
  toggleAvailable: false,
  setEnabled: () => undefined,
})

type SimplifiedChatProviderProps = {
  initialEnabled: boolean
  toggleAvailable?: boolean
  children: ReactNode
}

export function SimplifiedChatProvider({
  initialEnabled,
  toggleAvailable = false,
  children,
}: SimplifiedChatProviderProps) {
  const [enabled, setEnabledState] = useState(initialEnabled)

  useEffect(() => {
    setEnabledState(initialEnabled)
  }, [initialEnabled])

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value)
  }, [])

  const value = useMemo(
    () => ({
      enabled,
      toggleAvailable,
      setEnabled,
    }),
    [enabled, setEnabled, toggleAvailable],
  )

  return <SimplifiedChatContext.Provider value={value}>{children}</SimplifiedChatContext.Provider>
}

export function useSimplifiedChat(): SimplifiedChatContextValue {
  return useContext(SimplifiedChatContext)
}
