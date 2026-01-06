"""
Custom browser use agent action for solving CAPTCHA with CapSolver.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from browser_use import ActionResult
from browser_use.browser import BrowserSession
import httpx
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict

from config import settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

CAPSOLVER_CREATE_TASK_URL = "https://api.capsolver.com/createTask"
CAPSOLVER_GET_TASK_URL = "https://api.capsolver.com/getTaskResult"
CAPSOLVER_REQUEST_TIMEOUT_SEC = 30
CAPSOLVER_DEFAULT_POLL_INTERVAL_SEC = 5
CAPSOLVER_DEFAULT_MAX_WAIT_SEC = 120
CAPTCHA_DETECTION_ERRORS = (RuntimeError, ConnectionError)


@dataclass(frozen=True)
class _CaptchaDetection:
    captcha_type: str
    site_key: str
    task_type: str


class CaptchaOption(BaseModel):
    """Captcha solver option payload with flexible extra fields."""

    model_config = ConfigDict(extra="allow")

    type: str
    disabled: Optional[bool] = None


def _normalize_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "websiteURL" not in normalized and "website_url" in normalized:
        normalized["websiteURL"] = normalized.pop("website_url")
    if "websiteKey" not in normalized and "website_key" in normalized:
        normalized["websiteKey"] = normalized.pop("website_key")
    if "type" not in normalized and "task_type" in normalized:
        normalized["type"] = normalized.pop("task_type")
    normalized.pop("disabled", None)
    return normalized


def _extract_site_key_from_url(src: Optional[str]) -> Optional[str]:
    if not src:
        return None
    try:
        parsed = urlparse(src)
    except ValueError:
        return None
    query = parse_qs(parsed.query)
    for key in ("k", "sitekey", "render"):
        value = query.get(key)
        if value:
            return value[0]
    return None


async def _find_site_key_by_selector(page, selector: str) -> Optional[str]:
    try:
        elements = await page.get_elements_by_css_selector(selector)
    except CAPTCHA_DETECTION_ERRORS:
        logger.debug("Failed querying selector %s for captcha detection", selector, exc_info=True)
        return None
    for element in elements:
        try:
            site_key = await element.get_attribute("data-sitekey")
        except CAPTCHA_DETECTION_ERRORS:
            logger.debug("Failed reading data-sitekey from %s", selector, exc_info=True)
            continue
        if site_key:
            return site_key
    return None


async def _find_site_key_by_iframe(page, token: str) -> Optional[str]:
    try:
        elements = await page.get_elements_by_css_selector(f"iframe[src*='{token}']")
    except CAPTCHA_DETECTION_ERRORS:
        logger.debug("Failed querying iframe selector for token %s", token, exc_info=True)
        return None
    for element in elements:
        try:
            src = await element.get_attribute("src")
        except CAPTCHA_DETECTION_ERRORS:
            logger.debug("Failed reading iframe src for token %s", token, exc_info=True)
            continue
        site_key = _extract_site_key_from_url(src)
        if site_key:
            return site_key
    return None


async def _detect_captcha(page) -> Optional[_CaptchaDetection]:
    site_key = await _find_site_key_by_selector(page, ".g-recaptcha")
    if site_key:
        return _CaptchaDetection(
            captcha_type="recaptcha_v2",
            site_key=site_key,
            task_type="ReCaptchaV2TaskProxyLess",
        )

    site_key = await _find_site_key_by_selector(page, ".cf-turnstile")
    if site_key:
        return _CaptchaDetection(
            captcha_type="turnstile",
            site_key=site_key,
            task_type="AntiTurnstileTaskProxyLess",
        )

    try:
        data_key_elements = await page.get_elements_by_css_selector("[data-sitekey]")
    except CAPTCHA_DETECTION_ERRORS:
        logger.debug("Failed querying data-sitekey elements", exc_info=True)
        data_key_elements = []
    for element in data_key_elements:
        try:
            class_name = (await element.get_attribute("class")) or ""
            site_key = await element.get_attribute("data-sitekey")
        except CAPTCHA_DETECTION_ERRORS:
            logger.debug("Failed reading attributes from data-sitekey element", exc_info=True)
            continue
        if not site_key:
            continue
        class_name = class_name.lower()
        if "turnstile" in class_name:
            return _CaptchaDetection(
                captcha_type="turnstile",
                site_key=site_key,
                task_type="AntiTurnstileTaskProxyLess",
            )
        if "recaptcha" in class_name or "g-recaptcha" in class_name:
            return _CaptchaDetection(
                captcha_type="recaptcha_v2",
                site_key=site_key,
                task_type="ReCaptchaV2TaskProxyLess",
            )

    site_key = await _find_site_key_by_iframe(page, "recaptcha")
    if site_key:
        return _CaptchaDetection(
            captcha_type="recaptcha_v2",
            site_key=site_key,
            task_type="ReCaptchaV2TaskProxyLess",
        )

    site_key = await _find_site_key_by_iframe(page, "turnstile")
    if site_key:
        return _CaptchaDetection(
            captcha_type="turnstile",
            site_key=site_key,
            task_type="AntiTurnstileTaskProxyLess",
        )

    return None


def _capsolver_error_message(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return "CapSolver returned an unexpected response."
    error_id = payload.get("errorId")
    if error_id in (None, 0):
        return None
    error_code = payload.get("errorCode") or payload.get("error")
    error_desc = payload.get("errorDescription") or payload.get("errorMessage")
    if error_code and error_desc:
        return f"{error_code}: {error_desc}"
    if error_desc:
        return str(error_desc)
    if error_code:
        return str(error_code)
    return "CapSolver returned an error response."


async def _capsolver_create_task(
    client: httpx.AsyncClient,
    api_key: str,
    task_payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(
        CAPSOLVER_CREATE_TASK_URL,
        json={"clientKey": api_key, "task": task_payload},
        timeout=CAPSOLVER_REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    return response.json()


async def _capsolver_poll_result(
    client: httpx.AsyncClient,
    api_key: str,
    task_id: str,
    poll_interval_sec: float,
    max_wait_sec: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait_sec
    while True:
        await asyncio.sleep(poll_interval_sec)
        response = await client.post(
            CAPSOLVER_GET_TASK_URL,
            json={"clientKey": api_key, "taskId": task_id},
            timeout=CAPSOLVER_REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status") if isinstance(payload, dict) else None
        if status in ("ready", "failed"):
            return payload
        if time.monotonic() >= deadline:
            if isinstance(payload, dict):
                payload = dict(payload)
            else:
                payload = {}
            payload["status"] = "timeout"
            payload.setdefault("error", "Timed out waiting for CapSolver result.")
            return payload


async def _inject_captcha_token(page, token: str) -> int:
    script = """
        (token) => {
            const selectors = [
                '#g-recaptcha-response',
                'textarea[name="g-recaptcha-response"]',
                'input[name="g-recaptcha-response"]',
                '#recaptcha-token',
                'textarea[name="recaptcha-token"]',
                'input[name="recaptcha-token"]',
                '#cf-turnstile-response',
                'textarea[name="cf-turnstile-response"]',
                'input[name="cf-turnstile-response"]',
                '#turnstile-response',
                'textarea[name="turnstile-response"]',
                'input[name="turnstile-response"]',
            ];
            let updated = 0;
            const applyToken = (el) => {
                if (!el) {
                    return;
                }
                if ('value' in el) {
                    el.value = token;
                } else {
                    el.innerHTML = token;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                updated += 1;
            };
            selectors.forEach((selector) => {
                document.querySelectorAll(selector).forEach(applyToken);
            });
            return updated;
        }
    """
    result = await page.evaluate(script, token)
    try:
        return int(result)
    except (TypeError, ValueError):
        return 0


def _parse_detect_timeout_ms(detect_timeout_ms: Optional[int]) -> tuple[Optional[int], Optional[str]]:
    if detect_timeout_ms is None:
        return None, None
    try:
        timeout_ms = int(detect_timeout_ms)
    except (TypeError, ValueError):
        return None, "Error: detect_timeout_ms must be an integer value in milliseconds."
    if timeout_ms <= 0:
        return None, "Error: detect_timeout_ms must be a positive integer value in milliseconds."
    return timeout_ms, None


def _build_options_payload(
    options: Optional[list[CaptchaOption]],
) -> tuple[list[dict[str, Any]], Optional[str]]:
    if not options:
        return [], None
    options_payload: list[dict[str, Any]] = []
    for option in options:
        if isinstance(option, CaptchaOption):
            options_payload.append(option.model_dump(exclude_none=True))
        elif isinstance(option, dict):
            options_payload.append(dict(option))
        else:
            return [], "Error: options must be a list of objects."
    return options_payload, None


def _select_task_payload(options_payload: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for option in options_payload:
        if option.get("disabled"):
            continue
        return _normalize_task_payload(option)
    return None


async def _detect_captcha_with_timeout(page, detect_timeout_ms: Optional[int]) -> Optional[_CaptchaDetection]:
    if detect_timeout_ms:
        deadline = time.monotonic() + (detect_timeout_ms / 1000)
        while time.monotonic() <= deadline:
            detection = await _detect_captcha(page)
            if detection:
                return detection
            await asyncio.sleep(0.5)
        return None
    return await _detect_captcha(page)


def _build_task_payload(
    detection: Optional[_CaptchaDetection],
    page_url: str,
    selected_task_payload: Optional[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if selected_task_payload is None:
        if not detection:
            return None, "No supported CAPTCHA detected on the page."
        task_payload = {
            "type": detection.task_type,
            "websiteURL": page_url,
            "websiteKey": detection.site_key,
        }
    else:
        task_payload = _normalize_task_payload(selected_task_payload)

    task_payload = _normalize_task_payload(task_payload)
    if "websiteURL" not in task_payload and page_url:
        task_payload["websiteURL"] = page_url
    if "websiteKey" not in task_payload and detection:
        task_payload["websiteKey"] = detection.site_key
    if "type" not in task_payload and detection:
        task_payload["type"] = detection.task_type

    missing_fields = [field for field in ("type", "websiteURL", "websiteKey") if not task_payload.get(field)]
    if missing_fields:
        return None, "Error: CapSolver task is missing required fields: " + ", ".join(missing_fields)

    return task_payload, None


def register_captcha_actions(controller) -> None:
    """Register the CAPTCHA solver action with the given controller."""

    @controller.action("Solve CAPTCHA using CapSolver API.")
    async def solve_captcha(
        browser_session: BrowserSession,
        detect_timeout_ms: Optional[int] = None,
        options: Optional[list[CaptchaOption]] = None,
    ) -> ActionResult:
        """Solve CAPTCHA using CapSolver and inject the solution token."""
        with tracer.start_as_current_span("Browser Agent Solve Captcha") as span:
            api_key = getattr(settings, "CAPSOLVER_API_KEY", "")
            if not api_key:
                return ActionResult(
                    extracted_content="Error: CAPSOLVER_API_KEY is not configured.",
                    include_in_memory=False,
                )

            detect_timeout_ms, error_message = _parse_detect_timeout_ms(detect_timeout_ms)
            if error_message:
                return ActionResult(
                    extracted_content=error_message,
                    include_in_memory=False,
                )

            span.set_attribute("captcha.detect_timeout_ms", detect_timeout_ms or 0)
            span.set_attribute("captcha.has_options", bool(options))
            span.set_attribute("captcha.provider", "capsolver")

            page = await browser_session.get_current_page()
            if page is None:
                return ActionResult(
                    extracted_content="Error: No active page available to solve CAPTCHA.",
                    include_in_memory=False,
                )

            detection = await _detect_captcha_with_timeout(page, detect_timeout_ms)

            options_payload, options_error = _build_options_payload(options)
            if options_error:
                return ActionResult(
                    extracted_content=options_error,
                    include_in_memory=False,
                )

            selected_task_payload = _select_task_payload(options_payload)
            page_url = await page.get_url()
            task_payload, task_error = _build_task_payload(detection, page_url, selected_task_payload)
            if task_error:
                return ActionResult(
                    extracted_content=task_error,
                    include_in_memory=False,
                )

            span.set_attribute("captcha.task_type", task_payload.get("type"))
            span.set_attribute("captcha.website_url", task_payload.get("websiteURL"))
            span.set_attribute("captcha.site_key_present", bool(task_payload.get("websiteKey")))
            if detection:
                span.set_attribute("captcha.detected_type", detection.captcha_type)

            async with httpx.AsyncClient() as client:
                try:
                    create_payload = await _capsolver_create_task(client, api_key, task_payload)
                except httpx.HTTPError as exc:
                    logger.exception("CapSolver createTask request failed")
                    return ActionResult(
                        extracted_content=f"CapSolver createTask failed: {exc}",
                        include_in_memory=False,
                    )

                error_message = _capsolver_error_message(create_payload)
                if error_message:
                    return ActionResult(
                        extracted_content=f"CapSolver createTask error: {error_message}",
                        include_in_memory=False,
                    )

                task_id = create_payload.get("taskId") if isinstance(create_payload, dict) else None
                if not task_id:
                    return ActionResult(
                        extracted_content="CapSolver did not return a taskId.",
                        include_in_memory=False,
                    )
                span.set_attribute("captcha.task_id", str(task_id))

                try:
                    result_payload = await _capsolver_poll_result(
                        client,
                        api_key,
                        str(task_id),
                        CAPSOLVER_DEFAULT_POLL_INTERVAL_SEC,
                        CAPSOLVER_DEFAULT_MAX_WAIT_SEC,
                    )
                except httpx.HTTPError as exc:
                    logger.exception("CapSolver getTaskResult request failed")
                    return ActionResult(
                        extracted_content=f"CapSolver getTaskResult failed: {exc}",
                        include_in_memory=False,
                    )

                error_message = _capsolver_error_message(result_payload)
                if error_message:
                    return ActionResult(
                        extracted_content=f"CapSolver getTaskResult error: {error_message}",
                        include_in_memory=False,
                    )

            status = result_payload.get("status") if isinstance(result_payload, dict) else None
            if status != "ready":
                error_detail = result_payload.get("error") if isinstance(result_payload, dict) else None
                message = f"CapSolver status: {status or 'unknown'}"
                if error_detail:
                    message = f"{message}; error: {error_detail}"
                return ActionResult(
                    extracted_content=message,
                    include_in_memory=False,
                )

            solution = None
            if isinstance(result_payload, dict):
                solution = result_payload.get("solution", {})
            token = None
            if isinstance(solution, dict):
                token = (
                    solution.get("gRecaptchaResponse")
                    or solution.get("token")
                    or solution.get("text")
                )

            if not token:
                return ActionResult(
                    extracted_content="CapSolver returned no solution token.",
                    include_in_memory=False,
                )

            updated_fields = await _inject_captcha_token(page, token)
            span.set_attribute("captcha.token_fields_updated", updated_fields)

            message_parts = ["Captcha solved via CapSolver"]
            if detection:
                message_parts.append(f"type: {detection.captcha_type}")
            message_parts.append(f"token_fields_updated: {updated_fields}")

            return ActionResult(
                extracted_content="; ".join(message_parts),
                include_in_memory=True,
            )
