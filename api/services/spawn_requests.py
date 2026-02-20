from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from api.models import (
    AgentSpawnRequest,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


class SpawnRequestResolutionError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, request_status: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.request_status = request_status


class SpawnRequestService:
    @staticmethod
    def get_request_status(
        *,
        agent: PersistentAgent,
        spawn_request_id: str,
    ) -> dict:
        spawn_request = (
            AgentSpawnRequest.objects.select_related("spawned_agent")
            .filter(id=spawn_request_id, agent=agent)
            .first()
        )
        if not spawn_request:
            raise SpawnRequestResolutionError("Spawn request not found.", status_code=404)

        if (
            spawn_request.status == AgentSpawnRequest.RequestStatus.PENDING
            and spawn_request.is_expired()
        ):
            spawn_request.status = AgentSpawnRequest.RequestStatus.EXPIRED
            spawn_request.responded_at = timezone.now()
            spawn_request.save(update_fields=["status", "responded_at"])

        payload = {
            "status": "ok",
            "request_status": spawn_request.status,
            "spawn_request_id": str(spawn_request.id),
        }
        if spawn_request.spawned_agent_id:
            payload["spawned_agent_id"] = str(spawn_request.spawned_agent_id)
            payload["spawned_agent_name"] = spawn_request.spawned_agent.name
        return payload

    @staticmethod
    def create_or_reuse_pending_request(
        *,
        agent: PersistentAgent,
        requested_charter: str,
        handoff_message: str,
        request_reason: str = "",
        expires_in_days: int = 7,
    ) -> tuple[AgentSpawnRequest, bool]:
        fingerprint = AgentSpawnRequest.build_request_fingerprint(
            requested_charter=requested_charter,
            handoff_message=handoff_message,
        )

        existing_request = (
            AgentSpawnRequest.objects.filter(
                agent=agent,
                status=AgentSpawnRequest.RequestStatus.PENDING,
                request_fingerprint=fingerprint,
            )
            .order_by("-requested_at")
            .first()
        )
        if existing_request:
            return existing_request, False

        try:
            spawn_request = AgentSpawnRequest.objects.create(
                agent=agent,
                requested_charter=requested_charter,
                handoff_message=handoff_message,
                request_reason=request_reason,
                expires_at=timezone.now() + timedelta(days=expires_in_days),
                request_fingerprint=fingerprint,
            )
            return spawn_request, True
        except IntegrityError:
            # Another worker may have created the same pending request concurrently.
            existing_request = (
                AgentSpawnRequest.objects.filter(
                    agent=agent,
                    status=AgentSpawnRequest.RequestStatus.PENDING,
                    request_fingerprint=fingerprint,
                )
                .order_by("-requested_at")
                .first()
            )
            if existing_request:
                return existing_request, False
            raise

    @staticmethod
    def resolve_request(
        *,
        agent: PersistentAgent,
        spawn_request_id: str,
        decision: str,
        actor,
    ) -> dict:
        normalized_decision = (decision or "").strip().lower()
        if normalized_decision not in {"approve", "decline"}:
            raise SpawnRequestResolutionError("decision must be 'approve' or 'decline'.")

        with transaction.atomic():
            spawn_request = (
                AgentSpawnRequest.objects.select_for_update()
                .filter(id=spawn_request_id, agent=agent)
                .first()
            )
            if not spawn_request:
                raise SpawnRequestResolutionError("Spawn request not found.", status_code=404)

            if (
                spawn_request.status == AgentSpawnRequest.RequestStatus.PENDING
                and spawn_request.is_expired()
            ):
                spawn_request.status = AgentSpawnRequest.RequestStatus.EXPIRED
                spawn_request.responded_at = timezone.now()
                spawn_request.save(update_fields=["status", "responded_at"])
                raise SpawnRequestResolutionError(
                    "Spawn request has expired.",
                    request_status=AgentSpawnRequest.RequestStatus.EXPIRED,
                )

            if spawn_request.status != AgentSpawnRequest.RequestStatus.PENDING:
                raise SpawnRequestResolutionError(
                    "Spawn request has already been resolved.",
                    request_status=spawn_request.status,
                )

            base_props = Analytics.with_org_properties(
                {
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "spawn_request_id": str(spawn_request.id),
                },
                organization=agent.organization,
            )

            if normalized_decision == "approve":
                spawned_agent, peer_link = spawn_request.approve(actor)
                step = PersistentAgentStep.objects.create(
                    agent=agent,
                    description=(
                        f"Spawn request approved: created peer agent {spawned_agent.name} "
                        f"({spawned_agent.id}) and linked for handoff."
                    ),
                )
                PersistentAgentSystemStep.objects.create(
                    step=step,
                    code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
                    notes=(
                        f"spawn_request_id={spawn_request.id}; decision=approved; "
                        f"spawned_agent_id={spawned_agent.id}; peer_link_id={peer_link.id}; actor_id={actor.id}"
                    ),
                )

                approved_props = {
                    **base_props,
                    "spawned_agent_id": str(spawned_agent.id),
                    "spawned_agent_name": spawned_agent.name,
                    "peer_link_id": str(peer_link.id),
                }
                transaction.on_commit(
                    lambda: Analytics.track_event(
                        user_id=actor.id,
                        event=AnalyticsEvent.AGENT_SPAWN_APPROVED,
                        source=AnalyticsSource.WEB,
                        properties=approved_props.copy(),
                    )
                )
                transaction.on_commit(
                    lambda: Analytics.track_event(
                        user_id=actor.id,
                        event=AnalyticsEvent.AGENT_SPAWN_AGENT_CREATED,
                        source=AnalyticsSource.WEB,
                        properties=approved_props.copy(),
                    )
                )

                return {
                    "status": "ok",
                    "request_status": AgentSpawnRequest.RequestStatus.APPROVED,
                    "message": f"Created and linked {spawned_agent.name}.",
                    "spawned_agent_id": str(spawned_agent.id),
                    "spawned_agent_name": spawned_agent.name,
                    "peer_link_id": str(peer_link.id),
                }

            spawn_request.reject(actor)
            step = PersistentAgentStep.objects.create(
                agent=agent,
                description="Spawn request declined by user.",
            )
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
                notes=(
                    f"spawn_request_id={spawn_request.id}; decision=rejected; actor_id={actor.id}"
                ),
            )

            transaction.on_commit(
                lambda: Analytics.track_event(
                    user_id=actor.id,
                    event=AnalyticsEvent.AGENT_SPAWN_REJECTED,
                    source=AnalyticsSource.WEB,
                    properties=base_props.copy(),
                )
            )

            return {
                "status": "ok",
                "request_status": AgentSpawnRequest.RequestStatus.REJECTED,
                "message": "Spawn request declined.",
            }
