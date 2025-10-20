from __future__ import annotations

from typing import Dict, Iterable, Tuple
from urllib.parse import urlencode


class UTMTrackingMiddleware:
    """Persist UTM/click IDs in the session so redirects don’t drop attribution."""

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

    PROPAGATION_ORDER: Tuple[str, ...] = (
        *UTM_PARAMS,
        *CLICK_ID_PARAMS,
        *EXTRA_PARAMS,
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET":
            self._capture_params(request)
        return self.get_response(request)

    def _capture_params(self, request) -> None:
        params = request.GET
        if not params:
            return

        session = request.session
        session_modified = False

        utm_values = self._clean_params(params, self.UTM_PARAMS)
        if utm_values:
            session_modified |= self._persist_first_last(
                session,
                self.SESSION_UTM_FIRST,
                self.SESSION_UTM_LAST,
                utm_values,
            )

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

        if session_modified:
            session[self.SESSION_QUERYSTRING] = self._build_querystring(session)
            session.modified = True

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

        click_values = session.get(self.SESSION_CLICK_FIRST) or {}
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
