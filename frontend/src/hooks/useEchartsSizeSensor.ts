import { useLayoutEffect, type RefObject } from 'react'
import * as sizeSensor from 'size-sensor'

type EChartsHost = {
  ele?: HTMLElement | null
}

export function useEchartsSizeSensor<T extends EChartsHost>(
  chartRef: RefObject<T | null>,
  enabled = true,
) {
  useLayoutEffect(() => {
    if (!enabled) {
      return
    }
    const element = chartRef.current?.ele
    if (!element) {
      return
    }
    sizeSensor.bind(element, () => {})
  }, [chartRef, enabled])
}
