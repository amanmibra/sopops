"""Alembic environment. `sopops_server.db.upgrade` passes a connection in; the `alembic` CLI (alembic.ini at the repo root)
builds one from DATABASE_URL.

A migration whose data backfill uses sopops's ORM code (which always matches the head schema) doesn't run it itself:
it appends `fn(connection, sopc)` to `config.attributes["after_upgrade"]`, and it runs once every migration has, in
the same transaction. Run inline, a later migration's new column would be missing when the ORM selects it."""

from alembic import context
from sqlalchemy import pool

from sopops_server.db import database_url, engine_from_url
from sopops_server.orm import metadata

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
        for backfill in config.attributes.pop("after_upgrade", []):
            backfill(connection, config.attributes.get("sopc"))


if context.is_offline_mode():
    run_offline()
else:
    run_online()
