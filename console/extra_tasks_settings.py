from constants.plans import EXTRA_TASKS_DEFAULT_MAX_TASKS


def derive_extra_tasks_settings(configured_limit, *, can_modify, endpoints):
    """
    Keep billing extra-tasks derived state consistent across server-rendered
    initial props and update/load API responses.
    """
    configured_limit = int(configured_limit or 0)
    enabled = configured_limit != 0
    infinite = configured_limit == -1
    max_tasks = configured_limit if configured_limit > 0 else EXTRA_TASKS_DEFAULT_MAX_TASKS
    return {
        "enabled": bool(enabled),
        "infinite": bool(infinite),
        "maxTasks": int(max_tasks),
        "configuredLimit": int(configured_limit),
        "canModify": bool(can_modify),
        "endpoints": endpoints,
    }

