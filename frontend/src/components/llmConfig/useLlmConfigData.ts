import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import * as llmApi from '../../api/llmConfig'
import {
  buildTierGroups, DEFAULT_INTELLIGENCE_TIERS, IMAGE_GENERATION_SECTION_CONFIG, mapBrowserTiers, mapBrowserTiersFromProfile, mapEmbeddingTiers, mapEmbeddingTiersFromProfile, mapFileHandlerTiers,
  mapImageGenerationTiers, mapPersistentData, mapProviders, mapVideoGenerationTiers, VIDEO_GENERATION_SECTION_CONFIG,
} from './shared'

const emptyEndpointChoices: llmApi.EndpointChoices = {
  persistent_endpoints: [],
  browser_endpoints: [],
  embedding_endpoints: [],
  file_handler_endpoints: [],
  image_generation_endpoints: [],
  video_generation_endpoints: [],
}

export function useLlmConfigData() {
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null)
  
  const profilesQuery = useQuery({
    queryKey: ['llm-routing-profiles'],
    queryFn: ({ signal }) => llmApi.fetchRoutingProfiles(signal),
    refetchOnWindowFocus: false,
  })
  
  useEffect(() => {
    if (profilesQuery.data?.profiles && !selectedProfileId) {
      const activeProfile = profilesQuery.data.profiles.find(p => p.is_active)
      if (activeProfile) {
        setSelectedProfileId(activeProfile.id)
      } else if (profilesQuery.data.profiles.length > 0) {
        setSelectedProfileId(profilesQuery.data.profiles[0].id)
      }
    }
  }, [profilesQuery.data?.profiles, selectedProfileId])
  
  const profileDetailQuery = useQuery({
    queryKey: ['llm-routing-profile', selectedProfileId],
    queryFn: ({ signal }) => selectedProfileId ? llmApi.fetchRoutingProfileDetail(selectedProfileId, signal) : Promise.resolve(null),
    enabled: Boolean(selectedProfileId),
    refetchOnWindowFocus: false,
  })
  
  const selectedProfile = profileDetailQuery.data?.profile ?? null
  const profiles = profilesQuery.data?.profiles ?? []
  
  const overviewQuery = useQuery({
    queryKey: ['llm-overview'],
    queryFn: ({ signal }) => llmApi.fetchLlmOverview(signal),
    refetchOnWindowFocus: false,
  })
  
  const intelligenceTiers = useMemo(() => {
    const tiers = overviewQuery.data?.intelligence_tiers
    if (tiers && tiers.length) {
      return [...tiers].sort((a, b) => a.rank - b.rank)
    }
    return DEFAULT_INTELLIGENCE_TIERS
  }, [overviewQuery.data?.intelligence_tiers])
  
  const stats = overviewQuery.data?.stats
  const providers = useMemo(() => mapProviders(overviewQuery.data?.providers), [overviewQuery.data?.providers])
  
  const persistentStructures = useMemo(() => {
    if (selectedProfile) {
      return mapPersistentData(selectedProfile.persistent.ranges)
    }
    return mapPersistentData(overviewQuery.data?.persistent.ranges)
  }, [selectedProfile, overviewQuery.data?.persistent.ranges])
  
  const browserTiers = useMemo(() => {
    if (selectedProfile) {
      return mapBrowserTiersFromProfile(selectedProfile.browser.tiers)
    }
    return mapBrowserTiers(overviewQuery.data?.browser ?? null)
  }, [selectedProfile, overviewQuery.data?.browser])
  
  const embeddingTiers = useMemo(() => {
    if (selectedProfile) {
      return mapEmbeddingTiersFromProfile(selectedProfile.embeddings.tiers)
    }
    return mapEmbeddingTiers(overviewQuery.data?.embeddings.tiers)
  }, [selectedProfile, overviewQuery.data?.embeddings.tiers])
  
  const fileHandlerTiers = useMemo(
    () => mapFileHandlerTiers(overviewQuery.data?.file_handlers?.tiers),
    [overviewQuery.data?.file_handlers?.tiers],
  )
  const imageGenerationTiers = useMemo(
    () => mapImageGenerationTiers(overviewQuery.data?.image_generations?.create_image_tiers, 'create_image'),
    [overviewQuery.data?.image_generations?.create_image_tiers],
  )
  const avatarImageGenerationTiers = useMemo(
    () => mapImageGenerationTiers(overviewQuery.data?.image_generations?.avatar_tiers, 'avatar'),
    [overviewQuery.data?.image_generations?.avatar_tiers],
  )
  const videoGenerationTiers = useMemo(
    () => mapVideoGenerationTiers(overviewQuery.data?.video_generations?.create_video_tiers, 'create_video'),
    [overviewQuery.data?.video_generations?.create_video_tiers],
  )
  const imageGenerationSections = useMemo(
    () => ([
      {
        ...IMAGE_GENERATION_SECTION_CONFIG.create_image,
        tiers: imageGenerationTiers,
      },
      {
        ...IMAGE_GENERATION_SECTION_CONFIG.avatar,
        tiers: avatarImageGenerationTiers,
      },
    ]),
    [avatarImageGenerationTiers, imageGenerationTiers],
  )
  const videoGenerationSections = useMemo(
    () => ([
      {
        ...VIDEO_GENERATION_SECTION_CONFIG.create_video,
        tiers: videoGenerationTiers,
      },
    ]),
    [videoGenerationTiers],
  )
  
  const browserTierGroups = useMemo(
    () => buildTierGroups(browserTiers, intelligenceTiers),
    [browserTiers, intelligenceTiers],
  )
  const endpointChoices = overviewQuery.data?.choices ?? emptyEndpointChoices
  
  return {
    selectedProfileId,
    setSelectedProfileId,
    profilesQuery,
    profileDetailQuery,
    selectedProfile,
    profiles,
    overviewQuery,
    intelligenceTiers,
    stats,
    providers,
    persistentStructures,
    browserTiers,
    embeddingTiers,
    fileHandlerTiers,
    imageGenerationTiers,
    avatarImageGenerationTiers,
    videoGenerationTiers,
    imageGenerationSections,
    videoGenerationSections,
    browserTierGroups,
    endpointChoices,
  }
  
}
