# utils/mixins.py
from pyexpat.errors import messages

from django.db.utils import IntegrityError
from django.shortcuts import redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from api.models import UserPhoneNumber
from console.forms import PhoneVerifyForm, PhoneAddForm


class PhoneNumberMixin:
    """
    Drop this into any CBV (TemplateView, FormView, etc.) that should let the
    user add / verify / delete an SMS number inline.
    """

    # --- helpers -------------------------------------------------------------

    def _current_phone(self):
        return UserPhoneNumber.objects.filter(
            user=self.request.user, is_primary=True
        ).first()

    def phone_block_context(self):
        phone = self._current_phone()
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

            return self._render_phone_partial() if req.headers.get("HX-Request") else redirect(req.path)
            # fall through: invalid

        # Not a phone action or invalid → let caller handle
        return None

    def post(self, request, *args, **kwargs):
        resp = self._handle_phone_post()
        if resp:
            return resp
        return super().post(request, *args, **kwargs)
