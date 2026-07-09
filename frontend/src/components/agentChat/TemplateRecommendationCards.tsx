import { BriefcaseBusiness, Loader2, Plus, Search, Users } from 'lucide-react'
import type { TemplateRecommendation } from '../../api/agentSpawnIntent'
import { AgentChatSectionCard } from './uiPrimitives'

type TemplateRecommendationCardsProps = {
  recommendations: TemplateRecommendation[]
  onCreate?: (template: TemplateRecommendation, position: number) => void | Promise<void>
  submittingTemplateId?: string | null
}

function iconForTemplate(template: TemplateRecommendation) {
  const haystack = `${template.category} ${template.name}`.toLowerCase()
  if (haystack.includes('people') || haystack.includes('recruit')) {
    return Users
  }
  if (haystack.includes('revenue') || haystack.includes('sales') || haystack.includes('lead')) {
    return BriefcaseBusiness
  }
  return Search
}

export function TemplateRecommendationCards({
  recommendations,
  onCreate,
  submittingTemplateId = null,
}: TemplateRecommendationCardsProps) {
  if (!recommendations.length) {
    return null
  }

  return (
    <AgentChatSectionCard
      className="timeline-event template-recommendations-card"
      tone="info"
      density="compact"
      aria-label="Recommended templates"
    >
      <div className="template-recommendations-card__header">
        <h3 className="template-recommendations-card__title">Start with a template</h3>
        <p className="template-recommendations-card__subtitle">
          Popular templates based on your workspace.
        </p>
      </div>
      <div className="template-recommendations-card__grid">
        {recommendations.map((template, index) => {
          const Icon = iconForTemplate(template)
          const isSubmitting = submittingTemplateId === template.id
          const isDisabled = Boolean(submittingTemplateId) || !onCreate
          return (
            <button
              key={template.id}
              type="button"
              className="template-recommendations-card__item"
              aria-label={`Create agent from template: ${template.name}`}
              disabled={isDisabled}
              onClick={() => {
                void onCreate?.(template, index)
              }}
            >
              <span className="template-recommendations-card__icon-wrap" aria-hidden="true">
                <Icon className="template-recommendations-card__icon" />
              </span>
              <span className="template-recommendations-card__body">
                <span className="template-recommendations-card__meta">{template.category}</span>
                <span className="template-recommendations-card__name">{template.name}</span>
                <span className="template-recommendations-card__description">
                  {template.tagline || template.description}
                </span>
                <span className="template-recommendations-card__cta">
                  {isSubmitting ? 'Creating' : 'Create agent'}
                  {isSubmitting ? (
                    <Loader2 className="template-recommendations-card__chevron animate-spin" aria-hidden="true" />
                  ) : (
                    <Plus className="template-recommendations-card__chevron" aria-hidden="true" />
                  )}
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </AgentChatSectionCard>
  )
}
