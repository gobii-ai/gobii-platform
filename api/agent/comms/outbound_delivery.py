import logging
import os

from django.core.mail import get_connection
from django.conf import settings
from django.utils import timezone
from waffle import switch_is_active
from anymail.message import AnymailMessage
from anymail.exceptions import AnymailAPIError

from api.models import (
    AgentEmailAccount,
    AgentFsNode,
    CommsChannel,
    DeliveryStatus,
    OutboundMessageAttempt,
    PersistentAgentMessage,
)
from api.agent.files.attachment_helpers import track_file_send_failed, track_file_unsupported
from opentelemetry.trace import get_current_span
from opentelemetry import trace
from django.template.loader import render_to_string

from util import sms
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.integrations import postmark_status

from .email_content import convert_body_to_html_and_plaintext
from .email_footer_service import append_footer_if_needed
from .smtp_transport import SmtpTransport


# ──────────────────────────────────────────────────────────────────────────────
# SMS Content Conversion Helper
# ──────────────────────────────────────────────────────────────────────────────


def _convert_sms_body_to_plaintext(body: str) -> str:
    """Detect whether *body* is HTML, Markdown, or plaintext and return a
    plaintext string suitable for SMS. The detection and conversion process
    closely mirrors the email conversion logic but targets a single plaintext
    output.

    Steps:
    1. If any common HTML tag is present, strip to text via *inscriptis*.
    2. Else if Markdown syntax is detected, render to plaintext via *pypandoc*.
    3. Otherwise, treat as generic plaintext.

    All stages emit detailed INFO-level logs for observability.
    """
    import re
    import logging
    from inscriptis import get_text  # type: ignore
    from inscriptis.model.config import ParserConfig  # type: ignore
    from inscriptis.css_profiles import CSS_PROFILES  # type: ignore
    import pypandoc  # type: ignore

    logger = logging.getLogger(__name__)

    body_length = len(body)
    body_preview = body[:200] + "..." if len(body) > 200 else body
    logger.info(
        "SMS content conversion starting. Input body length: %d characters. Preview: %r",
        body_length,
        body_preview,
    )

    # ------------------------------------------------------------------ Detect HTML
    logger.info("=== SMS HTML DETECTION START ===")
    html_tag_pattern = r"</?(?:p|br|div|span|a|ul|ol|li|h[1-6]|strong|em|b|i|code|pre|blockquote)\b[^>]*>"
    html_match = re.search(html_tag_pattern, body, re.IGNORECASE)
    
    if html_match:
        logger.info("=== HTML DETECTED ===")
        logger.info(
            "Content type: HTML. Found tag %r at position %d",
            html_match.group(0),
            html_match.start(),
        )
        
        # Show context around the match
        match_start = max(0, html_match.start() - 20)
        match_end = min(len(body), html_match.end() + 20)
        context = body[match_start:match_end]
        logger.info("HTML tag context: %r", context)
        
        logger.info("=== INSCRIPTIS CONVERSION START ===")
        logger.info("Input to inscriptis (length=%d):\n%s", len(body), body)
        
        strict_css = CSS_PROFILES['strict'].copy()
        config = ParserConfig(css=strict_css, display_links=True, display_anchors=True)
        logger.info("Inscriptis config: css=strict, display_links=True, display_anchors=True")
        
        raw_output = get_text(body, config)
        logger.info("=== INSCRIPTIS RAW OUTPUT ===")
        logger.info("Raw inscriptis output (before .strip()):\n%r", raw_output)
        
        plaintext = raw_output.strip()
        logger.info("=== INSCRIPTIS FINAL OUTPUT ===")
        logger.info("Final output after .strip() (length=%d):\n%r", len(plaintext), plaintext)
        
        logger.info(
            "✓ HTML → plaintext conversion SUCCESSFUL. Input length: %d → Output length: %d",
            len(body),
            len(plaintext)
        )
        logger.info("=== SMS HTML CONVERSION COMPLETE ===")
        return plaintext
    else:
        logger.info("✗ No HTML tags detected, proceeding to markdown detection")

    # ------------------------------------------------------------------ Detect Markdown
    markdown_patterns = [
        (r"^\s{0,3}#", "heading"),                 # Heading
        (r"\*\*.+?\*\*", "bold_asterisk"),        # Bold **text**
        (r"__.+?__", "bold_underscore"),          # Bold __text__
        (r"`{1,3}.+?`{1,3}", "code"),             # Inline/fenced code
        (r"\[[^\]]+\]\([^)]+\)", "link"),         # Link [text](url)
        (r"^\s*[-*+] ", "unordered_list"),        # Unordered list
        (r"^\s*\d+\. ", "ordered_list"),          # Ordered list
    ]
    
    # Detailed pattern analysis
    detected_patterns = []
    logger.info("=== SMS MARKDOWN PATTERN DETECTION START ===")
    logger.info("Analyzing input body for markdown patterns...")
    logger.info("Input body (full):\n%r", body)
    
    for pattern, pattern_name in markdown_patterns:
        matches = list(re.finditer(pattern, body, flags=re.MULTILINE))
        if matches:
            detected_patterns.append((pattern_name, len(matches)))
            logger.info(
                "✓ PATTERN MATCH: '%s' (%s) found %d times",
                pattern_name,
                pattern,
                len(matches)
            )
            for i, match in enumerate(matches):
                logger.info(
                    "  Match %d: %r at position %d-%d (line context: %r)",
                    i + 1,
                    match.group(0),
                    match.start(),
                    match.end(),
                    body[max(0, match.start()-20):match.end()+20]
                )
        else:
            logger.info("✗ No match: '%s' (%s)", pattern_name, pattern)
    
    if detected_patterns:
        logger.info("=== MARKDOWN DETECTED ===")
        logger.info(
            "Content type: MARKDOWN. Detected patterns: %s",
            ", ".join([f"{name}({count})" for name, count in detected_patterns])
        )
        
        logger.info("=== PYPANDOC CONVERSION START ===")
        logger.info("Input to pypandoc (length=%d):", len(body))
        logger.info("Input content:\n%s", body)
        
        logger.info("Pypandoc args: to='plain', format='gfm', extra_args=['--wrap=preserve', '--reference-links']")
        
        try:
            # Convert markdown to plaintext using pypandoc
            # Using GFM format with --wrap=preserve to properly handle list formatting
            # and --reference-links to preserve link formatting
            raw_output = pypandoc.convert_text(
                body,
                to="plain",
                format="gfm",
                extra_args=["--wrap=preserve", "--reference-links"]
            )
            logger.info("=== PYPANDOC RAW OUTPUT ===")
            logger.info("Raw pypandoc output (before .strip()):\n%r", raw_output)
            logger.info("Raw pypandoc output formatted:\n%s", raw_output)
            
            plaintext = raw_output.strip()
            logger.info("=== PYPANDOC FINAL OUTPUT ===")
            logger.info("Final output after .strip() (length=%d):\n%r", len(plaintext), plaintext)
            logger.info("Final output formatted:\n%s", plaintext)
            
            # Character-by-character comparison for debugging
            if body != plaintext:
                logger.info("=== INPUT vs OUTPUT COMPARISON ===")
                logger.info("Input chars: %r", [c for c in body])
                logger.info("Output chars: %r", [c for c in plaintext])
                
                # Line-by-line comparison
                input_lines = body.split('\n')
                output_lines = plaintext.split('\n')
                logger.info("Input lines (%d): %r", len(input_lines), input_lines)
                logger.info("Output lines (%d): %r", len(output_lines), output_lines)
                
                for i, (input_line, output_line) in enumerate(zip(input_lines, output_lines)):
                    if input_line != output_line:
                        logger.info("Line %d changed: %r → %r", i, input_line, output_line)
            
            logger.info(
                "✓ Markdown → plaintext conversion SUCCESSFUL. Input length: %d → Output length: %d",
                len(body),
                len(plaintext)
            )
            logger.info("=== SMS MARKDOWN CONVERSION COMPLETE ===")
            return plaintext
            
        except Exception as e:
            logger.error("=== PYPANDOC CONVERSION FAILED ===")
            logger.error("Exception type: %s", type(e).__name__)
            logger.error("Exception message: %s", str(e))
            logger.error("Falling back to original markdown text (stripped)")
            fallback = body.strip()
            logger.info("Fallback output: %r", fallback)
            return fallback

    # ------------------------------------------------------------------ Plaintext fallback
    logger.info("=== SMS PLAINTEXT FALLBACK ===")
    logger.info("No markdown patterns detected. Treating as plaintext.")
    logger.info("Content type detected for SMS: Plaintext. No HTML or Markdown patterns found.")
    fallback = body.strip()
    logger.info("Plaintext output (after .strip(), length=%d): %r", len(fallback), fallback)
    logger.info("=== SMS PLAINTEXT CONVERSION COMPLETE ===")
    return fallback

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


_postmark_connection = None


def _get_postmark_connection():
    """Return a reusable Postmark connection when integration is enabled."""
    global _postmark_connection
    if _postmark_connection is not None:
        return _postmark_connection
    if not postmark_status().enabled:
        return None
    _postmark_connection = get_connection("anymail.backends.postmark.EmailBackend")
    return _postmark_connection


def _prepare_email_content(message: PersistentAgentMessage, body_raw: str) -> tuple[str, str]:
    """Convert the raw body into HTML/plaintext and append footers when required."""
    html_snippet, plaintext_body = convert_body_to_html_and_plaintext(body_raw)
    agent = getattr(message, "owner_agent", None)
    return append_footer_if_needed(agent, html_snippet, plaintext_body)


def _attach_email_attachments(message: PersistentAgentMessage, msg: AnymailMessage) -> int:
    attachments = list(message.attachments.select_related("filespace_node"))
    if not attachments:
        return 0

    agent = getattr(message, "owner_agent", None)
    message_id = str(getattr(message, "id", "")) if getattr(message, "id", None) else None
    channel = getattr(getattr(message, "from_endpoint", None), "channel", None)
    user_initiated = bool(getattr(agent, "user_id", None)) if agent else None
    max_bytes = getattr(settings, "MAX_FILE_SIZE", None)
    attached = 0
    for att in attachments:
        filename = att.filename or "attachment"
        content_type = att.content_type or "application/octet-stream"
        file_field = None

        if att.file and getattr(att.file, "name", None):
            file_field = att.file
        else:
            node = getattr(att, "filespace_node", None)
            if node and getattr(node, "node_type", None) == AgentFsNode.NodeType.FILE:
                file_field = node.content
                if node.name:
                    filename = node.name
                if node.mime_type:
                    content_type = node.mime_type or content_type

        if not file_field or not getattr(file_field, "name", None):
            logger.warning("Skipping attachment %s for message %s (missing file content)", att.id, message.id)
            track_file_send_failed(
                agent,
                node=getattr(att, "filespace_node", None),
                path=getattr(getattr(att, "filespace_node", None), "path", None),
                filename=filename,
                size_bytes=getattr(att, "file_size", None),
                mime_type=content_type,
                channel=channel,
                message_id=message_id,
                reason_code="missing_blob",
                user_initiated=user_initiated,
            )
            continue

        size_bytes = att.file_size or getattr(file_field, "size", None)
        try:
            if max_bytes and size_bytes and int(size_bytes) > int(max_bytes):
                logger.warning(
                    "Skipping attachment %s for message %s (size %s exceeds max %s)",
                    att.id,
                    message.id,
                    size_bytes,
                    max_bytes,
                )
                track_file_unsupported(
                    agent,
                    node=getattr(att, "filespace_node", None),
                    path=getattr(getattr(att, "filespace_node", None), "path", None),
                    filename=filename,
                    size_bytes=int(size_bytes),
                    mime_type=content_type,
                    channel=channel,
                    message_id=message_id,
                    reason_code="too_large",
                    user_initiated=user_initiated,
                )
                continue
        except (TypeError, ValueError):
            logger.warning(
                "Skipping attachment %s for message %s (failed size validation)",
                att.id,
                message.id,
            )
            track_file_send_failed(
                agent,
                node=getattr(att, "filespace_node", None),
                path=getattr(getattr(att, "filespace_node", None), "path", None),
                filename=filename,
                size_bytes=None,
                mime_type=content_type,
                channel=channel,
                message_id=message_id,
                reason_code="validation_failed",
                user_initiated=user_initiated,
            )
            continue

        storage = file_field.storage
        name = file_field.name
        if hasattr(storage, "exists") and not storage.exists(name):
            logger.warning("Skipping attachment %s for message %s (missing storage blob)", att.id, message.id)
            track_file_send_failed(
                agent,
                node=getattr(att, "filespace_node", None),
                path=getattr(getattr(att, "filespace_node", None), "path", None),
                filename=filename,
                size_bytes=size_bytes if isinstance(size_bytes, int) else None,
                mime_type=content_type,
                channel=channel,
                message_id=message_id,
                reason_code="missing_blob",
                user_initiated=user_initiated,
            )
            continue

        try:
            with storage.open(name, "rb") as handle:
                content = handle.read()
            msg.attach(filename, content, content_type)
            attached += 1
        except Exception:
            logger.exception("Failed attaching file %s to message %s", att.id, message.id)
            track_file_send_failed(
                agent,
                node=getattr(att, "filespace_node", None),
                path=getattr(getattr(att, "filespace_node", None), "path", None),
                filename=filename,
                size_bytes=size_bytes if isinstance(size_bytes, int) else None,
                mime_type=content_type,
                channel=channel,
                message_id=message_id,
                reason_code="validation_failed",
                user_initiated=user_initiated,
            )

    return attached


@tracer.start_as_current_span("AGENT - Deliver Agent Email")
def deliver_agent_email(message: PersistentAgentMessage):
    """
    Sends an agent's email message using Postmark and updates its status.
    """
    span = get_current_span()

    if not message.is_outbound or message.from_endpoint.channel != CommsChannel.EMAIL:
        logger.warning(
            "deliver_agent_email called for non-outbound or non-email message %s. Skipping.",
            message.id,
        )
        return

    if message.latest_status != DeliveryStatus.QUEUED:
        logger.info(
            "Skipping email delivery for message %s because its status is '%s', not 'queued'.",
            message.id,
            message.latest_status,
        )
        return

    # First: per-endpoint SMTP override
    acct = None
    try:
        # Use a direct DB check to avoid stale related-object caches
        acct = (
            AgentEmailAccount.objects.select_related("endpoint")
            .filter(endpoint=message.from_endpoint, is_outbound_enabled=True)
            .first()
        )
    except Exception:
        acct = None

    if acct is not None:
        logger.info(
            "Using per-endpoint SMTP for message %s from %s",
            message.id,
            message.from_endpoint.address,
        )
        # Mark sending and create attempt for SMTP
        message.latest_status = DeliveryStatus.SENDING
        message.save(update_fields=["latest_status"])

        attempt = OutboundMessageAttempt.objects.create(
            message=message,
            provider="smtp",
            status=DeliveryStatus.SENDING,
        )

        try:
            from_address = message.from_endpoint.address
            to_address = message.to_endpoint.address if message.to_endpoint else ""
            subject = message.raw_payload.get("subject", "")
            body_raw = message.body

            # content conversion
            html_snippet, plaintext_body = _prepare_email_content(message, body_raw)
            html_body = render_to_string(
                "emails/persistent_agent_email.html",
                {"body": html_snippet},
            )

            # Collect all recipients (To + CC)
            recipient_list = [to_address] if to_address else []
            if message.cc_endpoints.exists():
                recipient_list.extend(list(message.cc_endpoints.values_list("address", flat=True)))

            with tracer.start_as_current_span("SMTP Transport Send") as smtp_span:
                smtp_span.set_attribute("from", from_address)
                smtp_span.set_attribute("to_count", 1)
                try:
                    cc_count = message.cc_endpoints.count()
                except Exception:
                    cc_count = 0
                smtp_span.set_attribute("cc_count", cc_count)
                smtp_span.set_attribute("recipient_total", len(recipient_list))
                provider_id = SmtpTransport.send(
                    account=acct,
                    from_addr=from_address,
                    to_addrs=recipient_list,
                    subject=subject,
                    plaintext_body=plaintext_body,
                    html_body=html_body,
                    attempt_id=str(attempt.id),
                )

            now = timezone.now()
            attempt.status = DeliveryStatus.SENT
            attempt.provider_message_id = provider_id or ""
            attempt.sent_at = now
            attempt.save(update_fields=["status", "provider_message_id", "sent_at"])

            message.latest_status = DeliveryStatus.SENT
            message.latest_sent_at = now
            message.latest_error_message = ""
            message.save(update_fields=["latest_status", "latest_sent_at", "latest_error_message"])

            if span is not None and getattr(span, "is_recording", lambda: False)():
                span.add_event(
                    'Email - SMTP Delivery',
                    {
                        'message_id': str(message.id),
                        'from_address': from_address,
                        'to_address': to_address,
                    },
                )

            props = Analytics.with_org_properties(
                {
                    'agent_id': str(message.owner_agent_id),
                    'message_id': str(message.id),
                    'from_address': from_address,
                    'to_address': to_address,
                    'subject': subject,
                    'provider': 'smtp',
                },
                organization=getattr(message.owner_agent, "organization", None),
            )
            Analytics.track_event(
                user_id=message.owner_agent.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_EMAIL_SENT,
                source=AnalyticsSource.AGENT,
                properties=props.copy(),
            )
            return

        except Exception as e:
            logger.exception(
                "SMTP error sending message %s from %r to %r",
                message.id,
                getattr(message.from_endpoint, 'address', None),
                getattr(message.to_endpoint, 'address', None),
            )
            error_str = str(e)
            attempt.status = DeliveryStatus.FAILED
            attempt.error_message = error_str
            attempt.save(update_fields=["status", "error_message"])

            message.latest_status = DeliveryStatus.FAILED
            message.latest_error_message = error_str
            message.save(update_fields=["latest_status", "latest_error_message"])
            return

    # Check environment and token once up front (Postmark or simulation)
    postmark_state = postmark_status()
    postmark_token = os.getenv("POSTMARK_SERVER_TOKEN")
    release_env = getattr(settings, "GOBII_RELEASE_ENV", os.getenv("GOBII_RELEASE_ENV", "local"))
    missing_token = (not postmark_token) or not postmark_state.enabled
    simulation_flag = getattr(settings, "SIMULATE_EMAIL_DELIVERY", False)

    # Simulate only when explicitly enabled and Postmark is not configured.
    # SMTP (per-endpoint) was handled above and takes precedence when present.
    if simulation_flag and missing_token:
        # Tailor message to reason for simulation (explicit flag vs missing token)
        if release_env != "prod" and missing_token:
            logger.info(
                "Running in non-prod environment without POSTMARK_SERVER_TOKEN. Simulating email delivery for message %s.",
                message.id,
            )
        else:
            logger.info(
                "SIMULATE_EMAIL_DELIVERY enabled in %s (POSTMARK_SERVER_TOKEN %s). Simulating email delivery for message %s.",
                release_env,
                "present" if postmark_token else "missing",
                message.id,
            )
        subject = message.raw_payload.get("subject", "")
        body_raw = message.body
        html_snippet, plaintext_body = _prepare_email_content(message, body_raw)

        # Log simulated content details for parity with non-prod simulation branch
        logger.info(
            "--- SIMULATED EMAIL ---\nFrom: %s\nTo: %s\nSubject: %s\n\n=== ORIGINAL RAW BODY ===\n%s\n\n=== CONVERTED HTML VERSION ===\n%s\n\n=== CONVERTED PLAINTEXT VERSION ===\n%s\n-----------------------",
            message.from_endpoint.address,
            message.to_endpoint.address,
            subject,
            message.body,
            html_snippet,
            plaintext_body,
        )

        now = timezone.now()
        OutboundMessageAttempt.objects.create(
            message=message,
            provider="postmark_simulation",
            status=DeliveryStatus.DELIVERED,
            sent_at=now,
            delivered_at=now,
        )
        message.latest_status = DeliveryStatus.DELIVERED
        message.latest_sent_at = now
        message.latest_delivered_at = now
        message.latest_error_message = ""
        message.save(
            update_fields=["latest_status", "latest_sent_at", "latest_delivered_at", "latest_error_message"]
        )
        if span is not None and getattr(span, "is_recording", lambda: False)():
            span.add_event('Email - Simulated Delivery (flag)', {
                'message_id': str(message.id),
                'from_address': message.from_endpoint.address,
                'to_address': message.to_endpoint.address,
            })
        return

    if release_env != "prod" and not postmark_token:
        logger.info(
            "Running in non-prod environment without POSTMARK_SERVER_TOKEN. "
            "Simulating email delivery for message %s.",
            message.id,
        )
        subject = message.raw_payload.get("subject", "")
        body_raw = message.body
        
        # Log raw message details for simulation as well
        logger.info(
            "SIMULATION - Raw agent message details for message %s: "
            "from=%r, to=%r, subject=%r, body_length=%d",
            message.id,
            message.from_endpoint.address,
            message.to_endpoint.address,
            subject,
            len(body_raw)
        )
        
        # For simulation, also show content conversion results
        logger.info("SIMULATION - Processing content conversion for message %s", message.id)
        html_snippet, plaintext_body = _prepare_email_content(message, body_raw)
        
        logger.info(
            "SIMULATION - Content conversion results for message %s: "
            "HTML snippet length: %d, plaintext length: %d",
            message.id,
            len(html_snippet),
            len(plaintext_body)
        )
        
        logger.info(
            "--- SIMULATED EMAIL ---\n"
            "From: %s\nTo: %s\nSubject: %s\n\n"
            "=== ORIGINAL RAW BODY ===\n%s\n\n"
            "=== CONVERTED HTML VERSION ===\n%s\n\n"
            "=== CONVERTED PLAINTEXT VERSION ===\n%s\n"
            "-----------------------",
            message.from_endpoint.address,
            message.to_endpoint.address,
            subject,
            message.body,
            html_snippet,
            plaintext_body,
        )

        now = timezone.now()
        OutboundMessageAttempt.objects.create(
            message=message,
            provider="postmark_simulation",
            status=DeliveryStatus.DELIVERED,
            sent_at=now,
            delivered_at=now,
        )
        message.latest_status = DeliveryStatus.DELIVERED
        message.latest_sent_at = now
        message.latest_delivered_at = now
        message.latest_error_message = ""
        message.save(
            update_fields=["latest_status", "latest_sent_at", "latest_delivered_at", "latest_error_message"]
        )

        if span is not None and getattr(span, "is_recording", lambda: False)():
            span.add_event('Email - Simulated Delivery', {
                'message_id': str(message.id),
                'from_address': message.from_endpoint.address,
                'to_address': message.to_endpoint.address,
            })

        return

    # Start by creating an attempt record and updating message status
    message.latest_status = DeliveryStatus.SENDING
    message.save(update_fields=["latest_status"])
    
    attempt = OutboundMessageAttempt.objects.create(
        message=message,
        provider="postmark",
        status=DeliveryStatus.SENDING,
    )

    try:
        from_address = message.from_endpoint.address
        to_address = message.to_endpoint.address
        subject = message.raw_payload.get("subject", "")
        body_raw = message.body
        
        # Log the raw message received from the agent
        logger.info(
            "Processing email message %s. Raw agent message details: "
            "from=%r, to=%r, subject=%r, body_length=%d, raw_payload_keys=%s",
            message.id,
            from_address,
            to_address, 
            subject,
            len(body_raw),
            list(message.raw_payload.keys())
        )
        
        # Log the complete raw body for debugging
        logger.info(
            "Raw message body for message %s: %r",
            message.id,
            body_raw
        )

        # Detect content type and convert appropriately
        logger.info("Starting content type detection and conversion for message %s", message.id)
        html_snippet, plaintext_body = _prepare_email_content(message, body_raw)
        
        # Log the conversion results
        logger.info(
            "Content conversion completed for message %s. HTML snippet length: %d, plaintext length: %d",
            message.id,
            len(html_snippet),
            len(plaintext_body)
        )

        # Wrap with our mobile-first template
        logger.info("Wrapping HTML snippet with email template for message %s", message.id)
        html_body = render_to_string(
            "emails/persistent_agent_email.html",
            {
                "body": html_snippet,
            },
        )
        
        # Log the final template-wrapped HTML
        logger.info(
            "Email template rendering complete for message %s. Final HTML body length: %d",
            message.id,
            len(html_body)
        )
        
        # Log the final message versions (with length limits for readability)
        final_plaintext_preview = plaintext_body[:500] + "..." if len(plaintext_body) > 500 else plaintext_body
        final_html_preview = html_body[:500] + "..." if len(html_body) > 500 else html_body
        
        logger.info(
            "Final email content for message %s - PLAINTEXT VERSION (length: %d): %r",
            message.id,
            len(plaintext_body),
            final_plaintext_preview
        )
        
        logger.info(
            "Final email content for message %s - HTML VERSION (length: %d): %r",
            message.id,
            len(html_body),
            final_html_preview
        )

        # Create the email message object
        logger.info(
            "Creating AnymailMessage for message %s with metadata: agent_id=%s, attempt_id=%s",
            message.id,
            message.owner_agent_id,
            attempt.id
        )
        
        # Get CC addresses if any
        cc_addresses = []
        if message.cc_endpoints.exists():
            cc_addresses = list(message.cc_endpoints.values_list('address', flat=True))
            logger.info(
                "Email message %s includes CC recipients: %s",
                message.id,
                cc_addresses
            )
        
        msg = AnymailMessage(
            subject=subject,
            body=plaintext_body,
            from_email=from_address,
            to=[to_address],
            cc=cc_addresses if cc_addresses else None,
            connection=_get_postmark_connection(),
            tags=["persistent-agent"],
            metadata={
                "agent_id": str(message.owner_agent_id),
                "message_id": str(message.id),
                "attempt_id": str(attempt.id),
            },
        )

        # Attach the HTML alternative
        logger.info("Attaching HTML alternative to message %s", message.id)
        msg.attach_alternative(html_body, "text/html")

        attachment_count = _attach_email_attachments(message, msg)
        if attachment_count:
            logger.info(
                "Attached %d file(s) to email message %s",
                attachment_count,
                message.id,
            )
        
        # Send the message
        logger.info(
            "Sending email message %s via Postmark. Final message summary: "
            "subject_length=%d, plaintext_length=%d, html_length=%d, to_recipients=%d",
            message.id,
            len(subject),
            len(plaintext_body),
            len(html_body),
            len([to_address])
        )
        
        msg.send(fail_silently=False)
        
        logger.info("Email message %s sent successfully via Postmark", message.id)

        span.add_event('Email - Postmark Delivery', {
            'message_id': str(message.id),
            'from_address': from_address,
            'to_address': to_address,
        })

        # On success, update records
        now = timezone.now()
        attempt.status = DeliveryStatus.SENT
        attempt.provider_message_id = msg.anymail_status.message_id or ""
        attempt.sent_at = now
        attempt.save()

        message.latest_status = DeliveryStatus.SENT
        message.latest_sent_at = now
        message.latest_error_message = ""
        message.save(update_fields=["latest_status", "latest_sent_at", "latest_error_message"])

        logger.info("Successfully sent agent email message %s via Postmark.", message.id)
        success_props = Analytics.with_org_properties(
            {
                'agent_id': str(message.owner_agent_id),
                'message_id': str(message.id),
                'from_address': from_address,
                'to_address': to_address,
                'subject': subject,
            },
            organization=getattr(message.owner_agent, "organization", None),
        )
        Analytics.track_event(
            user_id=message.owner_agent.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_EMAIL_SENT,
            source=AnalyticsSource.AGENT,
            properties=success_props.copy(),
        )

    except AnymailAPIError as e:
        logger.exception(
            "Postmark API error sending message %s. Message details: from=%r, to=%r, subject=%r",
            message.id,
            message.from_endpoint.address,
            message.to_endpoint.address,
            message.raw_payload.get("subject", "")
        )
        error_str = str(e)
        logger.error(
            "Email delivery failed for message %s with Postmark API error: %s",
            message.id,
            error_str
        )
        
        attempt.status = DeliveryStatus.FAILED
        attempt.error_message = error_str
        attempt.save()

        message.latest_status = DeliveryStatus.FAILED
        message.latest_error_message = error_str
        message.save(update_fields=["latest_status", "latest_error_message"])

    except Exception as e:
        logger.exception(
            "Unexpected error sending message %s. Message details: from=%r, to=%r, subject=%r",
            message.id,
            message.from_endpoint.address,
            message.to_endpoint.address,
            message.raw_payload.get("subject", "")
        )
        error_str = f"An unexpected error occurred: {str(e)}"
        logger.error(
            "Email delivery failed for message %s with unexpected error: %s",
            message.id,
            error_str
        )
        
        attempt.status = DeliveryStatus.FAILED
        attempt.error_message = error_str
        attempt.save()

        message.latest_status = DeliveryStatus.FAILED
        message.latest_error_message = error_str
        message.save(update_fields=["latest_status", "latest_error_message"])


@tracer.start_as_current_span("AGENT - Deliver Agent SMS")
def deliver_agent_sms(message: PersistentAgentMessage):
    """Send an SMS and record the delivery attempt."""

    # Mark the message as sending and record a new attempt
    message.latest_status = DeliveryStatus.SENDING
    message.save(update_fields=["latest_status"])

    attempt = OutboundMessageAttempt.objects.create(
        message=message,
        provider="twilio",
        status=DeliveryStatus.SENDING,
    )

    # Convert content to an SMS-friendly plaintext version
    original_body = message.body
    plaintext_body = _convert_sms_body_to_plaintext(original_body)

    try:
        from constants.feature_flags import AGENT_CRON_THROTTLE
        from config.redis_client import get_redis_client
        from api.services.cron_throttle import (
            build_upgrade_link,
            cron_throttle_footer_cooldown_key,
            cron_throttle_pending_footer_key,
            evaluate_free_plan_cron_throttle,
            select_cron_throttle_sms_suffix,
        )

        agent = getattr(message, "owner_agent", None)
        if agent is not None and switch_is_active(AGENT_CRON_THROTTLE):
            redis_client = get_redis_client()
            pending_key = cron_throttle_pending_footer_key(str(agent.id))
            if redis_client.get(pending_key):
                decision = evaluate_free_plan_cron_throttle(agent, (getattr(agent, "schedule", None) or ""))
                if decision.throttling_applies:
                    suffix = select_cron_throttle_sms_suffix(
                        agent_name=agent.name,
                        effective_interval_seconds=decision.effective_interval_seconds,
                        upgrade_link=build_upgrade_link(),
                    )
                    plaintext_body = f"{plaintext_body}\n\n{suffix}".strip()
                    ttl_days = int(getattr(settings, "AGENT_CRON_THROTTLE_NOTICE_TTL_DAYS", 7))
                    ttl_seconds = max(1, ttl_days * 86400)
                    redis_client.delete(pending_key)
                    redis_client.set(
                        cron_throttle_footer_cooldown_key(str(agent.id)),
                        "1",
                        ex=ttl_seconds,
                    )
                else:
                    redis_client.delete(pending_key)
    except Exception:
        logger.debug("Failed applying cron throttle SMS notice for message %s", message.id, exc_info=True)

    logger.info(
        "Prepared SMS body for message %s. Original length: %d, Plaintext length: %d",
        message.id,
        len(original_body),
        len(plaintext_body),
    )

    # Collect all recipient numbers (primary + CC for group messaging)
    recipient_numbers = [message.to_endpoint.address]
    if message.cc_endpoints.exists():
        cc_numbers = list(message.cc_endpoints.values_list('address', flat=True))
        recipient_numbers.extend(cc_numbers)
        logger.info(
            "SMS message %s is a group message with %d total recipients: %s",
            message.id,
            len(recipient_numbers),
            recipient_numbers
        )

    # Send to all recipients
    # Note: This sends individual messages to each recipient
    # For true group messaging, you'd need a different approach with your SMS provider
    send_results = []
    all_successful = True
    
    for recipient in recipient_numbers:
        result = sms.send_sms(
            to_number=recipient,
            from_number=message.from_endpoint.address,
            body=plaintext_body,
        )
        send_results.append((recipient, result))
        if not result:
            all_successful = False
            logger.error(
                "Failed to send SMS to %s for message %s",
                recipient,
                message.id
            )
    
    send_result = all_successful

    now = timezone.now()

    if send_result:
        logger.info("Successfully sent agent SMS message %s via Twilio to all recipients.", message.id)
        # Store first successful message ID as the primary one
        provider_message_id = next((r[1] for r in send_results if r[1]), "")
        attempt.status = DeliveryStatus.SENT
        attempt.provider_message_id = provider_message_id
        attempt.sent_at = now
        attempt.save(update_fields=["status", "provider_message_id", "sent_at"])

        message.latest_status = DeliveryStatus.SENT
        message.latest_sent_at = now
        message.latest_error_message = ""

        sms_props = Analytics.with_org_properties(
            {
                "agent_id": str(message.owner_agent_id),
                "message_id": str(message.id),
                "sms_id": provider_message_id,
                "from_address": message.from_endpoint.address,
                "to_address": message.to_endpoint.address,
                "is_group": len(recipient_numbers) > 1,
                "recipient_count": len(recipient_numbers),
            },
            organization=getattr(message.owner_agent, "organization", None),
        )
        Analytics.track_event(
            user_id=message.owner_agent.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SMS_SENT,
            source=AnalyticsSource.AGENT,
            properties=sms_props.copy(),
        )
    else:
        logger.error("Failed to send agent SMS message %s via Twilio.", message.id)
        attempt.status = DeliveryStatus.FAILED
        attempt.error_message = "Failed to send SMS via Twilio."
        attempt.save(update_fields=["status", "error_message"])

        message.latest_status = DeliveryStatus.FAILED
        message.latest_error_message = "Failed to send SMS via Twilio."

        failure_props = Analytics.with_org_properties(
            {
                "agent_id": str(message.owner_agent_id),
                "message_id": str(message.id),
                "from_address": message.from_endpoint.address,
                "to_address": message.to_endpoint.address,
            },
            organization=getattr(message.owner_agent, "organization", None),
        )
        Analytics.track_event(
            user_id=message.owner_agent.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SMS_FAILED,
            source=AnalyticsSource.AGENT,
            properties=failure_props.copy(),
        )

    message.save(update_fields=["latest_status", "latest_sent_at", "latest_error_message"])

    return send_result
