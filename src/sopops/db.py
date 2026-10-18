"""The database: an engine from DATABASE_URL, and Alembic migrations run on startup.

Postgres in production, SQLite for local use and tests. `upgrade` brings the schema to head; a database created by
sopops before it used Alembic (same tables, no `alembic_version`) is stamped with the initial revision first.
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Engine
from sqlmodel import create_engine, inspect

from .sopc import Sopc

MIGRATIONS = Path(__file__).parent / "migrations"
INITIAL = "0001"


def database_url() -> str:
    """DATABASE_URL, else ./sopops.db."""
    return os.environ.get("DATABASE_URL") or "sqlite:///sopops.db"


def engine_from_url(url: str, **kw) -> Engine:
    """`postgres://` and `postgresql://` URLs (as Supabase and Neon hand them out) use psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
    return create_engine(url, pool_pre_ping=True, **kw)


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return cfg


def upgrade(engine: Engine, sopc: Sopc | None = None) -> None:
    """Run every migration up to head. `sopc`: for migrations that read releases (0013's lineage backfill)."""
    cfg = alembic_config()
    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        cfg.attributes["connection"] = conn
        cfg.attributes["sopc"] = sopc
        if "workspaces" in tables and "alembic_version" not in tables:
            command.stamp(cfg, INITIAL)  # created by create_all before Alembic: already the initial schema
        command.upgrade(cfg, "head")


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()
