export function getReturnToPath(): string {
  if (typeof window === 'undefined') {
    return '/'
  }
  return `${window.location.pathname}${window.location.search}${window.location.hash}` || '/'
}

export function appendReturnTo(baseUrl: string, returnTo?: string): string {
  if (!baseUrl) {
    return baseUrl
  }
  if (typeof window === 'undefined') {
    return baseUrl
  }
  try {
    const url = new URL(baseUrl, window.location.origin)
    if (url.origin !== window.location.origin) {
      return baseUrl
    }
    url.searchParams.set('return_to', returnTo ?? getReturnToPath())
    return url.toString()
  } catch {
    return baseUrl
  }
}
