import type { StaffViewContext } from '../api/context'

export function parseStaffViewContext(search: string): StaffViewContext | null {
  const params = new URLSearchParams(search)
  const type = params.get('staff_context_type')
  const id = params.get('staff_context_id')?.trim()
  if ((type !== 'personal' && type !== 'organization') || !id) return null
  return { type, id }
}
