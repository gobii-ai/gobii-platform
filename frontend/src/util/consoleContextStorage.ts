type StoredConsoleContext = {
  type: string
  id: string
  name?: string | null
}

const STORAGE_KEYS = {
  type: 'gobii:console:context-type',
  id: 'gobii:console:context-id',
  name: 'gobii:console:context-name',
}

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function readStoredConsoleContext(): StoredConsoleContext | null {
  const storage = getSessionStorage()
  if (!storage) {
    return null
  }
  const type = storage.getItem(STORAGE_KEYS.type)
  const id = storage.getItem(STORAGE_KEYS.id)
  if (!type || !id) {
    return null
  }
  const name = storage.getItem(STORAGE_KEYS.name)
  return {
    type,
    id,
    name: name && name.trim() ? name : null,
  }
}

export function storeConsoleContext(context: StoredConsoleContext): void {
  const storage = getSessionStorage()
  if (!storage) {
    return
  }
  storage.setItem(STORAGE_KEYS.type, context.type)
  storage.setItem(STORAGE_KEYS.id, context.id)
  if (context.name) {
    storage.setItem(STORAGE_KEYS.name, context.name)
  } else {
    storage.removeItem(STORAGE_KEYS.name)
  }
}

export function clearStoredConsoleContext(): void {
  const storage = getSessionStorage()
  if (!storage) {
    return
  }
  storage.removeItem(STORAGE_KEYS.type)
  storage.removeItem(STORAGE_KEYS.id)
  storage.removeItem(STORAGE_KEYS.name)
}
