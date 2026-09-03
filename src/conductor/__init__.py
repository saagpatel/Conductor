"""conductor: one dispatch contract across four agent fleets."""

from .fleets import EFFORTS, FLEETS, MODES, DispatchRefused, Spec, build_argv
from .runner import Result, dispatch
from .verify import GitState, Verdict, compare, run_tests

__version__ = "0.1.0"

__all__ = [
    "EFFORTS",
    "FLEETS",
    "MODES",
    "DispatchRefused",
    "GitState",
    "Result",
    "Spec",
    "Verdict",
    "build_argv",
    "compare",
    "dispatch",
    "run_tests",
]
