from typing import Any

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError
from django.http import HttpRequest, JsonResponse
from django.views.generic import DetailView

from console.views import *  # noqa: F403 - transitional extraction from the legacy view module.
from console.views import _agent_avatar_thumbnail_name, _agent_collaborator_invite_app_path, _agent_settings_app_path, _format_validation_error, _posted_bool
from console.daily_credit import build_agent_daily_credit_context, serialize_daily_credit_payload
from console.mixins import AgentOwnerContextOverrideMixin
from console.agent_chat.access import resolve_manageable_agent_for_request, user_can_manage_agent, user_is_collaborator
from api.agent.tasks.process_events import process_agent_events_task
from api.models import CommsAllowlistEntry, UserPhoneNumber
from constants.feature_flags import CONTACT_AUTO_APPROVE_EMAIL


class _AgentSettingsService(AgentOwnerContextOverrideMixin, ConsoleViewMixin, DetailView):
    """Shared backend for agent settings payloads and mutations."""
    model = PersistentAgent
    context_object_name = "agent"
    pk_url_kwarg = "pk"

    @tracer.start_as_current_span("CONSOLE Agent Detail View - get_object")
    def get_queryset(self):
        """Scope agents to the effective console context.

        The effective context honors explicit request overrides (header/query)
        in addition to session state, which keeps deep links from chat aligned
        with the intended organization/personal scope.
        """
        qs = super().get_queryset().alive().select_related('user__billing')

        context = self.resolve_console_context_info().current_context

        if context.type == 'organization':
            return qs.filter(organization_id=context.id)

        if not can_user_use_personal_agents_and_api(self.request.user):
            return qs.none()
        return qs.filter(user=self.request.user, organization__isnull=True)

    @tracer.start_as_current_span("CONSOLE Agent Detail View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add the primary email to the context."""
        context = super().get_context_data(**kwargs)
        agent = self.object if getattr(self, "object", None) is not None else self.get_object()
        
        # Find the primary email endpoint for this agent
        primary_email = agent.comms_endpoints.filter(
            channel=CommsChannel.EMAIL, is_primary=True
        ).first()

        primary_sms = agent.comms_endpoints.filter(
            channel=CommsChannel.SMS, is_primary=True
        ).first()

        context['primary_email'] = primary_email
        context['primary_sms'] = primary_sms

        owner = agent.organization or agent.user
        browser_agent = getattr(agent, "browser_use_agent", None)
        preferred_proxy = browser_agent.preferred_proxy if browser_agent else None
        multi_assign = is_multi_assign_enabled()

        dedicated_total = 0
        dedicated_available = 0
        dedicated_options: list[dict[str, object]] = []

        if owner:
            allocated_qs = (
                DedicatedProxyService.allocated_proxies(owner)
                .select_related("dedicated_allocation")
                .prefetch_related("browser_agents__persistent_agent")
                .order_by("static_ip", "host", "port")
            )
            dedicated_total = allocated_qs.count()

            for proxy in allocated_qs:
                browser_agents = list(getattr(proxy, "browser_agents").all())
                assigned_agents = [
                    ba.persistent_agent
                    for ba in browser_agents
                    if getattr(ba, "persistent_agent", None) is not None
                ]
                selected = preferred_proxy is not None and proxy.id == preferred_proxy.id
                in_use_elsewhere = any(
                    pa.id != agent.id for pa in assigned_agents
                )
                label = proxy.static_ip or proxy.host
                assigned_names = [pa.name for pa in assigned_agents]

                dedicated_options.append(
                    {
                        "id": str(proxy.id),
                        "label": label,
                        "selected": selected,
                        "in_use_elsewhere": in_use_elsewhere,
                        "assigned_names": assigned_names,
                        "disabled": (not multi_assign and in_use_elsewhere and not selected),
                    }
                )

            if multi_assign:
                dedicated_available = dedicated_total
            else:
                dedicated_available = sum(
                    1
                    for option in dedicated_options
                    if not option["in_use_elsewhere"] or option["selected"]
                )

        context['dedicated_proxy_options'] = dedicated_options
        context['selected_dedicated_proxy_id'] = (
            str(preferred_proxy.id) if preferred_proxy else ""
        )
        context['dedicated_ip_total'] = dedicated_total
        context['dedicated_ip_available'] = dedicated_available
        context['dedicated_ip_multi_assign'] = multi_assign
        context['dedicated_ip_owner_type'] = (
            'organization' if agent.organization_id else 'user'
        )

        # Always include allowlist configuration (flag removed)
        context['show_allowlist'] = True
        context['whitelist_policy'] = agent.whitelist_policy
        context['allowlist_entries'] = CommsAllowlistEntry.objects.filter(
            agent=agent
        ).order_by('channel', 'address')
        context['pending_invites'] = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).order_by('channel', 'address')

        # Count active allowlist entries AND pending invitations for display
        allowlist_active_count = CommsAllowlistEntry.objects.filter(
            agent=agent,
            is_active=True
        ).count()
        allowlist_pending_count = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).count()
        total_contacts = allowlist_active_count + allowlist_pending_count
        contact_counts = get_agent_contact_counts(agent)
        if contact_counts is not None:
            total_contacts = contact_counts["total"]
        context['contact_counts'] = contact_counts
        context['active_allowlist_count'] = total_contacts
        max_contacts_limit = get_user_max_contacts_per_agent(
            agent.user,
            organization=agent.organization,
        )
        context['max_contacts_per_agent'] = max_contacts_limit

        max_contacts_override = None
        if agent.organization_id is None:
            try:
                billing = agent.user.billing
            except UserBilling.DoesNotExist:
                billing = None

            if (
                billing is not None
                and billing.max_contacts_per_agent is not None
                and billing.max_contacts_per_agent > 0
            ):
                max_contacts_override = int(billing.max_contacts_per_agent)
        context['max_contacts_per_agent_override'] = max_contacts_override
        context['allowlist_limit_reached'] = (
            max_contacts_limit > 0 and total_contacts >= max_contacts_limit
        )

        context['collaborators'] = AgentCollaborator.objects.filter(
            agent=agent,
        ).select_related('user').order_by('user__email')
        context['collaborator_invites'] = AgentCollaboratorInvite.objects.filter(
            agent=agent,
            status=AgentCollaboratorInvite.InviteStatus.PENDING,
            expires_at__gt=timezone.now(),
        ).order_by('email')

        context['can_manage_collaborators'] = self._can_manage_collaborators(self.request.user, agent)

        # Add pending contact requests count
        from api.models import CommsAllowlistRequest
        pending_contact_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).count()
        context['pending_contact_requests'] = pending_contact_requests

        context['agent_webhooks'] = agent.webhooks.order_by('name')
        # Add owner information for display
        context['owner_email'] = agent.user.email

        # Check if owner has verified phone for SMS display
        try:
            owner_phone = UserPhoneNumber.objects.filter(
                user=agent.user, 
                is_verified=True
            ).first()
            context['owner_phone'] = owner_phone.phone_number if owner_phone else None
        except (ImportError, DatabaseError):
            context['owner_phone'] = None

        # Provide organizations current user can reassign this agent into (owner/admin/solutions partner only)
        try:
            reassignable_orgs = Organization.objects.filter(
                organizationmembership__user=self.request.user,
                organizationmembership__status=OrganizationMembership.OrgStatus.ACTIVE,
                organizationmembership__role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
                ],
            ).order_by('name')
        except ImportError:
            reassignable_orgs = []

        context['reassignable_orgs'] = reassignable_orgs
        context['can_reassign'] = True

        peer_links_qs = (
            AgentPeerLink.objects.filter(Q(agent_a=agent) | Q(agent_b=agent))
            .select_related("agent_a", "agent_b")
            .prefetch_related("communication_states")
            .order_by("created_at")
        )

        peer_links: list[dict] = []
        linked_agent_ids: set = set()
        for link in peer_links_qs:
            counterpart = link.get_other_agent(agent)
            linked_agent_ids.add(link.agent_a_id)
            linked_agent_ids.add(link.agent_b_id)

            state = next(
                (s for s in link.communication_states.all() if s.channel == CommsChannel.OTHER),
                None,
            )

            peer_links.append(
                {
                    "link": link,
                    "counterpart": counterpart,
                    "state": state,
                }
            )

        context['peer_links'] = peer_links

        linked_agent_ids.discard(agent.id)
        if agent.organization_id:
            candidate_qs = PersistentAgent.objects.non_eval().alive().filter(
                organization_id=agent.organization_id
            )
        else:
            candidate_qs = PersistentAgent.objects.non_eval().alive().filter(
                user=agent.user,
                organization__isnull=True,
            )

        candidate_qs = candidate_qs.exclude(id=agent.id)
        if linked_agent_ids:
            candidate_qs = candidate_qs.exclude(id__in=linked_agent_ids)
        context['peer_link_candidates'] = candidate_qs.order_by('name')
        context['peer_link_defaults'] = {
            'messages_per_window': 30,
            'window_hours': 6,
        }

        server_overview = mcp_server_service.agent_server_overview(agent)
        context['inherited_mcp_servers'] = [s for s in server_overview if s.get('inherited')]
        context['organization_mcp_servers'] = [
            s for s in server_overview if s.get('scope') == MCPServerConfig.Scope.ORGANIZATION
        ]
        personal_servers = [s for s in server_overview if s.get('scope') == MCPServerConfig.Scope.USER]
        context['personal_mcp_servers'] = personal_servers
        context['show_personal_mcp_form'] = agent.organization_id is None and bool(personal_servers)

        context.update(build_agent_daily_credit_context(agent, owner))

        pending_transfer = AgentTransferInvite.objects.filter(
            agent=agent,
            status=AgentTransferInvite.Status.PENDING,
        ).first()
        context['pending_transfer_invite'] = pending_transfer

        context['agent_detail_props'] = self._build_agent_detail_props(context)

        return context

    def _serialize_allowlist_state(
        self,
        agent: PersistentAgent,
        *,
        entries=None,
        pending_invites=None,
        owner_email: str | None = None,
        owner_phone: str | None = None,
        active_count: int | None = None,
        pending_count: int | None = None,
        total_count: int | None = None,
        max_contacts: int | None = None,
        pending_contact_requests: int | None = None,
        email_verified: bool | None = None,
    ) -> dict[str, object]:
        from api.models import AgentAllowlistInvite
        from api.services.email_verification import has_verified_email

        entries_qs = entries
        if entries_qs is None:
            entries_qs = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
        entries_list = list(entries_qs)

        pending_qs = pending_invites
        if pending_qs is None:
            pending_qs = AgentAllowlistInvite.objects.filter(
                agent=agent,
                status=AgentAllowlistInvite.InviteStatus.PENDING,
            ).order_by('channel', 'address')
        pending_list = list(pending_qs)

        if owner_email is None:
            owner_email = agent.user.email

        if owner_phone is None:
            phone_obj = UserPhoneNumber.objects.filter(user=agent.user, is_verified=True).first()
            owner_phone = phone_obj.phone_number if phone_obj else None

        if active_count is None:
            active_count = CommsAllowlistEntry.objects.filter(agent=agent, is_active=True).count()
        if pending_count is None:
            pending_count = AgentAllowlistInvite.objects.filter(
                agent=agent,
                status=AgentAllowlistInvite.InviteStatus.PENDING,
            ).count()
        if email_verified is None:
            email_verified = has_verified_email(agent.user)

        display_total = total_count
        if display_total is None:
            display_total = (active_count or 0) + (pending_count or 0)

        return {
            'show': True,
            'ownerEmail': owner_email,
            'ownerPhone': owner_phone,
            'entries': [
                {
                    'id': str(entry.id),
                    'channel': entry.channel,
                    'address': entry.address,
                    'allowInbound': entry.allow_inbound,
                    'allowOutbound': entry.allow_outbound,
                    'smsContactPurpose': entry.sms_contact_purpose,
                    'smsContactPurposeDetails': entry.sms_contact_purpose_details,
                    'smsContactPermissionAttested': entry.sms_contact_permission_attested,
                    'smsContactPermissionAttestedAt': (
                        entry.sms_contact_permission_attested_at.isoformat()
                        if entry.sms_contact_permission_attested_at else None
                    ),
                }
                for entry in entries_list
            ],
            'pendingInvites': [
                {
                    'id': str(invite.id),
                    'channel': invite.channel,
                    'address': invite.address,
                    'allowInbound': invite.allow_inbound,
                    'allowOutbound': invite.allow_outbound,
                    'smsContactPurpose': invite.sms_contact_purpose,
                    'smsContactPurposeDetails': invite.sms_contact_purpose_details,
                    'smsContactPermissionAttested': invite.sms_contact_permission_attested,
                    'smsContactPermissionAttestedAt': (
                        invite.sms_contact_permission_attested_at.isoformat()
                        if invite.sms_contact_permission_attested_at else None
                    ),
                }
                for invite in pending_list
            ],
            'activeCount': display_total,
            'maxContacts': max_contacts,
            'pendingContactRequests': pending_contact_requests,
            'emailVerified': email_verified,
        }

    def _serialize_collaborator_state(
        self,
        agent: PersistentAgent,
        *,
        collaborators=None,
        pending_invites=None,
        counts: dict[str, int] | None = None,
        max_contacts: int | None = None,
        can_manage: bool | None = None,
    ) -> dict[str, object]:
        if collaborators is None:
            collaborators = (
                AgentCollaborator.objects
                .filter(agent=agent)
                .select_related("user")
                .order_by("user__email")
            )
        if pending_invites is None:
            pending_invites = AgentCollaboratorInvite.objects.filter(
                agent=agent,
                status=AgentCollaboratorInvite.InviteStatus.PENDING,
                expires_at__gt=timezone.now(),
            ).order_by("email")

        collab_list = [
            {
                "id": str(collab.id),
                "userId": str(collab.user_id),
                "email": collab.user.email if collab.user else "",
                "name": (
                    collab.user.get_full_name()
                    or collab.user.username
                    or collab.user.email
                )
                if collab.user
                else "",
            }
            for collab in list(collaborators)
        ]

        pending_list = [
            {
                "id": str(invite.id),
                "email": invite.email,
            }
            for invite in list(pending_invites)
        ]

        if counts is None:
            counts = get_agent_contact_counts(agent) or {}

        return {
            "entries": collab_list,
            "pendingInvites": pending_list,
            "activeCount": int(counts.get("collaborators_active", 0) or 0),
            "pendingCount": int(counts.get("collaborators_pending", 0) or 0),
            "totalCount": int(counts.get("total", 0) or 0),
            "maxContacts": max_contacts,
            "canManage": bool(can_manage),
        }

    def _can_manage_collaborators(self, user, agent: PersistentAgent) -> bool:
        if agent.user_id == user.id:
            return True
        if agent.organization_id:
            return OrganizationMembership.objects.filter(
                org=agent.organization,
                user=user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
                ],
            ).exists()
        return False

    def _build_allowlist_ajax_success_payload(
        self,
        request,
        agent: PersistentAgent,
        *,
        max_contacts_per_agent: int | None,
    ) -> dict[str, object]:
        entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
        pending_invites = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).order_by('channel', 'address')

        owner_email = agent.user.email
        try:
            phone_obj = UserPhoneNumber.objects.filter(
                user=agent.user,
                is_verified=True
            ).first()
            owner_phone = phone_obj.phone_number if phone_obj else None
        except (AttributeError, DatabaseError):
            owner_phone = None

        active_count = CommsAllowlistEntry.objects.filter(
            agent=agent,
            is_active=True
        ).count()
        pending_count = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).count()
        total_count = active_count + pending_count
        contact_counts = get_agent_contact_counts(agent)
        if contact_counts is not None:
            total_count = contact_counts["total"]

        return {
            'success': True,
            'active_count': total_count,
            'allowlist': self._serialize_allowlist_state(
                agent,
                entries=entries,
                pending_invites=pending_invites,
                owner_email=owner_email,
                owner_phone=owner_phone,
                active_count=active_count,
                pending_count=pending_count,
                total_count=total_count,
                max_contacts=max_contacts_per_agent,
            ),
            'collaborators': self._serialize_collaborator_state(
                agent,
                counts=contact_counts,
                max_contacts=max_contacts_per_agent,
                can_manage=self._can_manage_collaborators(request.user, agent),
            ),
        }

    def _allowlist_ajax_success_response(
        self,
        request,
        agent: PersistentAgent,
        *,
        max_contacts_per_agent: int | None,
    ) -> JsonResponse:
        return JsonResponse(
            self._build_allowlist_ajax_success_payload(
                request,
                agent,
                max_contacts_per_agent=max_contacts_per_agent,
            )
        )

    def _handle_add_allowlist_ajax(
        self,
        request,
        agent: PersistentAgent,
        *,
        max_contacts_per_agent: int | None,
    ) -> JsonResponse:
        channel = request.POST.get('channel', 'email')
        address = request.POST.get('address', '').strip()
        sms_contact_purpose = (request.POST.get('sms_contact_purpose') or '').strip() or None
        sms_contact_purpose_details = (
            request.POST.get('sms_contact_purpose_details') or ''
        ).strip() or None
        sms_contact_permission_attested = _posted_bool(
            request.POST.get('sms_contact_permission_attested')
        )

        if not address:
            return JsonResponse({'success': False, 'error': 'Address is required'})

        existing_entry = None
        try:
            existing_entry = CommsAllowlistEntry.objects.filter(
                agent=agent,
                channel=channel,
                address=address
            ).first()

            if existing_entry:
                if existing_entry.is_active:
                    return JsonResponse({'success': False, 'error': 'This address is already in the allowlist'})

                existing_entry.is_active = True
                allow_inbound = request.POST.get('allow_inbound')
                allow_outbound = request.POST.get('allow_outbound')
                update_fields = ['is_active', 'allow_inbound', 'allow_outbound']
                if allow_inbound is not None:
                    existing_entry.allow_inbound = allow_inbound.lower() == 'true'
                if allow_outbound is not None:
                    existing_entry.allow_outbound = allow_outbound.lower() == 'true'
                if channel == CommsChannel.SMS:
                    if 'sms_contact_purpose' in request.POST:
                        existing_entry.sms_contact_purpose = sms_contact_purpose
                        update_fields.append('sms_contact_purpose')
                    if 'sms_contact_purpose_details' in request.POST:
                        existing_entry.sms_contact_purpose_details = sms_contact_purpose_details
                        update_fields.append('sms_contact_purpose_details')
                    if 'sms_contact_permission_attested' in request.POST:
                        existing_entry.sms_contact_permission_attested = (
                            sms_contact_permission_attested
                        )
                        update_fields.append('sms_contact_permission_attested')
                existing_entry.save(update_fields=update_fields)
                entry = existing_entry
            else:
                allow_inbound = request.POST.get('allow_inbound', 'true').lower() == 'true'
                allow_outbound = request.POST.get('allow_outbound', 'true').lower() == 'true'

                entry = CommsAllowlistEntry.objects.create(
                    agent=agent,
                    channel=channel,
                    address=address,
                    is_active=True,
                    allow_inbound=allow_inbound,
                    allow_outbound=allow_outbound,
                    sms_contact_purpose=sms_contact_purpose if channel == CommsChannel.SMS else None,
                    sms_contact_purpose_details=(
                        sms_contact_purpose_details if channel == CommsChannel.SMS else None
                    ),
                    sms_contact_permission_attested=(
                        sms_contact_permission_attested if channel == CommsChannel.SMS else None
                    ),
                )

                contact_props = Analytics.with_org_properties(
                    {
                        'agent_id': str(agent.id),
                        'channel': channel,
                        'address': address,
                    },
                    organization=getattr(agent, "organization", None),
                )
                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.AGENT_CONTACTS_APPROVED,
                    source=AnalyticsSource.WEB,
                    properties=contact_props.copy(),
                )

            if channel == CommsChannel.SMS:
                track_sms_contact_approval(
                    user_id=request.user.id,
                    agent=agent,
                    address=entry.address,
                    approval_source="agent_detail_allowlist_ajax",
                    approval_action="reactivate" if existing_entry else "create",
                    allow_inbound=entry.allow_inbound,
                    allow_outbound=entry.allow_outbound,
                    can_configure=entry.can_configure,
                    sms_contact_purpose=entry.sms_contact_purpose,
                    sms_contact_purpose_details=entry.sms_contact_purpose_details,
                    sms_contact_permission_attested=entry.sms_contact_permission_attested,
                    allowlist_entry_id=str(entry.id),
                )

            process_agent_events_task.delay(str(agent.id))
            if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                agent.save(update_fields=['whitelist_policy'])

            return self._allowlist_ajax_success_response(
                request,
                agent,
                max_contacts_per_agent=max_contacts_per_agent,
            )
        except ValidationError as exc:
            if existing_entry and existing_entry.pk:
                existing_entry.refresh_from_db()
            return JsonResponse({'success': False, 'error': _format_validation_error(exc)})
        except IntegrityError:
            return JsonResponse({'success': False, 'error': 'This address is already in the allowlist'})
        except Exception as exc:
            return JsonResponse({'success': False, 'error': str(exc)})

    def _handle_remove_allowlist_ajax(
        self,
        request,
        agent: PersistentAgent,
        *,
        max_contacts_per_agent: int | None,
    ) -> JsonResponse:
        entry_id = request.POST.get('entry_id')
        try:
            CommsAllowlistEntry.objects.filter(agent=agent, id=entry_id).delete()
            return self._allowlist_ajax_success_response(
                request,
                agent,
                max_contacts_per_agent=max_contacts_per_agent,
            )
        except Exception as exc:
            return JsonResponse({'success': False, 'error': str(exc)})

    def _handle_update_allowlist_ajax(
        self,
        request,
        agent: PersistentAgent,
        *,
        max_contacts_per_agent: int | None,
    ) -> JsonResponse:
        entry_id = request.POST.get('entry_id')
        allow_inbound = request.POST.get('allow_inbound')
        allow_outbound = request.POST.get('allow_outbound')
        if allow_inbound not in {'true', 'false'} or allow_outbound not in {'true', 'false'}:
            return JsonResponse({'success': False, 'error': 'Select valid inbound and outbound permissions.'})

        entry = CommsAllowlistEntry.objects.filter(
            agent=agent,
            id=entry_id,
            is_active=True,
        ).first()
        if entry is None:
            return JsonResponse({'success': False, 'error': 'Contact not found.'}, status=404)

        entry.allow_inbound = allow_inbound == 'true'
        entry.allow_outbound = allow_outbound == 'true'
        try:
            entry.save(update_fields=['allow_inbound', 'allow_outbound', 'updated_at'])
        except ValidationError as exc:
            return JsonResponse({'success': False, 'error': _format_validation_error(exc)})

        process_agent_events_task.delay(str(agent.id))
        return self._allowlist_ajax_success_response(
            request,
            agent,
            max_contacts_per_agent=max_contacts_per_agent,
        )

    def _handle_cancel_invite_ajax(
        self,
        request,
        agent: PersistentAgent,
        *,
        max_contacts_per_agent: int | None,
    ) -> JsonResponse:
        invite_id = request.POST.get('invite_id')
        try:
            AgentAllowlistInvite.objects.filter(agent=agent, id=invite_id).delete()
            return self._allowlist_ajax_success_response(
                request,
                agent,
                max_contacts_per_agent=max_contacts_per_agent,
            )
        except Exception as exc:
            return JsonResponse({'success': False, 'error': str(exc)})

    def _handle_allowlist_ajax_action(
        self,
        request,
        agent: PersistentAgent,
        action: str,
        *,
        max_contacts_per_agent: int | None,
    ) -> JsonResponse | None:
        handlers = {
            'add_allowlist': self._handle_add_allowlist_ajax,
            'update_allowlist': self._handle_update_allowlist_ajax,
            'remove_allowlist': self._handle_remove_allowlist_ajax,
            'cancel_invite': self._handle_cancel_invite_ajax,
        }
        handler = handlers.get(action)
        if handler is None:
            return None
        return handler(
            request,
            agent,
            max_contacts_per_agent=max_contacts_per_agent,
        )

    def _build_mcp_servers_payload(
        self,
        request: HttpRequest,
        agent: PersistentAgent,
        *,
        server_overview: list[dict[str, object]] | None = None,
        current_context: dict[str, object] | None = None,
        can_manage_org_agents: bool | None = None,
    ) -> dict[str, object]:
        if server_overview is None:
            server_overview = mcp_server_service.agent_server_overview(agent)

        inherited_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in server_overview
            if server.get('inherited')
        ]

        organization_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in server_overview
            if server.get('scope') == MCPServerConfig.Scope.ORGANIZATION
        ]

        personal_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'assigned': bool(server.get('assigned')),
            }
            for server in server_overview
            if server.get('scope') == MCPServerConfig.Scope.USER
        ]

        if current_context is None or can_manage_org_agents is None:
            resolved = build_console_context(request)
            current_context = {
                'type': resolved.current_context.type,
            }
            can_manage_org_agents = resolved.can_manage_org_agents

        can_manage = False
        if current_context.get('type') == 'personal' or can_manage_org_agents:
            can_manage = True

        return {
            'inherited': inherited_servers,
            'organization': organization_servers,
            'personal': personal_servers,
            'showPersonalForm': agent.organization_id is None and bool(personal_servers),
            'canManage': can_manage,
            'manageUrl': '/app/integrations',
        }

    def _build_webhooks_payload(self, agent: PersistentAgent) -> list[dict[str, str]]:
        return [
            {
                'id': str(webhook.id),
                'name': webhook.name,
                'url': webhook.url,
            }
            for webhook in agent.webhooks.order_by('name')
        ]

    def _build_inbound_webhooks_payload(self, request: HttpRequest, agent: PersistentAgent) -> list[dict[str, object]]:
        payload = []
        for webhook in agent.inbound_webhooks.order_by('name'):
            endpoint_url = request.build_absolute_uri(
                reverse('api:inbound_agent_webhook', kwargs={'webhook_id': webhook.id})
            )
            payload.append(
                {
                    'id': str(webhook.id),
                    'name': webhook.name,
                    'url': f'{endpoint_url}?t={webhook.secret}',
                    'isActive': webhook.is_active,
                    'lastTriggeredAt': webhook.last_triggered_at.isoformat() if webhook.last_triggered_at else None,
                }
            )
        return payload

    def _build_peer_links_payload(self, agent: PersistentAgent) -> dict[str, object]:
        peer_links_qs = (
            AgentPeerLink.objects.filter(Q(agent_a=agent) | Q(agent_b=agent))
            .select_related("agent_a", "agent_b")
            .prefetch_related("communication_states")
            .order_by("created_at")
        )

        entries: list[dict[str, object]] = []
        linked_agent_ids: set[str] = set()

        for link in peer_links_qs:
            counterpart = link.get_other_agent(agent)
            if counterpart:
                linked_agent_ids.add(str(counterpart.id))
            linked_agent_ids.add(str(link.agent_a_id))
            linked_agent_ids.add(str(link.agent_b_id))

            state = next(
                (s for s in link.communication_states.all() if s.channel == CommsChannel.OTHER),
                None,
            )

            entries.append(
                {
                    'id': str(link.id),
                    'counterpartId': str(counterpart.id) if counterpart else None,
                    'counterpartName': counterpart.name if counterpart else None,
                    'isEnabled': link.is_enabled,
                    'messagesPerWindow': link.messages_per_window,
                    'windowHours': link.window_hours,
                    'featureFlag': link.feature_flag,
                    'createdOnLabel': date_format(timezone.localtime(link.created_at), "M j, Y"),
                    'state': (
                        {
                            'creditsRemaining': state.credits_remaining,
                            'windowResetLabel': date_format(timezone.localtime(state.window_reset_at), "M j, Y H:i"),
                        }
                        if state
                        else None
                    ),
                }
            )

        linked_agent_ids.discard(str(agent.id))

        if agent.organization_id:
            candidate_qs = PersistentAgent.objects.non_eval().alive().filter(organization_id=agent.organization_id)
        else:
            candidate_qs = PersistentAgent.objects.non_eval().alive().filter(user=agent.user, organization__isnull=True)

        if linked_agent_ids:
            candidate_qs = candidate_qs.exclude(id__in=linked_agent_ids)

        candidates = [
            {
                'id': str(candidate.id),
                'name': candidate.name,
            }
            for candidate in candidate_qs.exclude(id=agent.id).order_by('name')
        ]

        return {
            'entries': entries,
            'candidates': candidates,
            'defaults': {
                'messagesPerWindow': 30,
                'windowHours': 6,
            },
        }

    def _build_agent_detail_props(self, context: dict[str, Any]) -> dict[str, Any]:
        agent: PersistentAgent = context['agent']
        request = self.request
        upgrade_url = None
        if settings.GOBII_PROPRIETARY_MODE:
            try:
                upgrade_url = reverse('proprietary:pricing')
            except NoReverseMatch:
                upgrade_url = None

        if agent.organization_id:
            owner = agent.organization
            owner_type = 'organization'
            organization = agent.organization
        else:
            owner = agent.user
            owner_type = 'user'
            organization = None

        llm_intelligence = build_llm_intelligence_props(owner, owner_type, organization, upgrade_url)

        def _datetime_display(value, fmt: str):
            if not value:
                return None
            localized = timezone.localtime(value)
            return date_format(localized, fmt)

        daily_credits = serialize_daily_credit_payload(context)

        dedicated_options = [
            {
                'id': str(option.get('id')),
                'label': option.get('label'),
                'inUseElsewhere': bool(option.get('in_use_elsewhere')),
                'disabled': bool(option.get('disabled')),
            }
            for option in context.get('dedicated_proxy_options', [])
        ]

        account_usage = (context.get('account') or {}).get('usage') or {}
        max_contacts = context.get('max_contacts_per_agent') or account_usage.get('max_contacts_per_agent')

        allowlist = self._serialize_allowlist_state(
            agent,
            entries=context.get('allowlist_entries'),
            pending_invites=context.get('pending_invites'),
            owner_email=context.get('owner_email'),
            owner_phone=context.get('owner_phone'),
            total_count=context.get('active_allowlist_count'),
            max_contacts=max_contacts,
            pending_contact_requests=context.get('pending_contact_requests'),
        )
        allowlist['show'] = bool(context.get('show_allowlist'))

        collaborators = self._serialize_collaborator_state(
            agent,
            collaborators=context.get('collaborators'),
            pending_invites=context.get('collaborator_invites'),
            counts=context.get('contact_counts'),
            max_contacts=max_contacts,
            can_manage=context.get('can_manage_collaborators'),
        )

        inherited_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in context.get('inherited_mcp_servers', [])
        ]

        organization_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in context.get('organization_mcp_servers', [])
        ]

        personal_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'assigned': bool(server.get('assigned')),
            }
            for server in context.get('personal_mcp_servers', [])
        ]

        peer_link_entries = []
        for entry in context.get('peer_links', []):
            link = entry['link']
            counterpart = entry.get('counterpart')
            state = entry.get('state')
            peer_link_entries.append(
                {
                    'id': str(link.id),
                    'counterpartId': str(counterpart.id) if counterpart else None,
                    'counterpartName': counterpart.name if counterpart else None,
                    'isEnabled': link.is_enabled,
                    'messagesPerWindow': link.messages_per_window,
                    'windowHours': link.window_hours,
                    'featureFlag': link.feature_flag,
                    'createdOnLabel': _datetime_display(link.created_at, "M j, Y"),
                    'state': (
                        {
                            'creditsRemaining': state.credits_remaining,
                            'windowResetLabel': _datetime_display(state.window_reset_at, "M j, Y H:i"),
                        }
                        if state
                        else None
                    ),
                }
            )

        peer_link_candidates = [
            {
                'id': str(candidate.id),
                'name': candidate.name,
            }
            for candidate in context.get('peer_link_candidates', [])
        ]

        peer_link_defaults = context.get('peer_link_defaults', {})

        pending_transfer = context.get('pending_transfer_invite')
        pending_transfer_payload = None
        if pending_transfer:
            pending_transfer_payload = {
                'toEmail': pending_transfer.to_email,
                'createdAtDisplay': _datetime_display(pending_transfer.created_at, "M j, Y g:i A"),
            }

        primary_email = context.get('primary_email')
        primary_sms = context.get('primary_sms')

        features = {
            'organizations': flag_is_active(request, 'organizations'),
            'contactAutoApproveEmail': flag_is_active(request, CONTACT_AUTO_APPROVE_EMAIL),
        }

        can_reassign = bool(context.get('can_reassign'))
        reassignment = {
            'enabled': can_reassign,
            'organizations': [
                {
                    'id': str(org.id),
                    'name': org.name,
                }
                for org in context.get('reassignable_orgs', [])
            ],
            'assignedOrg': (
                {
                    'id': str(agent.organization_id),
                    'name': agent.organization.name,
                }
                if agent.organization_id
                else None
            ),
        }

        mcp_can_manage = False
        current_context = context.get('current_context', {}) or {}
        if current_context.get('type') == 'personal' or context.get('can_manage_org_agents'):
            mcp_can_manage = True

        webhooks = [
            {
                'id': str(webhook.id),
                'name': webhook.name,
                'url': webhook.url,
            }
            for webhook in context.get('agent_webhooks', [])
        ]
        inbound_webhooks = self._build_inbound_webhooks_payload(request, agent)
        mcp_manage_url = '/app/integrations'

        return {
            'csrfToken': get_token(request),
            'urls': {
                'detail': reverse('console_agent_settings', args=[agent.id]),
                'list': f"{IMMERSIVE_APP_BASE_PATH}/agents",
                'chat': build_immersive_chat_url(request, agent.id, return_to=request.get_full_path()),
                'secrets': f"/app/agents/{agent.id}/secrets",
                'emailSettings': f'{IMMERSIVE_APP_BASE_PATH}/agents/{agent.id}/email',
                'manageFiles': f'{IMMERSIVE_APP_BASE_PATH}/agents/{agent.id}/files',
                'contactRequests': build_immersive_contact_requests_url(
                    request,
                    agent.id,
                    return_to=request.get_full_path(),
                    organization_id=str(agent.organization_id) if agent.organization_id else None,
                ),
                'delete': reverse('agent_delete', args=[agent.id]),
            },
            'agent': {
                'id': str(agent.id),
                'name': agent.name,
                'charter': agent.charter,
                'miniDescription': agent.mini_description,
                'miniDescriptionMode': agent.mini_description_mode,
                'avatarUrl': agent.get_avatar_url(),
                'isActive': agent.is_active,
                'createdAtDisplay': _datetime_display(agent.created_at, "F j, Y \a\t g:i A"),
                'pendingTransfer': pending_transfer_payload,
                'whitelistPolicy': agent.whitelist_policy,
                'contactApprovalMode': agent.contact_approval_mode,
                'preferredLlmTier': getattr(getattr(agent, 'preferred_llm_tier', None), 'key', AgentLLMTier.STANDARD.value),
                'organization': (
                    {
                        'id': str(agent.organization_id),
                        'name': agent.organization.name,
                    }
                    if agent.organization_id
                    else None
                ),
            },
            'primaryEmail': {'address': primary_email.address} if primary_email else None,
            'primarySms': {'address': primary_sms.address} if primary_sms else None,
            'dailyCredits': daily_credits,
            'dedicatedIps': {
                'total': context.get('dedicated_ip_total', 0),
                'available': context.get('dedicated_ip_available', 0),
                'multiAssign': bool(context.get('dedicated_ip_multi_assign')),
                'ownerType': context.get('dedicated_ip_owner_type') or 'user',
                'selectedId': context.get('selected_dedicated_proxy_id') or None,
                'options': dedicated_options,
                'organizationName': agent.organization.name if agent.organization_id else None,
            },
            'allowlist': allowlist,
            'collaborators': collaborators,
            'mcpServers': {
                'inherited': inherited_servers,
                'organization': organization_servers,
                'personal': personal_servers,
                'showPersonalForm': bool(context.get('show_personal_mcp_form')),
                'canManage': mcp_can_manage,
                'manageUrl': mcp_manage_url,
            },
            'peerLinks': {
                'entries': peer_link_entries,
                'candidates': peer_link_candidates,
                'defaults': {
                    'messagesPerWindow': peer_link_defaults.get('messages_per_window', 30),
                    'windowHours': peer_link_defaults.get('window_hours', 6),
                },
            },
            'webhooks': webhooks,
            'inboundWebhooks': inbound_webhooks,
            'features': features,
            'reassignment': reassignment,
            'llmIntelligence': llm_intelligence,
        }

    @tracer.start_as_current_span("CONSOLE Agent Detail View - Post")
    def post(self, request, *args, **kwargs):
        """Handle agent configuration updates and allowlist management."""
        agent = self.get_object()
        self.object = agent
        owner = agent.organization or agent.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        max_contacts_per_agent = get_user_max_contacts_per_agent(
            agent.user,
            organization=agent.organization,
        )

        # Handle AJAX detection early so we can reuse for multiple branches
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'

        peer_action = request.POST.get('peer_link_action')
        if peer_action:
            return self._handle_peer_link_action(request, agent, peer_action, ajax=is_ajax)

        inbound_webhook_action = request.POST.get('inbound_webhook_action')
        if inbound_webhook_action:
            return self._handle_inbound_webhook_action(request, agent, inbound_webhook_action, ajax=is_ajax)

        webhook_action = request.POST.get('webhook_action')
        if webhook_action:
            return self._handle_webhook_action(request, agent, webhook_action, ajax=is_ajax)

        if request.POST.get('mcp_server_action'):
            return self._handle_mcp_server_update(request, agent, ajax=is_ajax)

        # Handle AJAX allowlist / reassignment operations
        # Check both modern header and legacy header for AJAX detection
        action = request.POST.get('action')
        ajax_actions = {
            'add_allowlist',
            'update_allowlist',
            'remove_allowlist',
            'cancel_invite',
            'add_collaborator',
            'remove_collaborator',
            'cancel_collaborator_invite',
            'reassign_org',
        }
        if is_ajax and action in ajax_actions:
            allowlist_response = self._handle_allowlist_ajax_action(
                request,
                agent,
                action,
                max_contacts_per_agent=max_contacts_per_agent,
            )
            if allowlist_response is not None:
                return allowlist_response

            if action == 'add_collaborator':
                if not self._can_manage_collaborators(request.user, agent):
                    return JsonResponse({'success': False, 'error': 'Not authorized to invite collaborators.'}, status=403)

                email = (request.POST.get('email') or '').strip().lower()
                if not email:
                    return JsonResponse({'success': False, 'error': 'Email is required'})

                try:
                    from django.core.validators import validate_email
                    validate_email(email)
                except ValidationError:
                    return JsonResponse({'success': False, 'error': 'Enter a valid email address'})

                owner_email = (agent.user.email or '').strip().lower()
                if owner_email and email == owner_email:
                    return JsonResponse({'success': False, 'error': 'Owner already has access to this agent'})

                if agent.organization_id:
                    if OrganizationMembership.objects.filter(
                        org=agent.organization,
                        status=OrganizationMembership.OrgStatus.ACTIVE,
                        user__email__iexact=email,
                    ).exists():
                        return JsonResponse({'success': False, 'error': 'Organization members already have access'})

                if AgentCollaborator.objects.filter(agent=agent, user__email__iexact=email).exists():
                    return JsonResponse({'success': False, 'error': 'This collaborator already has access'})

                now = timezone.now()
                if AgentCollaboratorInvite.objects.filter(
                    agent=agent,
                    email__iexact=email,
                    status=AgentCollaboratorInvite.InviteStatus.PENDING,
                    expires_at__gt=now,
                ).exists():
                    return JsonResponse({'success': False, 'error': 'An invitation is already pending for this email'})

                AgentCollaboratorInvite.objects.filter(
                    agent=agent,
                    email__iexact=email,
                    status=AgentCollaboratorInvite.InviteStatus.PENDING,
                    expires_at__lte=now,
                ).update(status=AgentCollaboratorInvite.InviteStatus.EXPIRED)
                AgentCollaboratorInvite.objects.filter(
                    agent=agent,
                    email__iexact=email,
                    status=AgentCollaboratorInvite.InviteStatus.ACCEPTED,
                ).update(status=AgentCollaboratorInvite.InviteStatus.EXPIRED)

                try:
                    invite = AgentCollaboratorInvite.objects.create(
                        agent=agent,
                        email=email,
                        invited_by=request.user,
                        expires_at=timezone.now() + timedelta(days=7),
                    )
                except ValidationError as exc:
                    return JsonResponse({'success': False, 'error': _format_validation_error(exc)})

                invite_props = Analytics.with_org_properties(
                    {
                        'agent_id': str(agent.id),
                        'agent_name': agent.name,
                        'invite_id': str(invite.id),
                        'invite_email': invite.email,
                        'invited_by_id': str(request.user.id),
                        'actor_id': str(request.user.id),
                    },
                    organization=getattr(agent, "organization", None),
                )
                transaction.on_commit(lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.AGENT_COLLABORATOR_INVITE_SENT,
                    source=AnalyticsSource.WEB,
                    properties=invite_props.copy(),
                ))

                accept_url = request.build_absolute_uri(
                    _agent_collaborator_invite_app_path(invite.token, "accept")
                )
                reject_url = request.build_absolute_uri(
                    _agent_collaborator_invite_app_path(invite.token, "decline")
                )
                context = {
                    'agent': agent,
                    'agent_owner': agent.user,
                    'collaborator_email': email,
                    'accept_url': accept_url,
                    'reject_url': reject_url,
                    'invite': invite,
                }
                subject = f"You've been invited to collaborate with {agent.name} on Gobii"
                text_body = render_to_string('emails/agent_collaborator_invite.txt', context)
                html_body = render_to_string('emails/agent_collaborator_invite.html', context)
                try:
                    send_mail(
                        subject=subject,
                        message=text_body,
                        from_email=None,
                        recipient_list=[email],
                        html_message=html_body,
                        fail_silently=True,
                    )
                except Exception:
                    logger.warning("Failed to send collaborator invitation email to %s", email, exc_info=True)

                contact_counts = get_agent_contact_counts(agent)
                total_count = contact_counts["total"] if contact_counts is not None else None
                allowlist_payload = self._serialize_allowlist_state(
                    agent,
                    total_count=total_count,
                    max_contacts=max_contacts_per_agent,
                )
                collaborators_payload = self._serialize_collaborator_state(
                    agent,
                    counts=contact_counts,
                    max_contacts=max_contacts_per_agent,
                    can_manage=True,
                )

                return JsonResponse({'success': True, 'allowlist': allowlist_payload, 'collaborators': collaborators_payload})

            elif action == 'remove_collaborator':
                if not self._can_manage_collaborators(request.user, agent):
                    return JsonResponse({'success': False, 'error': 'Not authorized to remove collaborators.'}, status=403)

                collaborator_id = request.POST.get('collaborator_id')
                if not collaborator_id:
                    return JsonResponse({'success': False, 'error': 'Collaborator id is required'})

                collaborator = (
                    AgentCollaborator.objects
                    .filter(agent=agent, id=collaborator_id)
                    .select_related("user")
                    .first()
                )
                if collaborator:
                    collaborator_props = Analytics.with_org_properties(
                        {
                            'agent_id': str(agent.id),
                            'agent_name': agent.name,
                            'collaborator_id': str(collaborator.id),
                            'collaborator_user_id': str(collaborator.user_id),
                            'collaborator_email': (
                                collaborator.user.email if collaborator.user else ''
                            ),
                            'actor_id': str(request.user.id),
                        },
                        organization=getattr(agent, "organization", None),
                    )
                    collaborator.delete()
                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.AGENT_COLLABORATOR_REMOVED,
                        source=AnalyticsSource.WEB,
                        properties=collaborator_props.copy(),
                    ))

                contact_counts = get_agent_contact_counts(agent)
                total_count = contact_counts["total"] if contact_counts is not None else None
                allowlist_payload = self._serialize_allowlist_state(
                    agent,
                    total_count=total_count,
                    max_contacts=max_contacts_per_agent,
                )
                collaborators_payload = self._serialize_collaborator_state(
                    agent,
                    counts=contact_counts,
                    max_contacts=max_contacts_per_agent,
                    can_manage=self._can_manage_collaborators(request.user, agent),
                )

                return JsonResponse({'success': True, 'allowlist': allowlist_payload, 'collaborators': collaborators_payload})

            elif action == 'cancel_collaborator_invite':
                if not self._can_manage_collaborators(request.user, agent):
                    return JsonResponse({'success': False, 'error': 'Not authorized to cancel invites.'}, status=403)

                invite_id = request.POST.get('invite_id')
                if not invite_id:
                    return JsonResponse({'success': False, 'error': 'Invite id is required'})

                invite = AgentCollaboratorInvite.objects.filter(agent=agent, id=invite_id).first()
                if invite:
                    invite_props = Analytics.with_org_properties(
                        {
                            'agent_id': str(agent.id),
                            'agent_name': agent.name,
                            'invite_id': str(invite.id),
                            'invite_email': invite.email,
                            'invited_by_id': str(invite.invited_by_id),
                            'actor_id': str(request.user.id),
                        },
                        organization=getattr(agent, "organization", None),
                    )
                    invite.delete()
                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.AGENT_COLLABORATOR_INVITE_CANCELLED,
                        source=AnalyticsSource.WEB,
                        properties=invite_props.copy(),
                    ))

                contact_counts = get_agent_contact_counts(agent)
                total_count = contact_counts["total"] if contact_counts is not None else None
                allowlist_payload = self._serialize_allowlist_state(
                    agent,
                    total_count=total_count,
                    max_contacts=max_contacts_per_agent,
                )
                collaborators_payload = self._serialize_collaborator_state(
                    agent,
                    counts=contact_counts,
                    max_contacts=max_contacts_per_agent,
                    can_manage=self._can_manage_collaborators(request.user, agent),
                )

                return JsonResponse({'success': True, 'allowlist': allowlist_payload, 'collaborators': collaborators_payload})

            elif action == 'reassign_org':
                # Reassign a user-owned agent to an organization, or move org-owned back to personal
                target_org_id = (request.POST.get('target_org_id') or '').strip() or None
                try:
                    result = reassign_agent_organization(request, agent, target_org_id)
                    if target_org_id:
                        messages.success(request, 'Agent assigned to organization.')
                    else:
                        messages.success(request, 'Agent moved to personal ownership.')
                    return JsonResponse({'success': True, **result})
                except PermissionDenied as exc:
                    return JsonResponse({'success': False, 'error': str(exc)}, status=403)
                except ValidationError as e:
                    err = e.messages[0] if hasattr(e, 'messages') and e.messages else str(e)
                    return JsonResponse({'success': False, 'error': err}, status=400)
                except Exception:
                    logger.exception("An error occurred during agent reassignment for agent %s", agent.id)
                    return JsonResponse({'success': False, 'error': 'An unexpected error occurred. Please try again.'}, status=500)
            
            return JsonResponse({'success': False, 'error': 'Invalid action'})

        # Handle regular form submission
        # Check if this is an allowlist action that shouldn't have gotten here
        action = action or ''
        if action in ['add_allowlist', 'remove_allowlist', 'cancel_invite', 'add_collaborator', 'remove_collaborator', 'cancel_collaborator_invite']:
            # This shouldn't happen, but if JavaScript failed, redirect back
            # Import messages here if needed
            from django.contrib import messages as django_messages
            django_messages.error(request, "Please enable JavaScript to manage contacts.")
            return redirect(_agent_settings_app_path(agent))

        if action == 'transfer_agent':
            transfer_email = (request.POST.get('transfer_email') or '').strip()
            transfer_message = (request.POST.get('transfer_message') or '').strip()

            try:
                invite = AgentTransferService.initiate_transfer(
                    agent,
                    transfer_email,
                    request.user,
                    message=transfer_message,
                )
                try:
                    dashboard_url = request.build_absolute_uri(IMMERSIVE_APP_BASE_PATH)
                    initiator_name = request.user.get_full_name() or request.user.email or "A Gobii user"
                    context = {
                        'agent': agent,
                        'invite': invite,
                        'recipient_email': invite.to_email,
                        'initiator_name': initiator_name,
                        'dashboard_url': dashboard_url,
                    }
                    text_body = render_to_string('emails/agent_transfer_invite.txt', context)
                    html_body = render_to_string('emails/agent_transfer_invite.html', context)
                    subject = f"{initiator_name} wants to transfer {agent.name} to you"
                    send_mail(
                        subject=subject,
                        message=text_body,
                        from_email=None,
                        recipient_list=[invite.to_email],
                        html_message=html_body,
                        fail_silently=True,
                    )
                except Exception as email_exc:  # pragma: no cover - best effort
                    logger.warning(
                        "Failed to send transfer invite email to %s: %s",
                        invite.to_email,
                        email_exc,
                    )
            except ValidationError as exc:
                error_message = '; '.join(exc.messages if hasattr(exc, 'messages') else exc.args)
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_message}, status=400)
                messages.error(request, error_message)
                return redirect(_agent_settings_app_path(agent))
            except AgentTransferError as exc:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': str(exc)}, status=400)
                messages.error(request, str(exc))
                return redirect(_agent_settings_app_path(agent))

            if is_ajax:
                return JsonResponse({'success': True})

            messages.success(
                request,
                f"Transfer invitation sent to {invite.to_email}. They'll need to sign in to accept it.",
            )
            return redirect(_agent_settings_app_path(agent))

        if action == 'cancel_transfer_invite':
            updated = AgentTransferInvite.objects.filter(
                agent=agent,
                status=AgentTransferInvite.Status.PENDING,
            ).update(
                status=AgentTransferInvite.Status.CANCELLED,
                responded_at=timezone.now(),
            )
            if updated:
                messages.success(request, "Transfer invitation cancelled.")
            else:
                messages.info(request, "There is no pending transfer invitation to cancel.")
            if is_ajax:
                return JsonResponse({'success': True, 'cancelled': bool(updated)})
            return redirect(_agent_settings_app_path(agent))

        def _general_error(message: str, status: int = 400):
            if is_ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect(_agent_settings_app_path(agent))

        def _validate_avatar_file(file_obj: UploadedFile) -> str | None:
            max_bytes = 5 * 1024 * 1024  # 5 MB limit
            if file_obj.size and file_obj.size > max_bytes:
                return "Avatar must be smaller than 5 MB."

            allowed_content_types = {
                'image/png',
                'image/jpeg',
                'image/jpg',
                'image/webp',
                'image/gif',
            }
            content_type = (file_obj.content_type or "").lower()
            allowed_by_content_type = bool(content_type and content_type in allowed_content_types)
            if content_type and not allowed_by_content_type:
                return "Avatar must be a PNG, JPG, WebP, or GIF image."

            # Lightweight signature check to weed out non-image uploads
            try:
                head = file_obj.read(16)
                file_obj.seek(0)
            except (OSError, ValueError):
                head = b""

            is_image_signature = (
                head.startswith(b"\x89PNG")
                or head.startswith(b"\xff\xd8")
                or head.startswith(b"GIF8")
                or (len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
            )

            if not is_image_signature:
                return "Avatar must be a valid image file."

            return None

        new_name = request.POST.get('name', '').strip()
        new_charter = request.POST.get('charter', '').strip()
        new_mini_description_mode = (
            request.POST.get('mini_description_mode')
            or agent.mini_description_mode
        ).strip().lower()
        if new_mini_description_mode not in PersistentAgent.MiniDescriptionMode.values:
            return _general_error("Mini description mode must be automatic or manual.")

        posted_mini_description = request.POST.get('mini_description', agent.mini_description)
        new_mini_description = " ".join((posted_mini_description or "").split())
        if new_mini_description_mode == PersistentAgent.MiniDescriptionMode.MANUAL:
            if not new_mini_description:
                return _general_error("Mini description cannot be empty in manual mode.")
            if len(new_mini_description) > 80:
                return _general_error("Mini description must be 80 characters or fewer.")
        restoring_automatic_mini_description = (
            agent.mini_description_mode != PersistentAgent.MiniDescriptionMode.AUTO
            and new_mini_description_mode == PersistentAgent.MiniDescriptionMode.AUTO
        )
        # Checkbox inputs are only present in POST data when checked. Determine the desired
        # active state based on whether the "is_active" field was submitted.
        new_is_active = 'is_active' in request.POST

        # Handle whitelist policy update (flag removed)
        new_whitelist_policy = request.POST.get('whitelist_policy', '').strip()
        new_contact_approval_mode = (
            request.POST.get('contact_approval_mode')
            or agent.contact_approval_mode
        ).strip()
        if new_contact_approval_mode not in PersistentAgent.ContactApprovalMode.values:
            return _general_error("Select a valid contact approval option.")
        if (
            new_contact_approval_mode == PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
            and agent.contact_approval_mode != PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
            and not flag_is_active(request, CONTACT_AUTO_APPROVE_EMAIL)
        ):
            return _general_error("Automatic email contact approval is not available.")

        avatar_file = request.FILES.get('avatar')
        clear_avatar_flag = (request.POST.get('clear_avatar') or '').strip().lower() in {'1', 'true', 'yes', 'on'}

        if avatar_file:
            avatar_error = _validate_avatar_file(avatar_file)
            if avatar_error:
                return _general_error(avatar_error)
            # If an upload is present, ignore any clear flag
            clear_avatar_flag = False
        elif clear_avatar_flag and not agent.avatar:
            # No avatar to clear; ignore the flag
            clear_avatar_flag = False

        raw_limit = (request.POST.get('daily_credit_limit') or '').strip()
        slider_value = (request.POST.get('daily_credit_limit_slider') or '').strip()
        limit_source = raw_limit

        if not new_name:
            return _general_error("Agent name cannot be empty.")
        agent_name_max_length = PersistentAgent._meta.get_field("name").max_length
        if agent_name_max_length and len(new_name) > agent_name_max_length:
            return _general_error(f"Agent name must be {agent_name_max_length} characters or fewer.")

        if not new_charter:
            return _general_error("Agent assignment cannot be empty.")

        # Fetch the browser agent defensively; it may be missing due to historical corruption.
        browser_agent: BrowserUseAgent | None = None
        if agent.browser_use_agent_id:
            browser_agent = BrowserUseAgent.objects.filter(pk=agent.browser_use_agent_id).first()
            if browser_agent is None:
                logger.warning(
                    "BrowserUseAgent %s not found while updating PersistentAgent %s",
                    agent.browser_use_agent_id,
                    agent.id,
                )

        owner = agent.organization or agent.user
        organization = agent.organization if agent.organization_id else None
        owner_type = 'organization' if agent.organization_id else 'user'
        multi_assign = is_multi_assign_enabled()
        dedicated_proxy_id = (request.POST.get('dedicated_proxy_id') or '').strip()
        selected_proxy: ProxyServer | None = None

        # Capture previous values for analytics
        prev_name = agent.name
        prev_is_active = agent.is_active
        prev_daily_limit = agent.daily_credit_limit
        prev_hard_limit = agent.get_daily_credit_hard_limit()
        prev_preferred_tier = getattr(getattr(agent, "preferred_llm_tier", None), "key", AgentLLMTier.STANDARD.value)
        prev_whitelist_policy = agent.whitelist_policy
        prev_contact_approval_mode = agent.contact_approval_mode

        plan = None
        if owner is not None:
            try:
                if owner_type == 'organization' and organization is not None:
                    plan = get_organization_plan(organization)
                else:
                    plan = reconcile_user_plan_from_stripe(owner)
            except Exception:
                plan = None

        allowed_llm_tier = max_allowed_tier_for_plan(plan, is_organization=(owner_type == 'organization'))
        allowed_llm_tier = apply_user_quota_tier_override(owner, allowed_llm_tier)
        if settings.GOBII_PROPRIETARY_MODE:
            can_edit_intelligence = bool(
                owner is not None
                and (owner_type == 'organization' or allowed_llm_tier != AgentLLMTier.STANDARD)
            )
        else:
            can_edit_intelligence = True
        current_preferred_tier_value = getattr(getattr(agent, "preferred_llm_tier", None), "key", AgentLLMTier.STANDARD.value)
        try:
            AgentLLMTier(current_preferred_tier_value)
        except ValueError:
            current_preferred_tier_value = AgentLLMTier.STANDARD.value

        preferred_tier_input = (request.POST.get('preferred_llm_tier') or '').strip()
        if not preferred_tier_input:
            preferred_tier_input = current_preferred_tier_value
        try:
            requested_preferred_tier = AgentLLMTier(preferred_tier_input)
        except ValueError:
            return _general_error("Select a valid intelligence level.")

        preferred_tier_warning = None
        if settings.GOBII_PROPRIETARY_MODE and TIER_ORDER[requested_preferred_tier] > TIER_ORDER[allowed_llm_tier]:
            requested_label = get_llm_tier_label(requested_preferred_tier.value)
            allowed_label = get_llm_tier_label(allowed_llm_tier.value)
            preferred_tier_warning = (
                f"Your plan allows up to {allowed_label}. "
                f"Your selection ({requested_label}) was adjusted to {allowed_label}."
            )
            requested_preferred_tier = allowed_llm_tier

        preferred_tier_changed = requested_preferred_tier.value != current_preferred_tier_value

        resolved_preferred_tier = IntelligenceTier.objects.filter(key=requested_preferred_tier.value).first()
        if resolved_preferred_tier is None:
            return _general_error("Select a valid intelligence level.")

        new_tier_multiplier = get_tier_credit_multiplier(resolved_preferred_tier)
        slider_bounds = get_daily_credit_slider_bounds(
            credit_settings,
            tier_multiplier=new_tier_multiplier,
        )
        if not limit_source and slider_value:
            try:
                parsed_slider = Decimal(slider_value)
            except InvalidOperation:
                return _general_error("Enter a whole number for the daily credit soft target.")
            if parsed_slider >= slider_bounds["slider_unlimited_value"]:
                limit_source = ""
            else:
                limit_source = slider_value

        new_daily_limit, error = parse_daily_credit_limit(
            {"daily_credit_limit": limit_source},
            credit_settings,
            tier_multiplier=new_tier_multiplier,
        )
        if error:
            return _general_error(error)

        if preferred_tier_changed:
            if new_daily_limit == prev_daily_limit:
                new_daily_limit = scale_daily_credit_limit_for_tier_change(
                    prev_daily_limit,
                    from_multiplier=get_tier_credit_multiplier(agent.preferred_llm_tier),
                    to_multiplier=new_tier_multiplier,
                    slider_min=slider_bounds["slider_min"],
                    slider_max=slider_bounds["slider_limit_max"],
                )
            elif new_daily_limit is not None:
                slider_min = slider_bounds["slider_min"]
                slider_max = slider_bounds["slider_limit_max"]
                if new_daily_limit < slider_min:
                    new_daily_limit = int(slider_min)
                if new_daily_limit > slider_max:
                    new_daily_limit = int(slider_max)

        if dedicated_proxy_id:
            if owner is None:
                return _general_error("Dedicated IPs require an account or organization owner.")
            try:
                selected_proxy = (
                    DedicatedProxyService.allocated_proxies(owner)
                    .select_related("dedicated_allocation")
                    .get(id=dedicated_proxy_id)
                )
            except ProxyServer.DoesNotExist:
                return _general_error("Invalid dedicated IP selection.")
            if browser_agent is None:
                return _general_error(
                    "Unable to assign a dedicated IP because the agent is missing its browser component."
                )
            if (
                not multi_assign
                and selected_proxy.browser_agents.exclude(persistent_agent=agent).exists()
            ):
                return _general_error("That dedicated IP is already assigned to another agent.")

        # Check for uniqueness, excluding the current agent's BrowserUseAgent (if present)
        exclude_pk = browser_agent.id if browser_agent else agent.browser_use_agent_id
        browser_name_conflict = BrowserUseAgent.objects.filter(
            user=request.user,
            name=new_name
        )
        if exclude_pk:
            browser_name_conflict = browser_name_conflict.exclude(pk=exclude_pk)
        if browser_name_conflict.exists():
            return _general_error(f"You already have an agent named '{new_name}'.")

        try:
            with transaction.atomic():
                old_avatar_name = agent.avatar.name if getattr(agent, "avatar", None) else None
                old_avatar_thumbnail_version = agent.get_avatar_thumbnail_version() if old_avatar_name else None
                old_avatar_thumbnail_name = (
                    _agent_avatar_thumbnail_name(agent.id, old_avatar_thumbnail_version)
                    if old_avatar_thumbnail_version
                    else None
                )
                avatar_changed = False

                # Track which fields changed
                agent_fields_to_update = []
                browser_agent_fields_to_update = []

                # Update names if they changed
                if agent.name != new_name:
                    agent.name = new_name
                    if browser_agent is not None:
                        browser_agent.name = new_name
                    agent_fields_to_update.append('name')
                    if browser_agent is not None:
                        browser_agent_fields_to_update.append('name')

                # Update charter if it changed
                if agent.charter != new_charter:
                    agent.charter = new_charter
                    agent_fields_to_update.append('charter')

                mini_description_updates = {}
                if new_mini_description_mode == PersistentAgent.MiniDescriptionMode.MANUAL:
                    mini_description_updates = {
                        'mini_description': new_mini_description,
                        'mini_description_mode': PersistentAgent.MiniDescriptionMode.MANUAL,
                        'mini_description_charter_hash': "",
                        'mini_description_requested_hash': "",
                    }
                elif restoring_automatic_mini_description:
                    mini_description_updates = {
                        'mini_description_mode': PersistentAgent.MiniDescriptionMode.AUTO,
                        'mini_description_charter_hash': "",
                        'mini_description_requested_hash': "",
                    }
                for field, value in mini_description_updates.items():
                    if getattr(agent, field) != value:
                        setattr(agent, field, value)
                        agent_fields_to_update.append(field)

                # Update active status if it changed
                if agent.is_active != new_is_active:
                    agent.is_active = new_is_active
                    agent_fields_to_update.append('is_active')

                # Update whitelist policy if provided and changed
                if new_whitelist_policy and agent.whitelist_policy != new_whitelist_policy:
                    if new_whitelist_policy in [choice[0] for choice in PersistentAgent.WhitelistPolicy.choices]:
                        agent.whitelist_policy = new_whitelist_policy
                        agent_fields_to_update.append('whitelist_policy')

                if agent.contact_approval_mode != new_contact_approval_mode:
                    agent.contact_approval_mode = new_contact_approval_mode
                    agent_fields_to_update.append('contact_approval_mode')

                # Update daily credit limit if changed
                if agent.daily_credit_limit != new_daily_limit:
                    agent.daily_credit_limit = new_daily_limit
                    agent_fields_to_update.append('daily_credit_limit')

                if agent.preferred_llm_tier_id != resolved_preferred_tier.id:
                    agent.preferred_llm_tier = resolved_preferred_tier
                    agent_fields_to_update.append('preferred_llm_tier')

                if avatar_file:
                    agent.avatar = avatar_file
                    agent_fields_to_update.append('avatar')
                    agent.avatar_charter_hash = compute_charter_hash(agent.charter or "")
                    agent.avatar_requested_hash = ""
                    agent_fields_to_update.append('avatar_charter_hash')
                    agent_fields_to_update.append('avatar_requested_hash')
                    avatar_changed = True
                elif clear_avatar_flag and agent.avatar:
                    agent.avatar = None
                    agent_fields_to_update.append('avatar')
                    agent.avatar_charter_hash = compute_charter_hash(agent.charter or "")
                    agent.avatar_requested_hash = ""
                    agent_fields_to_update.append('avatar_charter_hash')
                    agent_fields_to_update.append('avatar_requested_hash')
                    avatar_changed = True

                if browser_agent is not None:
                    current_proxy_id = browser_agent.preferred_proxy_id
                    new_proxy_id = selected_proxy.id if selected_proxy else None
                    if current_proxy_id != new_proxy_id:
                        browser_agent.preferred_proxy = selected_proxy
                        if 'preferred_proxy' not in browser_agent_fields_to_update:
                            browser_agent_fields_to_update.append('preferred_proxy')

                # Mark interaction time and reactivate if previously expired
                agent.last_interaction_at = timezone.now()
                agent_fields_to_update.append('last_interaction_at')

                # Persist changes if needed
                if agent_fields_to_update:
                    if 'updated_at' not in agent_fields_to_update:
                        agent_fields_to_update.append('updated_at')
                    agent.save(update_fields=agent_fields_to_update)
                    logger.info("Updated agent %s fields: %s", agent.id, ", ".join(agent_fields_to_update))
                    if 'name' in agent_fields_to_update:
                        maybe_sync_agent_email_display_name(agent, previous_name=prev_name)
                    daily_limit_changed = 'daily_credit_limit' in agent_fields_to_update
                    preferred_tier_changed = 'preferred_llm_tier' in agent_fields_to_update
                    if daily_limit_changed or preferred_tier_changed:
                        queue_settings_change_resume(
                            agent,
                            daily_credit_limit_changed=daily_limit_changed,
                            previous_daily_credit_limit=prev_daily_limit,
                            preferred_llm_tier_changed=preferred_tier_changed,
                            previous_preferred_llm_tier_key=prev_preferred_tier,
                            source="agent_detail_web",
                        )
                if browser_agent is not None and browser_agent_fields_to_update:
                    browser_agent.save(update_fields=browser_agent_fields_to_update)

                charter_changed = 'charter' in agent_fields_to_update
                if charter_changed or restoring_automatic_mini_description:
                    def _schedule_charter_artifacts() -> None:
                        if charter_changed:
                            try:
                                maybe_schedule_short_description(agent)
                            except Exception:
                                logger.exception(
                                    "Failed to schedule short description generation after charter update for agent %s",
                                    agent.id,
                                )
                        try:
                            maybe_schedule_mini_description(agent)
                        except Exception:
                            logger.exception(
                                "Failed to schedule mini description generation after settings update for agent %s",
                                agent.id,
                            )
                        if charter_changed:
                            try:
                                maybe_schedule_agent_tags(agent)
                            except Exception:
                                logger.exception(
                                    "Failed to schedule tag generation after charter update for agent %s",
                                    agent.id,
                                )
                            try:
                                maybe_schedule_agent_avatar(agent)
                            except Exception:
                                logger.exception(
                                    "Failed to schedule avatar generation after charter update for agent %s",
                                    agent.id,
                                )

                    transaction.on_commit(_schedule_charter_artifacts)

                # If agent was soft-expired, restore schedule (from snapshot if missing) and mark active
                if agent.life_state == PersistentAgent.LifeState.EXPIRED and agent.is_active:
                    fields = []
                    if agent.schedule_snapshot:
                        agent.schedule = agent.schedule_snapshot
                        fields.append('schedule')
                    agent.life_state = PersistentAgent.LifeState.ACTIVE
                    fields.append('life_state')
                    agent.save(update_fields=fields)

                if not is_ajax:
                    if preferred_tier_warning:
                        messages.warning(request, preferred_tier_warning)
                    messages.success(request, "Agent updated successfully.")

                soft_value = float(new_daily_limit) if new_daily_limit is not None else None
                hard_limit_value = agent.get_daily_credit_hard_limit()
                changed_fields_for_analytics = [
                    field for field in agent_fields_to_update if field not in {'updated_at', 'last_interaction_at'}
                ]
                update_props = Analytics.with_org_properties(
                    {
                        'agent_id': str(agent.pk),
                        'agent_name': new_name,
                        'is_active': new_is_active,
                        'charter': new_charter,
                        'daily_credit_limit': soft_value,
                        'daily_credit_soft_target': soft_value,
                        'daily_credit_hard_limit': float(hard_limit_value) if hard_limit_value is not None else None,
                        'preferred_llm_tier': resolved_preferred_tier.key,
                        'updated_fields': changed_fields_for_analytics,
                    },
                    organization=agent.organization,
                )
                if 'daily_credit_limit' in changed_fields_for_analytics:
                    update_props['previous_daily_credit_limit'] = (
                        float(prev_daily_limit) if prev_daily_limit is not None else None
                    )
                    update_props['previous_daily_credit_soft_target'] = (
                        float(prev_daily_limit) if prev_daily_limit is not None else None
                    )
                    update_props['previous_daily_credit_hard_limit'] = (
                        float(prev_hard_limit) if prev_hard_limit is not None else None
                    )
                if 'is_active' in changed_fields_for_analytics:
                    update_props['previous_is_active'] = prev_is_active
                if 'name' in changed_fields_for_analytics:
                    update_props['previous_name'] = prev_name
                if 'preferred_llm_tier' in changed_fields_for_analytics:
                    update_props['previous_preferred_llm_tier'] = prev_preferred_tier
                if 'whitelist_policy' in changed_fields_for_analytics:
                    update_props['previous_whitelist_policy'] = prev_whitelist_policy
                if 'contact_approval_mode' in changed_fields_for_analytics:
                    update_props['previous_contact_approval_mode'] = prev_contact_approval_mode
                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_UPDATED,
                    source=AnalyticsSource.WEB,
                    properties=update_props.copy(),
                )

                if avatar_changed:
                    new_avatar_name = agent.avatar.name if getattr(agent, "avatar", None) else None
                    if old_avatar_name and old_avatar_name != new_avatar_name:
                        transaction.on_commit(lambda name=old_avatar_name: default_storage.delete(name))
                    if old_avatar_thumbnail_name:
                        transaction.on_commit(lambda name=old_avatar_thumbnail_name: default_storage.delete(name))
        except ValidationError as e:
            message = _format_validation_error(e)
            if is_ajax:
                return JsonResponse({'success': False, 'error': message}, status=400)
            messages.error(request, message)
            return redirect(_agent_settings_app_path(agent))
        except Exception as e:
            if is_ajax:
                return JsonResponse({'success': False, 'error': f"Error updating agent: {e}"}, status=500)
            messages.error(request, f"Error updating agent: {e}")
            return redirect(_agent_settings_app_path(agent))

        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': "Agent updated successfully.",
                'avatarUrl': agent.get_avatar_url(),
                'miniDescription': agent.mini_description,
                'miniDescriptionMode': agent.mini_description_mode,
                'preferredLlmTier': getattr(getattr(agent, "preferred_llm_tier", None), "key", None),
                'contactApprovalMode': agent.contact_approval_mode,
                'warning': preferred_tier_warning,
            })

        return redirect(_agent_settings_app_path(agent))

    def _handle_inbound_webhook_action(self, request, agent: PersistentAgent, action: str, *, ajax: bool = False):
        redirect_response = redirect(_agent_settings_app_path(agent))
        normalized_action = (action or "").lower()

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'inboundWebhooks': self._build_inbound_webhooks_payload(request, agent),
                    }
                )
            messages.success(request, message)
            return redirect_response

        if normalized_action not in {"create", "update", "delete", "rotate_secret"}:
            return _error_response("Unsupported inbound webhook action.")

        def _track_inbound_webhook_event(
            event_type: AnalyticsEvent,
            webhook_obj: PersistentAgentInboundWebhook,
        ) -> None:
            props = Analytics.with_org_properties(
                {
                    'agent_id': str(agent.pk),
                    'agent_name': agent.name,
                    'webhook_id': str(webhook_obj.id),
                    'webhook_name': webhook_obj.name,
                    'is_active': webhook_obj.is_active,
                },
                organization=agent.organization,
            )
            transaction.on_commit(
                lambda evt=event_type, properties=props: Analytics.track_event(
                    user_id=request.user.id,
                    event=evt,
                    source=AnalyticsSource.WEB,
                    properties=properties.copy(),
                )
            )

        if normalized_action in {"delete", "rotate_secret", "update"}:
            webhook_id = request.POST.get("inbound_webhook_id")
            if not webhook_id:
                return _error_response("Missing inbound webhook identifier.")
            try:
                webhook = agent.inbound_webhooks.get(id=webhook_id)
            except PersistentAgentInboundWebhook.DoesNotExist:
                return _error_response("Inbound webhook not found or no longer exists.")
        else:
            webhook = None

        if normalized_action == "delete":
            _track_inbound_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_DELETED, webhook)
            webhook.delete()
            return _success_response("Inbound webhook removed.")

        if normalized_action == "rotate_secret":
            webhook.rotate_secret()
            _track_inbound_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_SECRET_ROTATED, webhook)
            return _success_response("Inbound webhook secret rotated.")

        name = (request.POST.get("inbound_webhook_name") or "").strip()
        is_active_raw = request.POST.get("inbound_webhook_is_active")
        is_active = True if is_active_raw is None else is_active_raw.lower() == "true"
        if not name:
            return _error_response("Inbound webhook name is required.")

        if normalized_action == "create":
            webhook = PersistentAgentInboundWebhook(agent=agent, name=name, is_active=is_active)
        else:
            webhook.name = name
            webhook.is_active = is_active

        try:
            webhook.save()
        except ValidationError as exc:
            error_messages = []
            if hasattr(exc, "message_dict"):
                for values in exc.message_dict.values():
                    error_messages.extend(values)
            elif hasattr(exc, "messages"):
                error_messages.extend(exc.messages)
            else:
                error_messages.append(str(exc))

            message_text = "; ".join(error_messages) if error_messages else "Invalid data."
            return _error_response(f"Unable to save inbound webhook: {message_text}")
        except IntegrityError:
            return _error_response("An inbound webhook with that name already exists for this agent.")

        if normalized_action == "create":
            _track_inbound_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_ADDED, webhook)
        else:
            _track_inbound_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_UPDATED, webhook)
        return _success_response("Inbound webhook saved.")

    def _handle_webhook_action(self, request, agent: PersistentAgent, action: str, *, ajax: bool = False):
        redirect_response = redirect(_agent_settings_app_path(agent))
        normalized_action = (action or "").lower()

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'webhooks': self._build_webhooks_payload(agent),
                    }
                )
            messages.success(request, message)
            return redirect_response

        if normalized_action not in {"create", "update", "delete"}:
            return _error_response("Unsupported webhook action.")

        def _track_webhook_event(event_type: AnalyticsEvent, webhook_obj: PersistentAgentWebhook) -> None:
            props = Analytics.with_org_properties(
                {
                    'agent_id': str(agent.pk),
                    'agent_name': agent.name,
                    'webhook_id': str(webhook_obj.id),
                    'webhook_name': webhook_obj.name,
                },
                organization=agent.organization,
            )
            transaction.on_commit(
                lambda evt=event_type, properties=props: Analytics.track_event(
                    user_id=request.user.id,
                    event=evt,
                    source=AnalyticsSource.WEB,
                    properties=properties.copy(),
                )
            )

        if normalized_action == "delete":
            webhook_id = request.POST.get("webhook_id")
            if not webhook_id:
                return _error_response("Missing webhook identifier.")
            try:
                webhook = agent.webhooks.get(id=webhook_id)
            except PersistentAgentWebhook.DoesNotExist:
                return _error_response("Webhook not found or no longer exists.")

            _track_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_DELETED, webhook)
            webhook.delete()
            return _success_response("Webhook removed.")

        name = (request.POST.get("webhook_name") or "").strip()
        url = (request.POST.get("webhook_url") or "").strip()
        if not name or not url:
            return _error_response("Webhook name and URL are required.")

        if normalized_action == "create":
            webhook = PersistentAgentWebhook(agent=agent, name=name, url=url)
        else:
            webhook_id = request.POST.get("webhook_id")
            if not webhook_id:
                return _error_response("Missing webhook identifier.")
            try:
                webhook = agent.webhooks.get(id=webhook_id)
            except PersistentAgentWebhook.DoesNotExist:
                return _error_response("Webhook not found or no longer exists.")
            webhook.name = name
            webhook.url = url

        try:
            webhook.full_clean()
            webhook.save()
        except ValidationError as exc:
            error_messages = []
            if hasattr(exc, "message_dict"):
                for values in exc.message_dict.values():
                    error_messages.extend(values)
            elif hasattr(exc, "messages"):
                error_messages.extend(exc.messages)
            else:
                error_messages.append(str(exc))

            message_text = "; ".join(error_messages) if error_messages else "Invalid data."
            return _error_response(f"Unable to save webhook: {message_text}")
        except IntegrityError:
            return _error_response("A webhook with that name already exists for this agent.")

        if normalized_action == "create":
            _track_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_ADDED, webhook)
            return _success_response("Webhook created.")
        else:
            _track_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_UPDATED, webhook)
            return _success_response("Webhook updated.")

    def _handle_mcp_server_update(self, request, agent: PersistentAgent, *, ajax: bool = False):
        redirect_response = redirect(_agent_settings_app_path(agent))

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'mcpServers': self._build_mcp_servers_payload(
                            request,
                            agent,
                        ),
                    }
                )
            messages.success(request, message)
            return redirect_response

        action = request.POST.get('mcp_server_action')
        if action == 'update_personal':
            if agent.organization_id:
                return _error_response("Personal MCP servers can only be configured for your own agents.")

            server_ids = request.POST.getlist('personal_servers')
            try:
                mcp_server_service.update_agent_personal_servers(
                    agent,
                    server_ids,
                    actor_user_id=request.user.id,
                    source=AnalyticsSource.WEB,
                )
            except ValueError as exc:
                return _error_response(str(exc))

            return _success_response("Personal MCP server access updated.")
        if action == 'update_org':
            if not agent.organization_id:
                return _error_response("Organization MCP servers can only be configured for organization agents.")
            server_ids = request.POST.getlist('org_servers')
            try:
                mcp_server_service.update_agent_org_servers(
                    agent,
                    server_ids,
                    actor_user_id=request.user.id,
                    source=AnalyticsSource.WEB,
                )
            except ValueError as exc:
                return _error_response(str(exc))

            return _success_response("Organization MCP server access updated.")

        return _error_response("Unsupported MCP server action.")

    def _handle_peer_link_action(self, request, agent: PersistentAgent, action: str, *, ajax: bool = False):
        redirect_response = redirect(_agent_settings_app_path(agent))

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'peerLinks': self._build_peer_links_payload(agent),
                    }
                )
            messages.success(request, message)
            return redirect_response

        def _track_peer_link_event(
            event_type: AnalyticsEvent,
            *,
            peer_agent: PersistentAgent | None,
            link_id: str,
            messages_per_window: int,
            window_hours: int,
            feature_flag: str | None,
            is_enabled: bool,
        ) -> None:
            props = {
                'agent_id': str(agent.pk),
                'agent_name': agent.name,
                'peer_link_id': link_id,
                'messages_per_window': messages_per_window,
                'window_hours': window_hours,
                'feature_flag': feature_flag or '',
                'is_enabled': is_enabled,
            }
            if peer_agent is not None:
                props['peer_agent_id'] = str(peer_agent.pk)
                props['peer_agent_name'] = peer_agent.name

            props = Analytics.with_org_properties(
                props,
                organization=agent.organization,
            )
            transaction.on_commit(
                lambda evt=event_type, properties=props: Analytics.track_event(
                    user_id=request.user.id,
                    event=evt,
                    source=AnalyticsSource.WEB,
                    properties=properties.copy(),
                )
            )

        try:
            if action == 'create':
                peer_agent_id = request.POST.get('peer_agent_id')
                if not peer_agent_id:
                    return _error_response('Select an agent to link.')

                try:
                    messages_per_window = int(request.POST.get('messages_per_window', 30))
                    window_hours = int(request.POST.get('window_hours', 6))
                except ValueError:
                    return _error_response('Quotas must be positive integers.')

                try:
                    peer_agent = PersistentAgent.objects.non_eval().alive().get(id=peer_agent_id)
                except PersistentAgent.DoesNotExist:
                    return _error_response('Selected agent no longer exists.')

                new_link = AgentPeerLink(
                    agent_a=agent,
                    agent_b=peer_agent,
                    messages_per_window=messages_per_window,
                    window_hours=window_hours,
                    created_by=request.user,
                )

                try:
                    with transaction.atomic():
                        new_link.save()
                except IntegrityError:
                    return _error_response('A peer link already exists for these agents.')

                _track_peer_link_event(
                    AnalyticsEvent.PERSISTENT_AGENT_PEER_LINKED,
                    peer_agent=peer_agent,
                    link_id=str(new_link.id),
                    messages_per_window=new_link.messages_per_window,
                    window_hours=new_link.window_hours,
                    feature_flag=new_link.feature_flag,
                    is_enabled=new_link.is_enabled,
                )
                return _success_response('Peer agent link created.')

            if action == 'update':
                link_id = request.POST.get('link_id')
                if not link_id:
                    return _error_response('Missing peer link identifier.')

                try:
                    with transaction.atomic():
                        link = AgentPeerLink.objects.select_for_update().prefetch_related('communication_states').get(id=link_id)
                        if agent.id not in {link.agent_a_id, link.agent_b_id}:
                            return _error_response('You do not have permission to update this link.')

                        if 'messages_per_window' in request.POST:
                            link.messages_per_window = int(request.POST.get('messages_per_window', link.messages_per_window))
                        if 'window_hours' in request.POST:
                            link.window_hours = int(request.POST.get('window_hours', link.window_hours))
                        if link.messages_per_window < 1 or link.window_hours < 1:
                            raise ValueError
                        if 'feature_flag' in request.POST:
                            link.feature_flag = (request.POST.get('feature_flag') or '').strip()
                        link.is_enabled = 'is_enabled' in request.POST
                        link.save()

                        for state in link.communication_states.all():
                            updates = []
                            if state.messages_per_window != link.messages_per_window:
                                state.messages_per_window = link.messages_per_window
                                updates.append('messages_per_window')
                            if state.window_hours != link.window_hours:
                                state.window_hours = link.window_hours
                                updates.append('window_hours')
                            if state.credits_remaining > link.messages_per_window:
                                state.credits_remaining = link.messages_per_window
                                updates.append('credits_remaining')
                            if updates:
                                updates.append('updated_at')
                                state.save(update_fields=updates)

                except AgentPeerLink.DoesNotExist:
                    return _error_response('Peer link not found.')

                return _success_response('Peer link updated.')

            if action == 'delete':
                link_id = request.POST.get('link_id')
                if not link_id:
                    return _error_response('Missing peer link identifier.')

                with transaction.atomic():
                    link = AgentPeerLink.objects.select_related('conversation').get(id=link_id)
                    if agent.id not in {link.agent_a_id, link.agent_b_id}:
                        return _error_response('You do not have permission to remove this link.')
                    peer_agent = link.agent_a if link.agent_a_id != agent.id else link.agent_b
                    link_snapshot = {
                        'link_id': str(link.id),
                        'messages_per_window': link.messages_per_window,
                        'window_hours': link.window_hours,
                        'feature_flag': link.feature_flag,
                        'is_enabled': link.is_enabled,
                    }

                    link.remove_preserving_history()

                _track_peer_link_event(
                    AnalyticsEvent.PERSISTENT_AGENT_PEER_UNLINKED,
                    peer_agent=peer_agent,
                    **link_snapshot,
                )
                return _success_response('Peer link removed.')

            return _error_response('Unsupported peer link action.')

        except AgentPeerLink.DoesNotExist:
            return _error_response('Peer link not found.')
        except ValueError:
            return _error_response('Invalid values supplied for peer link settings.')
        except ValidationError as exc:
            return _error_response('; '.join(exc.messages))
        except Exception as exc:
            logger.exception('Peer link operation failed for agent %s', agent.id, exc_info=True)
            return _error_response(f'Peer link operation failed: {exc}', status=500)


def build_agent_settings_payload(request: HttpRequest, agent: PersistentAgent) -> dict[str, Any]:
    view = _AgentSettingsService()
    view.setup(request, pk=str(agent.id))
    view.object = agent
    context = view.get_context_data(object=agent)
    return context["agent_detail_props"]


def handle_agent_settings_mutation(request: HttpRequest, agent: PersistentAgent):
    request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
    view = _AgentSettingsService()
    view.setup(request, pk=str(agent.id))
    view.object = agent
    response = view.post(request, pk=str(agent.id))
    if isinstance(response, JsonResponse):
        return response
    return JsonResponse({"success": True})
