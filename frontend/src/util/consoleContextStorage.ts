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

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const storage = window.sessionStorage
    return isUsableStorage(storage) ? storage : null
  } catch {
    return null
  }
}

function safeGetItem(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function safeSetItem(storage: Storage, key: string, value: string): void {
  try {
    storage.setItem(key, value)
  } catch {
    // Storage can be available but blocked, e.g. private or locked-down browser modes.
  }
}

function safeRemoveItem(storage: Storage, key: string): void {
  try {
    storage.removeItem(key)
  } catch {
    // Storage can be available but blocked, e.g. private or locked-down browser modes.
  }
}

function readContextFromStorage(storage: Storage | null): StoredConsoleContext | null {
  if (!storage) {
    return null
  }
  const type = safeGetItem(storage, STORAGE_KEYS.type)
  const id = safeGetItem(storage, STORAGE_KEYS.id)
  if (!type || !id) {
    return null
  }
  const name = safeGetItem(storage, STORAGE_KEYS.name)
  return {
    type,
    id,
    name: name && name.trim() ? name : null,
  }
}

function readLegacyContextFromStorage(storage: Storage | null): StoredConsoleContext | null {
  if (!storage) {
    return null
  }
  const type = safeGetItem(storage, LEGACY_LOCAL_STORAGE_KEYS.type)
  const id = safeGetItem(storage, LEGACY_LOCAL_STORAGE_KEYS.id)
  if (!type || !id) {
    return null
  }
  const name = safeGetItem(storage, LEGACY_LOCAL_STORAGE_KEYS.name)
  return {
    type,
    id,
    name: name && name.trim() ? name : null,
  }
}

function writeContextToStorage(storage: Storage | null, context: StoredConsoleContext): void {
  if (!storage) {
    return
  }
  safeSetItem(storage, STORAGE_KEYS.type, context.type)
  safeSetItem(storage, STORAGE_KEYS.id, context.id)
  if (context.name) {
    safeSetItem(storage, STORAGE_KEYS.name, context.name)
  } else {
    safeRemoveItem(storage, STORAGE_KEYS.name)
  }
}

export function readStoredConsoleContext(): StoredConsoleContext | null {
  const sessionContext = readContextFromStorage(getSessionStorage())
  if (sessionContext) {
    return sessionContext
  }

  const localStorageRef = getLocalStorage()
  return readContextFromStorage(localStorageRef) ?? readLegacyContextFromStorage(localStorageRef)
}

export function storeConsoleContext(context: StoredConsoleContext): void {
  writeContextToStorage(getSessionStorage(), context)
  writeContextToStorage(getLocalStorage(), context)
}

export function clearStoredConsoleContext(): void {
  const sessionStorageRef = getSessionStorage()
  if (sessionStorageRef) {
    safeRemoveItem(sessionStorageRef, STORAGE_KEYS.type)
    safeRemoveItem(sessionStorageRef, STORAGE_KEYS.id)
    safeRemoveItem(sessionStorageRef, STORAGE_KEYS.name)
  }

  const localStorageRef = getLocalStorage()
  if (!localStorageRef) {
    return
  }

  safeRemoveItem(localStorageRef, STORAGE_KEYS.type)
  safeRemoveItem(localStorageRef, STORAGE_KEYS.id)
  safeRemoveItem(localStorageRef, STORAGE_KEYS.name)
  safeRemoveItem(localStorageRef, LEGACY_LOCAL_STORAGE_KEYS.type)
  safeRemoveItem(localStorageRef, LEGACY_LOCAL_STORAGE_KEYS.id)
  safeRemoveItem(localStorageRef, LEGACY_LOCAL_STORAGE_KEYS.name)
}
