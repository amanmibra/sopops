__version__ = "0.1.0"

from .app import app_from_env, create_app  # noqa: E402
from .sop import Sop  # noqa: E402
from .store import NotFound, Store  # noqa: E402

Sopc = Sop  # deprecated: the old name, kept while sopops-hosted moves to Sop

__all__ = ["NotFound", "Sop", "Sopc", "Store", "app_from_env", "create_app"]
