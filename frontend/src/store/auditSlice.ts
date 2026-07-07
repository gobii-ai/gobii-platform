import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit'

import { fetchAuditEvents, fetchAuditTimeline } from '../api/agentAudit'
import type { AuditEvent, AuditTimelineBucket } from '../types/agentAudit'
import { pickHtmlCandidate, sanitizeHtml } from '../util/sanitize'
import type { RootState } from './appStore'

export type AuditState = {
  agentId: string | null
  events: AuditEvent[]
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  error: string | null
  processingActive: boolean
  timeline: AuditTimelineBucket[]
  timelineLoading: boolean
  timelineError: string | null
  selectedTimestamp: string | null
}

export const initialAuditState: AuditState = {
  agentId: null,
  events: [],
  nextCursor: null,
  hasMore: false,
  loading: false,
  error: null,
  processingActive: false,
  timeline: [],
  timelineLoading: false,
  timelineError: null,
  selectedTimestamp: null,
}

function mergeEvents(existing: AuditEvent[], incoming: AuditEvent[]): AuditEvent[] {
  const map = new Map<string, AuditEvent>()
  for (const event of existing) {
    const key = `${event.kind}:${(event as any).id}`
    map.set(key, event)
  }
  for (const event of incoming) {
    const key = `${event.kind}:${(event as any).id}`
    map.set(key, event)
  }
  const merged = Array.from(map.values())
  merged.sort((a, b) => {
    const at = (a as any).timestamp || ''
    const bt = (b as any).timestamp || ''
    if (at === bt) {
      return ((b as any).id || '').localeCompare((a as any).id || '')
    }
    return bt.localeCompare(at)
  })
  return merged
}

function normalizeAuditEvent(event: AuditEvent): AuditEvent {
  if (event.kind !== 'message') {
    return event
  }

  const explicitHtml = event.body_html?.trim()
  if (explicitHtml) {
    const sanitized = sanitizeHtml(explicitHtml)
    return (event.body_html ?? '') === sanitized ? event : { ...event, body_html: sanitized }
  }

  const candidate = event.channel?.toLowerCase() === 'web'
    ? null
    : pickHtmlCandidate(undefined, event.body_text)
  if (!candidate) {
    return event
  }

  const sanitized = sanitizeHtml(candidate)
  return (event.body_html ?? '') === sanitized ? event : { ...event, body_html: sanitized }
}

function normalizeAuditEvents(events: AuditEvent[]): AuditEvent[] {
  return events.map((event) => normalizeAuditEvent(event))
}

export const initializeAudit = createAsyncThunk(
  'audit/initialize',
  async (agentId: string, { rejectWithValue }) => {
    try {
      const payload = await fetchAuditEvents(agentId, { limit: 40, tzOffsetMinutes: -new Date().getTimezoneOffset() })
      return { agentId, payload }
    } catch (error) {
      return rejectWithValue(error instanceof Error ? error.message : 'Failed to load audit runs')
    }
  },
)

export const loadMoreAudit = createAsyncThunk<
  Awaited<ReturnType<typeof fetchAuditEvents>> | null,
  void,
  { state: RootState; rejectValue: string }
>('audit/loadMore', async (_, { getState, rejectWithValue }) => {
  const state = getState().audit
  if (!state.agentId || !state.hasMore || state.loading) {
    return null
  }
  try {
    return await fetchAuditEvents(state.agentId, {
      cursor: state.nextCursor,
      limit: 40,
      tzOffsetMinutes: -new Date().getTimezoneOffset(),
    })
  } catch (error) {
    return rejectWithValue(error instanceof Error ? error.message : 'Failed to load more runs')
  }
})

export const loadAuditTimeline = createAsyncThunk(
  'audit/loadTimeline',
  async (agentId: string, { rejectWithValue }) => {
    try {
      return await fetchAuditTimeline(agentId)
    } catch (error) {
      return rejectWithValue(error instanceof Error ? error.message : 'Failed to load timeline')
    }
  },
)

export const jumpAuditToTime = createAsyncThunk<
  Awaited<ReturnType<typeof fetchAuditEvents>>,
  string,
  { state: RootState; rejectValue: string }
>('audit/jumpToTime', async (timestamp, { getState, rejectWithValue }) => {
  const state = getState().audit
  if (!state.agentId) {
    return rejectWithValue('No agent selected')
  }
  const targetDate = new Date(timestamp)
  if (Number.isNaN(targetDate.getTime())) {
    return rejectWithValue('Invalid timestamp')
  }
  try {
    return await fetchAuditEvents(state.agentId, {
      limit: 40,
      day: timestamp,
      tzOffsetMinutes: -new Date().getTimezoneOffset(),
    })
  } catch (error) {
    return rejectWithValue(error instanceof Error ? error.message : 'Failed to jump to time')
  }
})

const auditSlice = createSlice({
  name: 'audit',
  initialState: initialAuditState,
  reducers: {
    setSelectedDay(state, action: PayloadAction<string | null>) {
      state.selectedTimestamp = action.payload
    },
    setProcessingActive(state, action: PayloadAction<boolean>) {
      state.processingActive = action.payload
    },
    receiveRealtimeEvent(state, action: PayloadAction<any>) {
      const kind: string | undefined = action.payload?.kind
      if (!state.agentId || !kind) {
        return
      }
      if (kind === 'processing_status') {
        state.processingActive = Boolean(action.payload?.active)
        return
      }
      if (kind === 'run_started') {
        return
      }
      state.events = mergeEvents(state.events, [normalizeAuditEvent(action.payload as AuditEvent)])
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(initializeAudit.pending, (state, action) => {
        state.loading = true
        state.agentId = action.meta.arg
        state.error = null
        state.selectedTimestamp = null
      })
      .addCase(initializeAudit.fulfilled, (state, action) => {
        state.events = normalizeAuditEvents(action.payload.payload.events || [])
        state.nextCursor = action.payload.payload.next_cursor
        state.hasMore = action.payload.payload.has_more
        state.processingActive = action.payload.payload.processing_active
        state.loading = false
      })
      .addCase(initializeAudit.rejected, (state, action) => {
        state.loading = false
        state.error = typeof action.payload === 'string' ? action.payload : 'Failed to load audit runs'
      })
      .addCase(loadMoreAudit.pending, (state) => {
        state.loading = true
      })
      .addCase(loadMoreAudit.fulfilled, (state, action) => {
        if (!action.payload) {
          state.loading = false
          return
        }
        state.events = mergeEvents(state.events, normalizeAuditEvents(action.payload.events || []))
        state.nextCursor = action.payload.next_cursor
        state.hasMore = action.payload.has_more
        state.processingActive = action.payload.processing_active
        state.loading = false
      })
      .addCase(loadMoreAudit.rejected, (state, action) => {
        state.loading = false
        state.error = action.payload ?? 'Failed to load more runs'
      })
      .addCase(loadAuditTimeline.pending, (state) => {
        state.timelineLoading = true
        state.timelineError = null
      })
      .addCase(loadAuditTimeline.fulfilled, (state, action) => {
        state.timeline = action.payload.buckets || []
        state.timelineLoading = false
        state.selectedTimestamp = state.selectedTimestamp || action.payload.latest || null
      })
      .addCase(loadAuditTimeline.rejected, (state, action) => {
        state.timelineLoading = false
        state.timelineError = typeof action.payload === 'string' ? action.payload : 'Failed to load timeline'
      })
      .addCase(jumpAuditToTime.pending, (state, action) => {
        state.loading = true
        state.error = null
        state.selectedTimestamp = action.meta.arg
      })
      .addCase(jumpAuditToTime.fulfilled, (state, action) => {
        state.events = normalizeAuditEvents(action.payload.events || [])
        state.nextCursor = action.payload.next_cursor
        state.hasMore = action.payload.has_more
        state.processingActive = action.payload.processing_active
        state.loading = false
      })
      .addCase(jumpAuditToTime.rejected, (state, action) => {
        state.loading = false
        state.error = action.payload ?? 'Failed to jump to time'
      })
  },
})

export const auditActions = auditSlice.actions
export const auditReducer = auditSlice.reducer

export const selectAuditState = (state: RootState): AuditState => state.audit
