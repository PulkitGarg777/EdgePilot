"""EP-015 declared-relevance contract tests."""

from dataclasses import replace
import json

import pytest

from edgepilot.robot.contracts import HOME, Mission, Point, Skill, Snapshot, Station
from edgepilot.robot.relevance import Relevance, RequestDependencies, classify_relevance


def snapshot(*, required=("station_a",), station_a=True, station_b=True,
             inspected=(), position=HOME, active=None, snapshot_id=1, observed_at_ns=0):
    return Snapshot(snapshot_id, observed_at_ns, Mission(1, 0, required), position,
                    (Station("station_a", Point(2, 0), station_a),
                     Station("station_b", Point(2, 2), station_b)),
                    inspected, active)


def classify(source, current):
    return classify_relevance(RequestDependencies("request-1", source), current)


def reasons(decision):
    return {item.reason for item in decision.evidence}


def test_declared_dependencies_are_inspectable_and_serializable():
    declaration = RequestDependencies("request-1", snapshot())
    encoded = json.loads(json.dumps(declaration.to_dict()))
    assert encoded["required_stations"] == ["station_a"]
    fields = {item["field"]: item["skills"] for item in encoded["fields"]}
    assert fields["stations.station_a.available"] == ["navigate_to", "inspect"]
    assert "stations.station_b.available" not in fields


def test_unchanged_or_outside_task_updates_are_harmless():
    source = snapshot()
    telemetry = replace(source, snapshot_id=2, observed_at_ns=10)
    outside = replace(telemetry, stations=(source.stations[0], replace(source.stations[1], available=False)))
    assert classify(source, telemetry).classification == Relevance.HARMLESS
    decision = classify(source, outside)
    assert decision.classification == Relevance.HARMLESS
    assert reasons(decision) == {"outside_declared_task"}


def test_unavailable_remaining_target_is_invalidating():
    source = snapshot()
    current = snapshot(station_a=False, snapshot_id=2, observed_at_ns=10)
    decision = classify(source, current)
    assert decision.classification == Relevance.INVALIDATING
    assert "required_station_unavailable" in reasons(decision)


@pytest.mark.parametrize("change, expected_reason", [
    ({"inspected": ("station_a",)}, "required_inspection_completed"),
    ({"station_a": True}, "required_station_restored"),
])
def test_progress_and_restored_feasibility_are_accommodating(change, expected_reason):
    source = snapshot(station_a=False if "station_a" in change else True)
    current = snapshot(snapshot_id=2, observed_at_ns=10, **change)
    decision = classify(source, current)
    assert decision.classification == Relevance.ACCOMMODATING
    assert expected_reason in reasons(decision)


@pytest.mark.parametrize("current, expected_reason", [
    (snapshot(position=None, snapshot_id=2, observed_at_ns=10), "position_missing"),
    (snapshot(station_a=None, snapshot_id=2, observed_at_ns=10), "required_availability_missing"),
    (snapshot(position=Point(1, 0), snapshot_id=2, observed_at_ns=10), "robot_moved"),
    (snapshot(active=Skill.WAIT, snapshot_id=2, observed_at_ns=10), "executor_occupancy_requires_validation"),
])
def test_missing_or_changed_execution_state_is_unknown(current, expected_reason):
    decision = classify(snapshot(), current)
    assert decision.classification == Relevance.UNKNOWN
    assert expected_reason in decision.uncertainty


def test_goal_supersession_dominates_other_uncertainty_and_never_authorizes_reuse():
    source = snapshot()
    current = replace(snapshot(position=None, snapshot_id=3, observed_at_ns=20),
                      mission=Mission(2, 20, ("station_a",)))
    decision = classify(source, current)
    assert decision.classification == Relevance.INVALIDATING
    assert "goal_superseded" in reasons(decision)
    assert "position_missing" in decision.uncertainty


def test_same_version_goal_mutation_and_reversed_observation_are_unknown():
    source = snapshot(snapshot_id=4, observed_at_ns=20)
    mutated = replace(source, snapshot_id=5, observed_at_ns=30,
                      mission=Mission(1, 0, ("station_a", "station_b")))
    assert "goal_changed_without_version" in classify(source, mutated).uncertainty
    reversed_state = replace(source, snapshot_id=3, observed_at_ns=10)
    assert "observation_order_reversed" in classify(source, reversed_state).uncertainty


def test_current_state_is_compared_to_request_snapshot_after_multiple_updates():
    source = snapshot()
    unavailable = snapshot(station_a=False, snapshot_id=2, observed_at_ns=10)
    telemetry_after = replace(unavailable, snapshot_id=3, observed_at_ns=20)
    assert classify(source, unavailable).classification == Relevance.INVALIDATING
    assert classify(source, telemetry_after).classification == Relevance.INVALIDATING
