function combineClassNames(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(' ')
}

type ProcessingIndicatorProps = {
  agentFirstName: string
  active: boolean
  className?: string
  fade?: boolean
}

export function ProcessingIndicator({ agentFirstName, active, className, fade = false }: ProcessingIndicatorProps) {
  if (!active) {
    return null
  }

  const classes = combineClassNames('processing-indicator', fade && 'processing-indicator--fade', className)

  return (
    <div id="agent-processing-indicator" className={classes} data-visible={active ? 'true' : 'false'}>
      <span className="processing-pip" aria-hidden="true" />
      <span className="processing-label">
        <strong>{agentFirstName}</strong> is working
      </span>
    </div>
  )
}
