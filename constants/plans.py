from django.db import models

class PlanNames:
    FREE = "free"
    STARTUP = "startup"


class PlanNamesChoices(models.TextChoices):
    FREE = PlanNames.FREE, "Free"
    STARTUP = PlanNames.STARTUP, "Startup"