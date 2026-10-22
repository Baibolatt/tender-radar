"""Causal SMC/ICT signal detection.

Every detector returns a frame stamped with ``confirm_index`` -- the bar at
whose close the fact becomes knowable.  Strategy code must never look at
``index`` alone.
"""

from .detect import SignalSet, detect
from .fvg import active_fvgs_at, find_fvgs
from .ob import find_order_blocks
from .structure import find_structure
from .sweep import find_sweeps
from .swing import find_swings

__all__ = [
    "SignalSet",
    "detect",
    "find_swings",
    "find_structure",
    "find_fvgs",
    "find_order_blocks",
    "find_sweeps",
    "active_fvgs_at",
]
