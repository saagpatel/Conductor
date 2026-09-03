"""conductor: one dispatch contract across four agent fleets."""

from .fleets import EFFORTS, FLEETS, MODES, DispatchRefused, Spec, build_argv
from .mission import Mission, MissionInvalid, MissionResult, load_mission, run_mission
from .prices import estimate, load_prices
from .runner import Result, dispatch
from .verify import GitState, Verdict, compare, run_tests

__version__ = "0.5.0"

__all__ = [
    "EFFORTS",
    "FLEETS",
    "MODES",
    "DispatchRefused",
    "GitState",
    "Mission",
    "MissionInvalid",
    "MissionResult",
    "Result",
    "Spec",
    "Verdict",
    "build_argv",
    "compare",
    "dispatch",
    "estimate",
    "load_mission",
    "load_prices",
    "run_mission",
    "run_tests",
]
