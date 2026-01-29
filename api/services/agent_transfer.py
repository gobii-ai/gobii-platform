from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional, Tuple

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from agents.services import AgentService
from api.agent.tasks import process_agent_events_task
from api.services.persistent_agents import maybe_sync_agent_email_display_name
from api.models import (
    AgentFileSpace,
    AgentPeerLink,
    AgentTransferInvite,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
)

User = get_user_model()
logger = logging.getLogger(__name__)


class AgentTransferError(Exception):
    """Base error for agent transfer operations."""


class AgentTransferDenied(AgentTransferError):
    """Raised when a transfer is not permitted."""


@dataclass
class TransferAllowance:
    allowed: bool
    reason: Optional[str] = None


class AgentTransferService:
    """Service helpers for initiating and accepting persistent agent transfers."""

    @staticmethod
    def allow_transfer(agent: PersistentAgent, target_user: User) -> TransferAllowance:
        """Stubbed transfer-eligibility gate.

        Returns True for all transfers today, but provides a single place where
        plan or policy checks can be implemented later.
        """

        return TransferAllowance(True, None)

    @staticmethod
    def handle_secrets_transfer(agent: PersistentAgent, from_user: User, to_user: User) -> None:
        """Placeholder hook for future secret revalidation."""

        return None

    @staticmethod
    def initiate_transfer(
        agent: PersistentAgent,
        to_email: str,
        initiated_by: User,
        *,
        message: str = "",
    ) -> AgentTransferInvite:
        """Create or replace a pending transfer invitation."""

        normalized_email = (to_email or "").strip().lower()
        if not normalized_email:
            raise ValidationError({"email": "Recipient email is required."})

        if initiated_by.email and initiated_by.email.lower() == normalized_email:
            raise ValidationError({"email": "You already own this agent."})

        now = timezone.now()

        with transaction.atomic():
            # Cancel any existing pending invite for this agent
            AgentTransferInvite.objects.filter(
                agent=agent,
                status=AgentTransferInvite.Status.PENDING,
            ).update(status=AgentTransferInvite.Status.CANCELLED, responded_at=now)

            try:
                target_user = User.objects.get(email__iexact=normalized_email)
            except User.DoesNotExist:
                target_user = None

            invite = AgentTransferInvite(
                agent=agent,
                initiated_by=initiated_by,
                to_email=normalized_email,
                to_user=target_user,
                message=message or "",
            )
            invite.full_clean()
            invite.save()
            return invite

    @staticmethod
    def decline_invite(invite: AgentTransferInvite, recipient: User) -> AgentTransferInvite:
        """Decline a pending transfer invite."""

        if invite.status != AgentTransferInvite.Status.PENDING:
            raise AgentTransferError("Invite already handled")

        with transaction.atomic():
            invite = AgentTransferService._lock_invite(invite.pk)
            if invite.status != AgentTransferInvite.Status.PENDING:
                raise AgentTransferError("Invite already handled")

            invite.to_user = recipient
            invite.status = AgentTransferInvite.Status.DECLINED
            invite.responded_at = timezone.now()
            invite.save(update_fields=["to_user", "status", "responded_at"])
            return invite

    @staticmethod
    def accept_invite(invite: AgentTransferInvite, recipient: User) -> AgentTransferInvite:
        """Accept an invite and migrate the persistent agent to the recipient."""

        if invite.status != AgentTransferInvite.Status.PENDING:
            raise AgentTransferError("Invite already handled")

        if not recipient.email:
            raise AgentTransferError("Recipient must have a verified email address")

        if invite.to_email.lower() != recipient.email.lower():
            raise AgentTransferDenied("This invite was sent to a different email address.")

        with transaction.atomic():
            invite = AgentTransferService._lock_invite(invite.pk)
            if invite.status != AgentTransferInvite.Status.PENDING:
                raise AgentTransferError("Invite already handled")

            agent = PersistentAgent.objects.select_for_update().get(pk=invite.agent_id)
            original_owner = agent.user
            previous_name = agent.name
            allowance = AgentTransferService.allow_transfer(agent, recipient)
            if not allowance.allowed:
                raise AgentTransferDenied(allowance.reason or "Transfer is not allowed.")

            browser_agent = getattr(agent, "browser_use_agent", None)
            had_capacity = AgentTransferService._recipient_has_capacity(recipient)

            new_agent_name = AgentTransferService._ensure_unique_agent_name(agent, recipient)
            new_browser_name = (
                AgentTransferService._ensure_unique_browser_name(browser_agent, recipient)
                if browser_agent
                else None
            )

            agent.user = recipient
            agent.organization = None
            agent.name = new_agent_name

            contact_endpoint = AgentTransferService._ensure_owner_contact_endpoint(agent, recipient)
            if contact_endpoint:
                agent.preferred_contact_endpoint = contact_endpoint
            else:
                agent.preferred_contact_endpoint = None

            if not had_capacity:
                agent.is_active = False

            update_fields = ["user", "organization", "name", "preferred_contact_endpoint"]
            if not had_capacity:
                update_fields.append("is_active")

            agent.full_clean()
            agent.save(update_fields=update_fields)
            if previous_name and agent.name != previous_name:
                maybe_sync_agent_email_display_name(agent, previous_name=previous_name)

            if browser_agent:
                browser_agent.user = recipient
                if new_browser_name:
                    browser_agent.name = new_browser_name
                browser_agent.full_clean()
                browser_agent.save(update_fields=["user", "name"] if new_browser_name else ["user"])

            AgentTransferService._migrate_filespaces(agent, original_owner, recipient)
            AgentTransferService._cleanup_peer_links(agent, recipient)
            AgentTransferService._expire_owner_sessions(agent, original_owner)
            AgentTransferService.handle_secrets_transfer(agent, original_owner, recipient)
            AgentTransferService._send_new_owner_welcome_message(agent, contact_endpoint, recipient)

            invite.to_user = recipient
            invite.status = AgentTransferInvite.Status.ACCEPTED
            now = timezone.now()
            invite.responded_at = now
            invite.accepted_at = now
            invite.save(update_fields=["to_user", "status", "responded_at", "accepted_at"])

            try:
                process_agent_events_task.delay(str(agent.id))
            except Exception:  # pragma: no cover - task queue failures shouldn't break transfer
                logger.warning("Failed to trigger event processing for transferred agent %s", agent.id, exc_info=True)

            return invite

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lock_invite(invite_id) -> AgentTransferInvite:
        return AgentTransferInvite.objects.select_for_update().get(pk=invite_id)

    @staticmethod
    def _recipient_has_capacity(user: User) -> bool:
        return AgentService.has_agents_available(user)

    @staticmethod
    def _ensure_unique_agent_name(agent: PersistentAgent, new_owner: User) -> str:
        base_name = agent.name
        existing = set(
            PersistentAgent.objects.filter(
                user=new_owner,
                organization__isnull=True,
            )
            .exclude(pk=agent.pk)
            .values_list("name", flat=True)
        )
        return AgentTransferService._dedupe_name(base_name, existing)

    @staticmethod
    def _ensure_unique_browser_name(browser_agent: Optional[BrowserUseAgent], new_owner: User) -> Optional[str]:
        if not browser_agent:
            return None
        base_name = browser_agent.name
        existing = set(
            BrowserUseAgent.objects.filter(user=new_owner)
            .exclude(pk=browser_agent.pk)
            .values_list("name", flat=True)
        )
        return AgentTransferService._dedupe_name(base_name, existing)

    @staticmethod
    def _dedupe_name(base_name: str, existing: set[str]) -> str:
        candidate = base_name
        idx = 2
        while candidate in existing:
            candidate = f"{base_name} ({idx})"
            idx += 1
        return candidate

    @staticmethod
    def _ensure_owner_contact_endpoint(agent: PersistentAgent, owner: User) -> Optional[PersistentAgentCommsEndpoint]:
        email = (owner.email or "").strip().lower()
        if not email:
            return None

        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=email,
            defaults={"owner_agent": None},
        )
        return endpoint

    @staticmethod
    def _send_new_owner_welcome_message(
        agent: PersistentAgent,
        owner_endpoint: Optional[PersistentAgentCommsEndpoint],
        new_owner: User,
    ) -> None:
        if owner_endpoint is None:
            return

        channel = owner_endpoint.channel
        if channel not in (CommsChannel.EMAIL, CommsChannel.SMS):
            return

        address = owner_endpoint.address
        if not address:
            return

        conversation, _ = PersistentAgentConversation.objects.get_or_create(
            owner_agent=agent,
            channel=channel,
            address=address,
            defaults={'display_name': new_owner.get_full_name() or address},
        )

        try:
            PersistentAgentConversationParticipant.objects.get_or_create(
                conversation=conversation,
                endpoint=owner_endpoint,
                defaults={'role': PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL},
            )
        except Exception:
            pass

        agent_endpoint = agent.comms_endpoints.filter(owner_agent=agent, channel=channel).first()
        if agent_endpoint is not None:
            try:
                PersistentAgentConversationParticipant.objects.get_or_create(
                    conversation=conversation,
                    endpoint=agent_endpoint,
                    defaults={'role': PersistentAgentConversationParticipant.ParticipantRole.AGENT},
                )
            except Exception:
                pass

        owner_display = new_owner.get_full_name() or new_owner.email or "your new owner"
        welcome_body = (
            f"Hi {agent.name}, I'm {owner_display} and I'm taking over as your owner. "
            "Please introduce yourself and share what you're currently working on right now so I can get up to speed."
        )

        try:
            PersistentAgentMessage.objects.create(
                is_outbound=False,
                from_endpoint=owner_endpoint,
                conversation=conversation,
                body=welcome_body,
                owner_agent=agent,
            )
            agent.last_interaction_at = timezone.now()
            agent.save(update_fields=["last_interaction_at"])
        except Exception:
            logger.warning("Failed to record new-owner welcome message for agent %s", agent.id, exc_info=True)

    @staticmethod
    def _migrate_filespaces(agent: PersistentAgent, from_user: User, to_user: User) -> None:
        AgentFileSpace.objects.filter(
            owner_user=from_user,
            access__agent=agent,
        ).update(owner_user=to_user)

    @staticmethod
    def _cleanup_peer_links(agent: PersistentAgent, new_owner: User) -> None:
        links = AgentPeerLink.objects.filter(models.Q(agent_a=agent) | models.Q(agent_b=agent))
        for link in links.select_related("agent_a", "agent_b"):
            other = link.get_other_agent(agent)
            if not other or other.user_id != new_owner.id:
                link.delete()

    @staticmethod
    def _expire_owner_sessions(agent: PersistentAgent, owner: User) -> None:
        from api.models import PersistentAgentWebSession

        now = timezone.now()
        PersistentAgentWebSession.objects.filter(
            agent=agent,
            user=owner,
            ended_at__isnull=True,
        ).update(ended_at=now, last_seen_at=now)


__all__ = ["AgentTransferService", "AgentTransferError", "AgentTransferDenied"]
