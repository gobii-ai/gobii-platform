from django.conf import settings

def get_sim_weather_url() -> str:
    """
    Construct the absolute URL for the weather simulation.
    Uses PUBLIC_SITE_URL from settings to handle different environments (local/preview/prod).
    """
    base_url = getattr(settings, "PUBLIC_SITE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/eval/sim/weather/"
