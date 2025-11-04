from django.db import models

class PlanNames:
    FREE = "free"
    STARTUP = "startup"
    SCALE = "pln_l_m_v1"

    # Org Plans
    ORG_TEAM = "org_team"



class PlanNamesChoices(models.TextChoices):
    FREE = PlanNames.FREE, "Free"
    STARTUP = PlanNames.STARTUP, "Startup"
    SCALE = PlanNames.SCALE, "Scale"

    # Org Plans
    ORG_TEAM = PlanNames.ORG_TEAM, "Team"


class UserPlanNamesChoices(models.TextChoices):
    FREE = PlanNames.FREE, "Free"
    STARTUP = PlanNames.STARTUP, "Startup"
    SCALE = PlanNames.SCALE, "Scale"


class OrganizationPlanNamesChoices(models.TextChoices):
    FREE = PlanNames.FREE, "Free"
    ORG_TEAM = PlanNames.ORG_TEAM, "Team"
