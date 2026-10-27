"""Mine coding-agent sessions for mistakes and turn them into regression cases."""

from retro.cases import Case, Check, Verdict, admit, grade
from retro.friction import Friction, FrictionKind, detect
from retro.model import Event, EventKind, Session
from retro.sources import load_session

__all__ = [
    "Case",
    "Check",
    "Event",
    "EventKind",
    "Friction",
    "FrictionKind",
    "Session",
    "Verdict",
    "admit",
    "detect",
    "grade",
    "load_session",
]
__version__ = "0.1.0"
