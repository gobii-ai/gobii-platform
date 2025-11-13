import random, time
from django.conf import settings

FBP_COOKIE_NAME = "_fbp"
FBP_MAX_AGE = 90 * 24 * 60 * 60  # 90 days

def get_or_make_fbp(request):
    fbp = request.COOKIES.get(settings.FBP_COOKIE_NAME) or request.session.get(settings.FBP_COOKIE_NAME)
    if not fbp:
        fbp = f"fb.1.{int(time.time() * 1000)}.{random.randint(10**9, 10**10 - 1)}"
        request.session[settings.FBP_COOKIE_NAME] = fbp
    return fbp

class FbpMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check consent before generating/setting
        fbp = request.COOKIES.get(settings.FBP_COOKIE_NAME)
        if not fbp:
            fbp = get_or_make_fbp(request)

        response = self.get_response(request)

        # If we generated one and don’t already have the cookie, set it
        sess_fbp = request.session.get(settings.FBP_COOKIE_NAME)
        if sess_fbp and settings.FBP_COOKIE_NAME not in request.COOKIES:
            response.set_cookie(
                settings.FBP_COOKIE_NAME,
                sess_fbp,
                max_age=settings.FBP_MAX_AGE,
                secure=True,
                samesite="Lax",
                httponly=False,  # JS needs to read it for client-side events
            )

        return response
