import asyncio

import httpx
from django.test import SimpleTestCase, tag

from api.agent.browser_actions.captcha_solver import (
    CAPSOLVER_CREATE_TASK_URL,
    CAPSOLVER_GET_TASK_URL,
    _CaptchaDetection,
    _build_task_payload,
    _capsolver_create_task,
    _capsolver_poll_result,
    register_captcha_actions,
)


@tag("batch_browser_config")
class CapSolverTests(SimpleTestCase):
    def test_solve_captcha_registration_terminates_action_sequence(self):
        class FakeController:
            def __init__(self):
                self.actions = []

            def action(self, description, **kwargs):
                def decorator(func):
                    self.actions.append((description, kwargs, func))
                    return func

                return decorator

        controller = FakeController()

        register_captcha_actions(controller)

        self.assertEqual(len(controller.actions), 1)
        description, kwargs, func = controller.actions[0]
        self.assertEqual(description, "Solve CAPTCHA using CapSolver API.")
        self.assertEqual(func.__name__, "solve_captcha")
        self.assertTrue(kwargs["terminates_sequence"])

    def test_build_task_payload_accepts_captcha_type_aliases(self):
        task_payload, error = _build_task_payload(
            detection=None,
            page_url="https://example.com/login",
            selected_task_payload={
                "type": "turnstile",
                "website_key": "0xsitekey",
                "disabled": False,
            },
        )

        self.assertIsNone(error)
        self.assertEqual(
            task_payload,
            {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": "https://example.com/login",
                "websiteKey": "0xsitekey",
            },
        )

    def test_build_task_payload_uses_detected_type_for_ambiguous_cloudflare_challenge(self):
        task_payload, error = _build_task_payload(
            detection=_CaptchaDetection(
                captcha_type="turnstile",
                site_key="0xdetected",
                task_type="AntiTurnstileTaskProxyLess",
            ),
            page_url="https://example.com/login",
            selected_task_payload={
                "type": "cloudflare_challenge",
            },
        )

        self.assertIsNone(error)
        self.assertEqual(
            task_payload,
            {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": "https://example.com/login",
                "websiteKey": "0xdetected",
            },
        )

    def test_create_task_returns_capsolver_error_payload_for_bad_request(self):
        async def run_request():
            def handler(request):
                self.assertEqual(str(request.url), CAPSOLVER_CREATE_TASK_URL)
                return httpx.Response(
                    400,
                    json={
                        "errorId": 1,
                        "errorCode": "ERROR_INVALID_TASK_DATA",
                        "errorDescription": "invalid task data: unsupported task type",
                    },
                    request=request,
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await _capsolver_create_task(
                    client,
                    "secret-key",
                    {
                        "type": "turnstile",
                        "websiteURL": "https://example.com/login",
                        "websiteKey": "0xsitekey",
                    },
                )

        payload = asyncio.run(run_request())

        self.assertEqual(payload["errorId"], 1)
        self.assertEqual(payload["errorCode"], "ERROR_INVALID_TASK_DATA")

    def test_poll_result_returns_capsolver_error_payload_for_bad_request(self):
        async def run_request():
            def handler(request):
                self.assertEqual(str(request.url), CAPSOLVER_GET_TASK_URL)
                return httpx.Response(
                    400,
                    json={
                        "errorId": 1,
                        "errorCode": "ERROR_TASKID_INVALID",
                        "errorDescription": "Task ID does not exist or is invalid",
                    },
                    request=request,
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await _capsolver_poll_result(
                    client,
                    "secret-key",
                    "bad-task-id",
                    poll_interval_sec=0,
                    max_wait_sec=1,
                )

        payload = asyncio.run(run_request())

        self.assertEqual(payload["errorId"], 1)
        self.assertEqual(payload["errorCode"], "ERROR_TASKID_INVALID")
