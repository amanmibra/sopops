"""sopops client: fetch an agent's instructions at call start, and send each finished session back.

Framework adapters live in their own modules and import their framework lazily: `sopops.livekit`.
`SessionStream` optionally sends a session's events while the call runs.
"""

from .client import Instructions, Sopops, SopopsError, SopopsUnavailable, fetch, release_pin
from .stream import SessionStream

__version__ = "0.1.0"
__all__ = ["Instructions", "SessionStream", "Sopops", "SopopsError", "SopopsUnavailable", "fetch", "release_pin"]
