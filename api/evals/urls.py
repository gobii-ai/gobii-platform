from django.urls import path
from api.evals.sim_views import WeatherSimView

app_name = "evals"

urlpatterns = [
    path("sim/weather/", WeatherSimView.as_view(), name="sim_weather"),
]
