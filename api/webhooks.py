import logging

from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from api.agent.comms import ingest_inbound_message, TwilioSmsAdapter, PostmarkEmailAdapter
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    OutboundMessageAttempt,
    DeliveryStatus,
    PipedreamConnectSession,
)
from opentelemetry import trace
import json
import re
from config import settings

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

@csrf_exempt
@require_POST
@tracer.start_as_current_span("COMM sms_webhook")
def sms_webhook(request):
    """Handle incoming SMS messages from Twilio"""

    # Get the GET parameter 't' to do our security check
    span = trace.get_current_span()
    api_key = request.GET.get('t', '').strip()

    if not api_key:
        logger.warning("SMS webhook called without 't' parameter; rejecting request.")
        span.add_event('SMS - Missing API KEY', {})
        return HttpResponse(status=400)

    # Validate it matches env var
    if api_key != settings.TWILIO_INCOMING_WEBHOOK_TOKEN:
        logger.warning(f"SMS webhook called with invalid API Key; got: {api_key}")
        span.add_event('SMS - Invalid API KEY', {'api_key': api_key})
        return HttpResponse(status=403)

    try:
        from_number = request.POST.get('From', "Unknown")
        to_number = request.POST.get('To', "Unknown")
        body = request.POST.get('Body', "Empty").strip()

        span.set_attribute("from_number", from_number)
        span.set_attribute("to_number", to_number)
        span.set_attribute("body", body)

        logger.info(f"Received SMS from {from_number} to {to_number}: {body}")

        with tracer.start_as_current_span("COMM sms whitelist check") as whitelist_span:
            try:
                endpoint = PersistentAgentCommsEndpoint.objects.select_related('owner_agent__user').get(
                    channel=CommsChannel.SMS,
                    address__iexact=to_number,
                    owner_agent__is_active=True
                )
                agent = endpoint.owner_agent
            except PersistentAgentCommsEndpoint.DoesNotExist:
                logger.info(f"Discarding SMS to unroutable number: {to_number}")
                whitelist_span.add_event('SMS - Unroutable Number', {'to_number': to_number})
                return HttpResponse(status=200)

            if not agent or not agent.user:
                logger.warning(f"Endpoint {to_number} is not associated with a usable agent/user. Discarding.")
                whitelist_span.add_event('SMS - No Agent/User', {'to_number': to_number})
                return HttpResponse(status=200)

            if not agent.is_sender_whitelisted(CommsChannel.SMS, from_number):
                logger.info(
                    f"Discarding SMS from non-whitelisted sender '{from_number}' to agent '{agent.name}'."
                )
                whitelist_span.add_event('SMS - Sender Not Whitelisted', {
                    'from_number': from_number,
                    'agent_id': str(agent.id),
                    'agent_name': agent.name,
                })
                return HttpResponse(status=200)



        # Add message via message service
        parsed_message = TwilioSmsAdapter.parse_request(request)
        ingest_inbound_message(CommsChannel.SMS, parsed_message)

        props = Analytics.with_org_properties(
            {
                'agent_id': str(agent.id),
                'agent_name': agent.name,
                'from_number': from_number,
                'to_number': to_number,
                'message_body': body,
            },
            organization=getattr(agent, "organization", None),
        )
        Analytics.track_event(
            user_id=agent.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SMS_RECEIVED,
            source=AnalyticsSource.SMS,
            properties=props.copy(),
        )

        # Return a 200 OK response to Twilio
        return HttpResponse(status=200)

    except Exception as e:
        logger.error(f"Error processing Twilio SMS webhook: {e}", exc_info=True)
        return HttpResponse(status=500)


@csrf_exempt
@require_POST
@tracer.start_as_current_span("COMM sms_status_webhook")
def sms_status_webhook(request):
    """Handle status callbacks from Twilio for outbound SMS."""

    api_key = request.GET.get("t", "").strip()
    if not api_key:
        logger.warning("SMS status webhook called without 't' parameter; rejecting request.")
        return HttpResponse(status=400)

    if api_key != settings.TWILIO_INCOMING_WEBHOOK_TOKEN:
        logger.warning(f"SMS status webhook called with invalid API Key; got: {api_key}")
        return HttpResponse(status=403)

    message_sid = request.POST.get("MessageSid")
    status = request.POST.get("MessageStatus")
    error_code = request.POST.get("ErrorCode") or ""
    error_message = request.POST.get("ErrorMessage") or ""

    logger.info(
        "Received SMS status update sid=%s status=%s code=%s",
        message_sid,
        status,
        error_code,
    )

    if not message_sid or not status:
        return HttpResponse(status=400)

    try:
        attempt = OutboundMessageAttempt.objects.filter(provider_message_id=message_sid).order_by("-queued_at").first()
        if not attempt:
            logger.warning("No OutboundMessageAttempt found for SID %s", message_sid)
            return HttpResponse(status=200)

        message = attempt.message
        now = timezone.now()

        if status in ["sent", "queued"]:
            attempt.status = DeliveryStatus.SENT
            attempt.sent_at = now
            message.latest_status = DeliveryStatus.SENT
            message.latest_sent_at = now
        elif status == "delivered":
            attempt.status = DeliveryStatus.DELIVERED
            attempt.delivered_at = now
            message.latest_status = DeliveryStatus.DELIVERED
            message.latest_delivered_at = now
            delivered_props = Analytics.with_org_properties(
                {
                    "agent_id": str(message.owner_agent_id),
                    "message_id": str(message.id),
                    "sms_id": message_sid,
                },
                organization=getattr(message.owner_agent, "organization", None),
            )
            Analytics.track_event(
                user_id=message.owner_agent.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_SMS_DELIVERED,
                source=AnalyticsSource.AGENT,
                properties=delivered_props.copy(),
            )
        elif status in ["failed", "undelivered"]:
            attempt.status = DeliveryStatus.FAILED
            attempt.error_code = str(error_code)
            attempt.error_message = error_message
            message.latest_status = DeliveryStatus.FAILED
            message.latest_error_code = str(error_code)
            message.latest_error_message = error_message
            failed_props = Analytics.with_org_properties(
                {
                    "agent_id": str(message.owner_agent_id),
                    "message_id": str(message.id),
                    "sms_id": message_sid,
                    "error_code": str(error_code),
                },
                organization=getattr(message.owner_agent, "organization", None),
            )
            Analytics.track_event(
                user_id=message.owner_agent.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_SMS_FAILED,
                source=AnalyticsSource.AGENT,
                properties=failed_props.copy(),
            )
        else:
            # Unknown or intermediate status
            return HttpResponse(status=200)

        attempt.save()
        message.save(
            update_fields=[
                "latest_status",
                "latest_sent_at",
                "latest_delivered_at",
                "latest_error_code",
                "latest_error_message",
            ]
        )

        return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"Error processing Twilio status webhook: {e}", exc_info=True)
        return HttpResponse(status=500)


@csrf_exempt
@require_POST
@tracer.start_as_current_span("COMM email_webhook")
def email_webhook(request):
    """
    Handle incoming email messages from a webhook (e.g., PostMark)
    """
    # Body is a JSON payload following the PostMark format
    raw_json = request.body.decode('utf-8')

    # Get the GET parameter 't' to do our security check
    api_key = request.GET.get('t', '').strip()

    if not api_key:
        logger.warning("Email webhook called without 't' parameter; rejecting request.")
        return HttpResponse(status=400)

    # Validate it matches env var
    if api_key != settings.POSTMARK_INCOMING_WEBHOOK_TOKEN:
        logger.warning(f"Email webhook called with invalid API Key; got: {api_key}")
        return HttpResponse(status=403)

    try:
        # Parse the JSON payload
        data = json.loads(raw_json)
        from_email_raw = data.get('From')
        subject = data.get('Subject')

        # Parse email addresses - they can be comma-separated strings
        def extract_emails_from_full_format(email_array):
            """Extract email addresses from array of dicts with Email, Name, MailboxHash format"""
            if not email_array or not isinstance(email_array, list):
                return []
            return [item.get('Email', '').strip() for item in email_array if item.get('Email', '').strip()]

            # Collect all recipient addresses (ToFull, CcFull, and BccFull)

        to_emails = extract_emails_from_full_format(data.get('ToFull', []))
        cc_emails = extract_emails_from_full_format(data.get('CcFull', []))
        bcc_emails = extract_emails_from_full_format(data.get('BccFull', []))

        all_recipient_addresses = []
        all_recipient_addresses.extend(to_emails)
        all_recipient_addresses.extend(cc_emails)
        all_recipient_addresses.extend(bcc_emails)

        logger.info(f"Received email from {from_email_raw} to {to_emails}, CC: {cc_emails}, BCC: {bcc_emails}: {subject}")

        # Find all agent endpoints that match any of the recipient addresses
        matching_endpoints = []
        with tracer.start_as_current_span("COMM email endpoint lookup") as span:
            # We need to check each address individually for case-insensitive matching
            # since Django doesn't support iexact__in
            for address in all_recipient_addresses:
                try:
                    endpoint = PersistentAgentCommsEndpoint.objects.select_related('owner_agent__user').get(
                        channel=CommsChannel.EMAIL,
                        address__iexact=address,
                        owner_agent__is_active=True
                    )
                    if endpoint.owner_agent and endpoint.owner_agent.user:
                        matching_endpoints.append(endpoint)
                        logger.info(f"Found agent endpoint for address: {address}")
                    else:
                        logger.warning(f"Endpoint {address} is not associated with a usable agent/user.")
                except PersistentAgentCommsEndpoint.DoesNotExist:
                    logger.debug(f"No agent endpoint found for address: {address}")
                    continue

            span.set_attribute("total_recipients", len(all_recipient_addresses))
            span.set_attribute("matching_endpoints", len(matching_endpoints))

        # If no matching endpoints found, discard the email
        if not matching_endpoints:
            logger.info(f"Discarding email - no routable agent addresses found in To/CC/BCC")
            with tracer.start_as_current_span("COMM email no endpoints") as span:
                span.add_event('Email - No Routable Addresses', {
                    'to_emails': to_emails,
                    'cc_emails': cc_emails,
                    'bcc_emails': bcc_emails
                })
            return HttpResponse(status=200)  # OK to prevent retries

        # Postmark 'From' format can be "Name <email@example.com>"
        match = re.search(r'<([^>]+)>', from_email_raw)
        from_email = (match.group(1) if match else from_email_raw).strip()

        # Process the email for each matching agent endpoint
        processed_agents = []
        for endpoint in matching_endpoints:
            agent = endpoint.owner_agent
            
            # --- Whitelist check for this specific agent ---
            with tracer.start_as_current_span("COMM email whitelist check") as span:
                span.set_attribute("agent_id", str(agent.id))
                span.set_attribute("agent_name", agent.name)
                span.set_attribute("endpoint_address", endpoint.address)
                
                if not agent.is_sender_whitelisted(CommsChannel.EMAIL, from_email):
                    logger.info(
                        f"Discarding email from non-whitelisted sender '{from_email}' to agent '{agent.name}' (endpoint: {endpoint.address})."
                    )
                    span.add_event('Email - Sender Not Whitelisted', {
                        'from_email': from_email,
                        'agent_id': str(agent.id),
                        'agent_name': agent.name,
                        'endpoint_address': endpoint.address
                    })
                    continue  # Skip this agent but continue processing for other agents
            
            # Process the message for this agent
            try:
                # Parse the message with the correct recipient address for this agent
                # We need to create a new parsed message with the endpoint's address as the recipient
                adapter = PostmarkEmailAdapter()
                parsed_message = adapter.parse_request(request)
                
                # Override the recipient to be this specific endpoint's address
                # This ensures the message is correctly associated with this agent
                parsed_message.recipient = endpoint.address
                
                # Add message via message service for this specific agent
                msg_info = ingest_inbound_message(CommsChannel.EMAIL, parsed_message)
                
                processed_agents.append(agent)
                
                email_props = Analytics.with_org_properties(
                    {
                        'agent_id': str(agent.id),
                        'agent_name': agent.name,
                        'from_email': from_email_raw,
                        'message_id': str(msg_info.message.id),
                        'endpoint_address': endpoint.address,
                        'recipient_type': 'to' if endpoint.address in to_emails else
                                         'cc' if endpoint.address in cc_emails else 'bcc'
                    },
                    organization=getattr(agent, "organization", None),
                )
                Analytics.track_event(
                    user_id=agent.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_EMAIL_RECEIVED,
                    source=AnalyticsSource.AGENT,
                    properties=email_props.copy(),
                )
                
                logger.info(f"Successfully processed email for agent '{agent.name}' (endpoint: {endpoint.address})")
            except Exception as e:
                logger.error(f"Error processing email for agent '{agent.name}': {e}", exc_info=True)
                continue  # Continue processing for other agents

        # Log summary
        if processed_agents:
            logger.info(f"Email processed for {len(processed_agents)} agent(s): {[a.name for a in processed_agents]}")
        else:
            logger.info("Email not processed for any agents due to whitelist restrictions")

        return HttpResponse(status=200)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in email webhook: {e}", exc_info=True)
        return HttpResponse(status=400)
    except Exception as e:
        logger.error(f"Error processing inbound email open/email link click webhook: {e}", exc_info=True)
        return HttpResponse(status=500)


@csrf_exempt
@require_POST
@tracer.start_as_current_span("COMM pipedream_connect_webhook")
def pipedream_connect_webhook(request, session_id):
    """
    Handle Pipedream Connect webhook callbacks for a one‑time Connect token session.
    Security: requires query parameter t matching the stored webhook_secret.
    """
    # Validate one‑time secret
    secret = request.GET.get("t", "").strip()
    if not secret:
        logger.warning("PD Connect: webhook missing secret session=%s", session_id)
        return HttpResponse(status=400)

    try:
        session = PipedreamConnectSession.objects.select_related("agent").get(id=session_id)
    except PipedreamConnectSession.DoesNotExist:
        logger.warning("PD Connect: webhook unknown session=%s", session_id)
        return HttpResponse(status=200)

    if secret != session.webhook_secret:
        logger.warning("PD Connect: webhook invalid secret for session=%s", session_id)
        return HttpResponse(status=403)

    # Idempotency: if already finalized, do nothing
    if session.status in (PipedreamConnectSession.Status.SUCCESS, PipedreamConnectSession.Status.ERROR):
        logger.info("PD Connect: webhook idempotent ignore session=%s status=%s", session_id, session.status)
        return HttpResponse(status=200)

    try:
        payload_raw = request.body.decode("utf-8")
        data = json.loads(payload_raw or "{}")
        event = data.get("event")
        connect_token = data.get("connect_token")
        logger.info(
            "PD Connect: webhook received session=%s agent=%s event=%s has_token=%s",
            str(session.id), str(session.agent_id), event, bool(connect_token)
        )

        # Optional: verify connect_token correlates (if we have it)
        if session.connect_token and connect_token and str(connect_token) != session.connect_token:
            logger.warning("PD Connect: webhook token mismatch session=%s", session_id)
            return HttpResponse(status=400)

        if event == "CONNECTION_SUCCESS":
            account = data.get("account") or {}
            account_id = account.get("id") or ""

            session.status = PipedreamConnectSession.Status.SUCCESS
            session.account_id = account_id or ""
            session.save(update_fields=["status", "account_id", "updated_at"])
            logger.info(
                "PD Connect: connection SUCCESS session=%s app=%s account=%s",
                str(session.id), session.app_slug, account_id or ""
            )

            # Record a system step and trigger processing
            try:
                from api.models import PersistentAgentStep, PersistentAgentSystemStep
                step = PersistentAgentStep.objects.create(
                    agent=session.agent,
                    description=(
                        f"Pipedream connection SUCCESS for app '{session.app_slug}'"
                        + (f"; account={account_id}" if account_id else "")
                    ),
                )
                PersistentAgentSystemStep.objects.create(
                    step=step,
                    code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
                    notes=f"pipedream_connect:{session.app_slug}:{account_id}",
                )
                from api.agent.tasks.process_events import process_agent_events_task
                process_agent_events_task.delay(str(session.agent.id))
            except Exception:
                logger.exception("PD Connect: failed to record success step or trigger resume session=%s", str(session.id))

            return HttpResponse(status=200)

        elif event == "CONNECTION_ERROR":
            err = data.get("error") or ""
            session.status = PipedreamConnectSession.Status.ERROR
            session.save(update_fields=["status", "updated_at"])
            logger.info(
                "PD Connect: connection ERROR session=%s app=%s error=%s",
                str(session.id), session.app_slug, err
            )

            try:
                from api.models import PersistentAgentStep, PersistentAgentSystemStep
                step = PersistentAgentStep.objects.create(
                    agent=session.agent,
                    description=f"Pipedream connection ERROR for app '{session.app_slug}'",
                )
                PersistentAgentSystemStep.objects.create(
                    step=step,
                    code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                    notes=f"pipedream_connect_error:{session.app_slug}:{err}",
                )
            except Exception:
                logger.exception("PD Connect: failed to record error step session=%s", str(session.id))
            return HttpResponse(status=200)

        else:
            logger.info("PD Connect: webhook unknown/ignored event session=%s event=%s", str(session.id), event)
            return HttpResponse(status=200)

    except Exception as e:
        logger.error("PD Connect: webhook processing failed session=%s error=%s", session_id, e, exc_info=True)
        return HttpResponse(status=500)

@csrf_exempt
@require_POST
@tracer.start_as_current_span("COMM open_and_link_webhook")
def open_and_link_webhook(request):
    """
    Handles open events and link click webhooks from email services.
    """

    # Get the header X-Gobii-Postmark-Key to do our security check with the Postmark token we created. Note that the
    # Postmark api does not support adding headers to the inbound email hook, but it does for this one. Hence the
    # different security check.
    api_key = request.headers.get('x-gobii-postmark-key', '').strip()

    if not api_key:
        logger.warning("Open/link click webhook called without 'X-Gobii-Postmark-Key' header; rejecting request.")
        return HttpResponse(status=400)

    # Validate it matches env var
    if api_key != settings.POSTMARK_INCOMING_WEBHOOK_TOKEN:
        logger.warning(f"Open/link click webhook called with invalid API Key; got: {api_key}")
        return HttpResponse(status=403)

    try:
        # Parse the JSON payload in the request body
        raw_json = request.body.decode('utf-8')
        data = json.loads(raw_json)
        record_type = data.get('RecordType')

        if record_type == 'Open':
            Analytics.track_agent_email_opened(data)
        elif record_type == 'Click':
            Analytics.track_agent_email_link_clicked(data)
        else:
            logger.warning(f"Received email event webhook '{record_type}' which is not handled; disregarding it.")

        # Try to attribute the event back to an agent and update last_interaction_at
        try:

            provider_msg_id = data.get('MessageID') or data.get('MessageId')
            agent: PersistentAgent | None = None

            if provider_msg_id:
                attempt = (
                    OutboundMessageAttempt.objects
                    .select_related('message__owner_agent')
                    .filter(provider_message_id=provider_msg_id)
                    .order_by('-queued_at')
                    .first()
                )
                if attempt and attempt.message and attempt.message.owner_agent_id:
                    agent = attempt.message.owner_agent

            if agent is not None:
                with transaction.atomic():
                    locked_agent = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
                    locked_agent.last_interaction_at = timezone.now()
                    locked_agent.save(update_fields=['last_interaction_at'])
            else:
                logger.warning("Email %s event attribution failed: no agent found. Searched for Message Id %s", record_type, provider_msg_id)

        except Exception as attr_err:
            logger.warning("Email %s event attribution failed: %s", record_type, attr_err)

        return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"Error processing link click webhook: {e}", exc_info=True)
        return HttpResponse(status=500)
