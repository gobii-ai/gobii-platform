export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    return '0 B'
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let current = value
  let idx = 0
  while (current >= 1024 && idx < units.length - 1) {
    current /= 1024
    idx += 1
  }

  return `${current.toFixed(current >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`
}
