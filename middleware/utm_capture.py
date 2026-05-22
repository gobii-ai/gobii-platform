import logging
from typing import Dict, Iterable, Tuple
from urllib.parse import urlencode

from pages.mini_mode import (
    campaign_matches_mini_mode,
    set_mini_mode_cookie,
    set_request_mini_mode,
)
from pages.homepage_cache_safety import is_cache_safe_anonymous_homepage_request
from util.attribution_referrers import (
    clean_acquisition_referrer,
    is_internal_referrer,
    referrer_hostname,
)

logger = logging.getLogger(__name__)


class UTMTrackingMiddleware:
    """Persist UTM/click IDs in the session so redirects don't drop attribution."""

    UTM_PARAMS: Tuple[str, ...] = (
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
    )
    CLICK_ID_PARAMS: Tuple[str, ...] = ("gclid", "gbraid", "wbraid", "msclkid", "ttclid", "rdt_cid")
    EXTRA_PARAMS: Tuple[str, ...] = ("fbclid",)

    SESSION_UTM_FIRST = "utm_first_touch"
    SESSION_UTM_LAST = "utm_last_touch"
    SESSION_CLICK_FIRST = "click_ids_first"
    SESSION_CLICK_LAST = "click_ids_last"
    SESSION_FBCLID_FIRST = "fbclid_first"
    SESSION_FBCLID_LAST = "fbclid_last"
    SESSION_QUERYSTRING = "utm_querystring"
    SESSION_FIRST_REFERRER = "first_referrer"
    SESSION_LAST_REFERRER = "last_referrer"
    SESSION_FIRST_PATH = "first_path"
    SESSION_LAST_PATH = "last_path"

    # Referral tracking session keys
    SESSION_REFERRER_CODE = "referrer_code"
    SESSION_SIGNUP_TEMPLATE_CODE = "signup_template_code"

    PROPAGATION_ORDER: Tuple[str, ...] = (
        *UTM_PARAMS,
        *CLICK_ID_PARAMS,
        *EXTRA_PARAMS,
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        should_set_mini_mode_cookie = False
        if request.method == "GET" and not is_cache_safe_anonymous_homepage_request(request):
            has_attribution_params = self.has_attribution_params(request.GET)
            self.capture_referrer_context(
                request,
                has_attribution_params=has_attribution_params,
            )
            should_set_mini_mode_cookie = self._capture_params(request)

        response = self.get_response(request)
        if should_set_mini_mode_cookie and hasattr(response, "set_cookie"):
            set_mini_mode_cookie(response, request)
        return response

    def _capture_params(self, request) -> bool:
        return self.capture_params(request, request.GET)

    def has_attribution_params(self, params) -> bool:
        if not params:
            return False
        if self._clean_params(params, self.UTM_PARAMS):
            return True
        if self._clean_params(params, self.CLICK_ID_PARAMS):
            return True
        if (params.get("rdt_click_id") or "").strip():
            return True
        if (params.get("fbclid") or "").strip():
            return True
        if (params.get("ref") or "").strip():
            return True
        return False

    def _is_same_host_referrer(self, request, referrer: str) -> bool:
        hostname = referrer_hostname(referrer)
        if not hostname:
            return False

        request_host = (request.get_host() or "").split(":", 1)[0].strip().lower().rstrip(".")
        return bool(request_host and hostname == request_host)

    def _external_acquisition_referrer(self, request) -> str:
        referrer = clean_acquisition_referrer(request.META.get("HTTP_REFERER"))
        if not referrer:
            return ""
        if self._is_same_host_referrer(request, referrer):
            return ""
        if is_internal_referrer(referrer):
            return ""
        return referrer

    def capture_referrer_context(self, request, *, has_attribution_params: bool = False) -> bool:
        referrer = self._external_acquisition_referrer(request)
        if not referrer and not has_attribution_params:
            return False

        session = request.session
        modified = False

        current_path = request.get_full_path()
        if current_path:
            if not session.get(self.SESSION_FIRST_PATH):
                session[self.SESSION_FIRST_PATH] = current_path
                modified = True
            if session.get(self.SESSION_LAST_PATH) != current_path:
                session[self.SESSION_LAST_PATH] = current_path
                modified = True

        if referrer:
            if not session.get(self.SESSION_FIRST_REFERRER):
                session[self.SESSION_FIRST_REFERRER] = referrer
                modified = True
            if session.get(self.SESSION_LAST_REFERRER) != referrer:
                session[self.SESSION_LAST_REFERRER] = referrer
                modified = True

        if modified:
            session.modified = True
        return modified

    def capture_params(self, request, params) -> bool:
        if not params:
            return False

        session = request.session
        session_modified = False
        should_set_mini_mode_cookie = False

        utm_values = self._clean_params(params, self.UTM_PARAMS)
        if utm_values:
            session_modified |= self._persist_first_last(
                session,
                self.SESSION_UTM_FIRST,
                self.SESSION_UTM_LAST,
                utm_values,
            )
            if campaign_matches_mini_mode(utm_values.get("utm_campaign")):
                set_request_mini_mode(request)
                should_set_mini_mode_cookie = True

        click_values = self._clean_params(params, self.CLICK_ID_PARAMS)
        if not click_values.get("rdt_cid"):
            rdt_click_id = (params.get("rdt_click_id") or "").strip()
            if rdt_click_id:
                click_values["rdt_cid"] = rdt_click_id
        if click_values:
            session_modified |= self._persist_first_last(
                session,
                self.SESSION_CLICK_FIRST,
                self.SESSION_CLICK_LAST,
                click_values,
            )

        fbclid_value = (params.get("fbclid") or "").strip()
        if fbclid_value:
            if not session.get(self.SESSION_FBCLID_FIRST):
                session[self.SESSION_FBCLID_FIRST] = fbclid_value
                session_modified = True
            if session.get(self.SESSION_FBCLID_LAST) != fbclid_value:
                session[self.SESSION_FBCLID_LAST] = fbclid_value
                session_modified = True

        # Capture direct referral code (?ref=CODE)
        # "Last one wins": if user clicks a ref link, clear any template referral
        ref_code = (params.get("ref") or "").strip()
        if ref_code:
            previous_code = session.get(self.SESSION_REFERRER_CODE)
            if previous_code != ref_code:
                previous_template = session.pop(self.SESSION_SIGNUP_TEMPLATE_CODE, None)
                session[self.SESSION_REFERRER_CODE] = ref_code
                session_modified = True

                # Track referral code capture (deferred to avoid import at module level)
                try:
                    from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
                    session_key = session.session_key if hasattr(session, 'session_key') else None
                    if session_key:
                        Analytics.track_event_anonymous(
                            anonymous_id=str(session_key),
                            event=AnalyticsEvent.REFERRAL_CODE_CAPTURED,
                            source=AnalyticsSource.WEB,
                            properties={
                                'referrer_code': ref_code,
                                'previous_referrer_code': previous_code or '',
                                'previous_template_code': previous_template or '',
                            },
                        )
                except Exception:
                    logger.debug("Failed to track referral code capture", exc_info=True)

        if session_modified:
            session[self.SESSION_QUERYSTRING] = self._build_querystring(session)
            session.modified = True
        return should_set_mini_mode_cookie

    def _clean_params(
        self, query_params, keys: Iterable[str]
    ) -> Dict[str, str]:
        cleaned: Dict[str, str] = {}
        for key in keys:
            value = (query_params.get(key) or "").strip()
            if value:
                cleaned[key] = value
        return cleaned

    def _persist_first_last(
        self,
        session,
        first_key: str,
        last_key: str,
        new_values: Dict[str, str],
    ) -> bool:
        modified = False

        first_existing = dict(session.get(first_key) or {})
        if not first_existing:
            session[first_key] = new_values.copy()
            modified = True
        else:
            updated_first = first_existing.copy()
            for key, value in new_values.items():
                if key not in updated_first:
                    updated_first[key] = value
            if updated_first != first_existing:
                session[first_key] = updated_first
                modified = True

        previous_last = dict(session.get(last_key) or {})
        updated_last = previous_last.copy()
        updated_last.update(new_values)
        if updated_last != previous_last:
            session[last_key] = updated_last
            modified = True

        return modified

    def _build_querystring(self, session) -> str:
        combined: Dict[str, str] = {}
        combined.update(session.get(self.SESSION_UTM_FIRST) or {})
        combined.update(session.get(self.SESSION_UTM_LAST) or {})

        click_values = dict(session.get(self.SESSION_CLICK_FIRST) or {})
        click_values.update(session.get(self.SESSION_CLICK_LAST) or {})
        combined.update(click_values)

        fbclid = session.get(self.SESSION_FBCLID_LAST) or session.get(
            self.SESSION_FBCLID_FIRST
        )

        if fbclid:
            combined["fbclid"] = fbclid

        ordered_pairs = [
            (key, combined[key])
            for key in self.PROPAGATION_ORDER
            if combined.get(key)
        ]
        return urlencode(ordered_pairs)
