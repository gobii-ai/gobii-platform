from django.db import models

class PlanNames:
    FREE = "free"
    STARTUP = "startup"

    # Org Plans
    ORG_TEAM = "org_team"



class PlanNamesChoices(models.TextChoices):
    FREE = PlanNames.FREE, "Free"
    STARTUP = PlanNames.STARTUP, "Startup"

    # Org Plans
    ORG_TEAM = PlanNames.ORG_TEAM, "Team"