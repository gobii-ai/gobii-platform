"""
Custom browser use agent action for triggering CAPTCHA solve via CDP.
"""

import logging
from typing import Any, Optional

from opentelemetry import trace
from browser_use import ActionResult
from browser_use.browser import BrowserSession
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


class CaptchaOption(BaseModel):
    """Captcha solver option payload with flexible extra fields."""

    model_config = ConfigDict(extra="allow")

    type: str
    disabled: Optional[bool] = None


def register_captcha_actions(controller) -> None:
    """Register the CAPTCHA solver action with the given controller."""

    @controller.action("Solve CAPTCHA using Browser API's Captcha.solve CDP command.")
    async def solve_captcha(
        browser_session: BrowserSession,
        detect_timeout_ms: Optional[int] = None,
        options: Optional[list[CaptchaOption]] = None,
    ) -> ActionResult:
        """Trigger Browser API's CAPTCHA solver and return the solve status."""
        with tracer.start_as_current_span("Browser Agent Solve Captcha") as span:
            params: dict[str, Any] = {}
            if detect_timeout_ms is not None:
                try:
                    detect_timeout_ms = int(detect_timeout_ms)
                except (TypeError, ValueError):
                    return ActionResult(
                        extracted_content="Error: detect_timeout_ms must be an integer value in milliseconds.",
                        include_in_memory=False,
                    )
                if detect_timeout_ms <= 0:
                    return ActionResult(
                        extracted_content="Error: detect_timeout_ms must be a positive integer value in milliseconds.",
                        include_in_memory=False,
                    )
                params["detectTimeout"] = detect_timeout_ms

            if options:
                options_payload = []
                for option in options:
                    if isinstance(option, CaptchaOption):
                        options_payload.append(option.model_dump(exclude_none=True))
                    elif isinstance(option, dict):
                        options_payload.append(option)
                    else:
                        return ActionResult(
                            extracted_content="Error: options must be a list of objects.",
                            include_in_memory=False,
                        )
                params["options"] = options_payload

            span.set_attribute("captcha.detect_timeout_ms", detect_timeout_ms or 0)
            span.set_attribute("captcha.has_options", bool(options))

            try:
                cdp_session = await browser_session.get_or_create_cdp_session()
                result = await cdp_session.cdp_client.send_raw(
                    "Captcha.solve",
                    params=params,
                    session_id=cdp_session.session_id,
                )
            except Exception as exc:
                logger.exception("Captcha.solve CDP command failed")
                return ActionResult(
                    extracted_content=f"Captcha.solve failed: {exc}",
                    include_in_memory=False,
                )

            if not isinstance(result, dict):
                return ActionResult(
                    extracted_content="Captcha.solve returned an unexpected response.",
                    include_in_memory=False,
                )

            status = result.get("status")
            captcha_type = result.get("type")
            error = result.get("error")

            message_parts = [f"Captcha solve status: {status or 'unknown'}"]
            if captcha_type:
                message_parts.append(f"type: {captcha_type}")
            if error:
                message_parts.append(f"error: {error}")

            return ActionResult(
                extracted_content="; ".join(message_parts),
                include_in_memory=True,
            )
