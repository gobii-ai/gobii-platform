import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { createPortal } from 'react-dom'
import { EyeOff, Settings } from 'lucide-react'

import type { UserPetPosition, UserPetSize } from '../../api/userPets'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useUpdateUserPetPreferences, useUserPets } from '../../hooks/useUserPets'
import { selectActiveChatSession } from '../../store/chatSlice'
import { useAppSelector } from '../../store/hooks'
import { navigateWithinApp } from '../../util/appNavigation'
import { PetSprite } from './PetSprite'
import {
  PET_ANIMATIONS,
  resolvePetAnimation,
  type PetAnimationName,
} from './petAnimation'
import './immersivePet.css'

const PET_WIDTH_BY_SIZE: Record<UserPetSize, number> = {
  small: 72,
  medium: 96,
  large: 128,
}
const PET_ASPECT_RATIO = 208 / 192
const VIEWPORT_MARGIN = 16
const DEFAULT_EDGE_GAP = 24
const PET_LOOK_DEADZONE_PX = 1
const CONTEXT_MENU_WIDTH = 208
const CONTEXT_MENU_HEIGHT = 104
const CONTEXT_MENU_MARGIN = 8
const PET_PROFILE_PATH = '/app/profile#workspace-pet'

type PixelPosition = {
  left: number
  top: number
}

type DragState = {
  pointerId: number
  offsetX: number
  offsetY: number
  lastClientX: number
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum))
}

function pixelPositionForPreference(
  position: UserPetPosition | null,
  viewportWidth: number,
  viewportHeight: number,
  petWidth: number,
  petHeight: number,
): PixelPosition {
  const left = position
    ? position.x * viewportWidth - petWidth / 2
    : viewportWidth - petWidth - DEFAULT_EDGE_GAP
  const top = position
    ? position.y * viewportHeight - petHeight / 2
    : viewportHeight - petHeight - DEFAULT_EDGE_GAP
  return {
    left: clamp(left, VIEWPORT_MARGIN, viewportWidth - petWidth - VIEWPORT_MARGIN),
    top: clamp(top, VIEWPORT_MARGIN, viewportHeight - petHeight - VIEWPORT_MARGIN),
  }
}

function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(false)
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReducedMotion(media.matches)
    sync()
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [])
  return reducedMotion
}

function useAnimationColumn(animation: PetAnimationName, reducedMotion: boolean): number {
  const [animationState, setAnimationState] = useState<{
    animation: PetAnimationName
    column: number
  }>({ animation, column: 0 })
  useEffect(() => {
    if (reducedMotion) return
    const durations = PET_ANIMATIONS[animation].durations
    let currentColumn = 0
    let timeoutId: number
    const schedule = () => {
      timeoutId = window.setTimeout(() => {
        currentColumn = (currentColumn + 1) % durations.length
        setAnimationState({ animation, column: currentColumn })
        schedule()
      }, durations[currentColumn])
    }
    schedule()
    return () => window.clearTimeout(timeoutId)
  }, [animation, reducedMotion])
  if (reducedMotion || animationState.animation !== animation) {
    return 0
  }
  return animationState.column
}

export function ImmersivePetLayer() {
  const petsQuery = useUserPets()
  const preferencesMutation = useUpdateUserPetPreferences()
  const activeSession = useAppSelector(selectActiveChatSession)
  const isMobile = useIsMobile()
  const reducedMotion = useReducedMotion()
  const petRef = useRef<HTMLDivElement | null>(null)
  const contextMenuRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const gazeFrameRef = useRef<number | null>(null)
  const [viewport, setViewport] = useState(() => ({
    width: typeof window === 'undefined' ? 0 : window.innerWidth,
    height: typeof window === 'undefined' ? 0 : window.innerHeight,
  }))
  const [dragPosition, setDragPosition] = useState<PixelPosition | null>(null)
  const [dragDirection, setDragDirection] = useState<'left' | 'right'>('right')
  const [lookIndex, setLookIndex] = useState<number | null>(null)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)
  const [expiredEmotionKey, setExpiredEmotionKey] = useState<string | null>(null)

  const library = petsQuery.data
  const selectedPet = useMemo(() => (
    library?.pets.find((pet) => pet.id === library.preferences.selectedPetId)
      ?? library?.pets[0]
      ?? null
  ), [library])
  const petWidth = library ? PET_WIDTH_BY_SIZE[library.preferences.size] : PET_WIDTH_BY_SIZE.medium
  const petHeight = petWidth * PET_ASPECT_RATIO
  const persistedPosition = library?.preferences.position ?? null
  const pixelPosition = dragPosition ?? pixelPositionForPreference(
    persistedPosition,
    viewport.width,
    viewport.height,
    petWidth,
    petHeight,
  )

  useEffect(() => {
    const handleResize = () => {
      setViewport({ width: window.innerWidth, height: window.innerHeight })
      setDragPosition(null)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    const expiresAt = activeSession.identity.emotionExpiresAt
      ? Date.parse(activeSession.identity.emotionExpiresAt)
      : Number.NaN
    if (!Number.isFinite(expiresAt)) return
    const emotionKey = `${activeSession.identity.emotion ?? ''}|${activeSession.identity.emotionExpiresAt}`
    const timeoutId = window.setTimeout(
      () => setExpiredEmotionKey(emotionKey),
      Math.max(0, expiresAt - Date.now()),
    )
    return () => window.clearTimeout(timeoutId)
  }, [activeSession.identity.emotion, activeSession.identity.emotionExpiresAt])

  const emotionKey = `${activeSession.identity.emotion ?? ''}|${activeSession.identity.emotionExpiresAt ?? ''}`
  const isAgentWorking = Boolean(
    activeSession.processing.processingActive
    || activeSession.processing.awaitingResponse
    || (activeSession.stream.streaming && !activeSession.stream.streaming.done),
  )
  const semanticAnimation = resolvePetAnimation({
    processingActive: isAgentWorking,
    emotion: activeSession.identity.emotion,
    emotionExpiresAt: activeSession.identity.emotionExpiresAt,
    now: expiredEmotionKey === emotionKey ? Number.POSITIVE_INFINITY : 0,
  })
  const isDragging = dragPosition !== null
  const canGaze = Boolean(library?.preferences.enabled && selectedPet)
    && !isMobile
    && !reducedMotion
    && semanticAnimation === 'idle'
    && !isDragging
  const animation = semanticAnimation === 'idle' && isDragging
    ? (dragDirection === 'left' ? 'running-left' : 'running-right')
    : semanticAnimation
  const animatedColumn = useAnimationColumn(animation, reducedMotion)
  const frame = useMemo(() => {
    if (animation !== 'idle' || reducedMotion) {
      return {
        row: PET_ANIMATIONS[animation].row,
        column: animatedColumn,
      }
    }
    if (canGaze && lookIndex !== null) {
      return {
        row: lookIndex < 8 ? 9 : 10,
        column: lookIndex % 8,
      }
    }
    return {
      row: PET_ANIMATIONS.idle.row,
      column: animatedColumn,
    }
  }, [animatedColumn, animation, canGaze, lookIndex, reducedMotion])

  useEffect(() => {
    if (!canGaze) return
    const handlePointerMove = (event: PointerEvent) => {
      if (gazeFrameRef.current !== null) {
        window.cancelAnimationFrame(gazeFrameRef.current)
      }
      gazeFrameRef.current = window.requestAnimationFrame(() => {
        gazeFrameRef.current = null
        const rect = petRef.current?.getBoundingClientRect()
        if (!rect) return
        const dx = event.clientX - (rect.left + rect.width / 2)
        const dy = event.clientY - (rect.top + rect.height / 2)
        if (Math.hypot(dx, dy) <= PET_LOOK_DEADZONE_PX) {
          setLookIndex(null)
          return
        }
        const clockwiseFromUp = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360
        setLookIndex(Math.round(clockwiseFromUp / 22.5) % 16)
      })
    }
    document.addEventListener('pointermove', handlePointerMove)
    return () => {
      document.removeEventListener('pointermove', handlePointerMove)
      if (gazeFrameRef.current !== null) {
        window.cancelAnimationFrame(gazeFrameRef.current)
        gazeFrameRef.current = null
      }
    }
  }, [canGaze])

  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    const handlePointerDown = (event: PointerEvent) => {
      if (contextMenuRef.current?.contains(event.target as Node)) {
        return
      }
      close()
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('pointerdown', handlePointerDown, true)
    document.addEventListener('keydown', handleKeyDown, true)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    contextMenuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true)
      document.removeEventListener('keydown', handleKeyDown, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [contextMenu])

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const rect = event.currentTarget.getBoundingClientRect()
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      lastClientX: event.clientX,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setContextMenu(null)
    setDragPosition({ left: rect.left, top: rect.top })
  }, [])

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const deltaX = event.clientX - drag.lastClientX
    if (Math.abs(deltaX) >= 1) {
      setDragDirection(deltaX < 0 ? 'left' : 'right')
    }
    drag.lastClientX = event.clientX
    setDragPosition({
      left: clamp(
        event.clientX - drag.offsetX,
        VIEWPORT_MARGIN,
        viewport.width - petWidth - VIEWPORT_MARGIN,
      ),
      top: clamp(
        event.clientY - drag.offsetY,
        VIEWPORT_MARGIN,
        viewport.height - petHeight - VIEWPORT_MARGIN,
      ),
    })
  }, [petHeight, petWidth, viewport.height, viewport.width])

  const finishDrag = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId || !dragPosition) return
    dragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    const position = {
      x: (dragPosition.left + petWidth / 2) / viewport.width,
      y: (dragPosition.top + petHeight / 2) / viewport.height,
    }
    void preferencesMutation.mutateAsync({ position })
    setDragPosition(null)
  }, [dragPosition, petHeight, petWidth, preferencesMutation, viewport.height, viewport.width])

  if (
    isMobile
    || !library
    || !library.preferences.enabled
    || !selectedPet
    || viewport.width <= 0
    || viewport.height <= 0
  ) {
    return null
  }

  const style = {
    left: pixelPosition.left,
    top: pixelPosition.top,
    width: petWidth,
  } as CSSProperties

  return (
    <div className="immersive-pet-layer" aria-live="off">
      <div
        ref={petRef}
        className="immersive-pet"
        data-dragging={isDragging ? 'true' : 'false'}
        style={style}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          setContextMenu({
            x: clamp(
              event.clientX,
              CONTEXT_MENU_MARGIN,
              viewport.width - CONTEXT_MENU_WIDTH - CONTEXT_MENU_MARGIN,
            ),
            y: clamp(
              event.clientY,
              CONTEXT_MENU_MARGIN,
              viewport.height - CONTEXT_MENU_HEIGHT - CONTEXT_MENU_MARGIN,
            ),
          })
        }}
        title={`Drag ${selectedPet.displayName}`}
      >
        <PetSprite
          spritesheetUrl={selectedPet.spritesheetUrl}
          row={frame.row}
          column={frame.column}
          label={`${selectedPet.displayName} workspace pet`}
        />
      </div>
      {contextMenu && typeof document !== 'undefined' ? createPortal(
        <div
          ref={contextMenuRef}
          className="immersive-pet__context-menu agent-roster-context-menu sidebar-settings__menu"
          role="menu"
          aria-label="Pet actions"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            role="menuitem"
            className="sidebar-settings__link agent-roster-context-menu__item"
            onClick={() => {
              setContextMenu(null)
              navigateWithinApp(PET_PROFILE_PATH)
            }}
          >
            <Settings className="sidebar-settings__link-icon" aria-hidden="true" />
            <span>Options</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="sidebar-settings__link agent-roster-context-menu__item"
            onClick={() => {
              setContextMenu(null)
              preferencesMutation.mutate({ enabled: false })
            }}
          >
            <EyeOff className="sidebar-settings__link-icon" aria-hidden="true" />
            <span>Dismiss pet</span>
          </button>
        </div>,
        document.body,
      ) : null}
    </div>
  )
}
