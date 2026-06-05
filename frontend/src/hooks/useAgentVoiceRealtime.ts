import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { createAgentVoiceRealtimeSession, sendAgentVoiceTurn, type AgentVoiceRealtimeSession } from '../api/agentChat'
import { HttpError } from '../api/http'
import type { PendingActionRequest, ProcessingWebTask, TimelineEvent, ToolCallEntry } from '../types/agentChat'

export type AgentVoiceRealtimeState =
  | 'unavailable'
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'speaking'
  | 'muted'
  | 'error'

type UseAgentVoiceRealtimeOptions = {
  agentId?: string | null
  processingActive?: boolean
  processingTasks?: ProcessingWebTask[]
  pendingActionRequests?: PendingActionRequest[]
  timelineEvents?: TimelineEvent[]
  onTimelineEvent?: (event: TimelineEvent) => void
}

type RealtimeTranscriptionEvent = {
  type?: string
  transcript?: unknown
  delta?: unknown
  item?: unknown
  item_id?: unknown
  itemId?: unknown
}

function supportsRealtimeVoice(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') {
    return false
  }
  return Boolean(
    window.RTCPeerConnection
      && navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function',
  )
}

function createRemoteAudioElement(): HTMLAudioElement {
  const audio = document.createElement('audio')
  audio.autoplay = true
  audio.setAttribute('playsinline', 'true')
  audio.style.display = 'none'
  document.body.appendChild(audio)
  return audio
}

function buildRealtimeResponseCreate(instructions: string): string {
  return JSON.stringify({
    type: 'response.create',
    response: {
      modalities: ['audio', 'text'],
      instructions,
    },
  })
}

function buildRealtimeContextUpdate(text: string): string {
  return JSON.stringify({
    type: 'conversation.item.create',
    item: {
      type: 'message',
      role: 'user',
      content: [
        {
          type: 'input_text',
          text: `Context update for the voice companion. Do not respond to this update directly.\n${text}`,
        },
      ],
    },
  })
}

function buildRealtimeSessionUpdate(session: AgentVoiceRealtimeSession): string {
  return JSON.stringify({
    type: 'session.update',
    session: {
      audio: {
        input: {
          turn_detection: {
            type: 'server_vad',
            interrupt_response: true,
          },
          transcription: {
            model: session.transcriptionModel || 'gpt-4o-transcribe-latest',
          },
        },
        output: {
          voice: session.voice,
        },
      },
    },
  })
}

function compactText(value: unknown, maxLength = 700): string {
  if (value == null) return ''
  let raw = ''
  if (typeof value === 'string') {
    raw = value
  } else {
    try {
      raw = JSON.stringify(value) || ''
    } catch {
      raw = String(value)
    }
  }
  const compacted = raw.replace(/\s+/g, ' ').trim()
  return compacted.length > maxLength ? `${compacted.slice(0, maxLength - 1).trim()}…` : compacted
}

async function parseRealtimeEventData(data: unknown): Promise<RealtimeTranscriptionEvent | null> {
  if (typeof data === 'string') {
    try {
      return JSON.parse(data) as RealtimeTranscriptionEvent
    } catch {
      return null
    }
  }
  if (data instanceof ArrayBuffer) {
    try {
      return JSON.parse(new TextDecoder().decode(data)) as RealtimeTranscriptionEvent
    } catch {
      return null
    }
  }
  if (data instanceof Blob) {
    try {
      return JSON.parse(await data.text()) as RealtimeTranscriptionEvent
    } catch {
      return null
    }
  }
  return null
}

function getRealtimeItemId(event: RealtimeTranscriptionEvent): string | undefined {
  if (typeof event.item_id === 'string') return event.item_id
  if (typeof event.itemId === 'string') return event.itemId
  const item = event.item
  if (item && typeof item === 'object') {
    const itemId = (item as { id?: unknown }).id
    if (typeof itemId === 'string') return itemId
  }
  return undefined
}

function extractTranscript(event: RealtimeTranscriptionEvent): string {
  if (typeof event.transcript === 'string') {
    return event.transcript.trim()
  }
  const item = event.item
  if (!item || typeof item !== 'object') {
    return ''
  }
  const content = (item as { content?: unknown }).content
  if (!Array.isArray(content)) {
    return ''
  }
  for (const part of content) {
    if (part && typeof part === 'object') {
      const transcript = (part as { transcript?: unknown }).transcript
      if (typeof transcript === 'string' && transcript.trim()) {
        return transcript.trim()
      }
      const text = (part as { text?: unknown }).text
      if (typeof text === 'string' && text.trim()) {
        return text.trim()
      }
    }
  }
  return ''
}

function isUserItemDoneWithTranscript(event: RealtimeTranscriptionEvent): boolean {
  if (event.type !== 'conversation.item.done') {
    return false
  }
  const item = event.item
  if (!item || typeof item !== 'object') {
    return false
  }
  const role = (item as { role?: unknown }).role
  return role === 'user' && Boolean(extractTranscript(event))
}

function timelineEventKey(event: TimelineEvent | undefined): string {
  if (!event) return ''
  if ('cursor' in event && event.cursor) return `${event.kind}:${event.cursor}`
  if (event.kind === 'message') return `${event.kind}:${event.message.id}`
  return `${event.kind}:${JSON.stringify(event).slice(0, 120)}`
}

function summarizeToolEntry(entry: ToolCallEntry): string {
  const label = entry.meta?.label || entry.toolName || 'Tool'
  const status = entry.status ? ` (${entry.status})` : ''
  const params = compactText(entry.parameters, 220)
  const result = compactText(entry.result || entry.caption || entry.summary || '', 320)
  return [
    `${label}${status}`,
    params ? `params: ${params}` : '',
    result ? `result: ${result}` : '',
  ].filter(Boolean).join('; ')
}

function summarizeTimelineEventForVoice(event: TimelineEvent | undefined): string | null {
  if (!event) return null
  if (event.kind === 'message') {
    const speaker = event.message.isOutbound ? 'Agent' : 'Human'
    const channel = event.message.channel || 'chat'
    const text = compactText(event.message.bodyText || event.message.bodyHtml || '', 700)
    return text ? `New ${speaker} message via ${channel}: ${text}` : null
  }
  if (event.kind === 'steps') {
    const summaries = event.entries.slice(-4).map(summarizeToolEntry).filter(Boolean)
    return summaries.length ? `New tool activity: ${summaries.join(' | ')}` : null
  }
  if (event.kind === 'plan' || event.kind === 'kanban') {
    const displayText = compactText(event.displayText, 500)
    return displayText ? `Plan update: ${displayText}` : null
  }
  return null
}

export function useAgentVoiceRealtime({
  agentId,
  processingActive = false,
  processingTasks = [],
  pendingActionRequests = [],
  timelineEvents = [],
  onTimelineEvent,
}: UseAgentVoiceRealtimeOptions) {
  const [state, setState] = useState<AgentVoiceRealtimeState>(() => (
    supportsRealtimeVoice() ? 'idle' : 'unavailable'
  ))
  const [muted, setMuted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const peerConnectionRef = useRef<RTCPeerConnection | null>(null)
  const dataChannelRef = useRef<RTCDataChannel | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const audioElementRef = useRef<HTMLAudioElement | null>(null)
  const mountedRef = useRef(true)
  const stateRef = useRef<AgentVoiceRealtimeState>(state)
  const mutedRef = useRef(muted)
  const processedRealtimeItemsRef = useRef<Set<string>>(new Set())
  const transcriptionDeltasRef = useRef<Map<string, string>>(new Map())
  const processingWasActiveRef = useRef(false)
  const previousTaskCountRef = useRef(0)
  const previousPendingActionCountRef = useRef(0)
  const latestTimelineEventKeyRef = useRef('')
  const pendingContextUpdatesRef = useRef<string[]>([])

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    mutedRef.current = muted
  }, [muted])

  const setVoiceState = useCallback((nextState: AgentVoiceRealtimeState) => {
    stateRef.current = nextState
    setState(nextState)
  }, [])

  const cleanup = useCallback(() => {
    const dataChannel = dataChannelRef.current
    dataChannelRef.current = null
    if (dataChannel && dataChannel.readyState !== 'closed') {
      dataChannel.close()
    }

    const peerConnection = peerConnectionRef.current
    peerConnectionRef.current = null
    if (peerConnection) {
      peerConnection.close()
    }

    const mediaStream = mediaStreamRef.current
    mediaStreamRef.current = null
    mediaStream?.getTracks().forEach((track) => track.stop())

    const audioElement = audioElementRef.current
    audioElementRef.current = null
    if (audioElement) {
      audioElement.srcObject = null
      audioElement.remove()
    }

    processedRealtimeItemsRef.current.clear()
    transcriptionDeltasRef.current.clear()
    previousTaskCountRef.current = 0
    previousPendingActionCountRef.current = 0
    processingWasActiveRef.current = false
  }, [])

  const sendRealtimeStatus = useCallback((instructions: string) => {
    const dataChannel = dataChannelRef.current
    if (!dataChannel || dataChannel.readyState !== 'open') {
      return
    }
    dataChannel.send(buildRealtimeResponseCreate(instructions))
  }, [])

  const flushPendingContextUpdates = useCallback(() => {
    const dataChannel = dataChannelRef.current
    if (!dataChannel || dataChannel.readyState !== 'open') {
      return
    }
    const pending = pendingContextUpdatesRef.current.splice(0, pendingContextUpdatesRef.current.length)
    for (const update of pending) {
      dataChannel.send(buildRealtimeContextUpdate(update))
    }
  }, [])

  const sendRealtimeContextUpdate = useCallback((text: string) => {
    const cleaned = compactText(text, 1200)
    if (!cleaned) return
    const dataChannel = dataChannelRef.current
    if (!dataChannel || dataChannel.readyState !== 'open') {
      pendingContextUpdatesRef.current.push(cleaned)
      pendingContextUpdatesRef.current = pendingContextUpdatesRef.current.slice(-20)
      return
    }
    dataChannel.send(buildRealtimeContextUpdate(cleaned))
  }, [])

  const markVoiceReady = useCallback(() => {
    if (!mountedRef.current || !peerConnectionRef.current) {
      return
    }
    setVoiceState(mutedRef.current ? 'muted' : 'listening')
  }, [setVoiceState])

  const handleTranscriptionCompleted = useCallback(async (event: RealtimeTranscriptionEvent) => {
    if (!agentId) {
      return
    }
    const realtimeItemId = getRealtimeItemId(event)
    const bufferedTranscript = realtimeItemId ? transcriptionDeltasRef.current.get(realtimeItemId) : ''
    const transcript = extractTranscript(event) || (bufferedTranscript || '').trim()
    if (!transcript) {
      return
    }
    const dedupeKey = realtimeItemId ? `item:${realtimeItemId}` : `text:${transcript}`
    if (processedRealtimeItemsRef.current.has(dedupeKey)) {
      return
    }
    processedRealtimeItemsRef.current.add(dedupeKey)
    if (realtimeItemId) {
      transcriptionDeltasRef.current.delete(realtimeItemId)
    }

    try {
      const timelineEvent = await sendAgentVoiceTurn(agentId, {
        transcript,
        realtimeItemId,
        endedAt: new Date().toISOString(),
      })
      onTimelineEvent?.(timelineEvent)
    } catch (sendError) {
      if (!mountedRef.current) {
        return
      }
      setError(sendError instanceof Error ? sendError.message : 'Could not send voice transcript.')
      setVoiceState('error')
    }
  }, [agentId, onTimelineEvent, setVoiceState])

  const handleRealtimeEvent = useCallback(async (rawEvent: MessageEvent) => {
    if (!mountedRef.current) {
      return
    }
    const event = await parseRealtimeEventData(rawEvent.data)
    if (!event) {
      return
    }

    if (event.type === 'conversation.item.input_audio_transcription.delta') {
      const realtimeItemId = getRealtimeItemId(event)
      if (realtimeItemId && typeof event.delta === 'string') {
        const previous = transcriptionDeltasRef.current.get(realtimeItemId) || ''
        transcriptionDeltasRef.current.set(realtimeItemId, previous + event.delta)
      }
      return
    }
    if (event.type === 'conversation.item.input_audio_transcription.completed') {
      void handleTranscriptionCompleted(event)
      return
    }
    if (isUserItemDoneWithTranscript(event)) {
      void handleTranscriptionCompleted(event)
      return
    }
    if (event.type === 'response.audio.delta') {
      setVoiceState(mutedRef.current ? 'muted' : 'speaking')
      return
    }
    if (
      event.type === 'response.audio.done'
      || event.type === 'response.done'
      || event.type === 'input_audio_buffer.speech_started'
    ) {
      setVoiceState(mutedRef.current ? 'muted' : 'listening')
    }
  }, [handleTranscriptionCompleted, setVoiceState])

  const disconnect = useCallback(() => {
    cleanup()
    if (mountedRef.current) {
      mutedRef.current = false
      setMuted(false)
      setError(null)
      setVoiceState(supportsRealtimeVoice() ? 'idle' : 'unavailable')
    }
  }, [cleanup, setVoiceState])

  const connect = useCallback(async () => {
    if (!agentId || stateRef.current === 'connecting') {
      return
    }
    mountedRef.current = true
    if (!supportsRealtimeVoice()) {
      setVoiceState('unavailable')
      setError('Voice mode is not available in this browser.')
      return
    }

    cleanup()
    mutedRef.current = false
    setMuted(false)
    setVoiceState('connecting')
    setError(null)

    try {
      const session = await createAgentVoiceRealtimeSession(agentId)
      const peerConnection = new RTCPeerConnection()
      peerConnectionRef.current = peerConnection

      const remoteAudio = createRemoteAudioElement()
      audioElementRef.current = remoteAudio
      peerConnection.ontrack = (event) => {
        const [remoteStream] = event.streams
        if (remoteStream) {
          remoteAudio.srcObject = remoteStream
          markVoiceReady()
        }
      }

      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      mediaStreamRef.current = mediaStream
      mediaStream.getTracks().forEach((track) => peerConnection.addTrack(track, mediaStream))

      const dataChannel = peerConnection.createDataChannel('realtime-channel')
      dataChannelRef.current = dataChannel
      dataChannel.onmessage = (event) => {
        void handleRealtimeEvent(event)
      }
      dataChannel.onopen = () => {
        if (mountedRef.current) {
          dataChannel.send(buildRealtimeSessionUpdate(session))
          setVoiceState(mutedRef.current ? 'muted' : 'listening')
          flushPendingContextUpdates()
          sendRealtimeStatus('You are connected to voice mode. Say one short sentence that you are listening.')
        }
      }
      dataChannel.onerror = () => {
        if (mountedRef.current) {
          setError('Voice data channel failed.')
          setVoiceState('error')
        }
      }

      peerConnection.onconnectionstatechange = () => {
        if (!mountedRef.current) {
          return
        }
        if (peerConnection.connectionState === 'failed' || peerConnection.connectionState === 'disconnected') {
          setError('Voice connection dropped.')
          setVoiceState('error')
          return
        }
        if (peerConnection.connectionState === 'connected') {
          markVoiceReady()
        }
      }

      peerConnection.oniceconnectionstatechange = () => {
        if (!mountedRef.current) {
          return
        }
        if (peerConnection.iceConnectionState === 'connected' || peerConnection.iceConnectionState === 'completed') {
          markVoiceReady()
        }
      }

      const offer = await peerConnection.createOffer()
      await peerConnection.setLocalDescription(offer)
      const sdpResponse = await fetch(session.callsUrl, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${session.clientSecret}`,
          'Content-Type': 'application/sdp',
        },
        body: offer.sdp ?? '',
      })
      if (!sdpResponse.ok) {
        throw new Error('Voice WebRTC handshake failed.')
      }
      const answerSdp = await sdpResponse.text()
      await peerConnection.setRemoteDescription({
        type: 'answer',
        sdp: answerSdp,
      })
      markVoiceReady()
    } catch (connectError) {
      cleanup()
      if (!mountedRef.current) {
        return
      }
      mutedRef.current = false
      setMuted(false)
      if (connectError instanceof HttpError && connectError.status === 503) {
        setVoiceState('unavailable')
        setError('Voice mode is not enabled.')
        return
      }
      setError(connectError instanceof Error ? connectError.message : 'Could not start voice mode.')
      setVoiceState('error')
    }
  }, [agentId, cleanup, handleRealtimeEvent, markVoiceReady, sendRealtimeStatus, setVoiceState])

  const toggleMute = useCallback(() => {
    const nextMuted = !mutedRef.current
    mutedRef.current = nextMuted
    setMuted(nextMuted)
    mediaStreamRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !nextMuted
    })
    if (stateRef.current !== 'idle' && stateRef.current !== 'connecting' && stateRef.current !== 'unavailable') {
      setVoiceState(nextMuted ? 'muted' : 'listening')
    }
  }, [setVoiceState])

  useEffect(() => {
    if (stateRef.current === 'idle' || stateRef.current === 'unavailable' || stateRef.current === 'connecting') {
      previousTaskCountRef.current = processingTasks.length
      previousPendingActionCountRef.current = pendingActionRequests.length
      processingWasActiveRef.current = processingActive
      return
    }

    if (processingActive && !processingWasActiveRef.current) {
      sendRealtimeStatus("I'm working on that now. Keep it under one sentence.")
    } else if (!processingActive && processingWasActiveRef.current) {
      sendRealtimeStatus('That step is complete. Keep it under one sentence.')
    }

    if (processingTasks.length > previousTaskCountRef.current && processingTasks.length > 0) {
      sendRealtimeStatus("I'm using tools now and will keep the voice updates brief.")
    }

    if (pendingActionRequests.length > previousPendingActionCountRef.current && pendingActionRequests.length > 0) {
      sendRealtimeStatus('The agent needs your input in chat before it can continue.')
    }

    processingWasActiveRef.current = processingActive
    previousTaskCountRef.current = processingTasks.length
    previousPendingActionCountRef.current = pendingActionRequests.length
  }, [pendingActionRequests.length, processingActive, processingTasks.length, sendRealtimeStatus])

  useEffect(() => {
    const latestEvent = timelineEvents[timelineEvents.length - 1]
    const latestKey = timelineEventKey(latestEvent)
    if (!latestKey) {
      return
    }
    if (stateRef.current !== 'listening' && stateRef.current !== 'speaking' && stateRef.current !== 'muted') {
      latestTimelineEventKeyRef.current = latestKey
      return
    }
    if (latestTimelineEventKeyRef.current === latestKey) {
      return
    }
    latestTimelineEventKeyRef.current = latestKey
    const summary = summarizeTimelineEventForVoice(latestEvent)
    if (summary) {
      sendRealtimeContextUpdate(summary)
    }
  }, [sendRealtimeContextUpdate, timelineEvents])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      cleanup()
    }
  }, [cleanup])

  useEffect(() => {
    disconnect()
  }, [agentId, disconnect])

  const connected = state === 'listening' || state === 'speaking' || state === 'muted'
  const available = state !== 'unavailable'

  return useMemo(() => ({
    state,
    muted,
    error,
    connected,
    available,
    connect,
    disconnect,
    toggleMute,
    sendRealtimeStatus,
  }), [available, connect, connected, disconnect, error, muted, sendRealtimeStatus, state, toggleMute])
}
