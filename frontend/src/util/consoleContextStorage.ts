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

const LEGACY_LOCAL_STORAGE_KEYS = {
  type: 'contextType',
  id: 'contextId',
  name: 'contextName',
}

function getLocalStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    return window.localStorage
  } catch {
    return null
  }
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
  const sessionStorageRef = getSessionStorage()
  if (sessionStorageRef) {
    sessionStorageRef.removeItem(STORAGE_KEYS.type)
    sessionStorageRef.removeItem(STORAGE_KEYS.id)
    sessionStorageRef.removeItem(STORAGE_KEYS.name)
  }

  const localStorageRef = getLocalStorage()
  if (!localStorageRef) {
    return
  }

  localStorageRef.removeItem(STORAGE_KEYS.type)
  localStorageRef.removeItem(STORAGE_KEYS.id)
  localStorageRef.removeItem(STORAGE_KEYS.name)
  localStorageRef.removeItem(LEGACY_LOCAL_STORAGE_KEYS.type)
  localStorageRef.removeItem(LEGACY_LOCAL_STORAGE_KEYS.id)
  localStorageRef.removeItem(LEGACY_LOCAL_STORAGE_KEYS.name)
}
