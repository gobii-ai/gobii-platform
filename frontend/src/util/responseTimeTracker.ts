const STORAGE_KEY = 'agent_response_times'
const MAX_SAMPLES = 20
const DEFAULT_ESTIMATE_MS = 8000

type ResponseTimeSample = {
  duration: number
  timestamp: number
}

function loadSamples(): ResponseTimeSample[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (!stored) return []
    return JSON.parse(stored) as ResponseTimeSample[]
  } catch {
    return []
  }
}

function saveSamples(samples: ResponseTimeSample[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(samples.slice(-MAX_SAMPLES)))
  } catch {
    // localStorage may be unavailable
  }
}

export function recordResponseTime(durationMs: number): void {
  const samples = loadSamples()
  samples.push({ duration: durationMs, timestamp: Date.now() })
  saveSamples(samples)
}

export function getEstimatedResponseTime(): number {
  const samples = loadSamples()
  if (samples.length === 0) return DEFAULT_ESTIMATE_MS

  // Weighted average favoring recent samples
  let weightSum = 0
  let weightedSum = 0
  samples.forEach((sample, i) => {
    const weight = i + 1 // More recent = higher weight
    weightSum += weight
    weightedSum += sample.duration * weight
  })

  const average = weightedSum / weightSum
  // Add a buffer to avoid the bar completing too early
  return Math.max(average * 1.1, 1000)
}

export function getResponseTimeStats(): { average: number; count: number } {
  const samples = loadSamples()
  if (samples.length === 0) {
    return { average: DEFAULT_ESTIMATE_MS, count: 0 }
  }
  const sum = samples.reduce((acc, s) => acc + s.duration, 0)
  return {
    average: sum / samples.length,
    count: samples.length,
  }
}
