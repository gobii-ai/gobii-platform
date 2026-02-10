import logging
from typing import Dict, Iterable, Tuple
from urllib.parse import urlencode

from pages.mini_mode import (
    campaign_matches_mini_mode,
    set_mini_mode_cookie,
    set_request_mini_mode,
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
    CLICK_ID_PARAMS: Tuple[str, ...] = ("gclid", "gbraid", "wbraid", "msclkid", "ttclid")
    EXTRA_PARAMS: Tuple[str, ...] = ("fbclid",)

    SESSION_UTM_FIRST = "utm_first_touch"
    SESSION_UTM_LAST = "utm_last_touch"
    SESSION_CLICK_FIRST = "click_ids_first"
    SESSION_CLICK_LAST = "click_ids_last"
    SESSION_FBCLID_FIRST = "fbclid_first"
    SESSION_FBCLID_LAST = "fbclid_last"
    SESSION_QUERYSTRING = "utm_querystring"

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
        if request.method == "GET":
            should_set_mini_mode_cookie = self._capture_params(request)

        response = self.get_response(request)
        if should_set_mini_mode_cookie and hasattr(response, "set_cookie"):
            set_mini_mode_cookie(response, request)
        return response

    def _capture_params(self, request) -> bool:
        params = request.GET
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
