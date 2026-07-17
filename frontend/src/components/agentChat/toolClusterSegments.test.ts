import { describe, expect, it } from 'vitest'
import { Wrench } from 'lucide-react'

import { buildToolClusterRenderSegments } from './toolClusterSegments'
import type { ToolEntryDisplay } from './tooling/types'
import { GenericToolDetail } from './toolDetails/details/common'

function entry(id: string, separateFromPreview = false): ToolEntryDisplay {
  return {
    id,
    clusterCursor: 'cluster',
    toolName: 'test',
    label: id,
    icon: Wrench,
    iconBgClass: '',
    iconColorClass: '',
    parameters: null,
    rawParameters: null,
    result: null,
    detailComponent: GenericToolDetail,
    separateFromPreview,
  }
}

describe('buildToolClusterRenderSegments', () => {
  it('preserves chronological runs and marks only the trailing activity run active', () => {
    const segments = buildToolClusterRenderSegments([
      entry('older-action'),
      entry('assignment', true),
      entry('newer-action'),
      entry('newest-action'),
    ])

    expect(segments.map((segment) => (
      segment.kind === 'preview'
        ? { kind: segment.kind, ids: segment.entries.map((item) => item.id), isTrailing: segment.isTrailing }
        : { kind: segment.kind, id: segment.entry.id }
    ))).toEqual([
      { kind: 'preview', ids: ['older-action'], isTrailing: false },
      { kind: 'separate', id: 'assignment' },
      { kind: 'preview', ids: ['newer-action', 'newest-action'], isTrailing: true },
    ])
  })
})
