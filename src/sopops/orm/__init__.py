"""Database tables, one module per table, as SQLModel table models. API request and response shapes live in
`schemas`, so these never leak.

The schema is managed by Alembic (`sopops/migrations`); `db.upgrade` brings a database to head on startup. Everything
is re-exported here, and `metadata` holds every table (Alembic compares it with the migrations).
"""

from sqlmodel import SQLModel

from .agent_delivery import AgentDelivery
from .calibrations import Calibration
from .columns import JSONDoc
from .fetches import Fetch
from .file_versions import FileVersion
from .finding_overrides import FindingOverride
from .findings import Finding
from .import_events import ImportEvent
from .imports import ImportRecord
from .patterns import Pattern
from .platform_secrets import PlatformSecret
from .procedure_checks import ProcedureCheck
from .push_results import PushResult
from .releases import Release
from .rule_checks import RuleChecks
from .rule_results import RuleResult
from .runtime_serves import RuntimeServe
from .sessions import SessionRecord
from .suggestions import Suggestion
from .themes import Theme
from .tokens import Token
from .verdicts import Verdict
from .workspaces import Workspace

metadata = SQLModel.metadata

__all__ = [
    "Calibration","AgentDelivery", "Fetch", "FileVersion", "Finding", "FindingOverride", "ImportEvent", "ImportRecord", "JSONDoc", "Pattern", "PlatformSecret", "ProcedureCheck", "PushResult", "Release", "RuleChecks", "RuleResult", "RuntimeServe", "SessionRecord", "Suggestion", "Theme", "Token", "Verdict", "Workspace", "metadata"]
