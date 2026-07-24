export type PetAnimationName =
  | 'idle'
  | 'running-right'
  | 'running-left'
  | 'waving'
  | 'jumping'
  | 'failed'
  | 'waiting'
  | 'running'
  | 'review'

export type PetAnimationSpec = {
  row: number
  durations: number[]
}

export const PET_ANIMATIONS: Record<PetAnimationName, PetAnimationSpec> = {
  idle: { row: 0, durations: [280, 110, 110, 140, 140, 320] },
  'running-right': { row: 1, durations: [120, 120, 120, 120, 120, 120, 120, 220] },
  'running-left': { row: 2, durations: [120, 120, 120, 120, 120, 120, 120, 220] },
  waving: { row: 3, durations: [140, 140, 140, 280] },
  jumping: { row: 4, durations: [140, 140, 140, 140, 280] },
  failed: { row: 5, durations: [140, 140, 140, 140, 140, 140, 140, 240] },
  waiting: { row: 6, durations: [150, 150, 150, 150, 150, 260] },
  running: { row: 7, durations: [120, 120, 120, 120, 120, 220] },
  review: { row: 8, durations: [150, 150, 150, 150, 150, 280] },
}

const NEGATIVE_EMOTIONS = [
  '😞', '😔', '😟', '😢', '😭', '😣', '😖', '😫', '😩', '😓', '😥', '😰',
  '😨', '😱', '😡', '😠', '🤬', '💔', '❌', '⚠', '🫠', '😵', '🤕', '🤒',
]
const FOCUSED_EMOTIONS = ['🤔', '🧐', '🤓', '👀', '🔍', '🧠', '💭']
const WAITING_EMOTIONS = ['😕', '😬', '🤨', '❓', '⌛', '⏳']
const CELEBRATING_EMOTIONS = [
  '🥳', '🎉', '🎊', '🚀', '🔥', '🤩', '😆', '😁', '😄', '😃', '🙌', '💪',
  '🏆', '✨', '⚡', '✅',
]

function containsAny(value: string, candidates: string[]): boolean {
  return candidates.some((candidate) => value.includes(candidate))
}

export function animationForEmotion(emotion: string | null): PetAnimationName | null {
  if (!emotion) return null
  if (containsAny(emotion, NEGATIVE_EMOTIONS)) return 'failed'
  if (containsAny(emotion, FOCUSED_EMOTIONS)) return 'review'
  if (containsAny(emotion, WAITING_EMOTIONS)) return 'waiting'
  if (containsAny(emotion, CELEBRATING_EMOTIONS)) return 'jumping'
  return 'waving'
}

export function resolvePetAnimation({
  processingActive,
  emotion,
  emotionExpiresAt,
  now,
}: {
  processingActive: boolean
  emotion: string | null
  emotionExpiresAt: string | null
  now: number
}): PetAnimationName {
  if (processingActive) return 'running'
  if (!emotion || !emotionExpiresAt) return 'idle'
  const expiresAt = Date.parse(emotionExpiresAt)
  if (!Number.isFinite(expiresAt) || expiresAt <= now) return 'idle'
  return animationForEmotion(emotion) ?? 'idle'
}
