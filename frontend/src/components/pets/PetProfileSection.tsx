import { useEffect, useRef } from 'react'
import { Fish } from 'lucide-react'

import { PetOptionsPanel } from './PetOptionsPanel'
import './pets.css'

export function PetProfileSection() {
  const sectionRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const revealPetOptions = () => {
      if (window.location.hash !== '#workspace-pet') return
      window.requestAnimationFrame(() => {
        sectionRef.current?.scrollIntoView({ block: 'start' })
      })
    }

    revealPetOptions()
    window.addEventListener('popstate', revealPetOptions)
    window.addEventListener('hashchange', revealPetOptions)
    return () => {
      window.removeEventListener('popstate', revealPetOptions)
      window.removeEventListener('hashchange', revealPetOptions)
    }
  }, [])

  return (
    <section id="workspace-pet" ref={sectionRef} className="profile-screen__section pet-profile">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <Fish className="h-4 w-4" />
        </div>
        <div>
          <h2>Workspace Pet</h2>
          <p>Choose your chat companion</p>
        </div>
      </div>
      <PetOptionsPanel />
    </section>
  )
}
