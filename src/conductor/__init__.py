"""conductor: one dispatch contract across four agent fleets."""

from .breakers import Breaker, tool_events
from .fleets import EFFORTS, FLEETS, MODES, DispatchRefused, Spec, build_argv
from .mission import Mission, MissionInvalid, MissionResult, load_mission, run_mission
from .prices import estimate, load_prices
from .runner import Result, dispatch
from .verdicts import (
    Criterion,
    checklist_contract,
    checklist_schema,
    parse_checklist,
    parse_verdict,
)
from .verdicts import Verdict as ChecklistVerdict
from .verify import GitState, Verdict, compare, run_tests

__version__ = "0.23.0"

__all__ = [
    "EFFORTS",
    "FLEETS",
    "MODES",
    "DispatchRefused",
    "Breaker",
    "Criterion",
    "ChecklistVerdict",
    "GitState",
    "Mission",
    "MissionInvalid",
    "MissionResult",
    "Result",
    "Spec",
    "Verdict",
    "build_argv",
    "checklist_contract",
    "checklist_schema",
    "compare",
    "dispatch",
    "estimate",
    "load_mission",
    "load_prices",
    "parse_checklist",
    "parse_verdict",
    "run_mission",
    "run_tests",
    "tool_events",
]
