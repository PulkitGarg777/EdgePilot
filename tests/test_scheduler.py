"""Offline only: a controllable worker and manually advanced monotonic clock."""

from dataclasses import replace
import json

import pytest

from edgepilot.contracts import FailureKind, Policy, Result, TerminalState
from edgepilot.scenarios import SCENARIOS, Scenario, goal, observation
from edgepilot.scheduler import Scheduler
from edgepilot.validation import validate_response


def reply(content=None, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content":
            json.dumps({"action": "wait", "reason": "idle"}) if content is None else content}}]}


class FakeWorker:
    def __init__(self, duration=.5, outcomes=None):
        self.duration = duration
        self.outcomes = outcomes or {}
        self.active = None
        self.submitted = []
        self.closed = False

    def submit(self, request, now):
        assert self.active is None, "Overlapping inference"
        self.active = (request, now)
        self.submitted.append(request.goal.version)

    def poll(self, now):
        if self.active is None:
            return None
        request, started = self.active
        if now < started + self.duration:
            return None
        self.active = None
        fields = {"response": reply(), **self.outcomes.get(request.goal.version, {})}
        return Result(request.request_id, request.goal.version, started, started+self.duration, **fields)

    def close(self):
        assert self.active is None
        self.closed = True


def run(policy, scenario=SCENARIOS["burst"], worker=None, deadline=20):
    worker = worker or FakeWorker()
    events = []
    scheduler = Scheduler(policy, scenario, worker, lambda **event: events.append(event), deadline_seconds=deadline)
    for tick in range(2001):
        if scheduler.step(tick / 100):
            break
    assert scheduler.done, "Episode did not terminate under virtual clock"
    worker.close()
    assert worker.closed
    assert len(scheduler.terminals) == len(scheduler.goals)
    assert len({t.request_id for t in scheduler.terminals}) == len(scheduler.terminals)
    return scheduler, worker, events


def test_fifo_finishes_every_request_in_order_even_superseded_pending():
    scheduler, worker, _ = run(Policy.FIFO)
    assert worker.submitted == [1, 2, 3, 4, 5]
    assert [t.goal_version for t in scheduler.terminals] == worker.submitted
    assert [t.state for t in scheduler.terminals] == [TerminalState.STALE]*4 + [TerminalState.ACCEPTED]
    assert scheduler.max_pending == 3


def test_newest_pending_never_replaces_active_and_has_only_one_pending_slot():
    scheduler, worker, events = run(Policy.NEWEST_PENDING)
    assert worker.submitted == [1, 4, 5]
    assert scheduler.max_pending == 1
    assert [t.goal_version for t in scheduler.terminals if t.state == TerminalState.COALESCED] == [2, 3]
    assert [t.goal_version for t in scheduler.terminals if t.state == TerminalState.ACCEPTED] == [4, 5]
    assert len([e for e in events if e["event"] == "pending_replaced"]) == 2
    assert scheduler.rejected_worker_wall_seconds == .5


@pytest.mark.parametrize("policy", list(Policy))
def test_same_schedule_advances_and_observations_progress_during_slow_work(policy):
    scheduler, _, events = run(policy, worker=FakeWorker(duration=1))
    delivered = [e for e in events if e["event"] == "scenario_event"]
    assert [e["scheduled_at"] for e in delivered] == [e.at for e in SCENARIOS["burst"].events]
    assert all(e["elapsed_seconds"] == e["scheduled_at"] for e in delivered)
    assert len([e for e in events if e["event"] == "observation_tick" and 0 < e["elapsed_seconds"] < 1]) >= 15
    assert scheduler.summary()["max_observation_gap_seconds"] <= .061


@pytest.mark.parametrize("policy", list(Policy))
def test_irrelevant_updates_do_not_create_requests_or_invalidate_current_plan(policy):
    scheduler, worker, events = run(policy, SCENARIOS["irrelevant"])
    assert worker.submitted == [1]
    assert scheduler.terminals[0].state == TerminalState.ACCEPTED
    assert scheduler.observation.version == 4
    assert scheduler.accepted_plan == {"action": "wait", "reason": "idle"}
    assert all(not e["triggers_request"] for e in events if e["event"] == "observation_updated")


@pytest.mark.parametrize("policy", list(Policy))
@pytest.mark.parametrize("scenario", ["isolated", "spaced"])
def test_slow_isolated_changes_all_accepted_with_both_policies(policy, scenario):
    scheduler, worker, _ = run(policy, SCENARIOS[scenario])
    assert worker.submitted == [1, 2, 3]
    assert all(t.state == TerminalState.ACCEPTED for t in scheduler.terminals)
    assert all(g["latency_seconds"] == .5 for g in scheduler.goals.values())


def test_superseded_goals_are_censored_not_zero_latency_successes():
    scheduler, _, _ = run(Policy.NEWEST_PENDING)
    for version in (1, 2, 3):
        record = scheduler.goals[version]
        assert record["status"] == "superseded"
        assert record["latency_seconds"] is None
        assert record["accepted_at"] is None
    assert scheduler.goals[4]["latency_seconds"] == .7


@pytest.mark.parametrize("policy", list(Policy))
def test_goal_change_at_completion_boundary_rejects_old_result(policy):
    scenario = Scenario("tie", (goal(0, "A"), goal(.5, "B")), 1)
    scheduler, _, _ = run(policy, scenario)
    assert [t.state for t in scheduler.terminals] == [TerminalState.STALE, TerminalState.ACCEPTED]


@pytest.mark.parametrize("policy", list(Policy))
def test_invalid_output_uses_same_wait_fallback_and_next_request_runs(policy):
    worker = FakeWorker(outcomes={1: {"response": reply('{"action":"move","reason":"go"}')}})
    scheduler, _, events = run(policy, SCENARIOS["isolated"], worker)
    assert scheduler.terminals[0].state == TerminalState.INVALID
    assert worker.submitted == [1, 2, 3]
    assert any(e["event"] == "wait_fallback" and e["elapsed_seconds"] == .5 for e in events)
    assert scheduler.rejected_worker_wall_seconds == .5


@pytest.mark.parametrize("policy", list(Policy))
def test_confirmed_worker_failure_does_not_block_later_goals(policy):
    worker = FakeWorker(outcomes={1: {"error": "fake confirmed failure", "failure_kind": FailureKind.ERROR}})
    scheduler, _, _ = run(policy, SCENARIOS["isolated"], worker)
    assert scheduler.terminals[0].state == TerminalState.FAILED
    assert worker.submitted == [1, 2, 3]
    assert scheduler.goals[1]["latency_seconds"] is None


@pytest.mark.parametrize("policy", list(Policy))
def test_http_timeout_ends_episode_without_claiming_server_cancellation(policy):
    worker = FakeWorker(outcomes={1: {"error": "socket timeout", "failure_kind": FailureKind.TIMEOUT,
                                     "server_state_uncertain": True}})
    scheduler, _, events = run(policy, worker=worker)
    assert worker.submitted == [1]
    assert scheduler.stop_reason == "episode_aborted"
    assert any(t.state == TerminalState.TIMED_OUT for t in scheduler.terminals)
    assert any(t.state == TerminalState.ABORTED for t in scheduler.terminals)
    assert not any("cancel" in e["event"] for e in events)


@pytest.mark.parametrize("policy", list(Policy))
def test_deadline_discards_pending_but_observes_until_active_actually_returns(policy):
    scheduler, worker, events = run(policy, worker=FakeWorker(duration=2), deadline=.4)
    assert worker.submitted == [1]
    assert scheduler.stop_reason == "episode_deadline"
    assert scheduler.last_now == 2
    assert scheduler.accepted_plan is None
    assert any(e["event"] == "observation_tick" and e["elapsed_seconds"] > .4 for e in events)
    assert scheduler.terminals[-1].state == TerminalState.DEADLINE
    assert scheduler.goals[4]["status"] == "censored_episode_deadline"
    assert scheduler.summary()["unprocessed_scenario_events"] == 1


def test_deadline_wins_tie_with_goal_and_completion():
    scenario = Scenario("deadline-tie", (goal(0, "A"), goal(.5, "B")), 1)
    scheduler, worker, _ = run(Policy.FIFO, scenario, deadline=.5)
    assert worker.submitted == [1]
    assert scheduler.terminals[0].state == TerminalState.DEADLINE
    assert len(scheduler.goals) == 1


def test_backlog_drains_after_scenario_ends():
    scenario = Scenario("drain", (goal(0, "A"), goal(.1, "B"), goal(.2, "C")), .2)
    scheduler, worker, _ = run(Policy.FIFO, scenario)
    assert worker.submitted == [1, 2, 3]
    assert scheduler.last_now == 1.5


def test_empty_schedule_terminates_without_dispatch():
    scheduler, worker, _ = run(Policy.FIFO, Scenario("empty", (), 0))
    assert scheduler.done and not worker.submitted


def test_no_backfilled_observation_ticks_after_delayed_coordinator():
    events = []
    scheduler = Scheduler(Policy.FIFO, SCENARIOS["isolated"], FakeWorker(), lambda **e: events.append(e))
    scheduler.step(0)
    scheduler.step(1)
    assert scheduler.ticks == [0, 1]
    assert scheduler.summary()["max_observation_gap_seconds"] == 1


def test_request_snapshots_include_observation_versions():
    scenario = Scenario("snapshot", (goal(0, "A"), observation(.1), goal(.2, "B")), 1)
    _, _, events = run(Policy.FIFO, scenario)
    requests = [e["request"] for e in events if e["event"] == "request_queued"]
    assert [r["observation"]["version"] for r in requests] == [0, 1]
    assert [r["goal"]["version"] for r in requests] == [1, 2]


def test_mismatched_worker_identity_cannot_be_accepted():
    worker = FakeWorker()
    scheduler = Scheduler(Policy.FIFO, SCENARIOS["isolated"], worker, lambda **e: None)
    scheduler.step(0)
    result = replace(worker.poll(.5), goal_version=99)
    with pytest.raises(RuntimeError, match="different request/version"):
        scheduler._result(result, .5)
    assert scheduler.accepted_plan is None


def test_failed_submit_aborts_and_accounts_for_request():
    class BrokenWorker(FakeWorker):
        def submit(self, request, now):
            raise RuntimeError("Could not submit")
    scheduler, _, _ = run(Policy.FIFO, worker=BrokenWorker())
    assert scheduler.terminals[0].state == TerminalState.FAILED
    assert scheduler.stop_reason == "episode_aborted"


@pytest.mark.parametrize("response", [None, {}, [], reply("not json"), reply("[]"),
    reply('{"action":"move","reason":"go"}'), reply('{"action":"wait","reason":""}'),
    reply('{"action":"wait","reason":"  "}'), reply(finish="length"),
    reply('{"action":"wait","reason":"ok","extra":true}'),
    reply(json.dumps({"action": "wait", "reason": "x"*501}))])
def test_shared_validator_rejects_malformed_unsupported_extra_and_truncated_output(response):
    with pytest.raises((ValueError, KeyError, TypeError, IndexError)):
        validate_response(response)


def test_reject_invalid_schedule_and_clock():
    with pytest.raises(ValueError):
        Scenario("backwards", (goal(1, "A"), goal(0, "B")), 2)
    with pytest.raises(ValueError):
        Scenario("nan", (), float("nan"))
    scheduler = Scheduler(Policy.FIFO, SCENARIOS["isolated"], FakeWorker(), lambda **e: None)
    scheduler.step(.1)
    with pytest.raises(ValueError):
        scheduler.step(.05)
    with pytest.raises(ValueError):
        scheduler.step(float("nan"))
