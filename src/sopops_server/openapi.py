"""Print the OpenAPI spec: `python -m sopops_server.openapi > web/openapi.json` (the web client is generated from it)."""

import json

from .app import create_app
from .sopc import Sopc
from .store import Store

if __name__ == "__main__":
    spec = create_app(Store("sqlite://", Sopc(), migrate=False)).openapi()
    print(json.dumps(spec, indent=2, sort_keys=False))
