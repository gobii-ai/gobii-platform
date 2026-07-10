import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import type { RootState } from './appStore'
import type { AgentChatShellSubview, AgentChatSidebarMode } from '../types/immersiveShell'

export type ImmersiveConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'offline' | 'error'
export const IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY = 'gobii:immersive:selection-sidebar-mode'

export type ImmersiveShellViewerState = {
  userId: number | null
  email: string | null
  timeZone: string | null
}

export type ImmersiveShellConnectionState = {
  status: ImmersiveConnectionStatus
  label: string
  detail: string | null
}

export type ImmersiveShellState = {
  activeAgentId: string | null
  shellPathname: string
  shellSubview: AgentChatShellSubview
  sidebarMode: AgentChatSidebarMode
  viewer: ImmersiveShellViewerState
  connection: ImmersiveShellConnectionState
}

const initialState: ImmersiveShellState = {
  activeAgentId: null,
  shellPathname: '',
  shellSubview: 'chat',
  sidebarMode: 'list',
  viewer: {
    userId: null,
    email: null,
    timeZone: null,
  },
  connection: {
    status: 'connecting',
    label: 'Connecting',
    detail: null,
  },
}

const immersiveShellSlice = createSlice({
  name: 'immersiveShell',
  initialState,
  reducers: {
    setActiveAgentId(state, action: PayloadAction<string | null>) {
      state.activeAgentId = action.payload
    },
    setShellPathname(state, action: PayloadAction<string>) {
      state.shellPathname = action.payload
    },
    setShellSubview(state, action: PayloadAction<AgentChatShellSubview>) {
      state.shellSubview = action.payload
    },
    setSidebarMode(state, action: PayloadAction<AgentChatSidebarMode>) {
      state.sidebarMode = action.payload
    },
    setViewer(state, action: PayloadAction<ImmersiveShellViewerState>) {
      state.viewer = action.payload
    },
    setConnection(state, action: PayloadAction<ImmersiveShellConnectionState>) {
      state.connection = action.payload
    },
  },
})

export const immersiveShellActions = immersiveShellSlice.actions
export const immersiveShellReducer = immersiveShellSlice.reducer

export const selectImmersiveShellState = (state: RootState): ImmersiveShellState => state.immersiveShell
export const selectImmersiveShellActiveAgentId = (state: RootState): string | null => state.immersiveShell.activeAgentId
export const selectImmersiveShellViewer = (state: RootState): ImmersiveShellViewerState => state.immersiveShell.viewer
export const selectImmersiveShellConnection = (state: RootState): ImmersiveShellConnectionState => state.immersiveShell.connection
export const selectImmersiveSidebarMode = (state: RootState): AgentChatSidebarMode => state.immersiveShell.sidebarMode
export const selectImmersiveShellSubview = (state: RootState): AgentChatShellSubview => state.immersiveShell.shellSubview
