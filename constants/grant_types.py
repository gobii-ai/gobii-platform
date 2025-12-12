from django.db import models


class GrantTypeChoices(models.TextChoices):
    PLAN = "Plan", "Plan"
    COMPENSATION = "Compensation", "Compensation"
    PROMO = "Promo", "Promo"
    TASK_PACK = "task_pack", "Task Pack"
