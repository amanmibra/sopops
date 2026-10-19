"""Database tables, one module per table, as SQLModel table models. API request and response shapes live in
`schemas`, so these never leak.

The schema is managed by Alembic (`sopops/migrations`); `db.upgrade` brings a database to head on startup. Everything
is re-exported here, and `metadata` holds every table (Alembic compares it with the migrations).
"""

from sqlmodel import SQLModel

from .agent_delivery import AgentDelivery
from .ai_settings import AISetting
from .calibrations import Calibration
from .capture_settings import CaptureSetting
from .columns import JSONDoc
from .email_links import EmailLink
from .fetches import Fetch
from .file_versions import FileVersion
from .finding_overrides import FindingOverride
from .findings import Finding
from .github_apps import GitHubApp
from .github_deliveries import GitHubDelivery
from .github_installations import GitHubInstallation
from .identities import Identity
from .import_events import ImportEvent
from .imports import ImportRecord
from .invites import Invite
from .join_links import JoinLink
from .login_attempts import LoginAttempt
from .login_sessions import LoginSession
from .memberships import Membership
from .model_calls import ModelCall
from .orgs import Org
from .password_links import PasswordLink
from .patterns import Pattern
from .platform_secrets import PlatformSecret
from .procedure_checks import ProcedureCheck
from .push_results import PushResult
from .releases import Release
from .repo_connections import RepoConnection
from .rule_checks import RuleChecks
from .rule_lineage import RuleLineage
from .rule_results import RuleResult
from .runtime_serves import RuntimeServe
from .sessions import SessionRecord
from .suggestions import Suggestion
from .sync_conflicts import SyncConflict
from .themes import Theme
from .tokens import Token
from .users import User
from .verdicts import Verdict
from .workspaces import Workspace

metadata = SQLModel.metadata

__all__ = [
    "AgentDelivery", "AISetting", "Calibration", "CaptureSetting", "EmailLink", "Fetch", "FileVersion", "Finding", "FindingOverride", "GitHubApp", "GitHubDelivery", "GitHubInstallation", "Identity", "ImportEvent", "ImportRecord", "Invite", "JoinLink", "JSONDoc", "LoginAttempt", "LoginSession", "Membership", "ModelCall", "Org", "PasswordLink", "Pattern", "PlatformSecret", "ProcedureCheck", "PushResult", "Release", "RepoConnection", "RuleChecks", "RuleLineage", "RuleResult", "RuntimeServe", "SessionRecord", "Suggestion", "SyncConflict", "Theme", "Token", "User", "Verdict", "Workspace", "metadata"]
