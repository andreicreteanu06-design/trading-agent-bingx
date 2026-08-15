"""
Modulele de risc adaugate peste agent: regim de piata, circuit breaker si
dimensionarea pozitiei.

Cele trei fisiere `regime_analyzer.py`, `circuit_breaker.py` si
`position_sizer.py` sunt pastrate asa cum au fost primite - nu le modifica
aici. Adaptarea la agent (date reale, cache, cablare in scanner) se face in
`market_data.py`, `gate.py` si `trade_recorder.py`.
"""

from __future__ import annotations

from risk.circuit_breaker import CircuitConfig, append_trade, check_circuit_breaker
from risk.position_sizer import SizingParams, calculate_position, kelly_fraction
from risk.regime_analyzer import calculate_regime

__all__ = [
    "CircuitConfig",
    "append_trade",
    "check_circuit_breaker",
    "SizingParams",
    "calculate_position",
    "kelly_fraction",
    "calculate_regime",
]
