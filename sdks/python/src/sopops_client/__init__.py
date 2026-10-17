"""sopops client: fetch an agent's instructions at call start, and send each finished session back.

Framework adapters live in their own modules and import their framework lazily: `sopops_client.livekit`.
"""

from .client import Instructions, Sopops, SopopsError, SopopsUnavailable, release_pin

__version__ = "0.1.0"
__all__ = ["Instructions", "Sopops", "SopopsError", "SopopsUnavailable", "release_pin"]
