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

    KanbanCard = apps.get_model("api", "PersistentAgentKanbanCard")
    return KanbanState(
        todo_count=KanbanCard.objects.filter(assigned_agent=agent, status="todo").count(),
        doing_count=KanbanCard.objects.filter(assigned_agent=agent, status="doing").count(),
        done_count=KanbanCard.objects.filter(assigned_agent=agent, status="done").count(),
    )
