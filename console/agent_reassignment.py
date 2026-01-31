from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from api.models import (
    MCPServerConfig,
    OrganizationMembership,
    PersistentAgentEnabledTool,
    PersistentAgentMCPServer,
)


def reassign_agent_organization(request, agent, target_org_id: str | None) -> dict:
    if target_org_id:
        has_rights = OrganizationMembership.objects.filter(
            org_id=target_org_id,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=[
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.SERVICE_PARTNER,
            ],
        ).exists()
        if not has_rights:
            raise PermissionDenied("You must be an organization owner or admin to assign agents to that organization.")

        if (
            agent.__class__.objects.non_eval()
            .filter(organization_id=target_org_id, name=agent.name)
            .exclude(id=agent.id)
            .exists()
        ):
            raise ValidationError(
                "An agent with this name already exists in the selected organization. "
                "Please rename the agent first."
            )

        agent.organization_id = target_org_id
        agent.full_clean()
        agent.save(update_fields=["organization"])

        removed_personal_ids = list(
            PersistentAgentMCPServer.objects.filter(
                agent=agent, server_config__scope=MCPServerConfig.Scope.USER
            ).values_list("server_config_id", flat=True)
        )
        if removed_personal_ids:
            PersistentAgentMCPServer.objects.filter(
                agent=agent,
                server_config__scope=MCPServerConfig.Scope.USER,
            ).delete()
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                server_config_id__in=removed_personal_ids,
            ).delete()

        membership = OrganizationMembership.objects.get(
            org_id=target_org_id,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        request.session["context_type"] = "organization"
        request.session["context_id"] = str(membership.org.id)
        request.session["context_name"] = membership.org.name

        context = {
            "type": "organization",
            "id": str(membership.org.id),
            "name": membership.org.name,
        }
        switch = {"type": "organization", "id": str(target_org_id)}
        organization = {"id": str(membership.org.id), "name": membership.org.name}
    else:
        if (
            agent.__class__.objects.non_eval()
            .filter(user_id=agent.user_id, organization__isnull=True, name=agent.name)
            .exclude(id=agent.id)
            .exists()
        ):
            raise ValidationError(
                "You already have a personal agent with this name. Please rename the agent first."
            )

        agent.organization = None
        agent.save(update_fields=["organization"])

        request.session["context_type"] = "personal"
        request.session["context_id"] = str(request.user.id)
        request.session["context_name"] = request.user.get_full_name() or request.user.username

        context = {
            "type": "personal",
            "id": str(request.user.id),
            "name": request.user.get_full_name() or request.user.username,
        }
        switch = {"type": "personal", "id": str(request.user.id)}
        organization = None

    redirect_url = request.build_absolute_uri(reverse("agent_detail", args=[agent.id]))

    return {
        "context": context,
        "switch": switch,
        "redirect": redirect_url,
        "organization": organization,
    }
