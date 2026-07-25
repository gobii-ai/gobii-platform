import type { CSSProperties } from 'react'

import { PET_SPRITESHEET_COLUMNS, PET_SPRITESHEET_ROWS } from './petAnimation'

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
    '--pet-frame-x': `${(column / (PET_SPRITESHEET_COLUMNS - 1)) * 100}%`,
    '--pet-frame-y': `${(row / (PET_SPRITESHEET_ROWS - 1)) * 100}%`,
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
