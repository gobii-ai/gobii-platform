import { createSlice, type PayloadAction } from '@reduxjs/toolkit'

import type { RootState } from './appStore'

export type ImmersiveShellState = {
  activeAgentId: string | null
  shellPathname: string
  selectionSidebarMode: string | null
  currentAppPanel: string | null
  modals: Record<string, boolean>
}

const initialState: ImmersiveShellState = {
  activeAgentId: null,
  shellPathname: '',
  selectionSidebarMode: null,
  currentAppPanel: null,
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
    setSelectionSidebarMode(state, action: PayloadAction<string | null>) {
      state.selectionSidebarMode = action.payload
    },
    setCurrentAppPanel(state, action: PayloadAction<string | null>) {
      state.currentAppPanel = action.payload
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
