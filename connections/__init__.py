"""Independent connection circuits and the permanent connection state.

Public entry points:

``store``
    ``current-connection.json`` (areas plus the active one) and
    ``connection-profiles.v2.json`` (configured circuits).

``blueprints``
    Read-only shipped defaults under ``connections/defaults/``.

``circuits``
    One module per connection technology. Circuits never share settings.
"""

from __future__ import annotations

from . import blueprints, store
from .base import (
    Circuit,
    ConnectionConfigError,
    ConnectionSetupAborted,
    ConnectionVerifyError,
    Question,
    RuntimeContext,
)
from .circuits import CIRCUIT_CLASSES
from .store import ConnectionStoreError, ResolvedConnection

__all__ = [
    "CIRCUIT_CLASSES",
    "Circuit",
    "ConnectionConfigError",
    "ConnectionSetupAborted",
    "ConnectionStoreError",
    "ConnectionVerifyError",
    "Question",
    "ResolvedConnection",
    "RuntimeContext",
    "blueprints",
    "store",
]

VERSION = 1
