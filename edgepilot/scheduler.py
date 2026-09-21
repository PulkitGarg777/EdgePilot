"""Single-owner coordinator. step() supports a real or manually advanced clock."""

from collections import Counter, deque
from dataclasses import asdict
import math
from typing import Callable

from .contracts import (FailureKind, Goal, Observation, Policy, Request, Result,
                        Terminal, TerminalState, Worker)
from .scenarios import EventKind, Scenario
from .validation import validate_response


class Scheduler:
    def __init__(self, policy: Policy, scenario: Scenario, worker: Worker,
                 emit: Callable[..., None], tick_seconds: float = .05,
                 deadline_seconds: float = 20):
        if any(not math.isfinite(x) or x <= 0 for x in (tick_seconds, deadline_seconds)):
            raise ValueError("Tick interval and deadline must be finite and positive")
        self.policy = Policy(policy)
        self.scenario, self.worker, self.emit = scenario, worker, emit
        self.tick_seconds, self.deadline_seconds = tick_seconds, deadline_seconds
        self.pending: deque[Request] = deque()
        self.active: Request | None = None
        self.goal: Goal | None = None
        self.observation = Observation(0, 0, "Robot idle")
        self.goals: dict[int, dict] = {}
        self.terminals: list[Terminal] = []
        self.accepted_plan: dict | None = None
        self.event_index = 0
        self.next_tick = 0.0
        self.ticks: list[float] = []
        self.last_now = 0.0
        self.done = False
        self.stop_reason: str | None = None
        self.worker_wall_seconds = 0.0
        self.rejected_worker_wall_seconds = 0.0
        self.max_pending = 0
        self.dispatched = 0

    def _emit(self, now, event_name, **fields):
        self.emit(event=event_name, elapsed_seconds=now, **fields)

    def _terminal(self, request, state, now, error=None):
        terminal = Terminal(request.request_id, request.goal.version, state, now, error)
        self.terminals.append(terminal)
        self._emit(now, "request_terminal", **asdict(terminal))

    def _fallback(self, now):
        self.accepted_plan = None
        self._emit(now, "wait_fallback", action="wait", goal_version=self.goal.version if self.goal else None)

    def _stop(self, reason, now):
        if self.stop_reason:
            return
        self.stop_reason = reason
        self._emit(now, reason, active_request_id=self.active.request_id if self.active else None,
                   remaining_scenario_events=len(self.scenario.events) - self.event_index)
        state = TerminalState.DEADLINE if reason == "episode_deadline" else TerminalState.ABORTED
        while self.pending:
            self._terminal(self.pending.popleft(), state, now)
        self._fallback(now)

    def _event(self, event, now):
        self._emit(now, "scenario_event", scheduled_at=event.at, lateness_seconds=now-event.at,
                   kind=event.kind, detail=event.detail)
        if event.kind == EventKind.OBSERVATION:
            self.observation = Observation(self.observation.version + 1, now, event.detail)
            self._emit(now, "observation_updated", **asdict(self.observation), triggers_request=False)
            return
        if self.goal:
            old = self.goals[self.goal.version]
            old["superseded_at"] = now
            if old["accepted_at"] is None:
                old["status"] = "superseded"
        version = len(self.goals) + 1
        self.goal = Goal(version, now, event.at, event.detail)
        self.goals[version] = {**asdict(self.goal), "accepted_at": None, "superseded_at": None,
                               "latency_seconds": None, "scheduled_latency_seconds": None,
                               "status": "pending"}
        self._emit(now, "goal_changed", **asdict(self.goal))
        self._fallback(now)
        request = Request(f"r{version:04d}", now, self.goal, self.observation)
        if self.policy == Policy.NEWEST_PENDING:
            while self.pending:
                discarded = self.pending.popleft()
                self._terminal(discarded, TerminalState.COALESCED, now)
                self._emit(now, "pending_replaced", discarded_request_id=discarded.request_id,
                           replacement_request_id=request.request_id)
        self.pending.append(request)
        self.max_pending = max(self.max_pending, len(self.pending))
        self._emit(now, "request_queued", request=asdict(request), pending_count=len(self.pending))

    def _result(self, result: Result, now: float):
        request = self.active
        if request is None or (result.request_id, result.goal_version) != (request.request_id, request.goal.version):
            raise RuntimeError("Worker returned a result for a different request/version")
        if not request.created_at <= result.started_at <= result.finished_at <= now:
            raise RuntimeError("Invalid worker monotonic timestamps")
        elapsed = result.finished_at - result.started_at
        self.worker_wall_seconds += elapsed
        self._emit(now, "response_received", **asdict(result), http_elapsed_seconds=elapsed)
        error = result.error
        plan = None
        if result.failure_kind is not None or result.error is not None:
            state = TerminalState.TIMED_OUT if result.failure_kind == FailureKind.TIMEOUT else TerminalState.FAILED
        elif self.stop_reason:
            state = TerminalState.DEADLINE if self.stop_reason == "episode_deadline" else TerminalState.ABORTED
        elif self.goal is None or request.goal.version != self.goal.version:
            state = TerminalState.STALE
        else:
            try:
                plan = validate_response(result.response)
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                state, error = TerminalState.INVALID, f"{type(exc).__name__}: {exc}"
            else:
                state = TerminalState.ACCEPTED
        self._terminal(request, state, now, error)
        if state == TerminalState.ACCEPTED:
            self.accepted_plan = plan
            record = self.goals[request.goal.version]
            record.update(status="accepted", accepted_at=now,
                          latency_seconds=now-request.goal.created_at,
                          scheduled_latency_seconds=now-request.goal.scheduled_at)
            self._emit(now, "plan_accepted", request_id=request.request_id,
                       goal_version=request.goal.version, plan=plan)
        else:
            # Proxy includes failed HTTP attempts; never interpreted as GPU time/energy.
            self.rejected_worker_wall_seconds += elapsed
            if self.accepted_plan is None:
                self._fallback(now)
        self.active = None
        if result.server_state_uncertain:
            self._stop("episode_aborted", now)

    def step(self, now: float) -> bool:
        if not math.isfinite(now) or now < self.last_now:
            raise ValueError("Clock must be finite and monotonic")
        if self.done:
            return True
        self.last_now = now
        # Apply all due exogenous updates before considering a completed result.
        # Deadline wins ties; no new events or dispatch at/after its boundary.
        if now >= self.deadline_seconds:
            self._stop("episode_deadline", now)
        if not self.stop_reason:
            while self.event_index < len(self.scenario.events) and self.scenario.events[self.event_index].at <= now:
                self._event(self.scenario.events[self.event_index], now)
                self.event_index += 1
        if now >= self.next_tick:
            self.ticks.append(now)
            self._emit(now, "observation_tick", goal_version=self.goal.version if self.goal else None,
                       observation_version=self.observation.version,
                       gap_seconds=self.ticks[-1]-self.ticks[-2] if len(self.ticks) > 1 else None)
            self.next_tick = now + self.tick_seconds  # Never backfill missed ticks.
        if self.active:
            result = self.worker.poll(now)
            if result is not None:
                self._result(result, now)
        if not self.stop_reason and self.active is None and self.pending:
            request = self.pending.popleft()
            self.active = request
            self.dispatched += 1
            self._emit(now, "request_dispatched", request_id=request.request_id,
                       goal_version=request.goal.version, queue_wait_seconds=now-request.created_at,
                       pending_count=len(self.pending))
            try:
                self.worker.submit(request, now)
            except Exception as exc:
                # A failed submit cannot establish that no remote work began.
                self._result(Result(request.request_id, request.goal.version, now, now,
                                    error=f"{type(exc).__name__}: {exc}",
                                    failure_kind=FailureKind.ERROR, server_state_uncertain=True), now)
        schedule_finished = self.event_index == len(self.scenario.events) and now >= self.scenario.end_at
        if (self.stop_reason or schedule_finished) and self.active is None and not self.pending:
            self.done = True
            for record in self.goals.values():
                if record["status"] == "pending":
                    record["status"] = "censored_" + (self.stop_reason or "no_accepted_result")
            self._emit(now, "episode_finished", reason=self.stop_reason or "completed")
        return self.done

    def summary(self):
        return {
            "policy": self.policy.value, "scenario": self.scenario.name,
            "finished": self.done, "episode_outcome": self.stop_reason or ("completed" if self.done else "running"),
            "elapsed_seconds": self.last_now, "requests_created": len(self.goals),
            "requests_dispatched": self.dispatched, "max_pending_count": self.max_pending,
            "terminal_counts": dict(Counter(t.state.value for t in self.terminals)),
            "goals": list(self.goals.values()), "accepted_plan": self.accepted_plan,
            "observation_ticks": len(self.ticks),
            "max_observation_gap_seconds": max((b-a for a, b in zip(self.ticks, self.ticks[1:])), default=None),
            "worker_wall_seconds": self.worker_wall_seconds,
            "rejected_worker_wall_seconds": self.rejected_worker_wall_seconds,
            "unprocessed_scenario_events": len(self.scenario.events)-self.event_index,
            "note": "Wait-only scheduler plumbing. Synthetic observations; no robot action. "
                    "Rejected worker wall time is a discarded-work proxy, not GPU compute or energy. "
                    "Pending coalescing is not inference cancellation.",
        }
