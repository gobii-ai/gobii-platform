import random, time
from django.conf import settings

FBP_EXCLUDED_PATHS = frozenset(
    {
        "/install.sh",
        "/llms-full.txt",
        "/llms.txt",
        "/manifest.json",
        "/robots.txt",
        "/sitemap.xml",
    }
)


def _should_skip_fbp(request):
    return getattr(request, "path_info", "") in FBP_EXCLUDED_PATHS


def get_or_make_fbp(request):
    """
    Generates or retrieves the Facebook Browser Pixel (fbp) identifier.

    This function attempts to acquire the fbp identifier from the request's cookies.
    If the identifier does not exist, it generates a new one and surfaces it on
    the current request without creating an anonymous Django session.

    Arguments:
        request: A Django HttpRequest object which provides access to cookies and
                 session data.

    Returns:
        str: The Facebook Browser Pixel (fbp) identifier.
    """
    fbp = (
        request.COOKIES.get(settings.FBP_COOKIE_NAME)
        or getattr(request, "fbp", None)
    )
    if not fbp:
        fbp = f"fb.1.{int(time.time() * 1000)}.{random.randint(10**9, 10**10 - 1)}"
        setattr(request, "fbp", fbp)
        # Request.COOKIES is request-scoped; updating it lets downstream
        # attribution code see the generated value before the browser returns it.
        try:
            request.COOKIES[settings.FBP_COOKIE_NAME] = fbp
        except (AttributeError, TypeError):
            pass
    else:
        # normalize onto request for convenience
        setattr(request, "fbp", fbp)
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
        if _should_skip_fbp(request):
            return self.get_response(request)

        # Check consent before generating/setting
        had_cookie = settings.FBP_COOKIE_NAME in request.COOKIES

        # Ensure an fbp exists and is visible to downstream code right now
        fbp = get_or_make_fbp(request)

        response = self.get_response(request)

        # If the browser didn't send one, set it on the response
        if not had_cookie and fbp:
            response.set_cookie(
                settings.FBP_COOKIE_NAME,
                fbp,
                max_age=settings.FBP_MAX_AGE,
                secure=True,
                samesite="Lax",
                httponly=False,  # JS needs to read it for client-side events
            )

        return response
