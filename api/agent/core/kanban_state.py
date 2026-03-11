"""Helpers for reading persistent kanban state."""

from dataclasses import dataclass

from api.models import PersistentAgent


@dataclass(frozen=True)
class KanbanState:
    todo_count: int
    doing_count: int
    done_count: int

    @property
    def has_open_work(self) -> bool:
        return (self.todo_count + self.doing_count) > 0

    @property
    def is_work_complete(self) -> bool:
        return not self.has_open_work and self.done_count > 0


def get_kanban_state(agent: PersistentAgent) -> KanbanState:
    """Return the current persistent kanban counts for an agent."""
    from django.apps import apps
    from django.db.models import Count, Q

    KanbanCard = apps.get_model("api", "PersistentAgentKanbanCard")
    counts = KanbanCard.objects.filter(
        assigned_agent=agent,
        status__in=[
            KanbanCard.Status.TODO,
            KanbanCard.Status.DOING,
            KanbanCard.Status.DONE,
        ],
    ).aggregate(
        todo_count=Count("id", filter=Q(status=KanbanCard.Status.TODO)),
        doing_count=Count("id", filter=Q(status=KanbanCard.Status.DOING)),
        done_count=Count("id", filter=Q(status=KanbanCard.Status.DONE)),
    )
    return KanbanState(
        todo_count=counts["todo_count"] or 0,
        doing_count=counts["doing_count"] or 0,
        done_count=counts["done_count"] or 0,
    )
