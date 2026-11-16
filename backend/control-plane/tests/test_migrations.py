"""Migrations must stay healthy: one line of history, reversible, in sync with the models."""

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _config() -> Config:
    return Config("alembic.ini")


def test_single_head() -> None:
    # Two heads would mean two migrations claim to be "latest" (e.g. after a
    # merge of two branches), and `upgrade head` would fail.
    assert len(ScriptDirectory.from_config(_config()).get_heads()) == 1


def test_downgrade_to_base_and_upgrade_again() -> None:
    # Every migration's downgrade() must work, so a bad deploy can be rolled back.
    command.downgrade(_config(), "base")
    command.upgrade(_config(), "head")


def test_models_and_migrations_are_in_sync() -> None:
    # Fails if someone changed a model but forgot to create a migration.
    command.check(_config())
