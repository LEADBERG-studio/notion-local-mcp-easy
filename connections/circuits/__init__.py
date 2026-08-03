"""Circuit implementations. One module per connection technology."""

from __future__ import annotations

from .reverse_proxy import ReverseProxyCircuit
from .serveo_stable import ServeoStableCircuit
from .serveo_temporary import ServeoTemporaryCircuit
from .sish import SishCircuit
from .tunnellio_bridge import TunnellioBridgeCircuit
from .tunnellio_random import TunnellioRandomCircuit
from .tunnellio_stable import TunnellioStableCircuit

CIRCUIT_CLASSES = {
    ServeoStableCircuit.id: ServeoStableCircuit,
    ServeoTemporaryCircuit.id: ServeoTemporaryCircuit,
    TunnellioStableCircuit.id: TunnellioStableCircuit,
    TunnellioRandomCircuit.id: TunnellioRandomCircuit,
    TunnellioBridgeCircuit.id: TunnellioBridgeCircuit,
    SishCircuit.id: SishCircuit,
    ReverseProxyCircuit.id: ReverseProxyCircuit,
}

__all__ = [
    "CIRCUIT_CLASSES",
    "ReverseProxyCircuit",
    "ServeoStableCircuit",
    "ServeoTemporaryCircuit",
    "SishCircuit",
    "TunnellioBridgeCircuit",
    "TunnellioRandomCircuit",
    "TunnellioStableCircuit",
]
