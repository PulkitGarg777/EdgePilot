"""Controllable scripted planner; delay is simulated, not model inference."""

from dataclasses import dataclass

from .contracts import Action, Plan, Skill, Snapshot, integer


@dataclass(frozen=True)
class PlanningRequest:
    request_id: str
    created_at_ns: int
    snapshot: Snapshot


@dataclass(frozen=True)
class PlanningResult:
    request_id: str
    ready_at_ns: int
    plan: Plan | None
    error: str | None = None


def scripted_plan(request: PlanningRequest) -> Plan:
    """Explicit station order, independent of future events and scheduling policy."""
    state = request.snapshot
    actions = []
    for name in state.mission.required_stations:
        if name not in state.inspected:
            actions.extend((Action(Skill.NAVIGATE, name), Action(Skill.INSPECT, name)))
    actions.append(Action(Skill.HOME))
    return Plan(request.request_id, state.mission.version, state.snapshot_id, tuple(actions))


class ScriptedPlanner:
    def __init__(self, delay_ns: int = 0, *, fail: bool = False):
        integer(delay_ns, "planner delay")
        self.delay_ns, self.fail = delay_ns, fail
        self.pending: PlanningRequest | None = None

    def submit(self, request: PlanningRequest):
        if self.pending is not None:
            raise RuntimeError("Scripted worker already occupied")
        self.pending = request

    def poll(self, now_ns: int) -> PlanningResult | None:
        if self.pending is None or now_ns < self.pending.created_at_ns+self.delay_ns:
            return None
        request = self.pending
        self.pending = None
        return PlanningResult(request.request_id, request.created_at_ns+self.delay_ns,
                              None if self.fail else scripted_plan(request),
                              "Injected scripted-worker failure" if self.fail else None)
