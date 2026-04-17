"""Password reset bridge views that keep raw reset tokens out of rendered URLs."""

import re

from django import forms
from allauth.account.views import LoginView, SignupView
from allauth.account.internal.decorators import login_not_required
from django.contrib.auth import get_user_model
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView, View
from util.onboarding import is_truthy_flag
from util.urls import append_query_params


PASSWORD_RESET_BRIDGE_SESSION_KEY = "_password_reset_bridge"
PASSWORD_RESET_BRIDGE_INVALID_SENTINEL = "invalid"
_UID_RE = re.compile(r"^[0-9A-Za-z]+$")


class ModalEmailStartForm(forms.Form):
    email = forms.EmailField()


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

    def _get_redirect_value(self) -> str:
        return (
            self.request.POST.get(REDIRECT_FIELD_NAME)
            or self.request.GET.get(REDIRECT_FIELD_NAME)
            or ""
        ).strip()

    def _get_prefilled_email(self) -> str:
        return (
            self.request.POST.get("email")
            or self.request.GET.get("email")
            or ""
        ).strip()

    def _build_modal_url(self, route_name: str, **params) -> str:
        url = reverse(route_name)
        query_params = {}
        redirect_to = self._get_redirect_value()
        if redirect_to:
            query_params[REDIRECT_FIELD_NAME] = redirect_to
        query_params.update({key: value for key, value in params.items() if value not in (None, "")})
        if query_params:
            url = append_query_params(url, query_params)
        return url

    def get_initial(self):
        initial = super().get_initial()
        prefilled_email = self._get_prefilled_email()
        if prefilled_email:
            initial.setdefault("email", prefilled_email)
            initial.setdefault("login", prefilled_email)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prefilled_email = self._get_prefilled_email()
        context.update(
            {
                "auth_modal": True,
                "auth_active_tab": self.auth_active_tab,
                "auth_modal_signup_url": self._build_modal_url("account_signup_modal"),
                "auth_modal_login_url": self._build_modal_url("account_login_modal"),
                "auth_modal_start_url": self._build_modal_url(
                    "account_signup_modal",
                    email=prefilled_email or None,
                ),
                "auth_popup_complete_url": reverse("account_auth_popup_complete"),
                "auth_prefilled_email": prefilled_email,
                "auth_email_locked": is_truthy_flag(
                    self.request.POST.get("lock_email") or self.request.GET.get("lock_email")
                ),
            }
        )
        return context


class AccountSignupModalView(ModalAuthViewMixin, SignupView):
    template_name = "account/modal_signup.html"
    auth_active_tab = "signup"

    def _render_email_start(self, form: ModalEmailStartForm, *, status: int = 200) -> HttpResponse:
        html = render_to_string(
            "account/_modal_email_start_content.html",
            {
                "auth_modal": True,
                "auth_modal_signup_url": self._build_modal_url("account_signup_modal"),
                "auth_popup_complete_url": reverse("account_auth_popup_complete"),
                "redirect_field_name": REDIRECT_FIELD_NAME,
                "redirect_field_value": self._get_redirect_value(),
                "email_start_form": form,
            },
            request=self.request,
        )
        return HttpResponse(html, status=status)

    def _password_step_url(self, email: str) -> str:
        user_exists = get_user_model().objects.filter(email__iexact=email).exists()
        if user_exists:
            return self._build_modal_url(
                "account_login_modal",
                email=email,
                lock_email="1",
            )
        return self._build_modal_url(
            "account_signup_modal",
            step="password",
            email=email,
            lock_email="1",
        )

    def get(self, request, *args, **kwargs):
        if request.GET.get("step") == "password":
            return super().get(request, *args, **kwargs)

        form = ModalEmailStartForm(initial={"email": self._get_prefilled_email()})
        return self._render_email_start(form)

    def post(self, request, *args, **kwargs):
        if is_truthy_flag(request.POST.get("email_first")):
            form = ModalEmailStartForm(request.POST)
            if not form.is_valid():
                html = self._render_email_start(form, status=400).content.decode("utf-8")
                return JsonResponse({"html": html}, status=400)
            return JsonResponse({"auth_url": self._password_step_url(form.cleaned_data["email"])})
        return super().post(request, *args, **kwargs)


class AccountLoginModalView(ModalAuthViewMixin, LoginView):
    template_name = "account/modal_login.html"
    auth_active_tab = "login"


@method_decorator(login_not_required, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class AccountAuthPopupCompleteView(TemplateView):
    template_name = "account/auth_popup_complete.html"
