import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from api.agent.comms import ingest_inbound_message, TwilioSmsAdapter, PostmarkEmailAdapter
from api.models import (
    CommsChannel,
    PersistentAgentCommsEndpoint,
    OutboundMessageAttempt,
    DeliveryStatus,
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

        Analytics.track_event(
            user_id=agent.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SMS_RECEIVED,
            source=AnalyticsSource.SMS,
            properties={
                'agent_id': str(agent.id),
                'agent_name': agent.name,
                'from_number': from_number,
                'to_number': to_number,
                'message_body': body,
            }
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
            Analytics.track_event(
                user_id=message.owner_agent.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_SMS_DELIVERED,
                source=AnalyticsSource.AGENT,
                properties={
                    "agent_id": str(message.owner_agent_id),
                    "message_id": str(message.id),
                    "sms_id": message_sid,
                },
            )
        elif status in ["failed", "undelivered"]:
            attempt.status = DeliveryStatus.FAILED
            attempt.error_code = str(error_code)
            attempt.error_message = error_message
            message.latest_status = DeliveryStatus.FAILED
            message.latest_error_code = str(error_code)
            message.latest_error_message = error_message
            Analytics.track_event(
                user_id=message.owner_agent.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_SMS_FAILED,
                source=AnalyticsSource.AGENT,
                properties={
                    "agent_id": str(message.owner_agent_id),
                    "message_id": str(message.id),
                    "sms_id": message_sid,
                    "error_code": str(error_code),
                },
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
                
                Analytics.track_event(
                    user_id=agent.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_EMAIL_RECEIVED,
                    source=AnalyticsSource.AGENT,
                    properties={
                        'agent_id': str(agent.id),
                        'agent_name': agent.name,
                        'from_email': from_email_raw,
                        'message_id': str(msg_info.message.id),
                        'endpoint_address': endpoint.address,
                        'recipient_type': 'to' if endpoint.address in to_emails else
                                         'cc' if endpoint.address in cc_emails else 'bcc'
                    }
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

        return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"Error processing link click webhook: {e}", exc_info=True)
        return HttpResponse(status=500)
