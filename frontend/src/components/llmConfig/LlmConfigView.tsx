import { AlertCircle, Atom, BookText, Brain, Check, Clock3, Copy, PlugZap, Loader2, LoaderCircle, Pencil, Plus, PlusCircle, Scale, Search, Settings2, Sparkles, Trash2, X } from 'lucide-react'

import { SectionCard } from './SectionCard'
import { StatCard } from './StatCard'
import { ProviderCard } from './ProviderCard'
import { ProviderFormModal } from './ProviderFormModal'
import { ActivityDock, RangeSection, TierCard, TierGroupSection } from './RoutingSections'
import { actionKey, button } from './shared'
import type { LlmConfigController } from './useLlmConfigController'

export function LlmConfigView({ controller }: { controller: LlmConfigController }) {
  const { data, feedback, modal, showModal, statsCards, provider: providerState, routing } = controller
  const selectedProfile = data.selectedProfile
  const selectedProfileId = data.selectedProfileId

  return (
    <>
      {modal}
      <ActivityDock notices={feedback.notices} activeLabels={feedback.activeLabels} onDismiss={feedback.dismissNotice} />
      <div className="space-y-8">
        <div className="gobii-card-base space-y-2 px-6 py-6">
          <h1 className="text-2xl font-semibold text-slate-900/90">LLM configuration</h1>
          <p className="text-sm text-slate-600">Review providers, endpoints, and token tiers powering orchestrator, browser-use, and embedding flows.</p>
        </div>
        {data.overviewQuery.isError && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700 flex items-center gap-2">
            <AlertCircle className="size-4" />
            Unable to load configuration. Please refresh.
          </div>
        )}
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {statsCards.map((card) => (
            <StatCard key={card.label} label={card.label} value={card.value} hint={card.hint} icon={card.icon} />
          ))}
        </div>

        {/* Routing Profile Selector */}
        <div className="gobii-card-base px-6 py-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-indigo-100 p-2.5">
                <Settings2 className="size-5 text-indigo-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Routing Profile</h2>
                <p className="text-sm text-slate-500">
                  {selectedProfile?.description || 'Select a profile to view/edit its tier configuration'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {data.profilesQuery.isPending ? (
                <div className="flex items-center gap-2 text-slate-500 text-sm">
                  <LoaderCircle className="size-4 animate-spin" /> Loading profiles...
                </div>
              ) : (
                <>
                  <select
                    value={selectedProfileId || ''}
                    onChange={(e) => data.setSelectedProfileId(e.target.value || null)}
                    className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 min-w-[200px]"
                  >
                    {data.profiles.length === 0 && <option value="">No profiles</option>}
                    {data.profiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.display_name || profile.name}
                        {profile.is_active ? ' (Active)' : ''}
                      </option>
                    ))}
                  </select>
                  <div className="flex items-center gap-2">
                    {selectedProfile && !selectedProfile.is_active && (
                      <button
                        type="button"
                        className={button.primary}
                        onClick={() => routing.handleActivateProfile(selectedProfile.id)}
                        disabled={feedback.isBusy(actionKey('profile', selectedProfile.id, 'activate'))}
                      >
                        {feedback.isBusy(actionKey('profile', selectedProfile.id, 'activate')) ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <Check className="size-4" />
                        )}
                        Activate
                      </button>
                    )}
                    {selectedProfile && selectedProfile.is_active && (
                      <span className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-100 px-3 py-1.5 text-sm font-medium text-emerald-700">
                        <Check className="size-4" />
                        Active
                      </span>
                    )}
                    {selectedProfile && (
                      <button
                        type="button"
                        className={button.secondary}
                        onClick={() => routing.openEditProfileModal(selectedProfile)}
                        disabled={feedback.isBusy(actionKey('profile', selectedProfile.id, 'update'))}
                        title="Edit this profile"
                      >
                        {feedback.isBusy(actionKey('profile', selectedProfile.id, 'update')) ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <Pencil className="size-4" />
                        )}
                        Edit
                      </button>
                    )}
                    {selectedProfile && (
                      <button
                        type="button"
                        className={button.secondary}
                        onClick={() => routing.handleCloneProfile(selectedProfile.id)}
                        disabled={feedback.isBusy(actionKey('profile', selectedProfile.id, 'clone'))}
                        title="Clone this profile"
                      >
                        {feedback.isBusy(actionKey('profile', selectedProfile.id, 'clone')) ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <Copy className="size-4" />
                        )}
                        Clone
                      </button>
                    )}
                    <button
                      type="button"
                      className={button.secondary}
                      onClick={routing.openCreateProfileModal}
                    >
                      <Plus className="size-4" />
                      New
                    </button>
                    {selectedProfile && !selectedProfile.is_active && (
                      <button
                        type="button"
                        className={button.iconDanger}
                        onClick={() => routing.handleDeleteProfile(selectedProfile.id, selectedProfile.display_name || selectedProfile.name)}
                        disabled={feedback.isBusy(actionKey('profile', selectedProfile.id, 'delete'))}
                        title="Delete this profile"
                      >
                        {feedback.isBusy(actionKey('profile', selectedProfile.id, 'delete')) ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <Trash2 className="size-4" />
                        )}
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        <SectionCard
          title="Provider inventory"
          description="Toggle providers on/off, rotate keys, and review exposed endpoints."
          actions={
            <button
              type="button"
              className={button.primary}
              onClick={() => showModal((onClose) => (
                <ProviderFormModal
                  onCreate={providerState.onCreateProvider}
                  onClose={onClose}
                  busy={feedback.isBusy(actionKey('provider', 'create'))}
                />
              ))}
              disabled={feedback.isBusy(actionKey('provider', 'create'))}
            >
              <Plus className="size-4" />
              Add provider
            </button>
          }
        >
          <div className="grid gap-4 md:grid-cols-1 lg:grid-cols-2">
            {data.providers.map((provider) => (
              <ProviderCard
                key={provider.id}
                provider={provider}
                isBusy={feedback.isBusy}
                testStatuses={providerState.endpointTestStatuses}
                showModal={showModal}
                handlers={providerState.providerHandlers}
              />
            ))}
            {data.providers.length === 0 && (
              <div className="col-span-2">
                <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-slate-500">
                  {data.overviewQuery.isPending ? (
                    <div className="flex items-center justify-center gap-2">
                      <LoaderCircle className="size-5 animate-spin" /> Loading providers...
                    </div>
                  ) : (
                    'No providers found.'
                  )}
                </div>
              </div>
            )}
          </div>
        </SectionCard>
        <SectionCard
          title="Token-based failover tiers"
          description={selectedProfile ? `Editing profile: ${selectedProfile.display_name || selectedProfile.name}` : 'Manage token ranges, tier ordering, and weighted endpoints.'}
          actions={
            <button type="button" className={button.primary} onClick={selectedProfile ? routing.handleProfileRangeAdd : routing.handleAddRange}>
              <PlusCircle className="size-4" /> Add range
            </button>
          }
        >
          <div className="space-y-6">
            {data.persistentStructures.ranges.map((range) => (
              <RangeSection
                key={range.id}
                range={range}
                tiers={data.persistentStructures.tiers.filter((tier) => tier.rangeId === range.id)}
                intelligenceTiers={data.intelligenceTiers}
                onAddTier={(tierKey) => selectedProfile ? routing.handleProfileTierAdd(range.id, tierKey) : routing.handleTierAdd(range.id, tierKey)}
                onUpdate={(field, value) => selectedProfile ? routing.handleProfileRangeUpdate(range.id, field, value) : routing.handleRangeUpdate(range.id, field, value)}
                onRemove={() => selectedProfile ? routing.handleProfileRangeRemove(range) : routing.handleRangeRemove(range)}
                onMoveTier={(tierId, direction) => selectedProfile ? routing.handleProfileTierMove(range.id, tierId, direction) : routing.handleTierMove(range.id, tierId, direction)}
                onRemoveTier={selectedProfile ? routing.handleProfileTierRemove : routing.handleTierRemove}
                onAddEndpoint={(tier) => routing.handleTierEndpointAdd(tier, 'persistent')}
                onStageEndpointWeight={routing.stageTierEndpointWeight}
                onCommitEndpointWeights={(tier) => selectedProfile ? routing.commitProfileTierEndpointWeights(tier, 'persistent') : routing.commitTierEndpointWeights(tier, 'persistent')}
                onRemoveEndpoint={(tier, endpoint) => selectedProfile ? routing.handleProfileTierEndpointRemove(tier, endpoint, 'persistent') : routing.handleTierEndpointRemove(tier, endpoint, 'persistent')}
                onUpdateEndpointReasoning={routing.handleTierEndpointReasoning}
                pendingWeights={routing.pendingWeights}
                savingTierIds={routing.savingTierIds}
                dirtyTierIds={routing.dirtyTierIds}
                isActionBusy={feedback.isBusy}
              />
            ))}
            {data.persistentStructures.ranges.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-slate-500">
                {(data.overviewQuery.isPending || data.profileDetailQuery.isPending) ? (
                  <div className="flex items-center justify-center gap-2">
                    <LoaderCircle className="size-5 animate-spin" /> Loading ranges...
                  </div>
                ) : (
                  'No token ranges configured yet.'
                )}
              </div>
            )}
          </div>
        </SectionCard>
        <SectionCard
          title="Browser-use models"
          description={selectedProfile ? `Editing profile: ${selectedProfile.display_name || selectedProfile.name}` : 'Dedicated tiers for browser automations.'}
        >
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {data.browserTierGroups.map((group) => (
              <TierGroupSection
                key={`browser:${group.key}`}
                group={group}
                scope="browser"
                pendingWeights={routing.pendingWeights}
                savingTierIds={routing.savingTierIds}
                dirtyTierIds={routing.dirtyTierIds}
                onAddTier={(tierKey) => selectedProfile ? routing.handleProfileBrowserTierAdd(tierKey) : routing.handleBrowserTierAdd(tierKey)}
                onMoveTier={(tierId, direction) => selectedProfile ? routing.handleProfileBrowserTierMove(tierId, direction) : routing.handleBrowserTierMove(tierId, direction)}
                onRemoveTier={selectedProfile ? routing.handleProfileBrowserTierRemove : routing.handleBrowserTierRemove}
                onAddEndpoint={(tier) => routing.handleTierEndpointAdd(tier, 'browser')}
                onStageEndpointWeight={routing.stageTierEndpointWeight}
                onCommitEndpointWeights={(tier) => selectedProfile ? routing.commitProfileTierEndpointWeights(tier, 'browser') : routing.commitTierEndpointWeights(tier, 'browser')}
                onRemoveEndpoint={(tier, endpoint) => selectedProfile ? routing.handleProfileTierEndpointRemove(tier, endpoint, 'browser') : routing.handleTierEndpointRemove(tier, endpoint, 'browser')}
                onUpdateExtraction={(tier, endpoint, extractionId) =>
                  selectedProfile
                    ? routing.handleProfileTierEndpointExtraction(tier, endpoint, extractionId, 'browser')
                    : routing.handleTierEndpointExtraction(tier, endpoint, extractionId, 'browser')
                }
                browserChoices={data.endpointChoices.browser_endpoints}
                isActionBusy={feedback.isBusy}
              />
            ))}
          </div>
        </SectionCard>
        <SectionCard
          title="Other model consumers"
          description={selectedProfile ? `Editing profile: ${selectedProfile.display_name || selectedProfile.name}` : 'Surface-level overview of summarization, judge models, embeddings, file handling, image generation, and video generation.'}
        >
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="rounded-xl border border-slate-200/80 bg-white p-4">
                <div className="flex items-start gap-3">
                  <BookText className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <h4 className="font-semibold text-slate-900/90">Summaries</h4>
                    <p className="text-sm text-slate-600 mb-3">
                      Optional cheap-model override for summarization and follow-up suggestions. Falls back to tier routing.
                    </p>
                    {selectedProfile ? (
                      <div className="flex items-center gap-2">
                        <select
                          className="flex-1 min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm focus:border-amber-500 focus:outline-none focus:ring-2 focus:ring-amber-500/40"
                          value={selectedProfile.summarization_endpoint?.endpoint_id ?? ''}
                          onChange={(e) => routing.handleUpdateSummarizationEndpoint(e.target.value || null)}
                          disabled={feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'summarization'))}
                        >
                          <option value="">— Use default tier fallback —</option>
                          {data.endpointChoices.persistent_endpoints.map((ep) => (
                            <option key={ep.id} value={ep.id}>
                              {ep.label} ({ep.model})
                            </option>
                          ))}
                        </select>
                        {selectedProfile.summarization_endpoint && (
                          <button
                            type="button"
                            className="flex-shrink-0 inline-flex items-center justify-center gap-1.5 rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-200/60 disabled:opacity-50 disabled:cursor-not-allowed"
                            onClick={() => routing.handleUpdateSummarizationEndpoint(null)}
                            disabled={feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'summarization'))}
                          >
                            <X className="size-4" />
                          </button>
                        )}
                        {feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'summarization')) && (
                          <Loader2 className="size-4 text-amber-600 animate-spin flex-shrink-0" />
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500">Select a routing profile to configure this override.</p>
                    )}
                  </div>
                </div>
              </div>
              <div className="rounded-xl border border-slate-200/80 bg-white p-4">
                <div className="flex items-start gap-3">
                  <Brain className="size-5 text-violet-500 flex-shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <h4 className="font-semibold text-slate-900/90">Agent Judge</h4>
                    <p className="text-sm text-slate-600 mb-3">
                      Dedicated model for advisory trajectory judge calls. Does not use tier fallback when unset.
                    </p>
                    {selectedProfile ? (
                      <div className="flex items-center gap-2">
                        <select
                          className="flex-1 min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/40"
                          value={selectedProfile.agent_judge_endpoint?.endpoint_id ?? ''}
                          onChange={(e) => routing.handleUpdateAgentJudgeEndpoint(e.target.value || null)}
                          disabled={feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'agent-judge'))}
                        >
                          <option value="">— No judge endpoint —</option>
                          {data.endpointChoices.persistent_endpoints.map((ep) => (
                            <option key={ep.id} value={ep.id}>
                              {ep.label} ({ep.model})
                            </option>
                          ))}
                        </select>
                        {selectedProfile.agent_judge_endpoint && (
                          <button
                            type="button"
                            className="flex-shrink-0 inline-flex items-center justify-center gap-1.5 rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-200/60 disabled:opacity-50 disabled:cursor-not-allowed"
                            onClick={() => routing.handleUpdateAgentJudgeEndpoint(null)}
                            disabled={feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'agent-judge'))}
                          >
                            <X className="size-4" />
                          </button>
                        )}
                        {feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'agent-judge')) && (
                          <Loader2 className="size-4 text-violet-600 animate-spin flex-shrink-0" />
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500">Select a routing profile to configure this judge model.</p>
                    )}
                  </div>
                </div>
              </div>
              <div className="rounded-xl border border-slate-200/80 bg-white p-4">
                <div className="flex items-start gap-3">
                  <Search className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-slate-900/90">Search tools</h4>
                    <p className="text-sm text-slate-600">Decisions are delegated to the main agent tiers.</p>
                  </div>
                </div>
              </div>
            </div>
            {selectedProfile && (
              <div className="bg-amber-50/50 p-4 rounded-xl">
                <div className="flex items-start gap-3">
                  <Scale className="size-5 text-amber-600 flex-shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <h4 className="font-semibold text-slate-900/90">Eval Judge</h4>
                    <p className="text-sm text-slate-600 mb-3">Endpoint used for evaluation judging/grading in this profile.</p>
                    <div className="flex items-center gap-2">
                      <select
                        className="flex-1 min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm focus:border-amber-500 focus:outline-none focus:ring-2 focus:ring-amber-500/40"
                        value={selectedProfile.eval_judge_endpoint?.endpoint_id ?? ''}
                        onChange={(e) => routing.handleUpdateEvalJudge(e.target.value || null)}
                        disabled={feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'eval-judge'))}
                      >
                        <option value="">— Use default tier fallback —</option>
                        {data.endpointChoices.persistent_endpoints.map((ep) => (
                          <option key={ep.id} value={ep.id}>
                            {ep.label} ({ep.model})
                          </option>
                        ))}
                      </select>
                      {selectedProfile.eval_judge_endpoint && (
                        <button
                          type="button"
                          className="flex-shrink-0 inline-flex items-center justify-center gap-1.5 rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-200/60 disabled:opacity-50 disabled:cursor-not-allowed"
                          onClick={() => routing.handleUpdateEvalJudge(null)}
                          disabled={feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'eval-judge'))}
                        >
                          <X className="size-4" />
                        </button>
                      )}
                      {feedback.isBusy(actionKey('profile', selectedProfileId ?? '', 'eval-judge')) && (
                        <Loader2 className="size-4 text-amber-600 animate-spin flex-shrink-0" />
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
            <div className="bg-slate-50/80 p-4 space-y-3 rounded-xl">
              <div className="flex items-center justify-between">
                <div className="flex items-start gap-3">
                  <PlugZap className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-slate-900/90">Embedding tiers</h4>
                    <p className="text-sm text-slate-600">Fallback order for generating embeddings.</p>
                  </div>
                </div>
                <button type="button" className={button.secondary} onClick={selectedProfile ? routing.handleProfileEmbeddingTierAdd : routing.handleEmbeddingTierAdd}>
                  <PlusCircle className="size-4" /> Add tier
                </button>
              </div>
              {data.embeddingTiers.map((tier, index) => {
                const lastIndex = data.embeddingTiers.length - 1
                return (
                <TierCard
                  key={tier.id}
                  tier={tier}
                  pendingWeights={routing.pendingWeights}
                  scope="embedding"
                  canMoveUp={index > 0}
                  canMoveDown={index < lastIndex}
                  isDirty={routing.dirtyTierIds.has(`embedding:${tier.id}`)}
                  isSaving={routing.savingTierIds.has(`embedding:${tier.id}`)}
                  onMove={(direction) => selectedProfile ? routing.handleProfileEmbeddingTierMove(tier.id, direction) : routing.handleEmbeddingTierMove(tier.id, direction)}
                  onRemove={selectedProfile ? routing.handleProfileEmbeddingTierRemove : routing.handleEmbeddingTierRemove}
                  onAddEndpoint={() => routing.handleTierEndpointAdd(tier, 'embedding')}
                  onStageEndpointWeight={(currentTier, tierEndpointId, weight) => routing.stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'embedding')}
                  onCommitEndpointWeights={(currentTier) => selectedProfile ? routing.commitProfileTierEndpointWeights(currentTier, 'embedding') : routing.commitTierEndpointWeights(currentTier, 'embedding')}
                  onRemoveEndpoint={(currentTier, endpoint) => selectedProfile ? routing.handleProfileTierEndpointRemove(currentTier, endpoint, 'embedding') : routing.handleTierEndpointRemove(currentTier, endpoint, 'embedding')}
                  isActionBusy={feedback.isBusy}
                />
                )
              })}
              {data.embeddingTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No embedding tiers configured.</p>}
            </div>
            <div className="rounded-xl border border-slate-200/80 bg-white p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-start gap-3">
                  <Sparkles className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h4 className="font-semibold text-slate-900/90">File handler tiers</h4>
                    <p className="text-sm text-slate-600">Fallback order for file-to-markdown conversion.</p>
                  </div>
                </div>
                <button type="button" className={button.secondary} onClick={routing.handleFileHandlerTierAdd}>
                  <PlusCircle className="size-4" /> Add tier
                </button>
              </div>
              {data.fileHandlerTiers.map((tier, index) => {
                const lastIndex = data.fileHandlerTiers.length - 1
                return (
                  <TierCard
                    key={tier.id}
                    tier={tier}
                    pendingWeights={routing.pendingWeights}
                    scope="file_handler"
                    canMoveUp={index > 0}
                    canMoveDown={index < lastIndex}
                    isDirty={routing.dirtyTierIds.has(`file_handler:${tier.id}`)}
                    isSaving={routing.savingTierIds.has(`file_handler:${tier.id}`)}
                    onMove={(direction) => routing.handleFileHandlerTierMove(tier.id, direction)}
                    onRemove={routing.handleFileHandlerTierRemove}
                    onAddEndpoint={() => routing.handleTierEndpointAdd(tier, 'file_handler')}
                    onStageEndpointWeight={(currentTier, tierEndpointId, weight) => routing.stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'file_handler')}
                    onCommitEndpointWeights={(currentTier) => routing.commitTierEndpointWeights(currentTier, 'file_handler')}
                    onRemoveEndpoint={(currentTier, endpoint) => routing.handleTierEndpointRemove(currentTier, endpoint, 'file_handler')}
                    isActionBusy={feedback.isBusy}
                  />
                )
              })}
              {data.fileHandlerTiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">No file handler tiers configured.</p>}
            </div>
            {data.imageGenerationSections.map((section) => (
              <div key={section.useCase} className="rounded-xl border border-slate-200/80 bg-white p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-start gap-3">
                    <Atom className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                    <div>
                      <h4 className="font-semibold text-slate-900/90">{section.title}</h4>
                      <p className="text-sm text-slate-600">{section.description}</p>
                    </div>
                  </div>
                  <button type="button" className={button.secondary} onClick={() => routing.handleImageGenerationTierAdd(section.useCase)}>
                    <PlusCircle className="size-4" /> Add tier
                  </button>
                </div>
                {section.tiers.map((tier, index) => {
                  const lastIndex = section.tiers.length - 1
                  return (
                    <TierCard
                      key={tier.id}
                      tier={tier}
                      pendingWeights={routing.pendingWeights}
                      scope="image_generation"
                      canMoveUp={index > 0}
                      canMoveDown={index < lastIndex}
                      isDirty={routing.dirtyTierIds.has(`image_generation:${tier.id}`)}
                      isSaving={routing.savingTierIds.has(`image_generation:${tier.id}`)}
                      onMove={(direction) => routing.handleImageGenerationTierMove(section.useCase, tier.id, direction)}
                      onRemove={(currentTier) => routing.handleImageGenerationTierRemove(section.useCase, currentTier)}
                      onAddEndpoint={() => routing.handleTierEndpointAdd(tier, 'image_generation')}
                      onStageEndpointWeight={(currentTier, tierEndpointId, weight) => routing.stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'image_generation')}
                      onCommitEndpointWeights={(currentTier) => routing.commitTierEndpointWeights(currentTier, 'image_generation')}
                      onRemoveEndpoint={(currentTier, endpoint) => routing.handleTierEndpointRemove(currentTier, endpoint, 'image_generation')}
                      isActionBusy={feedback.isBusy}
                    />
                  )
                })}
                {section.tiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">{section.emptyText}</p>}
              </div>
            ))}
            {data.videoGenerationSections.map((section) => (
              <div key={section.useCase} className="rounded-xl border border-slate-200/80 bg-white p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-start gap-3">
                    <Clock3 className="size-5 text-blue-500 flex-shrink-0 mt-0.5" />
                    <div>
                      <h4 className="font-semibold text-slate-900/90">{section.title}</h4>
                      <p className="text-sm text-slate-600">{section.description}</p>
                    </div>
                  </div>
                  <button type="button" className={button.secondary} onClick={() => routing.handleVideoGenerationTierAdd(section.useCase)}>
                    <PlusCircle className="size-4" /> Add tier
                  </button>
                </div>
                {section.tiers.map((tier, index) => {
                  const lastIndex = section.tiers.length - 1
                  return (
                    <TierCard
                      key={tier.id}
                      tier={tier}
                      pendingWeights={routing.pendingWeights}
                      scope="video_generation"
                      canMoveUp={index > 0}
                      canMoveDown={index < lastIndex}
                      isDirty={routing.dirtyTierIds.has(`video_generation:${tier.id}`)}
                      isSaving={routing.savingTierIds.has(`video_generation:${tier.id}`)}
                      onMove={(direction) => routing.handleVideoGenerationTierMove(section.useCase, tier.id, direction)}
                      onRemove={(currentTier) => routing.handleVideoGenerationTierRemove(section.useCase, currentTier)}
                      onAddEndpoint={() => routing.handleTierEndpointAdd(tier, 'video_generation')}
                      onStageEndpointWeight={(currentTier, tierEndpointId, weight) => routing.stageTierEndpointWeight(currentTier, tierEndpointId, weight, 'video_generation')}
                      onCommitEndpointWeights={(currentTier) => routing.commitTierEndpointWeights(currentTier, 'video_generation')}
                      onRemoveEndpoint={(currentTier, endpoint) => routing.handleTierEndpointRemove(currentTier, endpoint, 'video_generation')}
                      isActionBusy={feedback.isBusy}
                    />
                  )
                })}
                {section.tiers.length === 0 && <p className="text-center text-xs text-slate-400 py-4">{section.emptyText}</p>}
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    </>
  )

}
