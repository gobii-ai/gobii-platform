const MESSAGE_DRAFT_STORAGE_PREFIX = 'gobii:agent-chat:message-draft:'

function isUsableStorage(storage: Storage | null): storage is Storage {
  return Boolean(
    storage
    && typeof storage.getItem === 'function'
    && typeof storage.setItem === 'function'
    && typeof storage.removeItem === 'function'
  )
}

function getLocalStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const storage = window.localStorage
    return isUsableStorage(storage) ? storage : null
  } catch {
    return null
  }
}

function getMessageDraftStorageKey(agentId: string): string {
  return `${MESSAGE_DRAFT_STORAGE_PREFIX}${agentId}`
}

export function readAgentChatMessageDraft(agentId: string | null | undefined): string {
  if (!agentId) {
    return ''
  }

  const storage = getLocalStorage()
  if (!storage) {
    return ''
  }

  try {
    return storage.getItem(getMessageDraftStorageKey(agentId)) ?? ''
  } catch {
    return ''
  }
}

export function writeAgentChatMessageDraft(agentId: string | null | undefined, draft: string): void {
  if (!agentId) {
    return
  }

  const storage = getLocalStorage()
  if (!storage) {
    return
  }

  try {
    const key = getMessageDraftStorageKey(agentId)
    if (draft === '') {
      storage.removeItem(key)
    } else {
      storage.setItem(key, draft)
    }
  } catch {
    // Storage can be blocked or quota-limited; drafts should never break chat.
  }
}

export function clearAgentChatMessageDraft(agentId: string | null | undefined): void {
  writeAgentChatMessageDraft(agentId, '')
}
