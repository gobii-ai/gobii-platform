import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import type { RootState } from './appStore'
import type { AgentChatShellSubview } from '../util/agentChatShellRoutes'
import type { AgentChatSidebarMode } from '../components/agentChat/sidebarMode'
import type { SelectionShellPage } from '../components/agentChat/SelectionShellPageSwitcher'

export type ImmersiveConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'offline' | 'error'
export type ImmersiveEmbeddedPanel = Exclude<AgentChatShellSubview, 'chat'> | null

export const IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY = 'gobii:immersive:selection-sidebar-mode'

export type ImmersiveShellViewerState = {
  userId: number | null
  email: string | null
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
  embeddedPanel: ImmersiveEmbeddedPanel
  sidebarMode: AgentChatSidebarMode
  galleryPage: SelectionShellPage
  currentAppPanel: string | null
  viewer: ImmersiveShellViewerState
  connection: ImmersiveShellConnectionState
  modals: Record<string, boolean>
}

const initialState: ImmersiveShellState = {
  activeAgentId: null,
  shellPathname: '',
  shellSubview: 'chat',
  embeddedPanel: null,
  sidebarMode: 'list',
  galleryPage: 'agents',
  currentAppPanel: null,
  viewer: {
    userId: null,
    email: null,
  },
  connection: {
    status: 'connecting',
    label: 'Connecting',
    detail: null,
  },
  modals: {},
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
      state.embeddedPanel = action.payload === 'chat' ? null : action.payload
    },
    setEmbeddedPanel(state, action: PayloadAction<ImmersiveEmbeddedPanel>) {
      state.embeddedPanel = action.payload
      state.shellSubview = action.payload ?? 'chat'
    },
    setSidebarMode(state, action: PayloadAction<AgentChatSidebarMode>) {
      state.sidebarMode = action.payload
    },
    setGalleryPage(state, action: PayloadAction<SelectionShellPage>) {
      state.galleryPage = action.payload
    },
    setCurrentAppPanel(state, action: PayloadAction<string | null>) {
      state.currentAppPanel = action.payload
    },
    setViewer(state, action: PayloadAction<ImmersiveShellViewerState>) {
      state.viewer = action.payload
    },
    setConnection(state, action: PayloadAction<ImmersiveShellConnectionState>) {
      state.connection = action.payload
    },
    setModalOpen(state, action: PayloadAction<{ key: string; open: boolean }>) {
      state.modals[action.payload.key] = action.payload.open
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
export const selectImmersiveEmbeddedPanel = (state: RootState): ImmersiveEmbeddedPanel => state.immersiveShell.embeddedPanel
export const selectImmersiveGalleryPage = (state: RootState): SelectionShellPage => state.immersiveShell.galleryPage
