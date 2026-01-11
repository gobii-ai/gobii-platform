import { useEffect, useState, useRef } from 'react'

// Easing function for smooth animation
function easeOutExpo(t: number): number {
  return t === 1 ? 1 : 1 - Math.pow(2, -10 * t)
}

type AnimatedNumberProps = {
  value: number
  duration?: number
  prefix?: string
  suffix?: string
  decimals?: number
  className?: string
}

export function AnimatedNumber({
  value,
  duration = 1200,
  prefix = '',
  suffix = '',
  decimals = 0,
  className = '',
}: AnimatedNumberProps) {
  const [displayed, setDisplayed] = useState(0)
  const startValueRef = useRef(0)
  const animationRef = useRef<number | null>(null)

  useEffect(() => {
    const startValue = startValueRef.current
    const startTime = performance.now()

    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime
      const progress = Math.min(elapsed / duration, 1)
      const eased = easeOutExpo(progress)

      const current = startValue + (value - startValue) * eased
      setDisplayed(current)

      if (progress < 1) {
        animationRef.current = requestAnimationFrame(animate)
      } else {
        startValueRef.current = value
      }
    }

    animationRef.current = requestAnimationFrame(animate)

    return () => {
      if (animationRef.current !== null) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [value, duration])

  const formattedValue = displayed.toFixed(decimals)

  return (
    <span className={`animated-number ${className}`.trim()}>
      {prefix}
      {formattedValue}
      {suffix}
    </span>
  )
}
