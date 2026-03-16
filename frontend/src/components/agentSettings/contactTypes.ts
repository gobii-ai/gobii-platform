export type AllowlistInput = {
  address: string
  channel: string
  allowInbound: boolean
  allowOutbound: boolean
}

export type PendingAllowlistAction =
  | { type: 'create'; tempId: string; channel: string; address: string; allowInbound: boolean; allowOutbound: boolean }
  | { type: 'remove'; id: string }
  | { type: 'cancel_invite'; id: string }

export type AllowlistTableRow = {
  id: string
  kind: 'entry' | 'invite'
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
  pendingType?: PendingAllowlistAction['type']
  temp?: boolean
}
