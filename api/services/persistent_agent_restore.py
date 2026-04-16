import logging
import uuid
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from api.models import (
    AgentPeerLink,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)


logger = logging.getLogger(__name__)

PEER_CONVERSATION_PREFIX = "peer://"
PEER_ENDPOINT_PREFIX = "peer://agent/"
SNAPSHOT_VERSION = 1
User = get_user_model()


@dataclass
class RestoredAgentRepairResult:
    agent_id: str
    agent_name: str
    used_snapshot: bool = False
    restored_endpoint_ids: list[str] = field(default_factory=list)
    restored_peer_links: list[str] = field(default_factory=list)
    reattached_conversation_ids: list[str] = field(default_factory=list)
    skipped_peer_links: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_actions(self) -> bool:
        return bool(
            self.restored_endpoint_ids
            or self.restored_peer_links
            or self.reattached_conversation_ids
        )


class PersistentAgentRestoreRepairService:
    """Restore comms resources released by persistent-agent soft delete."""

    @staticmethod
    def snapshot_has_restore_payload(snapshot: dict | None) -> bool:
        snapshot = snapshot or {}
        return bool(snapshot.get("owned_endpoints") or snapshot.get("peer_links"))

    @classmethod
    def build_soft_delete_snapshot(cls, agent: PersistentAgent) -> dict:
        owned_endpoints = []
        endpoint_rows = (
            PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent)
            .order_by("channel", "address", "id")
            .values("id", "channel", "address", "is_primary")
        )
        for row in endpoint_rows:
            owned_endpoints.append(
                {
                    "id": str(row["id"]),
                    "channel": row["channel"],
                    "address": row["address"],
                    "is_primary": bool(row["is_primary"]),
                }
            )

        peer_links = []
        links = (
            AgentPeerLink.objects.filter(Q(agent_a=agent) | Q(agent_b=agent))
            .select_related("conversation")
            .order_by("pair_key")
        )
        for link in links:
            try:
                conversation = link.conversation
            except PersistentAgentConversation.DoesNotExist:
                conversation = None

            peer_agent = link.get_other_agent(agent)
            peer_links.append(
                {
                    "peer_agent_id": str(peer_agent.id) if peer_agent else "",
                    "pair_key": link.pair_key,
                    "conversation_id": str(conversation.id) if conversation else "",
                    "agent_a_id": str(link.agent_a_id),
                    "agent_b_id": str(link.agent_b_id),
                    "agent_a_endpoint_id": str(link.agent_a_endpoint_id) if link.agent_a_endpoint_id else "",
                    "agent_b_endpoint_id": str(link.agent_b_endpoint_id) if link.agent_b_endpoint_id else "",
                    "messages_per_window": int(link.messages_per_window),
                    "window_hours": int(link.window_hours),
                    "is_enabled": bool(link.is_enabled),
                    "feature_flag": link.feature_flag or "",
                    "created_by_id": link.created_by_id if link.created_by_id else None,
                }
            )

        return {
            "version": SNAPSHOT_VERSION,
            "captured_at": timezone.now().isoformat(),
            "owned_endpoints": owned_endpoints,
            "peer_links": peer_links,
        }

    @classmethod
    def repair(
        cls,
        agent: PersistentAgent,
        *,
        apply: bool,
        provision_email_fallback: bool = False,
    ) -> RestoredAgentRepairResult:
        result = RestoredAgentRepairResult(
            agent_id=str(agent.id),
            agent_name=agent.name or "",
        )

        if apply:
            with transaction.atomic():
                cls._repair(agent, result, apply=apply, provision_email_fallback=provision_email_fallback)
        else:
            cls._repair(agent, result, apply=apply, provision_email_fallback=provision_email_fallback)

        return result

    @classmethod
    def _repair(
        cls,
        agent: PersistentAgent,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
        provision_email_fallback: bool,
    ) -> None:
        snapshot = agent.soft_delete_restore_snapshot or {}

        if snapshot.get("owned_endpoints") or snapshot.get("peer_links"):
            result.used_snapshot = True
            cls._restore_snapshot_endpoints(agent, snapshot, result, apply=apply)

        cls._restore_fallback_peer_endpoint(agent, result, apply=apply)

        if not cls._has_owned_email_endpoint(agent):
            cls._restore_fallback_email_endpoints(agent, result, apply=apply)

        if provision_email_fallback and not cls._has_owned_email_endpoint(agent):
            cls._ensure_default_email_endpoint(agent, result, apply=apply)

        if snapshot.get("peer_links"):
            cls._restore_snapshot_peer_links(agent, snapshot, result, apply=apply)
        cls._restore_fallback_peer_links(agent, result, apply=apply)

    @classmethod
    def _restore_snapshot_endpoints(
        cls,
        agent: PersistentAgent,
        snapshot: dict,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
    ) -> None:
        primary_ids_by_channel: dict[str, list[str]] = {}

        for entry in snapshot.get("owned_endpoints", []):
            endpoint_id = (entry.get("id") or "").strip()
            if not endpoint_id:
                continue

            endpoint = PersistentAgentCommsEndpoint.objects.filter(pk=endpoint_id).first()
            if endpoint is None:
                result.notes.append(f"missing endpoint {endpoint_id} from restore snapshot")
                continue

            if endpoint.owner_agent_id not in (None, agent.id):
                result.notes.append(
                    f"skipped endpoint {endpoint.address}: owned by agent {endpoint.owner_agent_id}"
                )
                continue

            desired_primary = bool(entry.get("is_primary"))
            if endpoint.owner_agent_id != agent.id or endpoint.is_primary != desired_primary:
                cls._append_unique(result.restored_endpoint_ids, str(endpoint.id))
                if apply:
                    update_fields = []
                    if endpoint.owner_agent_id != agent.id:
                        endpoint.owner_agent = agent
                        update_fields.append("owner_agent")
                    if endpoint.is_primary != desired_primary:
                        endpoint.is_primary = desired_primary
                        update_fields.append("is_primary")
                    if update_fields:
                        endpoint.save(update_fields=update_fields)

            if desired_primary:
                primary_ids_by_channel.setdefault(endpoint.channel, []).append(str(endpoint.id))

        if not apply:
            return

        for channel, primary_ids in primary_ids_by_channel.items():
            if not primary_ids:
                continue
            (
                PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=channel)
                .exclude(id__in=primary_ids)
                .update(is_primary=False)
            )

    @classmethod
    def _restore_fallback_peer_endpoint(
        cls,
        agent: PersistentAgent,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
    ) -> None:
        endpoint = PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.OTHER,
            address=f"{PEER_ENDPOINT_PREFIX}{agent.id}",
        ).first()
        if endpoint is None:
            return
        if endpoint.owner_agent_id not in (None, agent.id):
            return
        if endpoint.owner_agent_id == agent.id and endpoint.is_primary is False:
            return

        cls._append_unique(result.restored_endpoint_ids, str(endpoint.id))
        if apply:
            update_fields = []
            if endpoint.owner_agent_id != agent.id:
                endpoint.owner_agent = agent
                update_fields.append("owner_agent")
            if endpoint.is_primary:
                endpoint.is_primary = False
                update_fields.append("is_primary")
            if update_fields:
                endpoint.save(update_fields=update_fields)

    @classmethod
    def _restore_fallback_email_endpoints(
        cls,
        agent: PersistentAgent,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
    ) -> None:
        if cls._has_owned_email_endpoint(agent):
            return

        message_rows = (
            PersistentAgentMessage.objects.filter(
                owner_agent=agent,
                is_outbound=True,
                from_endpoint__channel=CommsChannel.EMAIL,
            )
            .select_related("from_endpoint")
            .order_by("-timestamp")
        )

        candidates = []
        seen_endpoint_ids = set()
        for message in message_rows:
            endpoint = message.from_endpoint
            if endpoint is None or endpoint.id in seen_endpoint_ids:
                continue
            seen_endpoint_ids.add(endpoint.id)
            if endpoint.owner_agent_id not in (None, agent.id):
                continue
            candidates.append(endpoint)

        if not candidates:
            return

        primary_endpoint = cls._select_fallback_primary_email_endpoint(candidates)
        for endpoint in candidates:
            desired_primary = primary_endpoint is not None and endpoint.id == primary_endpoint.id
            if endpoint.owner_agent_id != agent.id or endpoint.is_primary != desired_primary:
                cls._append_unique(result.restored_endpoint_ids, str(endpoint.id))
                if apply:
                    update_fields = []
                    if endpoint.owner_agent_id != agent.id:
                        endpoint.owner_agent = agent
                        update_fields.append("owner_agent")
                    if endpoint.is_primary != desired_primary:
                        endpoint.is_primary = desired_primary
                        update_fields.append("is_primary")
                    if update_fields:
                        endpoint.save(update_fields=update_fields)

        if apply and primary_endpoint is not None:
            (
                PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=CommsChannel.EMAIL)
                .exclude(id=primary_endpoint.id)
                .update(is_primary=False)
            )

    @classmethod
    def _restore_snapshot_peer_links(
        cls,
        agent: PersistentAgent,
        snapshot: dict,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
    ) -> None:
        for entry in snapshot.get("peer_links", []):
            pair_key = (entry.get("pair_key") or "").strip()
            if not pair_key:
                continue

            peer_agent = cls._get_alive_peer_agent(entry.get("peer_agent_id"))
            if peer_agent is None:
                result.skipped_peer_links.append(f"{pair_key}: peer agent missing or deleted")
                continue

            link = AgentPeerLink.objects.filter(pair_key=pair_key).first()
            if link is None:
                link = cls._build_snapshot_peer_link(agent, peer_agent, entry, pair_key)
                if link is None:
                    result.skipped_peer_links.append(f"{pair_key}: invalid snapshot payload")
                    continue

                if apply:
                    try:
                        link.save()
                    except (IntegrityError, ValidationError) as exc:
                        logger.warning("Failed restoring peer link %s for agent %s", pair_key, agent.id, exc_info=True)
                        result.skipped_peer_links.append(f"{pair_key}: {exc}")
                        continue
                cls._append_unique(result.restored_peer_links, pair_key)

            cls._restore_preserved_peer_conversation(
                link,
                pair_key=pair_key,
                conversation_id=(entry.get("conversation_id") or "").strip(),
                result=result,
                apply=apply,
            )

    @classmethod
    def _restore_fallback_peer_links(
        cls,
        agent: PersistentAgent,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
    ) -> None:
        conversations = PersistentAgentConversation.objects.filter(
            address__startswith=PEER_CONVERSATION_PREFIX,
            peer_link__isnull=True,
            address__contains=str(agent.id),
        ).order_by("id")

        for conversation in conversations:
            pair_key, agent_ids = cls._parse_peer_pair_key(conversation.address)
            if not pair_key or str(agent.id) not in agent_ids:
                continue

            peer_agent_id = next((agent_id for agent_id in agent_ids if agent_id != str(agent.id)), "")
            peer_agent = cls._get_alive_peer_agent(peer_agent_id)
            if peer_agent is None:
                result.skipped_peer_links.append(f"{pair_key}: peer agent missing or deleted")
                continue

            link = AgentPeerLink.objects.filter(pair_key=pair_key).first()
            if link is None:
                link = cls._build_fallback_peer_link(agent, peer_agent)
                if apply:
                    try:
                        link.save()
                    except (IntegrityError, ValidationError) as exc:
                        logger.warning("Failed recreating peer link %s for agent %s", pair_key, agent.id, exc_info=True)
                        result.skipped_peer_links.append(f"{pair_key}: {exc}")
                        continue
                cls._append_unique(result.restored_peer_links, pair_key)

            cls._restore_preserved_peer_conversation(
                link,
                pair_key=pair_key,
                conversation_id=str(conversation.id),
                result=result,
                apply=apply,
            )

    @classmethod
    def _restore_preserved_peer_conversation(
        cls,
        link: AgentPeerLink,
        *,
        pair_key: str,
        conversation_id: str,
        result: RestoredAgentRepairResult,
        apply: bool,
    ) -> None:
        if not conversation_id:
            return

        conversation = PersistentAgentConversation.objects.filter(pk=conversation_id).first()
        if conversation is None:
            result.notes.append(f"missing preserved peer conversation {conversation_id} for {pair_key}")
            return
        if conversation.address != f"{PEER_CONVERSATION_PREFIX}{pair_key}":
            result.skipped_peer_links.append(f"{pair_key}: preserved conversation address mismatch")
            return

        try:
            existing_conversation = link.conversation
        except PersistentAgentConversation.DoesNotExist:
            existing_conversation = None

        if existing_conversation and existing_conversation.id != conversation.id:
            result.skipped_peer_links.append(f"{pair_key}: link already has conversation {existing_conversation.id}")
            return
        if conversation.peer_link_id not in (None, link.id):
            result.skipped_peer_links.append(
                f"{pair_key}: preserved conversation already claimed by {conversation.peer_link_id}"
            )
            return

        if conversation.peer_link_id != link.id or not conversation.is_peer_dm:
            cls._append_unique(result.reattached_conversation_ids, str(conversation.id))
            if apply:
                conversation.peer_link = link
                conversation.is_peer_dm = True
                conversation.save(update_fields=["peer_link", "is_peer_dm"])

    @classmethod
    def _build_snapshot_peer_link(
        cls,
        agent: PersistentAgent,
        peer_agent: PersistentAgent,
        entry: dict,
        pair_key: str,
    ) -> AgentPeerLink | None:
        agent_a_id = (entry.get("agent_a_id") or "").strip()
        agent_b_id = (entry.get("agent_b_id") or "").strip()
        if str(agent.id) not in {agent_a_id, agent_b_id}:
            return None
        if str(peer_agent.id) not in {agent_a_id, agent_b_id}:
            return None

        created_by_id = entry.get("created_by_id")
        if created_by_id and not User.objects.filter(pk=created_by_id).exists():
            created_by_id = None

        return AgentPeerLink(
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            created_by_id=created_by_id,
            messages_per_window=int(entry.get("messages_per_window") or AgentPeerLink._meta.get_field("messages_per_window").default),
            window_hours=int(entry.get("window_hours") or AgentPeerLink._meta.get_field("window_hours").default),
            is_enabled=bool(entry.get("is_enabled", True)),
            feature_flag=(entry.get("feature_flag") or "").strip(),
            agent_a_endpoint=cls._resolve_link_endpoint(
                (entry.get("agent_a_endpoint_id") or "").strip(),
                owner_agent_id=agent_a_id,
            ),
            agent_b_endpoint=cls._resolve_link_endpoint(
                (entry.get("agent_b_endpoint_id") or "").strip(),
                owner_agent_id=agent_b_id,
            ),
        )

    @classmethod
    def _build_fallback_peer_link(
        cls,
        agent: PersistentAgent,
        peer_agent: PersistentAgent,
    ) -> AgentPeerLink:
        ordered_ids = sorted([str(agent.id), str(peer_agent.id)])
        agent_a_id = ordered_ids[0]
        agent_b_id = ordered_ids[1]
        return AgentPeerLink(
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            agent_a_endpoint=cls._resolve_deterministic_peer_endpoint(agent_a_id),
            agent_b_endpoint=cls._resolve_deterministic_peer_endpoint(agent_b_id),
        )

    @classmethod
    def _resolve_link_endpoint(
        cls,
        endpoint_id: str,
        *,
        owner_agent_id: str,
    ) -> PersistentAgentCommsEndpoint | None:
        if not endpoint_id:
            return None
        endpoint = PersistentAgentCommsEndpoint.objects.filter(pk=endpoint_id).first()
        if endpoint is None or str(endpoint.owner_agent_id) != str(owner_agent_id):
            return None
        return endpoint

    @classmethod
    def _resolve_deterministic_peer_endpoint(cls, agent_id: str) -> PersistentAgentCommsEndpoint | None:
        return PersistentAgentCommsEndpoint.objects.filter(
            owner_agent_id=agent_id,
            channel=CommsChannel.OTHER,
            address=f"{PEER_ENDPOINT_PREFIX}{agent_id}",
        ).first()

    @classmethod
    def _select_fallback_primary_email_endpoint(
        cls,
        candidates: list[PersistentAgentCommsEndpoint],
    ) -> PersistentAgentCommsEndpoint | None:
        if not candidates:
            return None

        with_account = []
        default_domain = []
        remainder = []

        for endpoint in candidates:
            if hasattr(endpoint, "agentemailaccount"):
                with_account.append(endpoint)
            elif cls._is_default_agent_email(endpoint.address):
                default_domain.append(endpoint)
            else:
                remainder.append(endpoint)

        if with_account:
            return with_account[0]
        if default_domain:
            return default_domain[0]
        return remainder[0] if remainder else candidates[0]

    @classmethod
    def _ensure_default_email_endpoint(
        cls,
        agent: PersistentAgent,
        result: RestoredAgentRepairResult,
        *,
        apply: bool,
    ) -> None:
        from api.services.persistent_agents import ensure_default_agent_email_endpoint

        endpoint = ensure_default_agent_email_endpoint(agent, is_primary=True) if apply else None
        if apply and endpoint is not None:
            cls._append_unique(result.restored_endpoint_ids, str(endpoint.id))
            return

        if not apply:
            result.notes.append(f"would provision default email endpoint for agent {agent.id}")

    @classmethod
    def _has_owned_email_endpoint(cls, agent: PersistentAgent) -> bool:
        return PersistentAgentCommsEndpoint.objects.filter(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
        ).exists()

    @classmethod
    def _get_alive_peer_agent(cls, peer_agent_id: str) -> PersistentAgent | None:
        if not peer_agent_id:
            return None
        try:
            peer_uuid = uuid.UUID(str(peer_agent_id))
        except (TypeError, ValueError, AttributeError):
            return None
        return PersistentAgent.objects.alive().filter(pk=peer_uuid).first()

    @classmethod
    def _parse_peer_pair_key(cls, address: str) -> tuple[str, list[str]]:
        raw_address = (address or "").strip()
        if not raw_address.startswith(PEER_CONVERSATION_PREFIX):
            return "", []

        pair_key = raw_address[len(PEER_CONVERSATION_PREFIX):].strip()
        parts = pair_key.split("::")
        if len(parts) != 2:
            return "", []

        try:
            normalized_parts = [str(uuid.UUID(part.strip())) for part in parts]
        except (ValueError, AttributeError):
            return "", []
        return pair_key, normalized_parts

    @classmethod
    def _is_default_agent_email(cls, address: str) -> bool:
        from api.services.agent_email_aliases import is_default_agent_email_address

        return is_default_agent_email_address(address)

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value and value not in values:
            values.append(value)
