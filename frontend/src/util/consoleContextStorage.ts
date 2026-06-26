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

function readContextFromStorage(storage: Storage | null): StoredConsoleContext | null {
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

function readLegacyContextFromStorage(storage: Storage | null): StoredConsoleContext | null {
  if (!storage) {
    return null
  }
  const type = storage.getItem(LEGACY_LOCAL_STORAGE_KEYS.type)
  const id = storage.getItem(LEGACY_LOCAL_STORAGE_KEYS.id)
  if (!type || !id) {
    return null
  }
  const name = storage.getItem(LEGACY_LOCAL_STORAGE_KEYS.name)
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
  storage.setItem(STORAGE_KEYS.type, context.type)
  storage.setItem(STORAGE_KEYS.id, context.id)
  if (context.name) {
    storage.setItem(STORAGE_KEYS.name, context.name)
  } else {
    storage.removeItem(STORAGE_KEYS.name)
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
