"""Declared task applicability, using only request-time and observed state.

These assessments are advisory. They neither validate an unseen plan nor grant
permission to install a response, cancel inference, or reuse an occupied worker.
"""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from .contracts import Skill, Snapshot

RELEVANCE_SCHEMA = "edgepilot.relevance.v1"


class Relevance(StrEnum):
    INVALIDATING = "invalidating"
    HARMLESS = "harmless"
    ACCOMMODATING = "accommodating"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Dependency:
    field: str
    skills: tuple[Skill, ...]


@dataclass(frozen=True)
class RequestDependencies:
    request_id: str
    source: Snapshot

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("Request identity is required")
        if not isinstance(self.source, Snapshot):
            raise ValueError("Dependencies require a typed source snapshot")

    @property
    def fields(self) -> tuple[Dependency, ...]:
        """The fixed station-mission contract; no generated plan is inspected."""
        all_skills = tuple(Skill)
        fields = [Dependency("mission", all_skills), Dependency("position", all_skills),
                  Dependency("active_skill", all_skills),
                  Dependency("inspected", (Skill.NAVIGATE, Skill.INSPECT, Skill.HOME))]
        for name in self.source.mission.required_stations:
            for field in ("presence", "position", "available"):
                fields.append(Dependency(f"stations.{name}.{field}", (Skill.NAVIGATE, Skill.INSPECT)))
        return tuple(fields)

    def to_dict(self) -> dict:
        return {"relevance_schema": RELEVANCE_SCHEMA, "request_id": self.request_id,
                "source_snapshot_id": self.source.snapshot_id,
                "source_observed_at_ns": self.source.observed_at_ns,
                "source_goal_version": self.source.mission.version,
                "required_stations": self.source.mission.required_stations,
                "fields": [asdict(field) for field in self.fields]}


@dataclass(frozen=True)
class Evidence:
    field: str
    before: Any
    after: Any
    classification: Relevance
    reason: str


@dataclass(frozen=True)
class RelevanceDecision:
    request_id: str
    source_snapshot_id: int
    current_snapshot_id: int
    source_goal_version: int
    current_goal_version: int
    source_observed_at_ns: int
    current_observed_at_ns: int
    classification: Relevance
    evidence: tuple[Evidence, ...]
    relevance_schema: str = RELEVANCE_SCHEMA

    @property
    def uncertainty(self) -> tuple[str, ...]:
        return tuple(item.reason for item in self.evidence if item.classification == Relevance.UNKNOWN)

    def to_dict(self) -> dict:
        return {**asdict(self), "uncertainty": self.uncertainty}


def classify_relevance(dependencies: RequestDependencies, current: Snapshot) -> RelevanceDecision:
    """Compare current state to the original request, never the previous tick.

Invalidating dominates unknown, then accommodating, then harmless. Evidence is
retained for all dependencies, including uncertainty beside known invalidation.
Availability blocks completion of a required inspection; it does not imply that
every possible output (such as wait) would fail the independent plan validator.
"""
    source = dependencies.source
    evidence: list[Evidence] = []

    def record(field, before, after, classification, reason):
        evidence.append(Evidence(field, before, after, classification, reason))

    if current.snapshot_id < source.snapshot_id or current.observed_at_ns < source.observed_at_ns:
        record("observation", (source.snapshot_id, source.observed_at_ns),
               (current.snapshot_id, current.observed_at_ns), Relevance.UNKNOWN, "observation_order_reversed")
    elif current.snapshot_id == source.snapshot_id and current != source:
        record("observation", source.snapshot_id, current.snapshot_id,
               Relevance.UNKNOWN, "snapshot_identity_reused")

    same_goal = current.mission == source.mission
    if current.mission.version != source.mission.version:
        record("mission", source.mission, current.mission, Relevance.INVALIDATING, "goal_superseded")
    elif not same_goal:
        record("mission", source.mission, current.mission, Relevance.UNKNOWN, "goal_changed_without_version")

    if source.position is None or current.position is None:
        record("position", source.position, current.position, Relevance.UNKNOWN, "position_missing")
    elif source.position != current.position:
        record("position", source.position, current.position, Relevance.UNKNOWN, "robot_moved")

    if source.active_skill is not None or current.active_skill is not None:
        record("active_skill", source.active_skill, current.active_skill,
               Relevance.UNKNOWN, "executor_occupancy_requires_validation")

    required = set(source.mission.required_stations)
    before_credit = set(source.inspected) & required
    after_credit = set(current.inspected) & required
    # Inspection credit is scoped to a goal; replacement cannot count as progress.
    if same_goal and before_credit != after_credit:
        lost = before_credit - after_credit
        record("inspected", tuple(sorted(before_credit)), tuple(sorted(after_credit)),
               Relevance.UNKNOWN if lost else Relevance.ACCOMMODATING,
               "inspection_credit_lost" if lost else "required_inspection_completed")

    before_stations = {s.name: s for s in source.stations}
    after_stations = {s.name: s for s in current.stations}
    for name in source.mission.required_stations:
        before, after = before_stations.get(name), after_stations.get(name)
        field = f"stations.{name}"
        if before is None or after is None:
            record(field + ".presence", before is not None, after is not None,
                   Relevance.UNKNOWN, "required_station_missing")
        if before is not None and after is not None and before.position != after.position:
            record(field + ".position", before.position, after.position,
                   Relevance.UNKNOWN, "required_station_moved")
        old = before.available if before is not None else None
        new = after.available if after is not None else None
        if old is None or new is None:
            record(field + ".available", old, new, Relevance.UNKNOWN, "required_availability_missing")
        if same_goal and name not in after_credit and new is False:
            record(field + ".available", old, new, Relevance.INVALIDATING, "required_station_unavailable")
        elif old is not None and new is not None and old != new:
            if same_goal and name not in after_credit and new is True:
                record(field + ".available", old, new, Relevance.ACCOMMODATING, "required_station_restored")
            else:
                record(field + ".available", old, new, Relevance.UNKNOWN, "completed_or_replaced_target_changed")

    # Explicitly retain ignored changes so harmless decisions remain inspectable.
    for name in sorted((before_stations.keys() | after_stations.keys()) - required):
        before, after = before_stations.get(name), after_stations.get(name)
        if before != after:
            record(f"stations.{name}", before, after, Relevance.HARMLESS, "outside_declared_task")
    outside_before = tuple(sorted(set(source.inspected) - required))
    outside_after = tuple(sorted(set(current.inspected) - required))
    if outside_before != outside_after:
        record("inspected.outside_task", outside_before, outside_after,
               Relevance.HARMLESS, "outside_declared_task")
    if not evidence:
        record("dependencies", "unchanged", "unchanged", Relevance.HARMLESS, "declared_dependencies_unchanged")
    classifications = {item.classification for item in evidence}
    classification = next(kind for kind in (Relevance.INVALIDATING, Relevance.UNKNOWN,
                                            Relevance.ACCOMMODATING, Relevance.HARMLESS)
                          if kind in classifications)
    return RelevanceDecision(dependencies.request_id, source.snapshot_id, current.snapshot_id,
                             source.mission.version, current.mission.version,
                             source.observed_at_ns, current.observed_at_ns, classification, tuple(evidence))
