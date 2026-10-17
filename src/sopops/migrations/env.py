"""Alembic environment. `sopops.db.upgrade` passes a connection in; the `alembic` CLI (alembic.ini at the repo root)
builds one from DATABASE_URL."""

from alembic import context
from sqlalchemy import pool

from sopops.db import engine_from_url, database_url
from sopops.models import metadata

config = context.config
target_metadata = metadata


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = engine_from_url(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)


def _run(connection) -> None:
    # render_as_batch: SQLite can't ALTER most things, so Alembic copies the table instead.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
