from django.db import models

# When auto-purchase is disabled (0) or unlimited (-1), we still need a sane UI
# value for "Up to N additional tasks" inputs.
EXTRA_TASKS_DEFAULT_MAX_TASKS = 1000

class PlanNames:
    FREE = "free"
    STARTUP = "startup"
    SCALE = "pln_l_m_v1"

    # Org Plans
    ORG_TEAM = "org_team"

class PlanSlugs:
    FREE = "free"
    STARTUP = "startup"
    SCALE = "scale"

    # Org Plans
    ORG_TEAM = "org_team"


PLAN_SLUG_BY_LEGACY_CODE = {
    PlanNames.FREE: PlanSlugs.FREE,
    PlanNames.STARTUP: PlanSlugs.STARTUP,
    PlanNames.SCALE: PlanSlugs.SCALE,
    PlanNames.ORG_TEAM: PlanSlugs.ORG_TEAM,
}

LEGACY_PLAN_BY_SLUG = {
    PlanSlugs.FREE: PlanNames.FREE,
    PlanSlugs.STARTUP: PlanNames.STARTUP,
    PlanSlugs.SCALE: PlanNames.SCALE,
    PlanSlugs.ORG_TEAM: PlanNames.ORG_TEAM,
}



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
