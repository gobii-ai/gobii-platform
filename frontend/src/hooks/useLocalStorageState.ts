import { useCallback, useEffect, useRef, useState } from 'react'

type LocalStorageOptions<T> = {
  serialize?: (value: T) => string
  deserialize?: (raw: string) => T
}

const defaultSerialize = (value: unknown) => JSON.stringify(value)
const defaultDeserialize = (raw: string) => JSON.parse(raw) as unknown

function readStoredValue<T>(
  key: string | null,
  fallbackValue: T,
  deserialize: (raw: string) => T,
): T {
  if (!key || typeof window === 'undefined') {
    return fallbackValue
  }
  try {
    const stored = window.localStorage.getItem(key)
    if (stored === null) {
      return fallbackValue
    }
    return deserialize(stored)
  } catch {
    return fallbackValue
  }
}

export function useLocalStorageState<T>(
  key: string | null,
  fallbackValue: T,
  options: LocalStorageOptions<T> = {},
) {
  const serialize = options.serialize ?? (defaultSerialize as (value: T) => string)
  const deserialize = options.deserialize ?? (defaultDeserialize as (raw: string) => T)
  const fallbackValueRef = useRef(fallbackValue)
  const deserializeRef = useRef(deserialize)
  const serializeRef = useRef(serialize)

  fallbackValueRef.current = fallbackValue
  deserializeRef.current = deserialize
  serializeRef.current = serialize

  const [value, setValue] = useState<T>(() => readStoredValue(key, fallbackValue, deserialize))

  useEffect(() => {
    setValue(readStoredValue(key, fallbackValueRef.current, deserializeRef.current))
  }, [key])

  const setStoredValue = useCallback((nextValue: T | ((current: T) => T)) => {
    setValue((current) => {
      const resolved = typeof nextValue === 'function'
        ? (nextValue as (currentValue: T) => T)(current)
        : nextValue
      if (!key || typeof window === 'undefined') {
        return resolved
      }
      try {
        if (resolved === null) {
          window.localStorage.removeItem(key)
        } else {
          window.localStorage.setItem(key, serializeRef.current(resolved))
        }
      } catch {
        return resolved
      }
      return resolved
    })
  }, [key])

  return [value, setStoredValue] as const
}
