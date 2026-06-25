import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
from browser_use.browser import BrowserProfile, BrowserSession

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from api.agent.browser_actions.captcha_solver import (
    CAPSOLVER_DEFAULT_MAX_WAIT_SEC,
    CAPSOLVER_DEFAULT_POLL_INTERVAL_SEC,
    _build_task_payload,
    _capsolver_create_task,
    _capsolver_error_message,
    _capsolver_poll_result,
    _detect_captcha_with_timeout,
    _inject_captcha_token,
)
from config import settings


DEFAULT_URL = "https://gobii.ai/accounts/login/"


TURNSTILE_HOOK_SCRIPT = r"""
(() => {
  window.__gobiiTurnstileDebug = {
    renderCalls: [],
    callbacks: [],
    errors: [],
  };

  const wrapTurnstile = (turnstile) => {
    if (!turnstile || turnstile.__gobiiWrapped) {
      return turnstile;
    }
    const originalRender = turnstile.render && turnstile.render.bind(turnstile);
    if (originalRender) {
      turnstile.render = function(container, params) {
        const index = window.__gobiiTurnstileDebug.renderCalls.length;
        window.__gobiiTurnstileDebug.renderCalls.push({
          index,
          container: typeof container === 'string' ? container : (container && container.outerHTML || null),
          sitekey: params && params.sitekey || null,
          action: params && params.action || null,
          cData: params && (params.cData || params.cdata) || null,
          hasCallback: Boolean(params && params.callback),
          hasErrorCallback: Boolean(params && params['error-callback']),
          hasExpiredCallback: Boolean(params && params['expired-callback']),
        });
        if (params && typeof params.callback === 'function') {
          const originalCallback = params.callback;
          params.callback = function(token) {
            window.__gobiiTurnstileDebug.callbacks.push({
              index,
              tokenLength: token ? String(token).length : 0,
              tokenPrefix: token ? String(token).slice(0, 16) : null,
            });
            return originalCallback.apply(this, arguments);
          };
        }
        return originalRender(container, params);
      };
    }
    Object.defineProperty(turnstile, '__gobiiWrapped', { value: true });
    return turnstile;
  };

  let storedTurnstile = window.turnstile;
  Object.defineProperty(window, 'turnstile', {
    configurable: true,
    get() {
      return storedTurnstile;
    },
    set(value) {
      storedTurnstile = wrapTurnstile(value);
    },
  });
  if (storedTurnstile) {
    storedTurnstile = wrapTurnstile(storedTurnstile);
  }
})();
"""


async def _add_turnstile_hook(browser_session: BrowserSession) -> None:
    cdp_session = await browser_session.get_or_create_cdp_session()
    await cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
        params={"source": TURNSTILE_HOOK_SCRIPT},
        session_id=cdp_session.session_id,
    )


async def _page_snapshot(page) -> dict:
    raw = await page.evaluate(
        r"""
        () => {
          const fields = Array.from(document.querySelectorAll(
            'textarea[name="cf-turnstile-response"], input[name="cf-turnstile-response"], #cf-turnstile-response, textarea[name="g-recaptcha-response"], input[name="g-recaptcha-response"]'
          )).map((el) => ({
            tag: el.tagName,
            id: el.id || null,
            name: el.getAttribute('name'),
            valueLength: el.value ? el.value.length : 0,
            valuePrefix: el.value ? el.value.slice(0, 16) : null,
          }));
          const widgets = Array.from(document.querySelectorAll('.cf-turnstile, [data-sitekey]')).map((el) => ({
            tag: el.tagName,
            className: el.className || null,
            sitekey: el.getAttribute('data-sitekey'),
            action: el.getAttribute('data-action'),
            cdata: el.getAttribute('data-cdata'),
            text: (el.innerText || '').slice(0, 120),
            htmlPrefix: (el.outerHTML || '').slice(0, 300),
          }));
          const iframes = Array.from(document.querySelectorAll('iframe')).map((el) => ({
            title: el.getAttribute('title'),
            src: el.getAttribute('src'),
            hidden: el.hidden,
            width: el.getAttribute('width'),
            height: el.getAttribute('height'),
          })).filter((frame) => (frame.src || '').includes('turnstile') || (frame.title || '').toLowerCase().includes('cloudflare'));
          const submit = document.querySelector('[data-turnstile-submit]');
          return {
            url: location.href,
            title: document.title,
            bodyTextPrefix: (document.body && document.body.innerText || '').slice(0, 500),
            submit: submit ? {
              disabled: submit.disabled,
              ariaDisabled: submit.getAttribute('aria-disabled'),
              className: submit.className,
              text: (submit.innerText || '').trim(),
            } : null,
            fields,
            widgets,
            iframes,
            turnstileDebug: window.__gobiiTurnstileDebug || null,
          };
        }
        """
    )
    return _coerce_evaluate_object(raw)


async def _write_artifacts(browser_session: BrowserSession, artifact_dir: Path, label: str, data: dict) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{label}.json").write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    try:
        await browser_session.take_screenshot(path=str(artifact_dir / f"{label}.png"), full_page=True)
    except Exception as exc:
        print(f"[debug] failed to write screenshot {label}: {exc}")


def _redact_payload(payload: object) -> object:
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            if key.lower() in {"token", "grecaptcharesponse"} and isinstance(value, str):
                redacted[key] = {
                    "present": True,
                    "length": len(value),
                    "prefix": value[:16],
                }
            else:
                redacted[key] = _redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [_redact_payload(value) for value in payload]
    return payload


def _coerce_evaluate_object(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise TypeError(f"Expected page.evaluate object result, got {type(raw).__name__}")


async def run(args: argparse.Namespace) -> int:
    if not settings.CAPSOLVER_API_KEY:
        print("CAPSOLVER_API_KEY is not configured.")
        return 2

    artifact_dir = Path(args.artifact_dir).resolve()
    user_data_dir = tempfile.mkdtemp(prefix="gobii_captcha_debug_")
    print(f"[debug] artifact_dir={artifact_dir}")
    print(f"[debug] user_data_dir={user_data_dir}")

    profile = BrowserProfile(
        stealth=True,
        headless=args.headless,
        user_data_dir=user_data_dir,
        timeout=30_000,
        no_viewport=True,
        captcha_solver=False,
    )
    browser_session = BrowserSession(browser_profile=profile)

    try:
        await browser_session.start()
        await _add_turnstile_hook(browser_session)
        page = await browser_session.must_get_current_page()

        print(f"[debug] navigating to {args.url}")
        await page.goto(args.url)
        await asyncio.sleep(args.initial_wait)
        page = await browser_session.must_get_current_page()

        before = await _page_snapshot(page)
        await _write_artifacts(browser_session, artifact_dir, "before_solve", before)
        print("[debug] before_solve:")
        print(json.dumps(before, indent=2, sort_keys=True))

        detection = await _detect_captcha_with_timeout(page, args.detect_timeout_ms)
        print(f"[debug] detection={detection}")
        task_payload, task_error = _build_task_payload(detection, await page.get_url(), {"type": args.type})
        if task_error:
            print(f"[debug] task_error={task_error}")
            return 3
        print(f"[debug] task_payload={json.dumps(task_payload, sort_keys=True)}")

        async with httpx.AsyncClient() as client:
            create_payload = await _capsolver_create_task(client, settings.CAPSOLVER_API_KEY, task_payload)
            print(f"[debug] create_payload={json.dumps(create_payload, sort_keys=True)}")
            error_message = _capsolver_error_message(create_payload)
            if error_message:
                print(f"[debug] create_error={error_message}")
                return 4

            task_id = create_payload.get("taskId")
            result_payload = await _capsolver_poll_result(
                client,
                settings.CAPSOLVER_API_KEY,
                str(task_id),
                args.poll_interval,
                args.max_wait,
            )
        print(f"[debug] result_payload={json.dumps(_redact_payload(result_payload), sort_keys=True)}")
        error_message = _capsolver_error_message(result_payload)
        if error_message:
            print(f"[debug] result_error={error_message}")
            return 5

        solution = result_payload.get("solution", {}) if isinstance(result_payload, dict) else {}
        token = None
        if isinstance(solution, dict):
            token = solution.get("gRecaptchaResponse") or solution.get("token") or solution.get("text")
        print(f"[debug] token_present={bool(token)} token_length={len(token) if token else 0}")
        if not token:
            return 6

        updated_fields = await _inject_captcha_token(page, token)
        print(f"[debug] updated_fields={updated_fields}")
        await asyncio.sleep(args.after_inject_wait)

        after = await _page_snapshot(page)
        await _write_artifacts(browser_session, artifact_dir, "after_inject", after)
        print("[debug] after_inject:")
        print(json.dumps(after, indent=2, sort_keys=True))

        callback_probe_raw = await page.evaluate(
            r"""
            (token) => {
              const debug = window.__gobiiTurnstileDebug;
              const call = debug && debug.renderCalls && debug.renderCalls[0];
              return {
                hasDebug: Boolean(debug),
                renderCallCount: debug && debug.renderCalls ? debug.renderCalls.length : 0,
                callbackCallCount: debug && debug.callbacks ? debug.callbacks.length : 0,
                firstRenderCall: call || null,
              };
            }
            """,
            token,
        )
        callback_probe = _coerce_evaluate_object(callback_probe_raw)
        print("[debug] callback_probe:")
        print(json.dumps(callback_probe, indent=2, sort_keys=True))

        return 0
    finally:
        if args.keep_open:
            print("[debug] keep_open enabled; press Ctrl+C to stop the browser.")
            try:
                while True:
                    await asyncio.sleep(60)
            except KeyboardInterrupt:
                pass
        await browser_session.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug Gobii login Turnstile solving via CapSolver.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--type", default="cloudflare")
    parser.add_argument("--artifact-dir", default="tmp/captcha_debug")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--initial-wait", type=float, default=5.0)
    parser.add_argument("--after-inject-wait", type=float, default=5.0)
    parser.add_argument("--detect-timeout-ms", type=int, default=10_000)
    parser.add_argument("--poll-interval", type=float, default=CAPSOLVER_DEFAULT_POLL_INTERVAL_SEC)
    parser.add_argument("--max-wait", type=float, default=CAPSOLVER_DEFAULT_MAX_WAIT_SEC)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
