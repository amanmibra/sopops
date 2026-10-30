"""sopops client: fetch an agent's instructions at call start, and send each finished session back.

Platform adapters live in their own modules and import their framework lazily: `sopops.livekit`, `sopops.elevenlabs`.
`SessionStream` optionally sends a session's events while the call runs.
"""

from .client import (
    FileCache,
    InstructionCache,
    Instructions,
    MemoryCache,
    SopOps,
    SopOpsError,
    SopOpsUnavailable,
    fetch,
    fill,
    release_pin,
    unfilled,
)
from .stream import SessionStream

__version__ = "0.5.3"
__all__ = [
    "FileCache", "InstructionCache", "Instructions", "MemoryCache", "SessionStream", "SopOps", "SopOpsError", "SopOpsUnavailable",
    "fetch", "fill", "release_pin", "unfilled",
]
