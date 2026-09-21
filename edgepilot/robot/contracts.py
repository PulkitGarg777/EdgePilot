"""Simulation v1 contracts. Times are episode-relative monotonic integer nanoseconds."""

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any

NS = 1_000_000_000
TRACE_SCHEMA = "edgepilot.simulation.v1"
MAX_ACTIONS = 16


def integer(value: Any, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self):
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 4
               for v in (self.x, self.y)):
            raise ValueError("Position must be finite and inside the 4 by 4 room")

    def distance(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


HOME = Point(0, 0)


class Skill(StrEnum):
    NAVIGATE = "navigate_to"
    INSPECT = "inspect"
    WAIT = "wait"
    HOME = "return_home"


class Reason(StrEnum):
    OK = "ok"
    MALFORMED = "malformed_action"
    UNKNOWN_TARGET = "unknown_target"
    STALE_REQUEST = "superseded_request"
    STALE_GOAL = "superseded_goal"
    MISSING_STATE = "missing_state"
    PRECONDITION = "precondition_failed"
    FUTURE_SNAPSHOT = "future_snapshot"
    SNAPSHOT_MISMATCH = "snapshot_mismatch"
    BUSY = "executor_busy"
    FINISHED = "mission_finished"


@dataclass(frozen=True)
class Action:
    skill: Skill
    target: str | None = None
    duration_ns: int | None = None

    def __post_init__(self):
        if not isinstance(self.skill, Skill):
            raise ValueError("Use a declared Skill")
        if self.skill in (Skill.NAVIGATE, Skill.INSPECT):
            if not isinstance(self.target, str) or not 1 <= len(self.target) <= 64 or self.duration_ns is not None:
                raise ValueError("Navigation/inspection needs a target and no duration")
        elif self.skill == Skill.WAIT:
            integer(self.duration_ns, "duration_ns", 1)
            if self.target is not None or self.duration_ns > 10 * NS:
                raise ValueError("Wait needs no target and at most ten seconds")
        elif self.target is not None or self.duration_ns is not None:
            raise ValueError("return_home takes no arguments")

    @classmethod
    def from_dict(cls, value: Any) -> "Action":
        if not isinstance(value, dict) or "skill" not in value:
            raise ValueError("Action must be an object with a declared skill")
        skill = Skill(value["skill"])
        fields = {"skill", "target"} if skill in (Skill.NAVIGATE, Skill.INSPECT) else (
            {"skill", "duration_ns"} if skill == Skill.WAIT else {"skill"})
        if set(value) != fields:
            raise ValueError("Unexpected or missing action arguments")
        return cls(skill, value.get("target"), value.get("duration_ns"))

    def to_dict(self) -> dict:
        value = {"skill": self.skill.value}
        if self.target is not None:
            value["target"] = self.target
        if self.duration_ns is not None:
            value["duration_ns"] = self.duration_ns
        return value


@dataclass(frozen=True)
class Station:
    name: str
    position: Point
    available: bool | None  # None means unknown, never assumed safe to use.

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or not isinstance(self.position, Point):
            raise ValueError("Station needs a name and position")
        if self.available is not None and type(self.available) is not bool:
            raise ValueError("Availability must be boolean or unknown")


@dataclass(frozen=True)
class Mission:
    version: int
    created_at_ns: int
    required_stations: tuple[str, ...]

    def __post_init__(self):
        integer(self.version, "goal version", 1)
        integer(self.created_at_ns, "goal creation time")
        if not isinstance(self.required_stations, tuple) or any(
                not isinstance(s, str) or not s for s in self.required_stations):
            raise ValueError("Required stations must be a tuple of names")
        if len(set(self.required_stations)) != len(self.required_stations):
            raise ValueError("Duplicate mission targets")


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    observed_at_ns: int
    mission: Mission
    position: Point | None
    stations: tuple[Station, ...]
    inspected: tuple[str, ...]
    active_skill: Skill | None

    def __post_init__(self):
        integer(self.snapshot_id, "snapshot ID")
        integer(self.observed_at_ns, "observation time")
        if not isinstance(self.mission, Mission) or self.mission.created_at_ns > self.observed_at_ns:
            raise ValueError("Snapshot needs a mission already created at observation time")
        if self.position is not None and not isinstance(self.position, Point):
            raise ValueError("Position must be a Point or unknown")
        if not isinstance(self.stations, tuple) or any(not isinstance(s, Station) for s in self.stations):
            raise ValueError("Stations must be an immutable tuple")
        names = {s.name for s in self.stations}
        if len(names) != len(self.stations):
            raise ValueError("Duplicate station names")
        if not isinstance(self.inspected, tuple) or any(not isinstance(s, str) or s not in names for s in self.inspected):
            raise ValueError("Inspected stations must be known names")
        if self.active_skill is not None and not isinstance(self.active_skill, Skill):
            raise ValueError("Invalid active skill")


@dataclass(frozen=True)
class Plan:
    request_id: str
    goal_version: int
    snapshot_id: int
    actions: tuple[Action, ...]

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id.strip() or len(self.request_id) > 128:
            raise ValueError("Invalid request ID")
        integer(self.goal_version, "goal version", 1)
        integer(self.snapshot_id, "snapshot ID")
        if not isinstance(self.actions, tuple) or not 1 <= len(self.actions) <= MAX_ACTIONS or any(
                not isinstance(a, Action) for a in self.actions):
            raise ValueError(f"Plan must contain 1 to {MAX_ACTIONS} typed actions")

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "goal_version": self.goal_version,
                "snapshot_id": self.snapshot_id, "actions": [a.to_dict() for a in self.actions]}

    @classmethod
    def from_dict(cls, value: Any) -> "Plan":
        if not isinstance(value, dict) or set(value) != {"request_id", "goal_version", "snapshot_id", "actions"}:
            raise ValueError("Plan has unexpected or missing fields")
        if not isinstance(value["actions"], list):
            raise ValueError("Actions must be an array")
        return cls(value["request_id"], value["goal_version"], value["snapshot_id"],
                   tuple(Action.from_dict(a) for a in value["actions"]))


@dataclass(frozen=True)
class Validation:
    accepted: bool
    reason: Reason
    detail: str = ""


class EventKind(StrEnum):
    AVAILABILITY = "station_availability"
    GOAL = "goal_replaced"
    TELEMETRY = "irrelevant_telemetry"


@dataclass(frozen=True)
class WorldEvent:
    at_ns: int
    kind: EventKind
    station: str | None = None
    available: bool | None = None
    required_stations: tuple[str, ...] = ()

    def __post_init__(self):
        integer(self.at_ns, "event time")
        if not isinstance(self.kind, EventKind):
            raise ValueError("Unknown event kind")
        if self.kind == EventKind.AVAILABILITY:
            if not isinstance(self.station, str) or not self.station or type(self.available) is not bool or self.required_stations:
                raise ValueError("Availability event needs only station and a boolean")
        elif self.station is not None or self.available is not None:
            raise ValueError("Unexpected station arguments")
        if self.kind == EventKind.GOAL:
            Mission(1, self.at_ns, self.required_stations)
        elif self.required_stations:
            raise ValueError("Only a goal event has required stations")
