"""F21: the lane-graph policy extracted into graph.py (D3 and D4, already
repaired on this tree) pinned by tests that name the defect they check.

D3: taint used to depend on lane declaration order, because a single forward
pass computed each lane's inherited taint as the lane list was built, and a
`needs` (or `resume`) edge may point forward. `_propagate_taint` now runs to
a fixed point over the whole lane list, so the order lanes are written in
must never change the result.

D4: the resolver lane pasted every candidate sink's patch into its prompt
without being checked as a taint sink itself. `resolve_taint_sources` derives
its taint from every sink the same way `collate_taint_sources` already did
for the collate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conductor.graph import _tainted_names, resolve_taint_sources
from conductor.mission import MissionInvalid, mission_from_dict

CURSOR_TAINT_REFUSAL = "taint is enforceable on the claude and antigravity fleets only"


def _template_ref_raw(consumer_fleet: str, *, consumer_first: bool) -> dict:
    source = {"name": "source", "fleet": "claude", "untrusted_output": True, "prompt": "S"}
    consumer = {
        "name": "consumer",
        "fleet": consumer_fleet,
        "needs": ["source"],
        "prompt": "C sees {{lanes.source.answer}}",
    }
    lanes = [consumer, source] if consumer_first else [source, consumer]
    return {"cwd": "/tmp", "lanes": lanes}


def test_d3_cursor_taint_refusal_is_order_independent():
    """D3: an untrusted_output source taints its Cursor consumer, and the
    dispatch-capability refusal that follows must fire the same way whether
    the source is declared before or after the consumer that needs it."""
    for consumer_first in (True, False):
        raw = _template_ref_raw("cursor", consumer_first=consumer_first)
        with pytest.raises(MissionInvalid, match=CURSOR_TAINT_REFUSAL):
            mission_from_dict(raw, base_dir=Path("/tmp"))


def test_d3_claude_tainted_map_is_order_independent():
    """D3: with a fleet that may run tainted, both declaration orders must
    produce the identical per-lane (tainted, taint_from) map -- a single
    forward pass left this order-dependent before the fix."""
    forward = mission_from_dict(
        _template_ref_raw("claude", consumer_first=True), base_dir=Path("/tmp")
    )
    reversed_ = mission_from_dict(
        _template_ref_raw("claude", consumer_first=False), base_dir=Path("/tmp")
    )
    forward_map = {lane.name: (lane.tainted, lane.taint_from) for lane in forward.lanes}
    reversed_map = {lane.name: (lane.tainted, lane.taint_from) for lane in reversed_.lanes}
    assert forward_map == reversed_map
    # An untrusted_output lane is a taint source but is not itself tainted.
    assert forward_map["source"] == (False, [])
    assert forward_map["consumer"] == (True, ["source"])


def _resume_only_raw(*, consumer_first: bool) -> dict:
    source = {
        "name": "source",
        "fleet": "claude",
        "mode": "write",
        "taint": True,
        "prompt": "S",
    }
    consumer = {
        "name": "consumer",
        "fleet": "claude",
        "mode": "write",
        "needs": ["source"],
        "resume": "source",
        "prompt": "C",
    }
    lanes = [consumer, source] if consumer_first else [source, consumer]
    return {"cwd": "/tmp", "lanes": lanes}


def test_d3_resume_only_edge_is_order_independent():
    """D3: the same fixed point applies when the only edge between two
    lanes is `resume` (a lane resuming a tainted lane's session), not a
    template reference."""
    forward = mission_from_dict(_resume_only_raw(consumer_first=True), base_dir=Path("/tmp"))
    reversed_ = mission_from_dict(_resume_only_raw(consumer_first=False), base_dir=Path("/tmp"))
    forward_map = {lane.name: (lane.tainted, lane.taint_from) for lane in forward.lanes}
    reversed_map = {lane.name: (lane.tainted, lane.taint_from) for lane in reversed_.lanes}
    assert forward_map == reversed_map
    assert forward_map["source"] == (True, [])
    assert forward_map["consumer"] == (True, ["source"])


def test_d4_resolve_taint_sources_names_the_tainted_sink():
    """D4: `resolve_taint_sources` is what `Mission.validate` reads to gate
    the resolver's own dispatch -- it must name a tainted sink and leave a
    clean sink out, and whatever subset of sinks `_run_resolve` later
    recomputes from (the sinks that actually produced a patch) can only be
    a subset of what loaded, checked here directly on the shared helper
    rather than by running a resolver."""
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "tainted_sink",
                "fleet": "claude",
                "mode": "write",
                "taint": True,
                "prompt": "T",
            },
            {"name": "clean_sink", "fleet": "claude", "mode": "write", "prompt": "C"},
        ],
        "resolve": {"fleet": "claude"},
    }
    mission = mission_from_dict(raw, base_dir=Path("/tmp"))
    sources = resolve_taint_sources(mission)
    assert sources.tainted_sinks == ["tainted_sink"]
    assert {sink.name for sink in sources.sinks} == {"tainted_sink", "clean_sink"}

    for subset in ([], sources.sinks[:1], sources.sinks[1:], sources.sinks):
        assert set(_tainted_names(subset)) <= set(sources.tainted_sinks)


def test_d4_resolve_taint_sources_is_empty_with_no_tainted_sink():
    """D4: a mission whose sinks are all clean loads with an empty taint-
    sink set, so the resolver dispatches untainted."""
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "write", "prompt": "A"},
            {"name": "b", "fleet": "claude", "mode": "write", "prompt": "B"},
        ],
        "resolve": {"fleet": "codex"},
    }
    mission = mission_from_dict(raw, base_dir=Path("/tmp"))
    assert resolve_taint_sources(mission).tainted_sinks == []


def test_graph_module_does_not_import_mission_at_module_level():
    """The cycle graph.py must never reintroduce: mission.py imports names
    back from graph (item 1), so graph.py must never import mission at
    module level -- only inside a `TYPE_CHECKING` block, for annotations."""
    source = Path(__file__).resolve().parents[1] / "src" / "conductor" / "graph.py"
    in_type_checking = False
    for line in source.read_text().splitlines():
        if line.strip().startswith("if TYPE_CHECKING"):
            in_type_checking = True
            continue
        if in_type_checking and line and not line[0].isspace():
            in_type_checking = False
        if in_type_checking:
            continue
        assert "from .mission" not in line
        assert "import mission" not in line
