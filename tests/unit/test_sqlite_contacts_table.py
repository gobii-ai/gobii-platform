import os
import sqlite3
import tempfile
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone
from unittest.mock import patch

from api.agent.core.contact_results import ContactSQLiteRecord, store_contacts_for_prompt
from api.agent.core.contact_snapshot import (
    build_contacts_snapshot_records,
)
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.models import (
    AgentCollaborator,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    UserPhoneNumber,
)


@tag("batch_sqlite")
class SqliteContactsTableStorageTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "state.db")
        self.token = set_sqlite_db_path(self.db_path)

    def tearDown(self):
        reset_sqlite_db_path(self.token)
        self.tmp.cleanup()

    def test_store_contacts_for_prompt_creates_and_populates_table(self):
        records = [
            ContactSQLiteRecord(
                contact_id="allowlist_entry:1",
                channel="email",
                address="User@Example.COM",
                normalized_address="user@example.com",
                display_name="User Example",
                source="allowlist_entry",
                status="allowed",
                allow_inbound=True,
                allow_outbound=False,
                can_configure=True,
                requested_at=None,
                responded_at=None,
                updated_at="2026-01-01T00:00:00+00:00",
            )
        ]

        store_contacts_for_prompt(records)

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM "__contacts";')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute(
                """
                SELECT contact_id, channel, address, normalized_address, display_name,
                       source, status, allow_inbound, allow_outbound, can_configure,
                       requested_at, responded_at, updated_at
                FROM "__contacts"
                WHERE normalized_address='user@example.com';
                """
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], "allowlist_entry:1")
            self.assertEqual(row[1], "email")
            self.assertEqual(row[2], "User@Example.COM")
            self.assertEqual(row[3], "user@example.com")
            self.assertEqual(row[4], "User Example")
            self.assertEqual(row[5], "allowlist_entry")
            self.assertEqual(row[6], "allowed")
            self.assertEqual(row[7], 1)
            self.assertEqual(row[8], 0)
            self.assertEqual(row[9], 1)
            self.assertIsNone(row[10])
            self.assertIsNone(row[11])
            self.assertEqual(row[12], "2026-01-01T00:00:00+00:00")
        finally:
            conn.close()

    def test_store_contacts_for_prompt_replaces_previous_snapshot(self):
        first = ContactSQLiteRecord(
            contact_id="contact_request:old",
            channel="email",
            address="old@example.com",
            normalized_address="old@example.com",
            display_name="Old",
            source="contact_request",
            status="pending_request",
            allow_inbound=False,
            allow_outbound=False,
            can_configure=False,
            requested_at="2026-01-01T00:00:00+00:00",
            responded_at=None,
            updated_at=None,
        )
        second = ContactSQLiteRecord(
            contact_id="contact_request:new",
            channel="email",
            address="new@example.com",
            normalized_address="new@example.com",
            display_name="New",
            source="contact_request",
            status="rejected_request",
            allow_inbound=False,
            allow_outbound=False,
            can_configure=False,
            requested_at="2026-01-02T00:00:00+00:00",
            responded_at="2026-01-02T01:00:00+00:00",
            updated_at="2026-01-02T01:00:00+00:00",
        )

        store_contacts_for_prompt([first])
        store_contacts_for_prompt([second])

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM "__contacts";')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT contact_id FROM "__contacts";')
            self.assertEqual(cur.fetchone()[0], "contact_request:new")
        finally:
            conn.close()


@tag("batch_sqlite")
class SqliteContactsSnapshotBuilderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="owner",
            email="Owner@Example.COM",
            first_name="Owner",
            last_name="User",
        )
        EmailAddress.objects.create(
            user=self.owner,
            email=self.owner.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Contacts Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Contacts Agent",
            charter="Test contacts snapshot.",
            browser_use_agent=self.browser_agent,
        )
        self.configure_user_ids = {self.owner.id}

    def _display_name_for_user(self, user):
        return (user.get_full_name() or "").strip() or None

    def _user_can_configure(self, user_id):
        return user_id in self.configure_user_ids

    def _records_by_address(self):
        records = build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=self._display_name_for_user,
            user_can_configure=self._user_can_configure,
        )
        return {(record.channel, record.normalized_address): record for record in records}

    def test_owner_implicit_contact_appears_allowed(self):
        records = self._records_by_address()

        record = records[(CommsChannel.EMAIL, "owner@example.com")]
        self.assertEqual(record.source, "owner")
        self.assertEqual(record.status, "allowed")
        self.assertTrue(record.allow_inbound)
        self.assertTrue(record.allow_outbound)
        self.assertTrue(record.can_configure)

    def test_active_allowlist_entry_uses_direction_flags(self):
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="Friend@Example.COM",
            is_active=True,
            allow_inbound=True,
            allow_outbound=False,
            can_configure=True,
        )

        records = self._records_by_address()

        record = records[(CommsChannel.EMAIL, "friend@example.com")]
        self.assertEqual(record.source, "allowlist_entry")
        self.assertEqual(record.status, "allowed")
        self.assertTrue(record.allow_inbound)
        self.assertFalse(record.allow_outbound)
        self.assertTrue(record.can_configure)

    def test_contact_requests_are_non_sendable_diagnostics(self):
        pending = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="pending@example.com",
            name="Pending Person",
            reason="Coordinate launch.",
            purpose="Launch coordination",
            expires_at=timezone.now() + timedelta(days=1),
        )
        rejected = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="rejected@example.com",
            name="Rejected Person",
            reason="Coordinate launch.",
            purpose="Launch coordination",
            status=CommsAllowlistRequest.RequestStatus.REJECTED,
            responded_at=timezone.now(),
        )

        records = self._records_by_address()

        pending_record = records[(CommsChannel.EMAIL, pending.address)]
        self.assertEqual(pending_record.status, "pending_request")
        self.assertFalse(pending_record.allow_outbound)
        rejected_record = records[(CommsChannel.EMAIL, rejected.address)]
        self.assertEqual(rejected_record.status, "rejected_request")
        self.assertFalse(rejected_record.allow_outbound)

    def test_approved_request_without_active_allowlist_is_not_sendable(self):
        CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="approved-but-missing@example.com",
            name="Approved Missing",
            reason="Coordinate launch.",
            purpose="Launch coordination",
            status=CommsAllowlistRequest.RequestStatus.APPROVED,
            responded_at=timezone.now(),
        )

        records = self._records_by_address()

        record = records[(CommsChannel.EMAIL, "approved-but-missing@example.com")]
        self.assertEqual(record.status, "approved_missing_allowlist")
        self.assertFalse(record.allow_outbound)

    def test_allowed_row_overrides_request_for_same_address(self):
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="allowed@example.com",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )
        CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="allowed@example.com",
            name="Allowed Person",
            reason="Coordinate launch.",
            purpose="Launch coordination",
            status=CommsAllowlistRequest.RequestStatus.APPROVED,
            responded_at=timezone.now(),
        )

        records = self._records_by_address()

        record = records[(CommsChannel.EMAIL, "allowed@example.com")]
        self.assertEqual(record.source, "allowlist_entry")
        self.assertEqual(record.status, "allowed")
        self.assertTrue(record.allow_outbound)

    def test_org_members_and_collaborators_are_effective_contacts(self):
        User = get_user_model()
        org = Organization.objects.create(name="Org", slug="contacts-org", created_by=self.owner)
        with patch.object(PersistentAgent, "_validate_org_seats", return_value=None):
            self.agent.organization = org
            self.agent.save(update_fields=["organization"])
        org_member = User.objects.create_user(
            username="org-member",
            email="member@example.com",
            first_name="Org",
            last_name="Member",
        )
        OrganizationMembership.objects.create(
            org=org,
            user=org_member,
            role=OrganizationMembership.OrgRole.ADMIN,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.configure_user_ids.add(org_member.id)
        UserPhoneNumber.objects.create(
            user=org_member,
            phone_number="+15555550123",
            is_verified=True,
        )
        collaborator = User.objects.create_user(
            username="collaborator",
            email="collab@example.com",
            first_name="Collab",
            last_name="User",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=collaborator)

        records = self._records_by_address()

        org_email = records[(CommsChannel.EMAIL, "member@example.com")]
        self.assertEqual(org_email.source, "org_member")
        self.assertTrue(org_email.allow_outbound)
        self.assertTrue(org_email.can_configure)
        org_sms = records[(CommsChannel.SMS, "+15555550123")]
        self.assertEqual(org_sms.source, "org_member")
        self.assertTrue(org_sms.allow_inbound)
        self.assertFalse(org_sms.allow_outbound)
        collaborator_record = records[(CommsChannel.EMAIL, "collab@example.com")]
        self.assertEqual(collaborator_record.source, "collaborator")
        self.assertTrue(collaborator_record.allow_outbound)
