"""Database tables, one module per table, as SQLModel table models. API request and response shapes live in
`schemas`, so these never leak.

The schema is managed by Alembic (`sopops/migrations`); `db.upgrade` brings a database to head on startup. Everything
is re-exported here, and `metadata` holds every table (Alembic compares it with the migrations).
"""

from sqlmodel import SQLModel

from .workspaces import Workspace
from .file_versions import FileVersion
from .releases import Release
from .fetches import Fetch
from .sessions import SessionRecord
from .procedure_checks import ProcedureCheck
from .rule_results import RuleResult
from .rule_checks import RuleChecks
from .findings import Finding
from .themes import Theme
from .patterns import Pattern
from .tokens import Token
from .platform_secrets import PlatformSecret
from .agent_delivery import AgentDelivery
from .push_results import PushResult
from .runtime_serves import RuntimeServe
from .imports import ImportRecord
from .import_events import ImportEvent
from .columns import JSONDoc

metadata = SQLModel.metadata

__all__ = ["AgentDelivery", "Fetch", "FileVersion", "Finding", "ImportEvent", "ImportRecord", "JSONDoc", "Pattern", "PlatformSecret", "ProcedureCheck", "PushResult", "Release", "RuleChecks", "RuleResult", "RuntimeServe", "SessionRecord", "Theme", "Token", "Workspace", "metadata"]
