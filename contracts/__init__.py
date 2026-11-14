"""The shared contract between Dispatch, Cabin, Control Center, engine and UI.

Owned by An. Do not edit from another folder; ask An (CONTRIBUTING.md, Section 4).

Modules:
    enums       shared enumerations
    config      SRS constants, team decisions, Policy
    ids         deterministic ID generation
    messages    every message and value type
    codec       message <-> JSON for logs and replay
    routing     who receives what, and same-time priority
    interfaces  the Module protocol and factory signatures
    ui          snapshot shapes, REST commands, fake snapshot
"""

# Bumped by An on every contract change (CONTRIBUTING.md, Section 4).
# Defined before the imports below because contracts.ui reads it.
CONTRACT_VERSION = 1

from contracts.config import DEFAULT_POLICY, Policy  # noqa: E402
from contracts.messages import ALL_MESSAGES, Message  # noqa: E402

__all__ = ["ALL_MESSAGES", "CONTRACT_VERSION", "DEFAULT_POLICY", "Message", "Policy"]
