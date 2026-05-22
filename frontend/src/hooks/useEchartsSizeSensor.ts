import { useLayoutEffect, type RefObject } from 'react'
import * as sizeSensor from 'size-sensor'

type EChartsHost = {
  getEchartsInstance?: () => {
    getDom: () => HTMLElement | null
  } | null
}

export function useEchartsSizeSensor<T extends EChartsHost>(
  chartRef: RefObject<T | null>,
  enabled = true,
) {
  useLayoutEffect(() => {
    if (!enabled) {
      return
    }
    const element = chartRef.current?.getEchartsInstance?.()?.getDom()
    if (!element) {
      return
    }
    return sizeSensor.bind(element, () => {})
  }, [chartRef, enabled])
}
