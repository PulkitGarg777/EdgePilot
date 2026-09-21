"""Offline model-mission tests using a virtual clock and controlled model worker."""

from dataclasses import replace
import json

import pytest

from edgepilot.contracts import FailureKind, Policy
from edgepilot.mission_runtime import MissionEpisode, SCENARIOS
from edgepilot.robot.contracts import EventKind, HOME, NS, WorldEvent
from edgepilot.robot.model import ModelResult, decode_plan, make_request, payload_for
from edgepilot.robot.planner import scripted_plan
from edgepilot.robot.world import World


def reply(actions, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps({"actions": actions})}}]}


class FakeModel:
    def __init__(self, duration_ns=NS//2, outcomes=None):
        self.duration_ns = duration_ns
        self.outcomes = outcomes or {}
        self.now = 0
        self.active = None
        self.started = 0
        self.submitted = []
        self.closed = False

    def submit(self, request):
        assert self.active is None
        self.active, self.started = request, self.now
        self.submitted.append(request)

    def poll(self, now_ns):
        if self.active is None or now_ns < self.started+self.duration_ns:
            return None
        request = self.active
        fields = {"response": reply([a.to_dict() for a in scripted_plan(request).actions]),
                  **self.outcomes.get(len(self.submitted), {})}
        self.active = None
        return ModelResult(request.request_id, self.started, self.started+self.duration_ns, **fields)

    def close(self):
        assert self.active is None
        self.closed = True


def run(policy=Policy.FIFO, scenario="nominal", worker=None, **kwargs):
    worker = worker or FakeModel()
    log = []
    episode = MissionEpisode(policy, worker, SCENARIOS[scenario], emit=lambda **e: log.append(e), **kwargs)
    for now in range(0, 60*NS+1, NS//20):
        worker.now = now
        if episode.step(now):
            break
    assert episode.done
    worker.close()
    assert worker.closed
    assert len(episode.terminals) == episode.requests
    assert len({r["request_id"] for r in episode.terminals}) == episode.requests
    return episode, worker, log


@pytest.mark.parametrize("policy", list(Policy))
def test_nominal_model_response_executes_full_guarded_mission(policy):
    episode, worker, log = run(policy)
    assert episode.world.outcome == "succeeded"
    assert episode.world.position == HOME and episode.world.inspected == {"station_a", "station_b"}
    assert episode.world.actions_completed == 5
    assert len(worker.submitted) == 1
    assert episode.summary()["terminal_counts"] == {"accepted": 1}
    assert len([e for e in log if e["event"] == "validation" and e["phase"] == "dispatch"]) == 5


def test_fifo_and_newest_pending_share_world_checks_but_have_different_queue_order():
    fifo, fifo_worker, _ = run(Policy.FIFO, "goal-burst")
    newest, newest_worker, _ = run(Policy.NEWEST_PENDING, "goal-burst")
    assert [r.snapshot.mission.version for r in fifo_worker.submitted] == [1, 2, 3, 4]
    assert [r.snapshot.mission.version for r in newest_worker.submitted] == [1, 4]
    assert fifo.max_pending == 3 and newest.max_pending == 1
    assert fifo.summary()["terminal_counts"] == {"rejected_stale_goal": 3, "accepted": 1}
    assert newest.summary()["terminal_counts"] == {"coalesced_before_inference": 2, "rejected_stale_goal": 1, "accepted": 1}
    assert fifo.rejected_worker_wall_ns == 3*NS//2 and newest.rejected_worker_wall_ns == NS//2
    assert fifo.world.inspected == newest.world.inspected == {"station_a", "station_b"}
    for episode in (fifo, newest):
        for goal in list(episode.goal_metrics.values())[:3]:
            assert goal["latency_ns"] is None and goal["status"] == "superseded"


@pytest.mark.parametrize("policy", list(Policy))
def test_all_goals_preserved_when_late_loop_observes_multiple_updates(policy):
    worker = FakeModel(2*NS)
    episode = MissionEpisode(policy, worker, SCENARIOS["goal-burst"])
    episode.step(0)
    worker.now = NS
    episode.step(NS)
    assert list(episode.goal_metrics) == [1, 2, 3, 4]
    assert episode.requests == 4
    assert len(episode.pending) == (3 if policy == Policy.FIFO else 1)
    assert episode.world.actions_completed == 0


@pytest.mark.parametrize("policy", list(Policy))
def test_scene_events_and_observations_continue_during_inference(policy):
    episode, worker, log = run(policy, "irrelevant", worker=FakeModel(2*NS))
    assert len(worker.submitted) == 1
    updates = [e for e in log if e["event"] == "world_event"]
    assert [e["at_ns"] for e in updates] == [NS//5, 3*NS//5]
    assert len([e for e in log if e["event"] == "observation" and e["at_ns"] < 2*NS]) == 40
    assert episode.world.outcome == "succeeded"


@pytest.mark.parametrize("policy", list(Policy))
def test_station_change_before_response_rejects_invalid_plan_then_recovers(policy):
    episode, _, log = run(policy, "station-unavailable", worker=FakeModel(NS))
    assert episode.world.outcome == "succeeded"
    assert episode.summary()["terminal_counts"].get("rejected_precondition_failed") == 1
    assert not any(e["event"] == "skill_started" and NS//2 <= e["at_ns"] < 3*NS//2 for e in log)


@pytest.mark.parametrize("policy", list(Policy))
def test_goal_change_during_execution_stops_old_skill_and_completes_new_mission(policy):
    episode, _, _ = run(policy, "goal-replacement", worker=FakeModel(NS//10))
    assert episode.world.outcome == "succeeded"
    assert episode.world.actions_stopped == 1
    assert episode.world.inspected == {"station_b"}
    assert [m["outcome"] for m in episode.world.mission_records] == ["superseded", "succeeded"]


def test_all_due_updates_precede_inspection_completion_in_same_step():
    log = []
    worker = FakeModel(0)
    events = (WorldEvent(1550_000_000, EventKind.TELEMETRY),
              WorldEvent(1550_000_000, EventKind.GOAL, required_stations=("station_b",)))
    episode = MissionEpisode(Policy.FIFO, worker, events, emit=lambda **e: log.append(e))
    for now in range(0, 1600_000_000, NS//20):
        worker.now = now
        episode.step(now)
    assert not any(e["event"] == "skill_completed" and e["skill"]["skill"] == "inspect" for e in log)
    assert episode.world.inspected == set()


@pytest.mark.parametrize("policy", list(Policy))
def test_deadline_drains_active_call_and_keeps_observing_without_reuse(policy):
    episode, worker, log = run(policy, "goal-burst", worker=FakeModel(2*NS), deadline_ns=NS//2)
    assert len(worker.submitted) == 1
    assert episode.world.now_ns == 2*NS and episode.world.outcome == "timed_out"
    assert episode.stop_reason == "episode_deadline"
    assert episode.world.actions_completed == 0
    assert any(e["event"] == "observation" and e["at_ns"] > NS//2 and e["worker_occupied"] for e in log)
    assert episode.goal_metrics[4]["status"] == "censored_episode_deadline"
    assert not any("cancel" in e["event"] for e in log)


def test_inflight_request_deadline_expires_without_cancelling():
    episode, worker, _ = run(worker=FakeModel(NS), request_ttl_ns=NS//2, max_requests=2)
    assert len(worker.submitted) == 2
    assert episode.summary()["terminal_counts"] == {"request_expired": 2}
    assert episode.stop_reason == "request_budget_exhausted"
    assert episode.world.actions_completed == 0


def test_expired_queued_request_never_reaches_inference():
    worker = FakeModel(NS)
    episode = MissionEpisode(Policy.FIFO, worker, (WorldEvent(NS//10, EventKind.GOAL, required_stations=("station_b",)),),
                             request_ttl_ns=NS//5, max_requests=2)
    for now in range(0, 2*NS, NS//20):
        worker.now = now
        if episode.step(now):
            break
    assert episode.done and len(worker.submitted) == 1
    assert episode.summary()["terminal_counts"] == {"rejected_stale_goal": 1, "request_expired": 1}


def test_transport_timeout_aborts_without_another_http_request():
    worker = FakeModel(outcomes={1: {"error": "timeout", "failure_kind": FailureKind.TIMEOUT,
                                     "server_state_uncertain": True}})
    episode, worker, _ = run(scenario="goal-burst", worker=worker)
    assert len(worker.submitted) == 1
    assert episode.stop_reason == "transport_state_uncertain"
    assert episode.summary()["terminal_counts"]["http_timed_out"] == 1


def test_confirmed_fake_worker_failure_can_refresh_and_complete():
    worker = FakeModel(outcomes={1: {"error": "confirmed fake failure", "failure_kind": FailureKind.ERROR}})
    episode, _, _ = run(worker=worker)
    assert episode.world.outcome == "succeeded"
    assert episode.summary()["terminal_counts"] == {"worker_failed": 1, "accepted": 1}


@pytest.mark.parametrize("bad_response,state", [
    (reply([{"skill": "return_home"}], finish="length"), "rejected_invalid_plan"),
    (reply([{"skill": "inspect", "target": "station_a"}]), "rejected_precondition_failed"),
    (reply([{"skill": "navigate_to", "target": "invented"}]), "rejected_unknown_target"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "{broken"}}]}, "rejected_invalid_plan"),
    ({"choices": [None]}, "rejected_invalid_plan"),
    ({"choices": "not an array"}, "rejected_invalid_plan"),
    ({"choices": [{"finish_reason": "stop", "message": None}]}, "rejected_invalid_plan"),
])
def test_model_errors_are_classified_and_refreshed_without_weakening_checks(bad_response, state):
    episode, _, _ = run(worker=FakeModel(outcomes={1: {"response": bad_response}}))
    assert episode.world.outcome == "succeeded"
    assert episode.summary()["terminal_counts"] == {state: 1, "accepted": 1}


def test_accepted_wait_plans_do_not_count_as_completed_missions():
    wait = reply([{"skill": "wait", "duration_ns": NS//10}])
    episode, _, _ = run(worker=FakeModel(outcomes={1: {"response": wait}, 2: {"response": wait}}), max_requests=2)
    assert episode.summary()["terminal_counts"] == {"accepted": 2}
    assert episode.world.outcome == "planner_failed"
    assert episode.world.inspected == set()
    assert episode.goal_metrics[1]["status"] == "accepted"  # Plan acceptance and mission success are separate.


def test_required_unavailable_station_stays_in_mission_until_timeout():
    episode, worker, _ = run(scenario="blocked", deadline_ns=NS)
    assert episode.world.outcome == "timed_out"
    assert worker.submitted == []
    assert episode.world.mission.required_stations == ("station_a", "station_b")


def test_validation_profiling_is_optional_and_uses_injected_clock():
    clock = iter(range(0, 1_000_000, 10))
    wall_clock = iter(range(0, 1_000_000, 25))
    episode, _, log = run(validation_clock=lambda: next(clock), validation_wall_clock=lambda: next(wall_clock))
    cpu = episode.summary()["validation_cpu"]
    assert cpu["samples"] > 5 and cpu["total_ns"] == cpu["samples"]*10
    assert cpu["p95_ns"] == 10
    assert all(e["validation_time_ns"] == 10 for e in log if e["event"] == "validation")
    wall = episode.summary()["validation_wall"]
    assert wall["samples"] == cpu["samples"] and wall["p95_ns"] == 25
    assert all(e["validation_wall_ns"] == 25 for e in log if e["event"] == "validation")


def test_model_prompt_is_stable_under_request_identity_and_clock_jitter():
    state = World().snapshot()
    first = make_request("first", 0, state)
    second = make_request("second", NS, replace(state, snapshot_id=42, observed_at_ns=NS,
                                                mission=replace(state.mission, created_at_ns=NS)))
    assert first.prompt == second.prompt and first.prompt_sha256 == second.prompt_sha256
    assert payload_for(first) == payload_for(second)
    raw = reply([{"skill": "return_home"}])
    plan = decode_plan(raw, second)
    assert plan.request_id == "second" and plan.snapshot_id == 42


def test_request_provenance_rejects_invalid_hash_deadline_and_output_budget():
    request = make_request("request", 0, World().snapshot())
    for fields in ({"prompt_sha256": "wrong"}, {"max_tokens": True}, {"created_at_ns": NS, "deadline_ns": 0},
                   {"model_id": ""}):
        with pytest.raises(ValueError):
            replace(request, **fields)


@pytest.mark.parametrize("raw", [None, {}, [], reply([], "stop"),
    reply([{"skill": "return_home", "extra": True}]), reply([{"skill": "wait", "duration_ns": True}])])
def test_schema_constraints_never_replace_independent_response_validation(raw):
    with pytest.raises((ValueError, TypeError, KeyError, IndexError)):
        decode_plan(raw, make_request("request", 0, World().snapshot()))
