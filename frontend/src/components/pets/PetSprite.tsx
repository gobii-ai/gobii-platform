import type { CSSProperties } from 'react'

type PetSpriteProps = {
  spritesheetUrl: string
  row: number
  column: number
  className?: string
  label?: string
}

export function PetSprite({
  spritesheetUrl,
  row,
  column,
  className,
  label,
}: PetSpriteProps) {
  const style = {
    '--pet-spritesheet-url': `url("${spritesheetUrl.replaceAll('"', '%22')}")`,
    '--pet-frame-x': `${(column / 7) * 100}%`,
    '--pet-frame-y': `${(row / 10) * 100}%`,
  } as CSSProperties

  return (
    <span
      className={['pet-sprite', className].filter(Boolean).join(' ')}
      style={style}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  )
}
