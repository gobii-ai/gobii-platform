import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { EyeOff, Settings } from 'lucide-react'

import { getSelectedUserPet, type UserPetPosition, type UserPetSize } from '../../api/userPets'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useUpdateUserPetPreferences, useUserPets } from '../../hooks/useUserPets'
import { selectActiveChatSession } from '../../store/chatSlice'
import { useAppSelector } from '../../store/hooks'
import { navigateWithinApp } from '../../util/appNavigation'
import { FixedContextMenu } from '../common/FixedContextMenu'
import { PetSprite } from './PetSprite'
import {
  PET_ANIMATIONS,
  PET_SPRITESHEET_COLUMNS,
  PET_SPRITESHEET_ROWS,
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
const POINTER_IDLE_DELAY_MS = 2_500
const CLICK_HOLD_THRESHOLD_MS = 400
const DRAG_THRESHOLD_PX = 5
const CLICK_JUMP_DURATION_MS = PET_ANIMATIONS.jumping.durations.reduce(
  (total, duration) => total + duration,
  0,
) * 3
const PET_PROFILE_PATH = '/app/profile#workspace-pet'

type PixelPosition = {
  left: number
  top: number
}

type DragState = {
  pointerId: number
  offsetX: number
  offsetY: number
  startClientX: number
  startClientY: number
  lastClientX: number
  startedAt: number
  moved: boolean
  position: PixelPosition | null
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

function useAnimationColumn(
  animation: PetAnimationName,
  reducedMotion: boolean,
  restartKey: number,
): number {
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
  }, [animation, reducedMotion, restartKey])
  if (reducedMotion || animationState.animation !== animation) {
    return 0
  }
  return animationState.column
}

export function ImmersivePetLayer() {
  const isMobile = useIsMobile()
  const petsQuery = useUserPets(!isMobile)
  const preferencesMutation = useUpdateUserPetPreferences()
  const activeSession = useAppSelector(selectActiveChatSession)
  const reducedMotion = useReducedMotion()
  const petRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const gazeIdleTimerRef = useRef<number | null>(null)
  const clickJumpTimerRef = useRef<number | null>(null)
  const [viewport, setViewport] = useState(() => ({
    width: typeof window === 'undefined' ? 0 : window.innerWidth,
    height: typeof window === 'undefined' ? 0 : window.innerHeight,
  }))
  const [dragPosition, setDragPosition] = useState<PixelPosition | null>(null)
  const [dragDirection, setDragDirection] = useState<'left' | 'right'>('right')
  const [isHovered, setIsHovered] = useState(false)
  const [isClickJumping, setIsClickJumping] = useState(false)
  const [clickJumpVersion, setClickJumpVersion] = useState(0)
  const [lookIndex, setLookIndex] = useState<number | null>(null)
  const [pointerActive, setPointerActive] = useState(false)
  const [gazeEmotionKey, setGazeEmotionKey] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)
  const [expiredEmotionKey, setExpiredEmotionKey] = useState<string | null>(null)
  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  const library = petsQuery.data
  const selectedPet = getSelectedUserPet(library)
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
  const emotionAnimation = resolvePetAnimation({
    emotion: activeSession.identity.emotion,
    emotionExpiresAt: activeSession.identity.emotionExpiresAt,
    now: expiredEmotionKey === emotionKey ? Number.POSITIVE_INFINITY : 0,
  })
  const semanticAnimation = isAgentWorking ? 'running' : emotionAnimation
  const isDragging = dragPosition !== null
  const canTrackPointer = Boolean(library?.preferences.enabled && selectedPet)
    && !isMobile
    && !reducedMotion
    && !isAgentWorking
    && !isDragging
  const canGaze = canTrackPointer && pointerActive && gazeEmotionKey === emotionKey
  const animation = isAgentWorking
    ? semanticAnimation
    : isDragging
      ? (dragDirection === 'left' ? 'running-left' : 'running-right')
      : isClickJumping
        ? 'jumping'
        : isHovered
          ? 'waiting'
          : canGaze
            ? 'idle'
            : semanticAnimation
  const animatedColumn = useAnimationColumn(animation, reducedMotion, clickJumpVersion)
  const frame = useMemo(() => {
    if (animation !== 'idle' || reducedMotion) {
      return {
        row: PET_ANIMATIONS[animation].row,
        column: animatedColumn,
      }
    }
    if (canGaze && lookIndex !== null) {
      return {
        row: PET_SPRITESHEET_ROWS - 2 + Math.floor(lookIndex / PET_SPRITESHEET_COLUMNS),
        column: lookIndex % PET_SPRITESHEET_COLUMNS,
      }
    }
    return {
      row: PET_ANIMATIONS.idle.row,
      column: animatedColumn,
    }
  }, [animatedColumn, animation, canGaze, lookIndex, reducedMotion])

  useEffect(() => {
    if (!canTrackPointer) return
    const handlePointerMove = (event: PointerEvent) => {
      setPointerActive(true)
      setGazeEmotionKey(emotionKey)
      if (gazeIdleTimerRef.current !== null) {
        window.clearTimeout(gazeIdleTimerRef.current)
      }
      gazeIdleTimerRef.current = window.setTimeout(() => {
        gazeIdleTimerRef.current = null
        setPointerActive(false)
        setLookIndex(null)
      }, POINTER_IDLE_DELAY_MS)

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
    }
    document.addEventListener('pointermove', handlePointerMove)
    return () => {
      document.removeEventListener('pointermove', handlePointerMove)
    }
  }, [canTrackPointer, emotionKey])

  useEffect(() => () => {
    if (gazeIdleTimerRef.current !== null) {
      window.clearTimeout(gazeIdleTimerRef.current)
    }
    if (clickJumpTimerRef.current !== null) {
      window.clearTimeout(clickJumpTimerRef.current)
    }
  }, [])

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const rect = event.currentTarget.getBoundingClientRect()
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      startClientX: event.clientX,
      startClientY: event.clientY,
      lastClientX: event.clientX,
      startedAt: Date.now(),
      moved: false,
      position: null,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setContextMenu(null)
    setIsClickJumping(false)
    if (clickJumpTimerRef.current !== null) {
      window.clearTimeout(clickJumpTimerRef.current)
      clickJumpTimerRef.current = null
    }
  }, [])

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (!drag.moved) {
      const distance = Math.hypot(
        event.clientX - drag.startClientX,
        event.clientY - drag.startClientY,
      )
      if (distance < DRAG_THRESHOLD_PX) return
      drag.moved = true
    }
    const deltaX = event.clientX - drag.lastClientX
    if (Math.abs(deltaX) >= 1) {
      setDragDirection(deltaX < 0 ? 'left' : 'right')
    }
    drag.lastClientX = event.clientX
    drag.position = {
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
    }
    setDragPosition(drag.position)
  }, [petHeight, petWidth, viewport.height, viewport.width])

  const finishPointerGesture = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    if (event.type === 'pointerup' && drag.moved && drag.position) {
      void preferencesMutation.mutateAsync({
        position: {
          x: (drag.position.left + petWidth / 2) / viewport.width,
          y: (drag.position.top + petHeight / 2) / viewport.height,
        },
      })
    } else if (
      event.type === 'pointerup'
      && !drag.moved
      && Date.now() - drag.startedAt < CLICK_HOLD_THRESHOLD_MS
    ) {
      setIsClickJumping(true)
      setClickJumpVersion((version) => version + 1)
      clickJumpTimerRef.current = window.setTimeout(() => {
        clickJumpTimerRef.current = null
        setIsClickJumping(false)
      }, CLICK_JUMP_DURATION_MS)
    }
    setDragPosition(null)
  }, [petHeight, petWidth, preferencesMutation, viewport.height, viewport.width])

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
        onPointerEnter={() => setIsHovered(true)}
        onPointerLeave={() => setIsHovered(false)}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointerGesture}
        onPointerCancel={finishPointerGesture}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          setContextMenu({ x: event.clientX, y: event.clientY })
        }}
        title={`Click or drag ${selectedPet.displayName}`}
      >
        <PetSprite
          spritesheetUrl={selectedPet.spritesheetUrl}
          row={frame.row}
          column={frame.column}
          label={`${selectedPet.displayName} workspace pet`}
        />
      </div>
      {contextMenu ? (
        <FixedContextMenu
          position={contextMenu}
          ariaLabel="Pet actions"
          onClose={closeContextMenu}
          items={[
            {
              label: 'Options',
              icon: Settings,
              onSelect: () => navigateWithinApp(PET_PROFILE_PATH),
            },
            {
              label: 'Dismiss pet',
              icon: EyeOff,
              onSelect: () => preferencesMutation.mutate({ enabled: false }),
            },
          ]}
        />
      ) : null}
    </div>
  )
}
