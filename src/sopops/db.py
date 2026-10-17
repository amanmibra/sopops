"""The database: an engine from DATABASE_URL, and Alembic migrations run on startup.

Postgres in production, SQLite for local use and tests. `upgrade` brings the schema to head; a database created by
sopops before it used Alembic (same tables, no `alembic_version`) is stamped with the initial revision first.
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

MIGRATIONS = Path(__file__).parent / "migrations"
INITIAL = "0001"


def database_url() -> str:
    """DATABASE_URL, else ./sopops.db (or ./sopserve.db, the pre-rename default, if only that exists)."""
    if url := os.environ.get("DATABASE_URL"):
        return url
    if not Path("sopops.db").exists() and Path("sopserve.db").exists():
        return "sqlite:///sopserve.db"
    return "sqlite:///sopops.db"


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


def upgrade(engine: Engine) -> None:
    """Run every migration up to head."""
    cfg = alembic_config()
    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        cfg.attributes["connection"] = conn
        if "workspaces" in tables and "alembic_version" not in tables:
            command.stamp(cfg, INITIAL)  # created by create_all before Alembic: already the initial schema
        command.upgrade(cfg, "head")


def current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()
