"""Password reset bridge views that keep raw reset tokens out of rendered URLs."""

import re

from allauth.account.views import LoginView, SignupView
from allauth.account.internal.decorators import login_not_required
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView, View
from util.urls import append_query_params


PASSWORD_RESET_BRIDGE_SESSION_KEY = "_password_reset_bridge"
PASSWORD_RESET_BRIDGE_INVALID_SENTINEL = "invalid"
_UID_RE = re.compile(r"^[0-9A-Za-z]+$")


def _parse_opaque_key(opaque_key: str) -> tuple[str, str] | None:
    uidb36, separator, token = opaque_key.partition("-")
    if not separator or not uidb36 or not token or not _UID_RE.fullmatch(uidb36):
        return None
    return uidb36, token


@method_decorator(login_not_required, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PasswordResetBridgeStartView(View):
    def get(self, request: HttpRequest, key: str, *args, **kwargs) -> HttpResponse:
        # Keep the opaque key off any rendered page so analytics/scripts cannot
        # observe it through the current URL or Referer chain.
        request.session[PASSWORD_RESET_BRIDGE_SESSION_KEY] = (
            key if _parse_opaque_key(key) else PASSWORD_RESET_BRIDGE_INVALID_SENTINEL
        )
        return redirect("account_reset_password_bridge_confirm")


@method_decorator(login_not_required, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PasswordResetBridgeConfirmView(TemplateView):
    template_name = "account/password_reset_bridge.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        opaque_key = self.request.session.get(PASSWORD_RESET_BRIDGE_SESSION_KEY)
        context["can_continue"] = bool(opaque_key) and opaque_key != PASSWORD_RESET_BRIDGE_INVALID_SENTINEL
        return context


@method_decorator(login_not_required, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PasswordResetBridgeContinueView(View):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        opaque_key = request.session.pop(PASSWORD_RESET_BRIDGE_SESSION_KEY, None)
        parsed_key = None
        if opaque_key and opaque_key != PASSWORD_RESET_BRIDGE_INVALID_SENTINEL:
            parsed_key = _parse_opaque_key(opaque_key)

        if not parsed_key:
            return HttpResponseRedirect(reverse("account_reset_password"))

        uidb36, token = parsed_key

        return HttpResponseRedirect(
            reverse(
                "account_reset_password_from_key",
                kwargs={"uidb36": uidb36, "key": token},
            )
        )


class ModalAuthViewMixin:
    auth_active_tab = "signup"

    def _build_modal_url(self, route_name: str) -> str:
        url = reverse(route_name)
        redirect_to = (
            self.request.POST.get(REDIRECT_FIELD_NAME)
            or self.request.GET.get(REDIRECT_FIELD_NAME)
            or ""
        ).strip()
        if redirect_to:
            url = append_query_params(url, {REDIRECT_FIELD_NAME: redirect_to})
        return url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "auth_modal": True,
                "auth_active_tab": self.auth_active_tab,
                "auth_modal_signup_url": self._build_modal_url("account_signup_modal"),
                "auth_modal_login_url": self._build_modal_url("account_login_modal"),
                "auth_popup_complete_url": reverse("account_auth_popup_complete"),
            }
        )
        return context


class AccountSignupModalView(ModalAuthViewMixin, SignupView):
    template_name = "account/modal_signup.html"
    auth_active_tab = "signup"


class AccountLoginModalView(ModalAuthViewMixin, LoginView):
    template_name = "account/modal_login.html"
    auth_active_tab = "login"


@method_decorator(login_not_required, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class AccountAuthPopupCompleteView(TemplateView):
    template_name = "account/auth_popup_complete.html"
