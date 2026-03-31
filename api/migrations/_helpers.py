"""Reusable helpers for idempotent migration operations."""

from django.db import migrations
from django.db.migrations.operations.fields import AddField


class SafeRemoveConstraint(migrations.RemoveConstraint):
    """Remove a constraint but tolerate it already being absent in DB or state."""

    def _constraint_exists(self, connection, model):
        table = model._meta.db_table
        constraint_name = self.name.lower()
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table)
        return any(name.lower() == constraint_name for name in constraints)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        from_model_state = from_state.models[app_label, self.model_name_lower]
        try:
            constraint = from_model_state.get_constraint_by_name(self.name)
        except ValueError:
            return

        if not self._constraint_exists(schema_editor.connection, model):
            return

        schema_editor.remove_constraint(model, constraint)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        to_model_state = to_state.models[app_label, self.model_name_lower]
        try:
            constraint = to_model_state.get_constraint_by_name(self.name)
        except ValueError:
            return

        if self._constraint_exists(schema_editor.connection, model):
            return

        schema_editor.add_constraint(model, constraint)


class SafeAddIndex(migrations.AddIndex):
    """Add an index while gracefully handling duplicates."""

    def _index_exists(self, connection, model):
        if not self.index.name:
            return False

        table = model._meta.db_table
        index_name = self.index.name.lower()
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table)
        return any(name.lower() == index_name for name, meta in constraints.items() if meta.get("index") or meta.get("unique"))

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        if self._index_exists(schema_editor.connection, model):
            return

        schema_editor.add_index(model, self.index)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        if not self._index_exists(schema_editor.connection, model):
            return

        schema_editor.remove_index(model, self.index)


class SafeAddField(AddField):
    """Add a field while gracefully handling pre-existing DB columns."""

    def _column_exists(self, connection, model):
        table = model._meta.db_table
        column_name = model._meta.get_field(self.name).column.lower()
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, table)
        return any(column.name.lower() == column_name for column in columns)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        to_model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, to_model):
            return

        if self._column_exists(schema_editor.connection, to_model):
            return

        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        from_model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, from_model):
            return

        if not self._column_exists(schema_editor.connection, from_model):
            return

        super().database_backwards(app_label, schema_editor, from_state, to_state)


__all__ = ["SafeAddField", "SafeAddIndex", "SafeRemoveConstraint"]
