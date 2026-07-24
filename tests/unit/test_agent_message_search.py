from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.models import (
    AgentCollaborator,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentStep,
    PersistentAgentToolCall,
    Organization,
    OrganizationMembership,
    build_web_agent_address,
    build_web_user_address,
)


@tag("message_search_batch")
@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
class AgentMessageSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="message-search-owner",
            email="message-search-owner@example.com",
            password="password123",
        )
        cls.other_user = user_model.objects.create_user(
            username="message-search-other",
            email="message-search-other@example.com",
            password="password123",
        )
        cls.staff_user = user_model.objects.create_user(
            username="message-search-staff",
            email="message-search-staff@example.com",
            password="password123",
            is_staff=True,
        )
        cls.organization = Organization.objects.create(
            name="Search Team",
            slug="message-search-team",
            created_by=cls.user,
        )
        cls.organization.billing.purchased_seats = 1
        cls.organization.billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=cls.organization,
            user=cls.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        cls.agent = cls._create_agent(cls.user, "Search Agent")
        cls.organization_agent = cls._create_agent(
            cls.user,
            "Organization Search Agent",
            organization=cls.organization,
        )
        cls.shared_agent = cls._create_agent(cls.other_user, "Shared Agent")
        cls.foreign_agent = cls._create_agent(cls.other_user, "Foreign Agent")
        AgentCollaborator.objects.create(
            agent=cls.shared_agent,
            user=cls.user,
            invited_by=cls.other_user,
        )

    @classmethod
    def _create_agent(cls, owner, name, *, organization=None):
        browser_agent = BrowserUseAgent.objects.create(user=owner, name=f"{name} Browser")
        agent = PersistentAgent.objects.create(
            user=owner,
            name=name,
            charter="Search messages",
            browser_use_agent=browser_agent,
            organization=organization,
        )
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(agent.id),
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(owner.id, agent.id),
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        agent.search_agent_endpoint = agent_endpoint
        agent.search_user_endpoint = user_endpoint
        agent.search_conversation = conversation
        return agent

    @classmethod
    def _create_message(cls, agent, body, *, hidden=False, is_outbound=True):
        return PersistentAgentMessage.objects.create(
            owner_agent=agent,
            is_outbound=is_outbound,
            from_endpoint=agent.search_agent_endpoint if is_outbound else agent.search_user_endpoint,
            conversation=agent.search_conversation,
            body=body,
            raw_payload={"hide_in_chat": True} if hidden else {},
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _search(self, **params):
        return self.client.get(reverse("console_agent_message_search"), params)

    def test_text_search_uses_newest_first_cursor_pagination(self):
        older = self._create_message(self.agent, "alpha project older")
        newer = self._create_message(self.agent, "alpha project newer")

        first = self._search(q="alpha project", limit=1)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["results"][0]["message_id"], str(newer.id))
        self.assertEqual(first.json()["results"][0]["excerpt_text"], "alpha project newer")
        self.assertTrue(first.json()["next_cursor"])
        self.assertTrue(any(segment["highlighted"] for segment in first.json()["results"][0]["excerpt"]))

        second = self._search(
            q="alpha project",
            limit=1,
            cursor=first.json()["next_cursor"],
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["results"][0]["message_id"], str(older.id))
        self.assertIsNone(second.json()["next_cursor"])

    def test_search_includes_inbound_messages_and_excludes_tool_content(self):
        inbound = self._create_message(
            self.agent,
            "inbound searchable phrase",
            is_outbound=False,
        )
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="inbound searchable phrase",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="search_messages",
            tool_params={"q": "inbound searchable phrase"},
            result="inbound searchable phrase",
        )

        response = self._search(q="inbound searchable phrase")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["message_id"] for result in response.json()["results"]],
            [str(inbound.id)],
        )

    def test_attachment_filters_and_hidden_messages(self):
        image = self._create_message(self.agent, "image result")
        document = self._create_message(self.agent, "document result")
        hidden = self._create_message(self.agent, "hidden image result", hidden=True)
        PersistentAgentMessageAttachment.objects.create(
            message=image,
            file=SimpleUploadedFile("photo.png", b"png", content_type="image/png"),
            content_type="image/png",
            file_size=3,
            filename="photo.png",
        )
        PersistentAgentMessageAttachment.objects.create(
            message=document,
            file=SimpleUploadedFile("brief.pdf", b"pdf", content_type="application/pdf"),
            content_type="application/pdf",
            file_size=3,
            filename="brief.pdf",
        )
        PersistentAgentMessageAttachment.objects.create(
            message=hidden,
            file=SimpleUploadedFile("hidden.png", b"png", content_type="image/png"),
            content_type="image/png",
            file_size=3,
            filename="hidden.png",
        )

        attachment_response = self._search(attachment="attachment")
        self.assertEqual(
            {result["message_id"] for result in attachment_response.json()["results"]},
            {str(image.id), str(document.id)},
        )

        image_response = self._search(attachment="image")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(
            [result["message_id"] for result in image_response.json()["results"]],
            [str(image.id)],
        )
        self.assertTrue(image_response.json()["results"][0]["has_images"])

        file_response = self._search(attachment="file")
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(
            [result["message_id"] for result in file_response.json()["results"]],
            [str(document.id)],
        )

    def test_search_scope_includes_collaborators_but_not_foreign_agents(self):
        own = self._create_message(self.agent, "workspace needle")
        shared = self._create_message(self.shared_agent, "workspace needle")
        self._create_message(self.foreign_agent, "workspace needle")

        response = self._search(q="workspace needle")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {result["message_id"] for result in response.json()["results"]},
            {str(own.id), str(shared.id)},
        )

    def test_agent_filter_does_not_reveal_inaccessible_agent(self):
        self._create_message(self.foreign_agent, "private needle")

        response = self._search(q="private needle", agent_id=str(self.foreign_agent.id))

        self.assertEqual(response.status_code, 403)

    def test_agent_filter_can_search_without_text(self):
        target = self._create_message(self.agent, "agent-only result")
        self._create_message(self.shared_agent, "other agent result")

        response = self._search(agent_id=str(self.agent.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["message_id"] for result in response.json()["results"]],
            [str(target.id)],
        )

    def test_organization_scope_only_returns_organization_agents(self):
        organization_message = self._create_message(self.organization_agent, "organization scope needle")
        self._create_message(self.agent, "organization scope needle")
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.organization.id)
        session["context_name"] = self.organization.name
        session.save()

        response = self._search(q="organization scope needle")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["message_id"] for result in response.json()["results"]],
            [str(organization_message.id)],
        )

    def test_staff_context_is_limited_to_selected_owner(self):
        target = self._create_message(self.foreign_agent, "staff scope needle")
        self._create_message(self.agent, "staff scope needle")
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("console_agent_message_search"),
            {"q": "staff scope needle"},
            HTTP_X_GOBII_STAFF_CONTEXT_TYPE="personal",
            HTTP_X_GOBII_STAFF_CONTEXT_ID=str(self.other_user.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["message_id"] for result in response.json()["results"]],
            [str(target.id)],
        )

    def test_search_requires_query_or_attachment_filter(self):
        response = self._search()

        self.assertEqual(response.status_code, 400)
        self.assertIn("query", response.content.decode().lower())

    def test_timeline_anchor_returns_target_with_surrounding_window(self):
        messages = [
            self._create_message(self.agent, f"anchored message {index}")
            for index in range(60)
        ]
        target = messages[30]

        response = self.client.get(
            reverse("console_agent_timeline", kwargs={"agent_id": self.agent.id}),
            {"anchor_message_id": str(target.id), "limit": 50},
        )

        self.assertEqual(response.status_code, 200)
        message_ids = [
            event["message"]["id"]
            for event in response.json()["events"]
            if event["kind"] == "message"
        ]
        self.assertIn(str(target.id), message_ids)
        self.assertTrue(response.json()["has_more_older"])
        self.assertTrue(response.json()["has_more_newer"])

    def test_timeline_anchor_rejects_hidden_or_mismatched_message(self):
        hidden = self._create_message(self.agent, "hidden target", hidden=True)
        foreign = self._create_message(self.foreign_agent, "foreign target")
        timeline_url = reverse("console_agent_timeline", kwargs={"agent_id": self.agent.id})

        hidden_response = self.client.get(timeline_url, {"anchor_message_id": str(hidden.id)})
        foreign_response = self.client.get(timeline_url, {"anchor_message_id": str(foreign.id)})

        self.assertEqual(hidden_response.status_code, 404)
        self.assertEqual(foreign_response.status_code, 404)

    def test_developer_timeline_anchor_includes_target(self):
        target = self._create_message(self.agent, "developer target")
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        response = self.client.get(
            reverse("console_agent_timeline", kwargs={"agent_id": self.agent.id}),
            {
                "anchor_message_id": str(target.id),
                "developer": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            str(target.id),
            [
                event["message"]["id"]
                for event in response.json()["events"]
                if event["kind"] == "message"
            ],
        )
