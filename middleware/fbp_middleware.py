import random, time
from django.conf import settings

def get_or_make_fbp(request):
    """
    Generates or retrieves the Facebook Browser Pixel (fbp) identifier.

    This function attempts to acquire the fbp identifier from the request's cookies
    or session storage. If the identifier does not exist, it generates a new one,
    stores it in the session for future use, and returns the generated value.

    Arguments:
        request: A Django HttpRequest object which provides access to cookies and
                 session data.

    Returns:
        str: The Facebook Browser Pixel (fbp) identifier.
    """
    fbp = request.COOKIES.get(settings.FBP_COOKIE_NAME) or request.session.get(settings.FBP_COOKIE_NAME)
    if not fbp:
        fbp = f"fb.1.{int(time.time() * 1000)}.{random.randint(10**9, 10**10 - 1)}"
        request.session[settings.FBP_COOKIE_NAME] = fbp
    return fbp

class FbpMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """
        Handles the generation and management of the 'fbp' cookie for user requests.

        The __call__ method ensures that the 'fbp' cookie is appropriately set in the
        user's browser response if it doesn't already exist in incoming cookies. It
        handles both the generation of the 'fbp' identifier and the setting of the
        cookie on the response object, allowing proper session tracking and integration
        with client-side events.

        Parameters:
            request: HttpRequest
                The incoming HTTP request object from the user.

        Returns:
            HttpResponse
                The modified HTTP response object that includes the necessary 'fbp'
                cookie settings, if applicable.
        """
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
