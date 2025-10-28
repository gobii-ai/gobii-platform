import type { JSX } from 'react'
import { useCallback, useMemo, useState } from 'react'

type ModalRenderer = (onClose: () => void) => JSX.Element

export function useModal(): [JSX.Element | null, (renderer: ModalRenderer) => void, () => void] {
  const [renderer, setRenderer] = useState<ModalRenderer | null>(null)

  const close = useCallback(() => {
    setRenderer(null)
  }, [])

  const showModal = useCallback((nextRenderer: ModalRenderer) => {
    setRenderer(() => nextRenderer)
  }, [])

  const modal = useMemo(() => {
    if (!renderer) {
      return null
    }
    return renderer(close)
  }, [renderer, close])

  return [modal, showModal, close]
}
