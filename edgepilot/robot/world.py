"""Single-owner world/executor. No threads, sleeps, HTTP, or hidden time advancement."""

from collections import deque
from dataclasses import asdict, dataclass, replace
import math
from typing import Callable

from .contracts import (Action, EventKind, HOME, Mission, NS, Plan, Point, Reason,
                        Skill, Snapshot, Station, Validation, WorldEvent, integer)
from .validation import validate_action, validate_plan


@dataclass(frozen=True)
class ActiveSkill:
    action: Action
    request_id: str
    goal_version: int
    started_at_ns: int
    finishes_at_ns: int
    origin: Point
    destination: Point


class World:
    speed = 2.0  # Abstract room units / second; no acceleration or obstacles.
    inspection_ns = NS // 2

    def __init__(self, emit: Callable[..., None] | None = None):
        self.emit = emit or (lambda **event: None)
        self.now_ns = 0
        self.snapshot_id = 0
        self.position = HOME
        self.stations = (Station("station_a", Point(2, 0), True),
                         Station("station_b", Point(2, 2), True))
        self.mission = Mission(1, 0, ("station_a", "station_b"))
        self.inspected: set[str] = set()
        self.active: ActiveSkill | None = None
        self.pending: deque[Action] = deque()
        self.plan: Plan | None = None
        self.expected_request_id: str | None = None
        self.expected_snapshot_id: int | None = None
        self.outcome = "running"
        self.mission_records: list[dict] = []
        self.actions_completed = 0
        self.actions_stopped = 0
        self.validation_rejections = 0
        self.last_fallback: tuple | None = None

    def snapshot(self) -> Snapshot:
        return Snapshot(self.snapshot_id, self.now_ns, self.mission, self.position,
                        self.stations, tuple(sorted(self.inspected)),
                        self.active.action.skill if self.active else None)

    def log(self, event: str, **fields):
        self.emit(event=event, at_ns=self.now_ns, goal_version=self.mission.version, **fields)

    def _validation_log(self, check: Validation, phase: str, plan: Plan | None, action: Action | None = None,
                        checked_snapshot_id: int | None = None):
        self.log("validation", phase=phase,
                 checked_snapshot_id=self.snapshot_id if checked_snapshot_id is None else checked_snapshot_id,
                 request_id=plan.request_id if plan else None,
                 skill=action.skill if action else None, accepted=check.accepted,
                 reason_code=check.reason, failed_constraints=[] if check.accepted else [check.detail],
                 validation_time_ns=None)  # CPU overhead profiling is separate future work.
        if not check.accepted:
            self.validation_rejections += 1

    def fallback(self, reason: str):
        key = (self.mission.version, reason)
        if self.last_fallback != key:
            self.log("wait_fallback", reason=reason, refresh_needed=self.outcome == "running")
            self.last_fallback = key

    def _clear_plan(self, reason: str):
        if self.active:
            self.log("skill_stopped", request_id=self.active.request_id,
                     skill=self.active.action.to_dict(), reason=reason, position=asdict(self.position))
            self.actions_stopped += 1
        self.active = None
        self.snapshot_id += 1
        self.pending.clear()
        self.plan = None
        self.fallback(reason)

    def expect(self, request_id: str):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("Request ID must not be blank")
        self.expected_request_id = request_id
        self.expected_snapshot_id = self.snapshot_id

    def install(self, value: Plan | dict) -> bool:
        try:
            plan = value if isinstance(value, Plan) else Plan.from_dict(value)
        except (ValueError, TypeError, KeyError) as exc:
            self._validation_log(Validation(False, Reason.MALFORMED, str(exc)), "install", None)
            if self.active is None and not self.pending:
                self.fallback(Reason.MALFORMED)
            return False
        check = (Validation(False, Reason.FINISHED) if self.outcome != "running" else
                 validate_plan(plan, self.snapshot(), self.expected_request_id, self.expected_snapshot_id))
        self._validation_log(check, "install", plan)
        if not check.accepted:
            if self.active is None and not self.pending:
                self.fallback(check.reason)
            return False
        self.plan = plan
        self.pending = deque(plan.actions)
        self.expected_request_id = None  # A completed response cannot be replayed.
        self.last_fallback = None
        self.log("plan_installed", plan=plan.to_dict())
        return True

    def ready_for_plan(self) -> bool:
        return self.outcome == "running" and self.active is None and not self.pending

    def mission_feasible(self) -> bool:
        remaining = set(self.mission.required_stations) - self.inspected
        return all(s.available is True for s in self.stations if s.name in remaining)

    def _check_success(self):
        if self.outcome == "running" and self.active is None and not self.pending and (
                set(self.mission.required_stations) <= self.inspected and self.position.distance(HOME) <= 1e-9):
            self.outcome = "succeeded"
            record = {"goal_version": self.mission.version, "outcome": "succeeded",
                      "started_at_ns": self.mission.created_at_ns, "finished_at_ns": self.now_ns}
            self.mission_records.append(record)
            self.log("mission_succeeded", position=asdict(self.position), inspected=sorted(self.inspected))

    def advance(self, now_ns: int, events: tuple[WorldEvent, ...] = (), *, deadline: bool = False):
        integer(now_ns, "clock")
        if now_ns < self.now_ns:
            raise ValueError("Clock cannot go backwards")
        names = {s.name for s in self.stations}
        # Validate the whole event batch before any state mutation.
        for event in events:
            if event.at_ns > now_ns:
                raise ValueError("Cannot apply a future event")
            if event.kind == EventKind.AVAILABILITY and event.station not in names:
                raise ValueError("Unknown station in event")
            if event.kind == EventKind.GOAL and not set(event.required_stations) <= names:
                raise ValueError("Unknown mission target")
        self.now_ns = now_ns
        self.snapshot_id += 1
        # Integrate only the already-active skill. Never backfill multiple skills
        # in a delayed loop. External updates precede completion credit/dispatch.
        if self.active:
            active = self.active
            fraction = min(1.0, (now_ns-active.started_at_ns)/(active.finishes_at_ns-active.started_at_ns))
            self.position = Point(active.origin.x + (active.destination.x-active.origin.x)*fraction,
                                  active.origin.y + (active.destination.y-active.origin.y)*fraction)
        if deadline:
            self.terminate("timed_out")
            return
        for event in events:
            self.log("world_event", scheduled_at_ns=event.at_ns, lateness_ns=now_ns-event.at_ns,
                     update=asdict(event))
            if event.kind == EventKind.AVAILABILITY:
                self.stations = tuple(replace(s, available=event.available) if s.name == event.station else s
                                      for s in self.stations)
            elif event.kind == EventKind.GOAL:
                if self.outcome == "running":
                    self.mission_records.append({"goal_version": self.mission.version, "outcome": "superseded",
                                                 "started_at_ns": self.mission.created_at_ns, "finished_at_ns": now_ns})
                self._clear_plan(Reason.STALE_GOAL)
                self.mission = Mission(self.mission.version+1, now_ns, event.required_stations)
                self.inspected.clear()  # Inspection credit is scoped to a mission.
                self.snapshot_id += 1
                self.outcome = "running"
                self.last_fallback = None
        if self.active:
            check = validate_action(self.active.action, self.snapshot())
            if not check.accepted:
                self._validation_log(check, "monitor", self.plan, self.active.action)
                self._clear_plan(check.reason)
            elif now_ns >= self.active.finishes_at_ns:
                action = self.active.action
                if action.skill == Skill.INSPECT:
                    self.inspected.add(action.target)
                self.log("skill_completed", request_id=self.active.request_id, skill=action.to_dict(),
                         position=asdict(self.position), inspected=sorted(self.inspected))
                self.active = None
                self.snapshot_id += 1
                self.actions_completed += 1
        self._check_success()

    def dispatch(self) -> bool:
        if self.outcome != "running" or self.active or not self.pending:
            return False
        action = self.pending[0]
        checked_snapshot_id = self.snapshot_id
        check = (Validation(False, Reason.STALE_GOAL) if self.plan.goal_version != self.mission.version else
                 validate_action(action, self.snapshot()))
        if not check.accepted:
            self._validation_log(check, "dispatch", self.plan, action)
            self._clear_plan(check.reason)
            return False
        destination = self.position
        if action.skill == Skill.NAVIGATE:
            destination = next(s.position for s in self.stations if s.name == action.target)
        elif action.skill == Skill.HOME:
            destination = HOME
        duration = (action.duration_ns if action.skill == Skill.WAIT else self.inspection_ns
                    if action.skill == Skill.INSPECT else
                    max(1, math.ceil(self.position.distance(destination)/self.speed*NS)))
        # No callback or state update is permitted between this check and dispatch.
        self.active = ActiveSkill(action, self.plan.request_id, self.mission.version, self.now_ns,
                                  self.now_ns+duration, self.position, destination)
        self.snapshot_id += 1
        self.pending.popleft()
        self.last_fallback = None
        self._validation_log(check, "dispatch", self.plan, action, checked_snapshot_id)
        self.log("skill_started", request_id=self.plan.request_id, skill=action.to_dict(),
                 finishes_at_ns=self.active.finishes_at_ns, position=asdict(self.position))
        return True

    def terminate(self, outcome: str):
        if outcome not in ("timed_out", "planner_failed"):
            raise ValueError("Unknown episode outcome")
        was_running = self.outcome == "running"
        self.outcome = outcome
        self._clear_plan(outcome)
        if was_running:
            self.mission_records.append({"goal_version": self.mission.version, "outcome": outcome,
                                         "started_at_ns": self.mission.created_at_ns, "finished_at_ns": self.now_ns})
        self.log("mission_terminated", outcome=outcome)
