"""Headless simulation and guardrail checks; no real model or GPU is involved."""

from dataclasses import FrozenInstanceError, replace
import json

import pytest

from edgepilot.robot.contracts import (Action, EventKind, HOME, Mission, NS, Plan, Point,
                                      Reason, Skill, Station, WorldEvent)
from edgepilot.robot.planner import PlanningRequest, ScriptedPlanner, scripted_plan
from edgepilot.robot.validation import validate_action, validate_plan
from edgepilot.robot.world import World
from edgepilot.simulation import Episode, SCENARIOS


def install_script(world):
    request = PlanningRequest("test-request", world.now_ns, world.snapshot())
    world.expect(request.request_id)
    plan = scripted_plan(request)
    assert world.install(plan)
    return plan


def run_episode(scenario="nominal", delay=0, deadline=20*NS, planner=None):
    log = []
    episode = Episode(SCENARIOS[scenario], planner=planner or ScriptedPlanner(delay), deadline_ns=deadline,
                      emit=lambda **e: log.append(e))
    for now in range(0, deadline+NS//20, NS//20):
        if episode.step(now):
            break
    assert episode.done
    return episode, log


def test_nominal_mission_inspects_both_stations_and_returns_home():
    episode, log = run_episode()
    assert episode.world.outcome == "succeeded"
    assert episode.world.position == HOME
    assert episode.world.inspected == {"station_a", "station_b"}
    assert episode.world.active is None and not episode.world.pending
    assert episode.world.actions_completed == 5
    assert [e["skill"]["skill"] for e in log if e["event"] == "skill_started"] == [
        "navigate_to", "inspect", "navigate_to", "inspect", "return_home"]
    assert episode.world.mission_records[0]["outcome"] == "succeeded"


def test_navigation_interpolates_and_inspection_only_credits_after_duration():
    world = World()
    install_script(world)
    assert world.dispatch()
    world.advance(NS//2)
    assert world.position == Point(1, 0)
    assert not world.inspected
    world.advance(NS)
    assert world.position == Point(2, 0) and world.active is None
    assert world.dispatch()
    world.advance(NS + NS//4)
    assert not world.inspected
    world.advance(3*NS//2)
    assert world.inspected == {"station_a"}


def test_identical_script_and_virtual_clock_replay_exact_trace():
    first, first_log = run_episode("station-unavailable", delay=NS//5)
    second, second_log = run_episode("station-unavailable", delay=NS//5)
    assert first.summary() == second.summary()
    assert first_log == second_log


def test_external_events_and_observations_continue_while_planner_is_pending():
    episode, log = run_episode("irrelevant", delay=2*NS)
    assert episode.world.outcome == "succeeded"
    updates = [e for e in log if e["event"] == "world_event"]
    assert [e["at_ns"] for e in updates] == [NS//5, 3*NS//5]
    assert all(e["lateness_ns"] == 0 for e in updates)
    assert len([e for e in log if e["event"] == "observation" and e["at_ns"] < 2*NS]) == 40
    assert episode.requests == 1


def test_executor_can_advance_while_independent_scripted_worker_is_pending():
    world = World()
    install_script(world)
    world.dispatch()
    worker = ScriptedPlanner(delay_ns=2*NS)
    worker.submit(PlanningRequest("pending", 0, world.snapshot()))
    world.advance(NS//2)
    assert worker.poll(NS//2) is None
    assert world.position == Point(1, 0)
    world.advance(NS)
    assert world.position == Point(2, 0) and world.actions_completed == 1


def test_target_unavailability_stops_motion_then_recovers_without_teleporting():
    episode, log = run_episode("station-unavailable")
    stops = [e for e in log if e["event"] == "skill_stopped"]
    assert len(stops) == 1
    assert 0 < stops[0]["position"]["x"] < 2
    assert stops[0]["position"]["y"] == 0
    assert episode.world.outcome == "succeeded" and episode.world.position == HOME
    assert episode.world.inspected == {"station_a", "station_b"}
    assert episode.requests == 2
    assert not any(e["event"] == "skill_started" and NS//2 <= e["at_ns"] < 3*NS//2 for e in log)


def test_revalidation_catches_change_after_installation_before_dispatch():
    log = []
    world = World(lambda **e: log.append(e))
    install_script(world)
    world.advance(0, (WorldEvent(0, EventKind.AVAILABILITY, "station_a", False),))
    assert not world.dispatch()
    assert world.position == HOME and world.active is None
    assert not any(e["event"] == "skill_started" for e in log)
    assert any(e["event"] == "validation" and e["phase"] == "dispatch" and not e["accepted"] for e in log)


def test_invalidation_wins_tie_with_inspection_completion():
    world = World()
    install_script(world)
    world.dispatch()
    world.advance(NS)
    world.dispatch()
    world.advance(3*NS//2, (WorldEvent(3*NS//2, EventKind.AVAILABILITY, "station_a", False),))
    assert "station_a" not in world.inspected
    assert world.active is None and not world.pending
    assert world.actions_stopped == 1


def test_new_goal_stops_current_skill_and_only_new_mission_gets_credit():
    episode, log = run_episode("goal-replacement")
    assert episode.world.outcome == "succeeded"
    assert episode.world.mission.version == 2
    assert episode.world.inspected == {"station_b"}
    assert [r["outcome"] for r in episode.world.mission_records] == ["superseded", "succeeded"]
    assert any(e["event"] == "skill_stopped" and e["reason"] == Reason.STALE_GOAL for e in log)


def test_old_pending_result_cannot_install_after_goal_change():
    episode, log = run_episode("goal-replacement", delay=NS)
    assert episode.plans_rejected == 1 and episode.plans_installed == 1
    checks = [e for e in log if e["event"] == "validation" and e["phase"] == "install"]
    assert checks[0]["reason_code"] == Reason.STALE_GOAL
    assert episode.world.outcome == "succeeded"
    assert episode.world.inspected == {"station_b"}


def test_plan_is_not_rejected_just_because_observation_is_older():
    world = World()
    world.expect("request")
    plan = scripted_plan(PlanningRequest("request", 0, world.snapshot()))
    world.advance(NS, (WorldEvent(NS, EventKind.TELEMETRY),))
    assert world.install(plan)
    assert not world.install(plan)  # Completed request IDs cannot be replayed.


@pytest.mark.parametrize("change,reason", [
    ({"request_id": "unexpected"}, Reason.STALE_REQUEST),
    ({"goal_version": 2}, Reason.STALE_GOAL),
    ({"snapshot_id": 999}, Reason.FUTURE_SNAPSHOT),
    ({"snapshot_id": 1}, Reason.SNAPSHOT_MISMATCH),
])
def test_plan_identity_and_provenance_checks(change, reason):
    world = World()
    world.expect("request")
    plan = scripted_plan(PlanningRequest("request", 0, world.snapshot()))
    world.advance(NS)
    check = validate_plan(replace(plan, **change), world.snapshot(), "request", 0)
    assert not check.accepted and check.reason == reason


def test_plan_cannot_replace_busy_executor():
    log = []
    world = World(lambda **e: log.append(e))
    plan = install_script(world)
    world.dispatch()
    world.expect("replacement")
    assert not world.install(replace(plan, request_id="replacement"))
    assert world.active is not None and world.active.request_id == plan.request_id
    assert not any(e["event"] == "wait_fallback" for e in log)


def test_snapshot_identity_changes_on_dispatch_and_completion():
    world = World()
    install_script(world)
    idle = world.snapshot()
    world.dispatch()
    moving = world.snapshot()
    assert moving.snapshot_id > idle.snapshot_id
    assert idle.active_skill is None and moving.active_skill == Skill.NAVIGATE
    world.advance(NS)
    arrived = world.snapshot()
    assert arrived.snapshot_id > moving.snapshot_id
    assert arrived.active_skill is None


def test_installation_projects_navigation_before_inspection_but_rejects_inspect_here():
    world = World()
    world.expect("request")
    wrong = Plan("request", 1, 0, (Action(Skill.INSPECT, "station_a"),))
    assert not world.install(wrong)
    right = Plan("request", 1, 0, (Action(Skill.NAVIGATE, "station_a"), Action(Skill.INSPECT, "station_a")))
    assert world.install(right)


def test_unknown_target_and_unknown_state_fail_closed():
    state = World().snapshot()
    assert validate_action(Action(Skill.NAVIGATE, "missing"), state).reason == Reason.UNKNOWN_TARGET
    assert validate_action(Action(Skill.HOME), replace(state, position=None)).reason == Reason.MISSING_STATE
    unknown_station = replace(state.stations[0], available=None)
    assert validate_action(Action(Skill.NAVIGATE, "station_a"),
                           replace(state, stations=(unknown_station, state.stations[1]))).reason == Reason.MISSING_STATE


def test_wait_is_timed_and_is_not_mission_success():
    world = World()
    world.expect("wait-request")
    assert world.install(Plan("wait-request", 1, 0, (Action(Skill.WAIT, duration_ns=NS),)))
    world.dispatch()
    world.advance(NS//2)
    assert world.active is not None and world.position == HOME
    world.advance(NS)
    assert world.active is None and world.outcome == "running"
    assert world.inspected == set()


def test_permanently_blocked_mission_times_out_with_no_false_success():
    episode, log = run_episode("blocked", deadline=NS)
    assert episode.world.outcome == "timed_out"
    assert episode.requests == 0 and not episode.world.inspected
    assert not any(e["event"] == "mission_succeeded" for e in log)
    assert any(e["event"] == "wait_fallback" for e in log)


def test_deadline_stops_active_skill_and_wins_completion_tie():
    world = World()
    install_script(world)
    world.dispatch()
    world.advance(NS, deadline=True)
    assert world.outcome == "timed_out" and world.actions_completed == 0
    assert world.active is None and not world.pending


def test_mock_planning_delay_past_deadline_terminates_without_claiming_cancellation():
    episode, log = run_episode(delay=2*NS, deadline=NS)
    assert episode.world.outcome == "timed_out"
    assert episode.planner.pending is None and episode.plans_installed == 0
    assert any(e["event"] == "scripted_request_abandoned" for e in log)
    assert not any("cancel" in e["event"] for e in log)


def test_scripted_worker_failure_records_failure_and_never_executes():
    episode, log = run_episode(planner=ScriptedPlanner(NS//2, fail=True))
    assert episode.world.outcome == "planner_failed"
    assert episode.world.actions_completed == 0 and episode.world.active is None
    assert any(e["event"] == "scripted_result" and e["result"]["error"] for e in log)


def test_large_time_jump_does_not_backfill_a_sequence_of_skills():
    world = World()
    install_script(world)
    world.dispatch()
    world.advance(10*NS)
    assert world.position == Point(2, 0) and world.actions_completed == 1
    assert world.inspected == set()
    world.dispatch()
    assert world.active.started_at_ns == 10*NS


def test_unknown_event_target_rejected_before_mutation_and_clock_cannot_reverse():
    world = World()
    before = world.snapshot()
    with pytest.raises(ValueError):
        world.advance(NS, (WorldEvent(NS, EventKind.AVAILABILITY, "missing", False),))
    assert world.snapshot() == before
    with pytest.raises(ValueError):
        world.advance(NS, (WorldEvent(NS, EventKind.GOAL, required_stations=("missing",)),))
    world.advance(NS)
    with pytest.raises(ValueError):
        world.advance(0)


def test_snapshots_are_immutable_and_plan_wire_format_round_trips():
    world = World()
    snapshot = world.snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.position = Point(1, 1)
    plan = scripted_plan(PlanningRequest("request", 0, snapshot))
    assert Plan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan


@pytest.mark.parametrize("value", [
    None, [], {}, {"skill": "fly"}, {"skill": "return_home", "target": "station_a"},
    {"skill": "inspect"}, {"skill": "navigate_to", "target": 12},
    {"skill": "wait", "duration_ns": True}, {"skill": "wait", "duration_ns": 0},
    {"skill": "wait", "duration_ns": 11*NS}, {"skill": "wait", "duration_ns": 1.0},
    {"skill": "wait", "duration_ns": NS, "extra": True},
])
def test_malformed_action_contracts_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        Action.from_dict(value)


@pytest.mark.parametrize("alter", [
    {"extra": True}, {"actions": []}, {"actions": [{"skill": "return_home"}]*17},
    {"goal_version": True}, {"request_id": " "}, {"snapshot_id": -1},
])
def test_malformed_plan_rejected_without_dispatch(alter):
    world = World()
    world.expect("request")
    raw = {**scripted_plan(PlanningRequest("request", 0, world.snapshot())).to_dict(), **alter}
    assert not world.install(raw)
    assert not world.dispatch() and world.active is None


@pytest.mark.parametrize("point", [(float("nan"), 0), (0, float("inf")), (-1, 0), (5, 0), (True, 0)])
def test_positions_are_finite_and_bounded(point):
    with pytest.raises(ValueError):
        Point(*point)


def test_mission_and_event_contracts_reject_invalid_versions_targets_and_flags():
    with pytest.raises(ValueError):
        Mission(True, 0, ())
    with pytest.raises(ValueError):
        Mission(1, 0, ("station_a", "station_a"))
    with pytest.raises(ValueError):
        WorldEvent(-1, EventKind.TELEMETRY)
    with pytest.raises(ValueError):
        WorldEvent(0, EventKind.AVAILABILITY, "station_a", 1)
    with pytest.raises(ValueError):
        Station("a", HOME, 1)
