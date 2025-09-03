# utils/mixins.py
from pyexpat.errors import messages

import logging
from django.db.utils import IntegrityError
from django.shortcuts import redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from api.models import UserPhoneNumber
from console.forms import PhoneVerifyForm, PhoneAddForm
from util import sms
from django.utils import timezone

logger = logging.getLogger(__name__)


class PhoneNumberMixin:
    """
    Drop this into any CBV (TemplateView, FormView, etc.) that should let the
    user add / verify / delete an SMS number inline.
    """

    # --- helpers -------------------------------------------------------------

    def _get_cooldown_remaining(self, phone, cooldown_seconds: int = 60) -> int:
        """Compute remaining resend cooldown in seconds for a given phone.

        Returns 0 if no cooldown applies (no last attempt, already verified, etc.).
        """
        if not phone or phone.is_verified:
            return 0
        if not phone.last_verification_attempt:
            return 0
        elapsed = (timezone.now() - phone.last_verification_attempt).total_seconds()
        return max(0, int(cooldown_seconds - elapsed))

    def _current_phone(self):
        return UserPhoneNumber.objects.filter(
            user=self.request.user, is_primary=True
        ).first()

    def phone_block_context(self):
        phone = self._current_phone()
        cooldown_remaining = self._get_cooldown_remaining(phone)
        if phone:
            add_form    = None
            verify_form = (
                PhoneVerifyForm(
                    initial={"phone_number": phone.phone_number},
                    user=self.request.user,
                )
                if not phone.is_verified else None
            )
        else:
            add_form    = PhoneAddForm(user=self.request.user)
            verify_form = None

        return {
            "phone": phone,
            "add_form": add_form,
            "verify_form": verify_form,
            "post_url": self.request.path,   # posts back to this view
            "cooldown_remaining": cooldown_remaining,
        }

    def _render_phone_partial(self, error=None):
        context = self.phone_block_context()

        if error:
            context["error"] = error

        html = render_to_string("partials/_sms_form.html",
                                context,
                                self.request)
        return HttpResponse(html)

    # --- override CBV hooks --------------------------------------------------

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.phone_block_context())
        return ctx

    def _handle_phone_post(self):
        """
        Process add / verify / delete actions. Return an HttpResponse if handled,
        or None to let the subclass continue normal processing.
        """
        if self.request.headers.get("HX-Request") != "true":
            return None

        req = self.request
        user = req.user
        phone = self._current_phone()

        # DELETE
        if "delete_phone" in req.POST:
            if phone:
                phone.delete()
            return self._render_phone_partial() if req.headers.get("HX-Request") else redirect(req.path)

        # RESEND VERIFICATION
        if req.POST.get("resend_code") == "1":
            if phone and not phone.is_verified:
                # Enforce 60s cooldown based on DB timestamp
                remaining = self._get_cooldown_remaining(phone)

                if remaining == 0:
                    try:
                        sid = sms.start_verification(phone_number=phone.phone_number)
                    except Exception as e:
                        logger.error(f"Error resending verification for {phone.phone_number}: {e}")
                        sid = None
                    # Update DB timestamp (DB is source of truth)
                    phone.last_verification_attempt = timezone.now()
                    phone.verification_sid = sid
                    phone.save(update_fields=["last_verification_attempt", "verification_sid", "updated_at"])

            return self._render_phone_partial() if req.headers.get("HX-Request") else redirect(req.path)

        # VERIFY CODE
        if "verification_code" in req.POST:
            form = PhoneVerifyForm(req.POST, user=user)
            if form.is_valid():
                form.save()
            return self._render_phone_partial() if req.headers.get("HX-Request") else redirect(req.path)
            # fall through: invalid

        # ADD PHONE
        if "phone_number_hidden" in req.POST:       # hidden field always present
            form = PhoneAddForm(req.POST, user=user)
            if form.is_valid():
                try:
                    form.save()
                except IntegrityError as e:
                    return self._render_phone_partial(error="This phone number is already in use.") if req.headers.get("HX-Request") else redirect(req.path)
            else:
                # Provide a minimal error message without listing supported countries
                # Check error codes rather than brittle string matching
                unsupported_msg = "Phone numbers from this country are not yet supported."
                error_msg = unsupported_msg
                try:
                    data = form.errors.as_data()
                    has_unsupported = False
                    for _field, errors in data.items():
                        for err in errors:
                            if getattr(err, "code", None) == "unsupported_region":
                                has_unsupported = True
                                break
                        if has_unsupported:
                            break
                    if not has_unsupported:
                        error_msg = "Enter a valid phone number."
                except Exception:
                    error_msg = unsupported_msg

                return self._render_phone_partial(error=error_msg) if req.headers.get("HX-Request") else redirect(req.path)

            return self._render_phone_partial() if req.headers.get("HX-Request") else redirect(req.path)
            # fall through: invalid

        # Not a phone action or invalid → let caller handle
        return None

    def post(self, request, *args, **kwargs):
        resp = self._handle_phone_post()
        if resp:
            return resp
        return super().post(request, *args, **kwargs)
