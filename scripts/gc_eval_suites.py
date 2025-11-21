"""
Utility script: mark stale eval suites/runs as errored.

Usage:
  python manage.py shell < scripts/gc_eval_suites.py
"""
from django.utils import timezone

from api.models import EvalRun, EvalSuiteRun

now = timezone.now()

runs = EvalRun.objects.filter(status='running', finished_at__isnull=True)
suite_ids = set(runs.values_list('suite_run_id', flat=True))
updated_runs = runs.update(status=EvalRun.Status.ERRORED, finished_at=now, updated_at=now)

suites = EvalSuiteRun.objects.filter(status='running', finished_at__isnull=True)
updated_suites = suites.update(status=EvalSuiteRun.Status.ERRORED, finished_at=now, updated_at=now)

print(f"Marked runs errored: {updated_runs}")
print(f"Marked suites errored: {updated_suites}")

if suite_ids:
    repaired_suites = EvalSuiteRun.objects.filter(id__in=suite_ids)
    print(f"Suite IDs touched: {[str(s.id) for s in repaired_suites]}")
